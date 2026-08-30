"""Resident sidecar worker for bounded universal-video jobs.

The worker watches a local spool and never accepts shell commands. It is
intentionally separate from assistant-lab.service so enabling it does not
interrupt the proven DDS3 resident worker.
"""
from __future__ import annotations

import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any

from .contract import MAX_JOB_BYTES, VideoContractError, canonical_job_hash, validate_from_env, validate_job
from .drive_stage import DriveStageError, remove_staged_job, stage_drive_job
from .finops_observation import build_video_finops_observation, directory_bytes
from .result_conformance import ResultConformanceError, verify_result
from .runner import run_job
from .runtime_preflight import VideoRuntimeUnavailable, validate_staged_video, validate_video_runtime
from .server_review import ServerReviewError, build_server_review


ERROR_CODE_RE = re.compile(r"^UV_[A-Z0-9_]{1,96}$")
ERROR_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,119}$")
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
RUNTIME_ATTESTATION_FIELDS = frozenset({
    "schema", "job_id", "request_commit", "requested_runtime_commit",
    "installed_runtime_commit", "observed_job_runtime_commit", "profile",
    "job_hash", "source_file_id", "canonical_output_untouched",
    "canonical_promotion_allowed", "publication_state",
})


def _failure_type(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if ERROR_TYPE_RE.fullmatch(name) else "WorkerFailure"


def _failure_code(exc: BaseException) -> str:
    explicit = str(getattr(exc, "error_code", "") or "")
    if ERROR_CODE_RE.fullmatch(explicit):
        return explicit
    if isinstance(exc, VideoRuntimeUnavailable):
        message = str(exc)
        if message.startswith("VIDEO_RUNTIME_MISSING_TOOL:"):
            return "UV_RUNTIME_DEPENDENCY_MISSING"
        if message.startswith("VIDEO_RUNTIME_MISSING_ASR:"):
            return "UV_RUNTIME_ASR_MISSING"
        return "UV_RUNTIME_PREFLIGHT_FAILED"
    if isinstance(exc, VideoContractError):
        return "UV_JOB_CONTRACT_INVALID"
    if isinstance(exc, json.JSONDecodeError):
        return "UV_JOB_JSON_INVALID"
    if isinstance(exc, ResultConformanceError):
        return "UV_RESULT_CONFORMANCE_FAILED"
    if isinstance(exc, ServerReviewError):
        return "UV_SERVER_REVIEW_FAILED"
    if isinstance(exc, DriveStageError):
        return "UV_DRIVE_STAGE_FAILED"
    if isinstance(exc, TimeoutError):
        return "UV_WORKER_TIMEOUT"
    if isinstance(exc, OSError):
        return "UV_WORKER_IO_FAILED"
    if isinstance(exc, (TypeError, ValueError)):
        return "UV_WORKER_INPUT_INVALID"
    if isinstance(exc, RuntimeError):
        return "UV_WORKER_RUNTIME_FAILED"
    return "UV_WORKER_FAILED"


def _dirs(root: Path) -> dict[str, Path]:
    out = {name: root / name for name in ("inbox", "running", "done", "failed", "results", "progress")}
    for path in out.values():
        path.mkdir(parents=True, exist_ok=True)
    return out


def _write_progress(paths: dict[str, Path], job_id: str, state: str) -> None:
    if not re.fullmatch(r"^[A-Za-z0-9._:-]{1,160}$", job_id):
        raise RuntimeError("invalid progress job id")
    _atomic_write_json(
        paths["progress"] / f"{job_id}.json",
        {
            "schema": "universal-video-pipeline-progress-v1",
            "job_id": job_id,
            "state": state,
            "observed_at_unix": time.time(),
        },
    )


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


def _runtime_attestation(
    *, payload: dict[str, Any], result: dict[str, Any], job_hash: str
) -> dict[str, Any] | None:
    """Build provenance only from values observed by the resident worker.

    Legacy jobs without an explicit request commit remain unattested.  The
    exporter must classify them INCONCLUSIVE rather than reconstructing or
    guessing provenance after completion.
    """

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    request_commit = str(metadata.get("request_commit") or "").strip().lower()
    requested_runtime = str(metadata.get("requested_runtime_commit") or "").strip().lower()
    installed_runtime = os.getenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "").strip().lower()
    observed_runtime = str(result.get("processing_revision") or "").strip().lower()
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_file_id = str(source.get("file_id") or "").strip()
    if not (
        HEX40_RE.fullmatch(request_commit)
        and HEX40_RE.fullmatch(requested_runtime)
        and HEX40_RE.fullmatch(installed_runtime)
        and HEX40_RE.fullmatch(observed_runtime)
        and re.fullmatch(r"^[A-Za-z0-9_-]{10,200}$", source_file_id)
    ):
        return None
    return {
        "schema": "universal-video-runtime-job-attestation-v1",
        "job_id": str(result.get("job_id") or ""),
        "request_commit": request_commit,
        "requested_runtime_commit": requested_runtime,
        "installed_runtime_commit": installed_runtime,
        "observed_job_runtime_commit": observed_runtime,
        "profile": str(result.get("profile") or ""),
        "job_hash": job_hash,
        "source_file_id": source_file_id,
        "canonical_output_untouched": True,
        "canonical_promotion_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }


def write_resident_status(spool_root: Path, status_path: Path) -> dict[str, Any]:
    """Publish a fresh v2 status from resident-owned spool receipts."""

    paths = _dirs(spool_root)
    installed_runtime = os.getenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "").strip().lower()
    if not HEX40_RE.fullmatch(installed_runtime):
        raise RuntimeError("installed runtime commit is unavailable")
    active_jobs = sorted(path.stem for path in paths["running"].glob("*.json"))[:32]
    candidates: list[tuple[float, dict[str, Any]]] = []
    for path in paths["done"].glob("*.json"):
        valid, _ = _regular_payload(path)
        if not valid:
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            attestation = value.get("runtime_attestation") if isinstance(value, dict) else None
            if (
                isinstance(attestation, dict)
                and set(attestation) == RUNTIME_ATTESTATION_FIELDS
                and attestation.get("schema") == "universal-video-runtime-job-attestation-v1"
                and attestation.get("installed_runtime_commit") == installed_runtime
            ):
                candidates.append((path.stat().st_mtime, attestation))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
    attestations = [item for _, item in sorted(candidates, key=lambda row: row[0], reverse=True)[:32]]
    status = {
        "schema": "universal-video-resident-status-v2",
        "instance_state": "RUNNING",
        "active_jobs": active_jobs,
        "observed_at_unix": time.time(),
        "installed_runtime_commit": installed_runtime,
        "job_attestations": attestations,
    }
    status_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(status_path, status)
    return status


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


