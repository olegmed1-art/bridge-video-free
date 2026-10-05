"""Resident sidecar worker for bounded universal-video jobs.

The worker watches a local spool and never accepts shell commands. It is
intentionally separate from assistant-lab.service so enabling it does not
interrupt the proven DDS3 resident worker.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
import time
import requests
from pathlib import Path
from typing import Any

from .contract import MAX_JOB_BYTES, VideoContractError, canonical_job_hash, validate_from_env, validate_job
from .drive_stage import DriveStageError, remove_staged_job, stage_drive_job
from .durable_drive import (configured_binding, finalize_drive_job, cleanup_proof_matches,
                            read_receipt, prepare_compute_recovery)
from .finops_observation import build_video_finops_observation, directory_bytes
from .result_conformance import ResultConformanceError, verify_result
from .runner import run_job
from .book_contract import BookJob, strict_json, validate_book_job
from .book_worker import process_claimed_book, resume_pending_completion

from .runtime_preflight import VideoRuntimeUnavailable, validate_staged_video, validate_video_runtime
from .server_review import ServerReviewError, build_server_review
from .workload_lock import shared_workload_lock
from .drive_cleanup import queue_cleanup, retry_cleanup, preserve_pending_completion

def _spool_job(payload):
    if isinstance(payload, dict) and payload.get("schema") == "book-single-atom-job-v1":
        return validate_book_job(payload)
    return validate_job(payload)


def _spool_job_hash(job):
    return job.job_hash if isinstance(job, BookJob) else canonical_job_hash(job)



ERROR_CODE_RE = re.compile(r"^UV_[A-Z0-9_]{1,96}$")
ERROR_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,119}$")
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
PRECANARY_STARTUP_PROBE_ENV = "UNIVERSAL_VIDEO_PRECANARY_STARTUP_PROBE"
PRECANARY_SPOOL_ROOT = Path("/tmp/issue881/spool")
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
    out = {name: root / name for name in ("inbox", "running", "done", "failed", "results", "progress", "recovery")}
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


def _process_start_ticks(process_id: int) -> int:
    """Return Linux boot-relative start ticks for one exact process."""

    stat_path = Path("/proc/self/stat") if process_id == os.getpid() else Path(f"/proc/{process_id}/stat")
    raw = stat_path.read_text(encoding="utf-8")
    tail = raw.rsplit(")", 1)
    if len(tail) != 2:
        raise RuntimeError("resident process stat is malformed")
    fields = tail[1].split()
    if len(fields) <= 19:
        raise RuntimeError("resident process stat is incomplete")
    value = int(fields[19])
    if value <= 0:
        raise RuntimeError("resident process start ticks are invalid")
    return value


def write_resident_status(
    spool_root: Path,
    status_path: Path,
    *,
    resident_id: str | None = None,
    process_id: int | None = None,
    process_started_at_unix: float | None = None,
    process_start_ticks: int | None = None,
    process_nonce: str | None = None,
) -> dict[str, Any]:
    """Publish a fresh v2 status from resident-owned spool receipts."""

    paths = _dirs(spool_root)
    installed_runtime = os.getenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "").strip().lower()
    if not HEX40_RE.fullmatch(installed_runtime):
        raise RuntimeError("installed runtime commit is unavailable")
    resident = (resident_id or os.getenv("UNIVERSAL_VIDEO_RESIDENT_ID", "")).strip().lower()
    if resident not in {"source", "container"}:
        raise RuntimeError("resident identity is unavailable")
    pid = os.getpid() if process_id is None else process_id
    started_at = time.time() if process_started_at_unix is None else process_started_at_unix
    start_ticks = _process_start_ticks(pid) if process_start_ticks is None else process_start_ticks
    nonce = secrets.token_hex(16) if process_nonce is None else process_nonce
    if type(pid) is not int or pid <= 0:
        raise RuntimeError("resident process id is invalid")
    if isinstance(started_at, bool) or not isinstance(started_at, (int, float)):
        raise RuntimeError("resident process start is invalid")
    if type(start_ticks) is not int or start_ticks <= 0:
        raise RuntimeError("resident process start ticks are invalid")
    if not re.fullmatch(r"^[0-9a-f]{32}$", nonce):
        raise RuntimeError("resident process nonce is invalid")
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
        "resident_id": resident,
        "process_id": pid,
        "process_started_at_unix": float(started_at),
        "process_start_ticks": start_ticks,
        "process_nonce": nonce,
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


def _process_one_locked(spool_root: Path) -> bool:
    paths = _dirs(spool_root)
    media_root = Path(os.getenv("UNIVERSAL_VIDEO_MEDIA_ROOT", "/opt/bridge-school/universal-video/media"))
    if resume_pending_completion(paths, spool_root, media_root, progress=_write_progress):
        return True
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
            candidate_raw = path.read_text(encoding="utf-8")
            candidate_payload = json.loads(candidate_raw)
            if isinstance(candidate_payload, dict) and candidate_payload.get("schema") == "book-single-atom-job-v1":
                candidate_payload = strict_json(candidate_raw)
            candidate_job = _spool_job(candidate_payload)
            retry_path = paths["progress"] / f"{candidate_job.job_id}.recovery.json"
            if retry_path.exists():
                retry = read_receipt(retry_path)
                if (retry.get("job_hash") == _spool_job_hash(candidate_job)
                        and retry.get("retry_after_unix", 0) > time.time()):
                    continue
        except OSError:
            continue
        except (ValueError, RuntimeError, VideoContractError):
            pass  # Invalid JSON/contracts are quarantined by the claimed path.
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
    intake_identity: dict | None = None
    staged_job_dir: Path | None = None
    cleanup_ready = False
    finalizing = False
    attempt = 0
    media_root = Path(os.getenv("UNIVERSAL_VIDEO_MEDIA_ROOT", "/opt/bridge-school/universal-video/media"))
    try:
        valid, reason = _regular_payload(claimed)
        if not valid:
            raise RuntimeError(reason or "invalid claimed spool payload")
        claimed_raw = claimed.read_text(encoding="utf-8")
        payload = json.loads(claimed_raw)
        if isinstance(payload, dict) and payload.get("schema") == "book-single-atom-job-v1":
            payload = strict_json(claimed_raw)
            process_claimed_book(payload, claimed, source.name, paths, spool_root, media_root,
                                 progress=_write_progress)
            return True
        intake_job = validate_job(payload)
        intake_identity = {
            "job_id": intake_job.job_id,
            "profile": intake_job.profile,
            "job_hash": canonical_job_hash(intake_job),
            "source": {
                "kind": str(intake_job.source.get("kind") or ""),
                "file_id": str(intake_job.source.get("file_id") or ""),
            },
        }
        # Fail before a potentially large download when compute is unavailable.
        # A retained Drive result can instead enter publication-only recovery.
        if (intake_job.source.get("kind") != "google_drive"
                or not (paths["results"] / intake_job.job_id).exists()):
            validate_video_runtime()
        if intake_job.source.get("kind") == "google_drive":
            _write_progress(paths, intake_job.job_id, "DOWNLOADING_FROM_DRIVE")
            payload, staged_job_dir = stage_drive_job(intake_job, payload, media_root)
            _write_progress(paths, intake_job.job_id, "SOURCE_READY_ON_ORACLE")
        validated_job = validate_from_env(payload)
        _write_progress(paths, validated_job.job_id, "PROCESSING")
        existing_dir = paths["results"] / validated_job.job_id
        finalization_only = staged_job_dir is not None and existing_dir.exists()
        if finalization_only:
            # Recovery must not enter runner's compute/replacement path. Pin the
            # old runtime/artifact package; incompatible or partial data stays.
            if existing_dir.is_symlink():
                raise RuntimeError("unsafe existing result directory")
            try:
                result = read_receipt(existing_dir / "manifest.json")
            except (OSError, ValueError):
                # A pre-manifest crash is recoverable without discarding its
                # transcript/frames. Preserve the whole partial tree first.
                attempts_path = paths["progress"] / f"{validated_job.job_id}.compute-recovery.json"
                prepare_compute_recovery(existing_dir, paths["recovery"] / validated_job.job_id,
                                         attempts_path, job_hash=canonical_job_hash(validated_job))
                validate_video_runtime()
                validate_staged_video(Path(validated_job.source["path"]))
                result = run_job(payload, paths["results"])
                finalization_only = False
            if (result.get("status") != "COMPLETED"
                    or result.get("job_hash") != canonical_job_hash(validated_job)
                    or (result.get("media") or {}).get("sha256") != validated_job.source["sha256"]):
                raise RuntimeError("existing Drive result is incomplete/conflicting; retained for recovery")
        else:
            attempts_path = paths["progress"] / f"{validated_job.job_id}.compute-recovery.json"
            if staged_job_dir is not None and attempts_path.exists():
                prepare_compute_recovery(existing_dir, paths["recovery"] / validated_job.job_id,
                                         attempts_path, job_hash=canonical_job_hash(validated_job))
            if staged_job_dir is not None:
                validate_staged_video(Path(validated_job.source["path"]))
            result = run_job(payload, paths["results"])
        result_dir = paths["results"] / str(result.get("job_id") or "")
        media = result.get("media") or {}
        processing_model = result.get("processing_whisper_model") or (result.get("runtime") or {}).get("whisper_model")
        source_info = result.get("source") or {}
        reused_finalized_result = finalization_only or isinstance(result.get("finops_observation"), dict)
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
        if staged_job_dir is not None and str(result.get("status") or "") == "COMPLETED":
            binding = configured_binding(validated_job.job_id, str(validated_job.source["file_id"]),
                                         canonical_job_hash(validated_job))
            if binding is not None:
                retry_path = paths["progress"] / f"{validated_job.job_id}.recovery.json"
                retry = read_receipt(retry_path) if retry_path.exists() else {}
                if retry and retry.get("job_hash") != canonical_job_hash(validated_job):
                    raise RuntimeError("Drive retry identity changed")
                attempt = int(retry.get("attempts", 0)) + 1
                if attempt > 3:
                    raise RuntimeError("Drive finalization retry budget exhausted; evidence retained")
                _atomic_write_json(retry_path, {"job_hash": canonical_job_hash(validated_job),
                                               "attempts": attempt, "retry_after_unix": time.time() + 60})
                finalizing = True
                receipt_payload["drive_finalization"] = finalize_drive_job(
                    result_dir, staged_job_dir, binding, job_id=validated_job.job_id,
                    profile=validated_job.profile, job_hash=canonical_job_hash(validated_job))
        receipt = paths["done"] / source.name
        if not (staged_job_dir is not None and preserve_pending_completion(
                spool_root, media_root, validated_job.job_id, receipt)):
            _atomic_write_json(receipt, receipt_payload)
        if (staged_job_dir is not None and cleanup_proof_matches(result_dir, staged_job_dir,
                                                                job_id=validated_job.job_id)):
            queue_cleanup(spool_root, media_root, validated_job.job_id, receipt)
        cleanup_ready = True  # after durable done receipt, never from finally alone
        try:
            _write_progress(paths, validated_job.job_id, "RESULT_READY" if str(result.get("status") or "") == "COMPLETED" else "REVIEW")
        except OSError:
            pass
        claimed.unlink(missing_ok=True)
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        transient = (isinstance(exc, (requests.ConnectionError, requests.Timeout, TimeoutError, OSError))
                     or isinstance(exc, requests.HTTPError) and status_code in {429, 500, 502, 503, 504})
        if finalizing and transient and attempt < 3:
            # Durable completed results remain reusable; no new ASR on retry.
            # The attempt receipt was committed before writes, including crashes.
            _write_progress(paths, intake_identity["job_id"], "DRIVE_RECOVERY_PENDING")
            destination = paths["inbox"] / source.name
            if destination.exists() or destination.is_symlink():
                raise RuntimeError("Drive retry inbox collision; running evidence retained")
            claimed.rename(destination)
            return True
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
        if intake_identity is not None:
            failure.update(intake_identity)
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
        if (cleanup_ready and staged_job_dir is not None and staged_job_dir.exists()
                and cleanup_proof_matches(paths["results"] / staged_job_dir.name,
                                          staged_job_dir, job_id=staged_job_dir.name)):
            try:
                pending = queue_cleanup(spool_root, media_root, staged_job_dir.name,
                                        paths["done"] / source.name)
                retry_cleanup(spool_root, media_root, pending)
            except (DriveStageError, OSError, RuntimeError):
                # Intent survives transient deletion failure. Maintenance retries
                # under the same fence, without compute or publication.
                pass
    return True


def process_one(spool_root: Path) -> bool:
    """Process at most one local job while honoring the attestation fence."""

    # Serialize source/container workers through compute + publication. A shared
    # lock allowed two differently named retries to publish the same job.
    with shared_workload_lock(spool_root, exclusive=True):
        return _process_one_locked(spool_root)


def run_forever(spool_root: Path, poll_seconds: float) -> None:
    status_path = Path(
        os.getenv(
            "UNIVERSAL_VIDEO_STATUS_PATH",
            "/run/bridge-school/universal-video-status.json",
        )
    )
    resident_id = os.getenv("UNIVERSAL_VIDEO_RESIDENT_ID", "").strip().lower()
    process_id = os.getpid()
    process_started_at_unix = time.time()
    process_start_ticks = _process_start_ticks(process_id)
    process_nonce = secrets.token_hex(16)
    status_identity = {
        "resident_id": resident_id,
        "process_id": process_id,
        "process_started_at_unix": process_started_at_unix,
        "process_start_ticks": process_start_ticks,
        "process_nonce": process_nonce,
    }
    # Both resident implementations share this spool. Serialize startup
    # recovery on the common fence before either process advertises readiness.
    with shared_workload_lock(spool_root, exclusive=True):
        recovery = recover_orphaned_jobs(spool_root)
        if any(recovery.values()):
            print(json.dumps({"event": "spool_recovery", **recovery}, sort_keys=True), flush=True)
    # Publish resident readiness before accepting a potentially long queued job.
    write_resident_status(spool_root, status_path, **status_identity)
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
        write_resident_status(spool_root, status_path, **status_identity)
        if processed:
            continue
        time.sleep(poll_seconds)


def _run_precanary_startup_probe(spool_root: Path, poll_seconds: float) -> None:
    """Publish startup identity without recovering or polling any job."""

    if spool_root != PRECANARY_SPOOL_ROOT:
        raise RuntimeError("pre-canary startup probe spool is not isolated")
    paths = _dirs(spool_root)
    if any(any(path.iterdir()) for path in paths.values()):
        raise RuntimeError("pre-canary startup probe spool is not empty")
    write_resident_status(spool_root, Path(os.environ["UNIVERSAL_VIDEO_STATUS_PATH"]))
    while True:
        time.sleep(poll_seconds)


def main() -> None:
    root = Path(os.getenv("UNIVERSAL_VIDEO_SPOOL_ROOT", "/opt/bridge-school/universal-video/spool"))
    poll = max(1.0, float(os.getenv("UNIVERSAL_VIDEO_POLL_SECONDS", "2")))
    if os.getenv(PRECANARY_STARTUP_PROBE_ENV, "") == "1":
        _run_precanary_startup_probe(root, poll)
        return
    run_forever(root, poll)


if __name__ == "__main__":
    main()
