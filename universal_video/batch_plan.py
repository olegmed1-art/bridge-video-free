"""Offline Drive inventory and fixed subsets; this is NOT a queue intake request.

Input is a raw files.list JSON object (all pages collected, no nextPageToken or
incompleteSearch), or a JSON list of raw Drive file metadata. Every row must be
an original video in the exact source folder. Nothing is silently filtered.
version and modifiedTime are required even when a checksum is available.
Missing checksums and videoMediaMetadata.durationMillis remain null; the optional
estimated_cost_usd annotation is preserved, never estimated from size/duration.

Source identity is the Drive ID. The fingerprint binds parent, revision, size,
MIME and all supplied checksums, excluding the display name. Inventory digests
also bind names and observations; plan digests bind destinations and batch size.
These are metadata claims, not verification of remote bytes or current state.

Usage (stdout only, no credentials or service clients):
    python -m universal_video.batch_plan snapshot.json --source-folder-id ID \
        --output-folder-id ID --work-folder-id ID --max-jobs-per-batch 1000

Execution requires a separately reviewed subset-intake design and fresh source
verification. The existing queue still limits discovery to 1000 videos and must
not receive a full-folder request generated from these planning-only subsets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_JOBS_PER_BATCH = 1000  # Keep in sync with video_queue.MAX_FILES; never lift it.
MAX_SNAPSHOT_BYTES = 64 * 1024**2
MIN_FILE_BYTES = 1024**2
MAX_FILE_BYTES = 64 * 1024**3
DRIVE_ID_RE = re.compile(r"[A-Za-z0-9_-]{10,200}")
MODIFIED_TIME_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)
CHECKSUMS = (("sha256Checksum", "sha256", 64), ("sha1Checksum", "sha1", 40),
             ("md5Checksum", "md5", 32))


class BatchPlanError(ValueError):
    """The snapshot or planning bounds cannot safely define fixed subsets."""


def _json(value: Any, *, ensure_ascii: bool = False) -> str:
    try:
        return json.dumps(value, ensure_ascii=ensure_ascii, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise BatchPlanError("snapshot must contain finite JSON values") from exc


def _digest(value: Any) -> str:
    try:
        return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()
    except UnicodeError as exc:
        raise BatchPlanError("snapshot contains invalid Unicode") from exc


def _drive_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not DRIVE_ID_RE.fullmatch(value):
        raise BatchPlanError(f"invalid {field}")
    return value


def _integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value):
        value = int(value)
    if type(value) is not int or not minimum <= value <= maximum:
        raise BatchPlanError(f"invalid {field}")
    return value


def _source(raw: Any, source: str, destinations: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BatchPlanError("each Drive metadata row must be an object")
    file_id = _drive_id(raw.get("id"), "Drive file id")
    if file_id in destinations or file_id == source:
        raise BatchPlanError("source/output collision or folder used as source file")
    if raw.get("parents") != [source]:
        raise BatchPlanError("Drive file must be an exact direct child of source folder")
    if raw.get("trashed") is not False:
        raise BatchPlanError("Drive file is trashed or trash state is unknown")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 500:
        raise BatchPlanError("invalid Drive file name")
    mime = raw.get("mimeType")
    if (not isinstance(mime, str) or not re.fullmatch(r"video/[A-Za-z0-9.+_-]+", mime)
            or name.startswith("AI_PART_")):
        raise BatchPlanError("snapshot must contain original video sources only")
    size = _integer(raw.get("size"), "size", minimum=MIN_FILE_BYTES, maximum=MAX_FILE_BYTES)
    version = raw.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", version):
        raise BatchPlanError("source revision version is unresolved")
    _integer(version, "source revision version", minimum=1, maximum=2**64 - 1)
    modified = raw.get("modifiedTime")
    if not isinstance(modified, str) or not MODIFIED_TIME_RE.fullmatch(modified):
        raise BatchPlanError("source revision modifiedTime is unresolved")
    try:
        datetime.fromisoformat(modified.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BatchPlanError("invalid source revision modifiedTime") from exc
    checksums = {}
    checksum = None
    for field, label, length in CHECKSUMS:
        value = raw.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", value):
            raise BatchPlanError(f"invalid Drive checksum metadata: {field}")
        checksums[field] = value.lower()
        if checksum is None:
            checksum = f"{label}:{value.lower()}"
    media = raw.get("videoMediaMetadata")
    if media is not None and not isinstance(media, dict):
        raise BatchPlanError("invalid videoMediaMetadata")
    duration = (media or {}).get("durationMillis")
    if duration is not None:
        duration = _integer(duration, "durationMillis", minimum=1, maximum=2**63 - 1)
    cost = raw.get("estimated_cost_usd")
    if cost is not None:
        if type(cost) not in (int, float) or not 0 <= cost <= 1e308 or not math.isfinite(cost):
            raise BatchPlanError("invalid estimated_cost_usd")
    revision = {
        "source_identity": f"google_drive:{file_id}",
        "file_id": file_id,
        "parent": source,
        "version": version,
        "modifiedTime": modified,
        "mime_type": mime,
        "size_bytes": size,
        "checksum": checksum,
        "checksums": checksums,
    }
    return {
        **revision,
        "name": name,
        "trashed": False,
        "source_fingerprint": _digest(revision),
        "duration_ms": duration,
        "estimated_cost_usd": cost,
    }


def build_batch_plan(
    snapshot: Any, *, source_folder_id: str, output_folder_id: str,
    work_folder_id: str, max_jobs_per_batch: int = MAX_JOBS_PER_BATCH,
) -> dict[str, Any]:
    """Validate all rows before emitting a deterministic, execution-disabled plan.

    Sort by file_id, not display name. Every source occurs in exactly one fixed
    subset. No current-time, credential, network, database or queue dependency.
    """
    source = _drive_id(source_folder_id, "source_folder_id")
    output = _drive_id(output_folder_id, "output_folder_id")
    work = _drive_id(work_folder_id, "work_folder_id")
    if source in {output, work}:
        raise BatchPlanError("source/output collision: source folder must be isolated")
    if type(max_jobs_per_batch) is not int or not 1 <= max_jobs_per_batch <= MAX_JOBS_PER_BATCH:
        raise BatchPlanError("max_jobs_per_batch must be an integer from 1 to 1000")
    _json(snapshot)  # Reject non-finite values even in unused raw metadata.
    rows = snapshot
    if isinstance(snapshot, dict):
        if snapshot.get("nextPageToken") not in (None, ""):
            raise BatchPlanError("snapshot has unresolved pagination")
        if "incompleteSearch" in snapshot and snapshot["incompleteSearch"] is not False:
            raise BatchPlanError("snapshot search is incomplete or unknown")
        rows = snapshot.get("files")
    if not isinstance(rows, list) or not rows:
        raise BatchPlanError("snapshot files must be a non-empty list")
    files = []
    seen = set()
    for raw in rows:
        item = _source(raw, source, {output, work})
        if item["file_id"] in seen:
            raise BatchPlanError("duplicate Drive file id")
        seen.add(item["file_id"])
        files.append(item)
    files.sort(key=lambda item: item["file_id"])
    inventory = {"schema": "universal-video-inventory-v1", "source_folder_id": source, "files": files}
    batches = []
    for offset in range(0, len(files), max_jobs_per_batch):
        subset = files[offset:offset + max_jobs_per_batch]
        batches.append({
            "sequence": len(batches) + 1,
            "file_ids": [item["file_id"] for item in subset],
            "expected_count": len(subset),
            "subset_sha256": _digest(subset),
            "execution_allowed": False,
        })
    plan = {
        "schema": "universal-video-batch-plan-v1",
        "source_folder_id": source,
        "output_folder_id": output,
        "work_folder_id": work,
        "max_jobs_per_batch": max_jobs_per_batch,
        "expected_count": len(files),
        "total_size_bytes": sum(item["size_bytes"] for item in files),
        "inventory_sha256": _digest(inventory),
        "files": files,
        "batches": batches,
        "execution_allowed": False,
        "database_persistence_allowed": False,
        "canonical_promotion_allowed": False,
    }
    return {**plan, "plan_sha256": _digest(plan)}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise BatchPlanError("snapshot JSON has duplicate object keys")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise BatchPlanError("snapshot must contain finite JSON values")


def read_snapshot(path: Path) -> Any:
    """Read only a bounded regular local JSON file; never download metadata."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise BatchPlanError("snapshot must be a regular file")
        if info.st_size > MAX_SNAPSHOT_BYTES:
            raise BatchPlanError("snapshot exceeds bounded size")
        with path.open("rb") as handle:
            raw = handle.read(MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise BatchPlanError("snapshot exceeds bounded size")
        return json.loads(raw.decode("utf-8"), parse_constant=_reject_constant,
                          object_pairs_hook=_unique_object)
    except BatchPlanError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise BatchPlanError("cannot read valid local snapshot JSON") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--source-folder-id", required=True)
    parser.add_argument("--output-folder-id", required=True)
    parser.add_argument("--work-folder-id", required=True)
    parser.add_argument("--max-jobs-per-batch", type=int, default=MAX_JOBS_PER_BATCH)
    args = parser.parse_args(argv)
    try:
        result = build_batch_plan(
            read_snapshot(args.snapshot), source_folder_id=args.source_folder_id,
            output_folder_id=args.output_folder_id, work_folder_id=args.work_folder_id,
            max_jobs_per_batch=args.max_jobs_per_batch,
        )
    except BatchPlanError as exc:
        print(_json({"schema": "universal-video-batch-plan-error-v1", "error": str(exc),
                     "execution_allowed": False}))
        return 1
    # Redirected Windows stdout may use a legacy code page. Escape only the
    # transport JSON; canonical digests continue to use the original UTF-8 text.
    print(_json(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
