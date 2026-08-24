"""Bounded retention for the Oracle universal-video sidecar.

The maintenance pass never follows symlinks and never deletes media referenced by
pending/running jobs or a results directory for a running job. It is designed to
run from a systemd timer and emits a compact JSON report only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .contract import MAX_JOB_BYTES

GIB = 1024**3
DAY = 24 * 3600
HOUR = 3600
TERMINAL_STATUSES = frozenset({"COMPLETED", "REVIEW", "FAILED"})


@dataclass(frozen=True)
class RetentionPolicy:
    done_ttl_seconds: int = 14 * DAY
    failed_ttl_seconds: int = 30 * DAY
    results_ttl_seconds: int = 30 * DAY
    abandoned_results_ttl_seconds: int = 7 * DAY
    media_ttl_seconds: int = 7 * DAY
    max_receipts_per_bucket: int = 500
    max_result_dirs: int = 200
    max_result_bytes: int = 20 * GIB
    max_media_files: int = 10
    max_media_bytes: int = 32 * GIB
    budget_grace_seconds: int = 2 * DAY
    media_budget_grace_seconds: int = HOUR
    max_deletes_per_run: int = 100


@dataclass(frozen=True)
class Candidate:
    path: Path
    category: str
    mtime: float
    size_bytes: int
    reason: str


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} outside bounded range")
    return value


def policy_from_env() -> RetentionPolicy:
    return RetentionPolicy(
        done_ttl_seconds=_env_int("UNIVERSAL_VIDEO_RETENTION_DONE_DAYS", 14, 1, 365) * DAY,
        failed_ttl_seconds=_env_int("UNIVERSAL_VIDEO_RETENTION_FAILED_DAYS", 30, 1, 365) * DAY,
        results_ttl_seconds=_env_int("UNIVERSAL_VIDEO_RETENTION_RESULTS_DAYS", 30, 1, 365) * DAY,
        abandoned_results_ttl_seconds=_env_int("UNIVERSAL_VIDEO_RETENTION_ABANDONED_RESULTS_DAYS", 7, 1, 90) * DAY,
        media_ttl_seconds=_env_int("UNIVERSAL_VIDEO_RETENTION_MEDIA_DAYS", 7, 1, 90) * DAY,
        max_receipts_per_bucket=_env_int("UNIVERSAL_VIDEO_MAX_RECEIPTS", 500, 10, 100000),
        max_result_dirs=_env_int("UNIVERSAL_VIDEO_MAX_RESULT_DIRS", 200, 5, 10000),
        max_result_bytes=_env_int("UNIVERSAL_VIDEO_MAX_RESULT_BYTES", 20 * GIB, GIB, 512 * GIB),
        max_media_files=_env_int("UNIVERSAL_VIDEO_MAX_MEDIA_FILES", 10, 1, 1000),
        max_media_bytes=_env_int("UNIVERSAL_VIDEO_MAX_MEDIA_BYTES", 32 * GIB, GIB, 512 * GIB),
        budget_grace_seconds=_env_int("UNIVERSAL_VIDEO_RETENTION_BUDGET_GRACE_HOURS", 48, 1, 720) * HOUR,
        media_budget_grace_seconds=_env_int("UNIVERSAL_VIDEO_MEDIA_BUDGET_GRACE_HOURS", 1, 1, 168) * HOUR,
        max_deletes_per_run=_env_int("UNIVERSAL_VIDEO_MAX_DELETES_PER_MAINTENANCE", 100, 1, 1000),
    )


def _regular_file(path: Path, *, max_bytes: int | None = None) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    return max_bytes is None or info.st_size <= max_bytes


def _safe_json(path: Path) -> dict[str, Any] | None:
    if not _regular_file(path, max_bytes=MAX_JOB_BYTES):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _protected_state(spool_root: Path, media_root: Path) -> tuple[set[str], set[Path]]:
    active_job_ids: set[str] = set()
    protected_media: set[Path] = set()
    media_root = media_root.resolve()
    for bucket in ("inbox", "running"):
        root = spool_root / bucket
        if not root.is_dir():
            continue
        for path in root.glob("*.json"):
            payload = _safe_json(path)
            if not payload:
                continue
            job_id = str(payload.get("job_id") or "").strip()
            if job_id:
                active_job_ids.add(job_id)
            source = payload.get("source")
            if not isinstance(source, dict) or source.get("kind") != "local_path":
                continue
            raw = str(source.get("path") or "").strip()
            if not raw:
                continue
            try:
                candidate = Path(raw).resolve()
                candidate.relative_to(media_root)
            except (OSError, ValueError):
                continue
            protected_media.add(candidate)
    return active_job_ids, protected_media


def _tree_size_no_links(path: Path) -> int | None:
    total = 0
    try:
        for entry in path.rglob("*"):
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode):
                return None
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    except OSError:
        return None
    return total


def _terminal_result_dir(path: Path) -> bool:
    if not path.is_dir() or path.is_symlink():
        return False
    manifest = _safe_json(path / "manifest.json")
    if not manifest:
        return False
    return str(manifest.get("status") or "").upper() in TERMINAL_STATUSES


def _dedupe(candidates: Iterable[Candidate]) -> list[Candidate]:
    chosen: dict[Path, Candidate] = {}
    for item in candidates:
        current = chosen.get(item.path)
        if current is None or item.mtime < current.mtime:
            chosen[item.path] = item
    return sorted(chosen.values(), key=lambda item: (item.mtime, str(item.path)))


def build_cleanup_plan(
    base_dir: Path,
    *,
    policy: RetentionPolicy | None = None,
    now: float | None = None,
) -> list[Candidate]:
    policy = policy or policy_from_env()
    now = time.time() if now is None else float(now)
    spool_root = base_dir / "spool"
    media_root = base_dir / "media"
    active_job_ids, protected_media = _protected_state(spool_root, media_root)
    candidates: list[Candidate] = []

    for bucket, ttl in (("done", policy.done_ttl_seconds), ("failed", policy.failed_ttl_seconds)):
        root = spool_root / bucket
        files: list[tuple[float, Path, int]] = []
        if root.is_dir():
            for path in root.iterdir():
                if not _regular_file(path):
                    continue
                info = path.lstat()
                files.append((info.st_mtime, path, info.st_size))
        files.sort(key=lambda item: (item[0], item[1].name))
        excess = max(0, len(files) - policy.max_receipts_per_bucket)
        for index, (mtime, path, size) in enumerate(files):
            age = now - mtime
            if age >= ttl:
                candidates.append(Candidate(path, bucket, mtime, size, "ttl"))
            elif index < excess and age >= policy.budget_grace_seconds:
                candidates.append(Candidate(path, bucket, mtime, size, "count_budget"))

    result_dirs: list[tuple[float, Path, int]] = []
    results_root = spool_root / "results"
    if results_root.is_dir():
        for path in results_root.iterdir():
            if path.name in active_job_ids or path.is_symlink() or not path.is_dir():
                continue
            size = _tree_size_no_links(path)
            if size is None:
                continue
            try:
                mtime = path.lstat().st_mtime
            except OSError:
                continue
            age = now - mtime
            if not _terminal_result_dir(path):
                if age >= policy.abandoned_results_ttl_seconds:
                    candidates.append(Candidate(path, "results", mtime, size, "abandoned_ttl"))
                continue
            result_dirs.append((mtime, path, size))
    result_dirs.sort(key=lambda item: (item[0], item[1].name))
    total_result_bytes = sum(item[2] for item in result_dirs)
    excess_dirs = max(0, len(result_dirs) - policy.max_result_dirs)
    bytes_to_release = max(0, total_result_bytes - policy.max_result_bytes)
    released = 0
    for index, (mtime, path, size) in enumerate(result_dirs):
        age = now - mtime
        if age >= policy.results_ttl_seconds:
            candidates.append(Candidate(path, "results", mtime, size, "ttl"))
            released += size
            continue
        over_count = index < excess_dirs
        over_bytes = released < bytes_to_release
        if (over_count or over_bytes) and age >= policy.budget_grace_seconds:
            reason = "count_budget" if over_count and not over_bytes else "byte_budget"
            candidates.append(Candidate(path, "results", mtime, size, reason))
            released += size

    media_files: list[tuple[float, Path, int]] = []
    if media_root.is_dir():
        for path in media_root.rglob("*"):
            try:
                info = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            try:
                resolved = path.resolve()
                resolved.relative_to(media_root.resolve())
            except (OSError, ValueError):
                continue
            if resolved in protected_media:
                continue
            media_files.append((info.st_mtime, path, info.st_size))
    media_files.sort(key=lambda item: (item[0], str(item[1])))
    total_media_bytes = sum(item[2] for item in media_files)
    excess_media = max(0, len(media_files) - policy.max_media_files)
    media_bytes_to_release = max(0, total_media_bytes - policy.max_media_bytes)
    released_media = 0
    for index, (mtime, path, size) in enumerate(media_files):
        age = now - mtime
        if age >= policy.media_ttl_seconds:
            candidates.append(Candidate(path, "media", mtime, size, "ttl"))
            released_media += size
            continue
        over_count = index < excess_media
        over_bytes = released_media < media_bytes_to_release
        if (over_count or over_bytes) and age >= policy.media_budget_grace_seconds:
            reason = "count_budget" if over_count and not over_bytes else "byte_budget"
            candidates.append(Candidate(path, "media", mtime, size, reason))
            released_media += size

    return _dedupe(candidates)[: policy.max_deletes_per_run]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def apply_cleanup_plan(base_dir: Path, plan: Iterable[Candidate], *, dry_run: bool) -> dict[str, Any]:
    roots = {
        "done": base_dir / "spool" / "done",
        "failed": base_dir / "spool" / "failed",
        "results": base_dir / "spool" / "results",
        "media": base_dir / "media",
    }
    selected = list(plan)
    deleted = 0
    bytes_released = 0
    by_category: dict[str, int] = {}
    for item in selected:
        root = roots[item.category]
        if not _inside(item.path, root):
            raise RuntimeError("cleanup candidate escapes managed root")
        if item.path.is_symlink():
            raise RuntimeError("cleanup refuses symlink candidate")
        by_category[item.category] = by_category.get(item.category, 0) + 1
        if dry_run:
            continue
        if item.path.is_dir():
            shutil.rmtree(item.path)
        else:
            item.path.unlink(missing_ok=True)
        deleted += 1
        bytes_released += item.size_bytes
    return {
        "status": "DRY_RUN" if dry_run else "APPLIED",
        "candidates": len(selected),
        "deleted": deleted,
        "bytes_released": bytes_released,
        "by_category": by_category,
    }


def run_maintenance(base_dir: Path, *, dry_run: bool) -> dict[str, Any]:
    policy = policy_from_env()
    plan = build_cleanup_plan(base_dir, policy=policy)
    return apply_cleanup_plan(base_dir, plan, dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=os.getenv("UNIVERSAL_VIDEO_DIR", "/opt/bridge-school/universal-video"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = run_maintenance(Path(args.base_dir), dry_run=not args.apply)
    print(json.dumps(report, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
