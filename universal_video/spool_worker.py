"""Resident sidecar worker for bounded universal-video jobs.

The worker watches a local spool and never accepts shell commands. It is
intentionally separate from assistant-lab.service so enabling it does not
interrupt the proven DDS3 resident worker.
"""
from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any

from .contract import MAX_JOB_BYTES, canonical_job_hash, validate_from_env
from .finops_observation import build_video_finops_observation, directory_bytes
from .result_conformance import verify_result
from .runner import run_job
from .runtime_preflight import validate_video_runtime


def _dirs(root: Path) -> dict[str, Path]:
    out = {name: root / name for name in ("inbox", "running", "done", "failed", "results")}
    for path in out.values():
        path.mkdir(parents=True, exist_ok=True)
    return out


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a receipt/manifest without exposing a partially-written JSON file."""

    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temp.unlink(missing_ok=True)


def _regular_payload(path: Path) -> tuple[bool, str | None]:
    try:
        info = path.lstat()
    except OSError as exc:
        return False, f"cannot stat payload: {exc}"
    if stat.S_ISLNK(info.st_mode):
        return False, "symlink payloads are forbidden"
    if not stat.S_ISREG(info.st_mode):
        return False, "payload must be a regular file"
    if info.st_size > MAX_JOB_BYTES:
        return False, "payload exceeds bounded contract"
    return True, None


def _reject_payload(path: Path, failed_dir: Path, *, error_type: str, error: str) -> None:
    name = path.name
    try:
        path.unlink(missing_ok=True)
    finally:
        (failed_dir / name).write_text(
            json.dumps(
                {
                    "status": "FAILED",
                    "job_file": name,
                    "error_type": error_type,
                    "error": error[:4000],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def recover_orphaned_jobs(spool_root: Path) -> dict[str, int]:
    """Recover jobs left in running/ by a terminated single resident worker.

    universal-video.service owns this spool and runs one worker process. On a
    fresh process start, any pre-existing running/*.json file is therefore an
    orphan from a previous process. Identical duplicate inbox payloads are
    deduplicated; conflicting payloads are quarantined rather than overwritten.
    """

    paths = _dirs(spool_root)
    recovered = 0
    deduplicated = 0
    conflicts = 0
    rejected = 0
    for claimed in sorted(paths["running"].glob("*.json"), key=lambda p: p.name):
        valid, reason = _regular_payload(claimed)
        if not valid:
            _reject_payload(
                claimed,
                paths["failed"],
                error_type="INVALID_ORPHAN_PAYLOAD",
                error=reason or "invalid orphan payload",
            )
            rejected += 1
            continue

        destination = paths["inbox"] / claimed.name
        if not destination.exists() and not destination.is_symlink():
            claimed.rename(destination)
            recovered += 1
            continue

        destination_valid, destination_reason = _regular_payload(destination)
        if not destination_valid:
            stamp = int(time.time())
            payload_path = paths["failed"] / f"{claimed.stem}.recovery-conflict-{stamp}.payload.json"
            receipt_path = paths["failed"] / f"{claimed.stem}.recovery-conflict-{stamp}.receipt.json"
            claimed.rename(payload_path)
            receipt_path.write_text(
                json.dumps(
                    {
                        "status": "FAILED",
                        "error_type": "RECOVERY_CONFLICT",
                        "job_file": claimed.name,
                        "error": f"existing inbox payload is unsafe: {destination_reason}",
                        "quarantined_payload": payload_path.name,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            conflicts += 1
            continue

        try:
            identical = claimed.read_bytes() == destination.read_bytes()
        except OSError:
            identical = False
        if identical:
            claimed.unlink(missing_ok=True)
            deduplicated += 1
            continue

        stamp = int(time.time())
        payload_path = paths["failed"] / f"{claimed.stem}.recovery-conflict-{stamp}.payload.json"
        receipt_path = paths["failed"] / f"{claimed.stem}.recovery-conflict-{stamp}.receipt.json"
        claimed.rename(payload_path)
        receipt_path.write_text(
            json.dumps(
                {
                    "status": "FAILED",
                    "error_type": "RECOVERY_CONFLICT",
                    "job_file": claimed.name,
                    "quarantined_payload": payload_path.name,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        conflicts += 1
    return {
        "recovered": recovered,
        "deduplicated": deduplicated,
        "conflicts": conflicts,
        "rejected": rejected,
    }


def process_one(spool_root: Path) -> bool:
    paths = _dirs(spool_root)
    candidates: list[tuple[float, str, Path]] = []
    for path in paths["inbox"].glob("*.json"):
        valid, reason = _regular_payload(path)
        if not valid:
            _reject_payload(
                path,
                paths["failed"],
                error_type="INVALID_SPOOL_PAYLOAD",
                error=reason or "invalid spool payload",
            )
            return True
        try:
            mtime = path.lstat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, path.name, path))
    candidates.sort(key=lambda item: (item[0], item[1]))
    if not candidates:
        return False

    source = candidates[0][2]
    claimed = paths["running"] / source.name
    if claimed.exists() or claimed.is_symlink():
        _reject_payload(
            source,
            paths["failed"],
            error_type="RUNNING_NAME_COLLISION",
            error="running spool already contains this job filename",
        )
        return True
    try:
        source.rename(claimed)
    except FileNotFoundError:
        return False

    started = time.monotonic()
    payload: dict | None = None
    try:
        valid, reason = _regular_payload(claimed)
        if not valid:
            raise RuntimeError(reason or "invalid claimed spool payload")
        payload = json.loads(claimed.read_text(encoding="utf-8"))
        validate_video_runtime()
        validated_job = validate_from_env(payload)
        result = run_job(payload, paths["results"])
        result_dir = paths["results"] / str(result.get("job_id") or "")
        media = result.get("media") or {}
        processing_model = result.get("processing_whisper_model") or (result.get("runtime") or {}).get("whisper_model")
        source_info = result.get("source") or {}
        reused_finalized_result = isinstance(result.get("finops_observation"), dict)
        if not reused_finalized_result:
            runtime = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
            elapsed = runtime.get("elapsed_seconds") or (time.monotonic() - started)
            result["finops_observation"] = build_video_finops_observation(
                status=str(result.get("status") or "COMPLETED"),
                elapsed_seconds=float(elapsed),
                input_bytes=media.get("size_bytes"),
                output_bytes=directory_bytes(result_dir),
                video_seconds=media.get("duration_seconds"),
                whisper_model=processing_model,
                source_kind=source_info.get("kind"),
            )
        manifest_path = result_dir / "manifest.json"
        if manifest_path.exists() and not reused_finalized_result:
            _atomic_write_json(manifest_path, result)
        if str(result.get("status") or "") == "COMPLETED":
            conformance = verify_result(
                result_dir,
                expected_job_id=validated_job.job_id,
                expected_profile=validated_job.profile,
                expected_job_hash=canonical_job_hash(validated_job),
                expected_source_file_id=(
                    str(validated_job.source.get("file_id"))
                    if validated_job.source.get("kind") == "google_drive"
                    else None
                ),
                evidence_phase=("REUSE_OBSERVATION" if reused_finalized_result else "GENERATION_FINALIZATION"),
            )
        else:
            conformance = {
                "schema": "universal-video-result-conformance-v1",
                "state": "NOT_ELIGIBLE",
                "reason": "MANIFEST_REVIEW",
                "technical_bundle_ready": False,
                "bridge_production_ready": False,
                "pedagogical_status": "NOT_EVALUATED",
            }
        receipt_payload = dict(result)
        receipt_payload["receipt_version"] = "universal-video-compute-receipt-v1"
        receipt_payload["compute_status"] = str(result.get("status") or "")
        receipt_payload["result_dir"] = str(result_dir)
        receipt_payload["result_locator"] = {"kind": "local_directory", "path": str(result_dir)}
        receipt_payload["result_conformance"] = conformance
        receipt = paths["done"] / source.name
        _atomic_write_json(receipt, receipt_payload)
        claimed.unlink(missing_ok=True)
    except Exception as exc:
        source_kind = None
        if isinstance(payload, dict):
            source_kind = str((payload.get("source") or {}).get("kind") or "") or None
        failure = {
            "status": "FAILED",
            "job_file": source.name,
            "error_type": type(exc).__name__,
            "error": str(exc)[:4000],
            "finops_observation": build_video_finops_observation(
                status="FAILED",
                elapsed_seconds=time.monotonic() - started,
                source_kind=source_kind,
                error_class=type(exc).__name__,
            ),
        }
        _atomic_write_json(paths["failed"] / source.name, failure)
        claimed.unlink(missing_ok=True)
    return True


def run_forever(spool_root: Path, poll_seconds: float) -> None:
    recovery = recover_orphaned_jobs(spool_root)
    if any(recovery.values()):
        print(json.dumps({"event": "spool_recovery", **recovery}, sort_keys=True), flush=True)
    while True:
        if process_one(spool_root):
            continue
        time.sleep(poll_seconds)


def main() -> None:
    root = Path(os.getenv("UNIVERSAL_VIDEO_SPOOL_ROOT", "/opt/bridge-school/universal-video/spool"))
    poll = max(1.0, float(os.getenv("UNIVERSAL_VIDEO_POLL_SECONDS", "2")))
    run_forever(root, poll)


if __name__ == "__main__":
    main()