def _reject_payload(path: Path, failed_dir: Path, *, error_code: str) -> None:
    name = path.name
    if not ERROR_CODE_RE.fullmatch(error_code):
        error_code = "UV_SPOOL_PAYLOAD_REJECTED"
    try:
        path.unlink(missing_ok=True)
    finally:
        (failed_dir / name).write_text(
            json.dumps(
                {
                    "status": "FAILED",
                    "job_file": name,
                    "error_type": "SpoolPayloadRejected",
                    "error_code": error_code,
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
        valid, _ = _regular_payload(claimed)
        if not valid:
            _reject_payload(
                claimed,
                paths["failed"],
                error_code="UV_INVALID_ORPHAN_PAYLOAD",
            )
            rejected += 1
            continue

        destination = paths["inbox"] / claimed.name
        if not destination.exists() and not destination.is_symlink():
            claimed.rename(destination)
            recovered += 1
            continue

        destination_valid, _ = _regular_payload(destination)
        if not destination_valid:
            stamp = int(time.time())
            payload_path = paths["failed"] / f"{claimed.stem}.recovery-conflict-{stamp}.payload.json"
            receipt_path = paths["failed"] / f"{claimed.stem}.recovery-conflict-{stamp}.receipt.json"
            claimed.rename(payload_path)
            receipt_path.write_text(
                json.dumps(
                    {
                        "status": "FAILED",
                        "error_type": "SpoolRecoveryConflict",
                        "error_code": "UV_SPOOL_RECOVERY_CONFLICT",
                        "job_file": claimed.name,
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
                    "error_type": "SpoolRecoveryConflict",
                    "error_code": "UV_SPOOL_RECOVERY_CONFLICT",
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
                error_code="UV_INVALID_SPOOL_PAYLOAD",
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
            error_code="UV_RUNNING_NAME_COLLISION",
        )
        return True
    try:
        source.rename(claimed)
    except FileNotFoundError:
        return False

    started = time.monotonic()
    payload: dict | None = None
    staged_job_dir: Path | None = None
    media_root = Path(os.getenv("UNIVERSAL_VIDEO_MEDIA_ROOT", "/opt/bridge-school/universal-video/media"))
    try:
        valid, reason = _regular_payload(claimed)
        if not valid:
            raise RuntimeError(reason or "invalid claimed spool payload")
        payload = json.loads(claimed.read_text(encoding="utf-8"))
        validate_video_runtime()
        intake_job = validate_job(payload)
        if intake_job.source.get("kind") == "google_drive":
            _write_progress(paths, intake_job.job_id, "DOWNLOADING_FROM_DRIVE")
            payload, staged_job_dir = stage_drive_job(intake_job, payload, media_root)
            validate_staged_video(Path(str((payload.get("source") or {}).get("path") or "")))
            _write_progress(paths, intake_job.job_id, "SOURCE_READY_ON_ORACLE")
        validated_job = validate_from_env(payload)
        _write_progress(paths, validated_job.job_id, "PROCESSING")
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
            review_path = result_dir / "server_review.json"
            if not review_path.exists():
                base_conformance = verify_result(
                    result_dir,
                    expected_job_id=validated_job.job_id,
                    expected_profile=validated_job.profile,
                    expected_job_hash=canonical_job_hash(validated_job),
                    expected_source_file_id=(
                        str(validated_job.source.get("file_id"))
                        if validated_job.source.get("kind") in {"google_drive", "oracle_drive_staged"}
                        else None
                    ),
                    evidence_phase=("REUSE_OBSERVATION" if reused_finalized_result else "GENERATION_FINALIZATION"),
                )
                server_review = build_server_review(result_dir, base_conformance)
                _atomic_write_json(review_path, server_review)
            conformance = verify_result(
                result_dir,
                expected_job_id=validated_job.job_id,
                expected_profile=validated_job.profile,
                expected_job_hash=canonical_job_hash(validated_job),
                expected_source_file_id=(
                    str(validated_job.source.get("file_id"))
                    if validated_job.source.get("kind") in {"google_drive", "oracle_drive_staged"}
                    else None
                ),
                evidence_phase=("REUSE_OBSERVATION" if reused_finalized_result else "GENERATION_FINALIZATION"),
                require_server_review=True,
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
        attestation = _runtime_attestation(
            payload=payload,
            result=result,
            job_hash=canonical_job_hash(validated_job),
        )
        if attestation is not None:
            receipt_payload["runtime_attestation"] = attestation
        receipt = paths["done"] / source.name
        _atomic_write_json(receipt, receipt_payload)
        try:
            _write_progress(paths, validated_job.job_id, "RESULT_READY" if str(result.get("status") or "") == "COMPLETED" else "REVIEW")
        except OSError:
            pass
        claimed.unlink(missing_ok=True)
    except Exception as exc:
        source_kind = None
        if isinstance(payload, dict):
            source_kind = str((payload.get("source") or {}).get("kind") or "") or None
        failure = {
            "status": "FAILED",
            "job_file": source.name,
            "error_type": _failure_type(exc),
            "error_code": _failure_code(exc),
            "finops_observation": build_video_finops_observation(
                status="FAILED",
                elapsed_seconds=time.monotonic() - started,
                source_kind=source_kind,
                error_class=type(exc).__name__,
            ),
        }
        _atomic_write_json(paths["failed"] / source.name, failure)
        if isinstance(payload, dict):
            failed_job_id = str(payload.get("job_id") or "")
            if re.fullmatch(r"^[A-Za-z0-9._:-]{1,160}$", failed_job_id):
                try:
                    _write_progress(paths, failed_job_id, "FAILED")
                except OSError:
                    pass
        claimed.unlink(missing_ok=True)
    finally:
        if staged_job_dir is not None and staged_job_dir.exists():
            try:
                remove_staged_job(staged_job_dir, media_root)
            except (DriveStageError, OSError):
                # A terminal receipt remains authoritative. Maintenance may
                # later quarantine an undeletable staging directory.
                pass
    return True


def run_forever(spool_root: Path, poll_seconds: float) -> None:
    recovery = recover_orphaned_jobs(spool_root)
    if any(recovery.values()):
        print(json.dumps({"event": "spool_recovery", **recovery}, sort_keys=True), flush=True)
    status_path = Path(
        os.getenv(
            "UNIVERSAL_VIDEO_STATUS_PATH",
            "/run/bridge-school/universal-video-status.json",
        )
    )
    while True:
        processed = process_one(spool_root)
        queue_configured = bool(
            os.getenv("BRIDGE_VIDEO_QUEUE_DATABASE_URL", "").strip()
            or os.getenv("BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE", "").strip()
            or os.getenv("BRIDGE_WORKER_DATABASE_URL", "").strip()
        )
        if not processed and queue_configured:
            from .neon_worker import process_one_neon

            processed = process_one_neon()
        write_resident_status(spool_root, status_path)
        if processed:
            continue
        time.sleep(poll_seconds)


def main() -> None:
    root = Path(os.getenv("UNIVERSAL_VIDEO_SPOOL_ROOT", "/opt/bridge-school/universal-video/spool"))
    poll = max(1.0, float(os.getenv("UNIVERSAL_VIDEO_POLL_SECONDS", "2")))
    run_forever(root, poll)


if __name__ == "__main__":
    main()
