"""Generic, fail-closed intake for explicit Universal Video Drive jobs.

This replaces lesson-specific submission helpers: the identity belongs to the
job contract, never to a hard-coded video title or Drive id.
"""
from __future__ import annotations

import errno
import json
import os
import stat
import sys
import time
from pathlib import Path

from .contract import MAX_JOB_BYTES, VideoContractError, canonical_job_hash, validate_job
from .lifecycle import LifecycleError, attempt_records, directory, lifecycle_lock, read_json, retained_job_evidence, valid_id


_INTAKE_ERROR_CODES = frozenset(
    {
        "UV_INTAKE_COLLISION",
        "UV_INTAKE_CLEANUP_FAILED",
        "UV_INTAKE_CONTRACT_INVALID",
        "UV_INTAKE_CROSS_DEVICE",
        "UV_INTAKE_DISK_FULL",
        "UV_INTAKE_EXECUTION_FAILED",
        "UV_INTAKE_INBOX_UNSAFE",
        "UV_INTAKE_IO_FAILED",
        "UV_INTAKE_JOB_EXISTS",
        "UV_INTAKE_PERMISSION_DENIED",
        "UV_INTAKE_READ_ONLY",
        "UV_INTAKE_REJECTED",
        "UV_INTAKE_ROOT_UNSAFE",
        "UV_INTAKE_SOURCE_UNSUPPORTED",
        "UV_INTAKE_USAGE_INVALID",
        "UV_INTAKE_IDENTITY_CONFLICT",
        "UV_INTAKE_STATE_INVALID",
    }
)


class IntakeError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "UV_INTAKE_REJECTED") -> None:
        self.error_code = error_code
        super().__init__(message)


def _unlink_staged(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise IntakeError(
            "staged payload cleanup failed",
            error_code="UV_INTAKE_CLEANUP_FAILED",
        ) from exc


def _error_code(exc: BaseException) -> str:
    explicit = str(getattr(exc, "error_code", "") or "")
    if explicit in _INTAKE_ERROR_CODES:
        return explicit
    if isinstance(exc, OSError):
        return {
            errno.EACCES: "UV_INTAKE_PERMISSION_DENIED",
            errno.EPERM: "UV_INTAKE_PERMISSION_DENIED",
            errno.EXDEV: "UV_INTAKE_CROSS_DEVICE",
            errno.ENOSPC: "UV_INTAKE_DISK_FULL",
            errno.EROFS: "UV_INTAKE_READ_ONLY",
            errno.EEXIST: "UV_INTAKE_COLLISION",
        }.get(exc.errno, "UV_INTAKE_IO_FAILED")
    if isinstance(exc, (json.JSONDecodeError, VideoContractError, ValueError)):
        return "UV_INTAKE_CONTRACT_INVALID"
    return "UV_INTAKE_EXECUTION_FAILED"


def _write_new(path: Path, payload: dict, *, worker_gid: int) -> None:
    raw = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o640)
    try:
        # The privileged intake runs with a restrictive umask and publishes
        # this inode into a spool consumed by an unprivileged worker. Keep the
        # root owner, but grant read access only to the worker group inherited
        # from the already-validated inbox directory.
        os.fchown(fd, -1, worker_gid)
        os.fchmod(fd, 0o640)
        handle = os.fdopen(fd, "wb")
        fd = -1
        with handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:
        if fd >= 0:
            os.close(fd)
        try:
            _unlink_staged(path)
        except IntakeError as cleanup_exc:
            raise cleanup_exc from exc
        raise


def _status_locked(job_id: str, spool_root: Path, expected_hash: str | None = None) -> dict:
    valid_id(job_id)
    directory(spool_root)
    states = []
    for state in ("inbox", "running", "done", "failed", "progress"):
        parent = spool_root / state
        if not parent.exists() and not parent.is_symlink():
            continue
        directory(parent)
        path = parent / f"{job_id}.json"
        if not path.exists() and not path.is_symlink():
            continue
        row = read_json(path, max_bytes=MAX_JOB_BYTES if state in {"inbox", "running"} else 16 * 1024 * 1024)
        if state == "progress":
            # Progress is a hint, never the authoritative request or receipt.
            if row.get("job_id") != job_id:
                raise LifecycleError("UV_STATE_IDENTITY_INVALID")
            continue
        if state in {"inbox", "running"}:
            job = validate_job(row)
            observed_hash = canonical_job_hash(job)
            observed_id = job.job_id
            status = "QUEUED" if state == "inbox" else "RUNNING"
        else:
            observed_hash = row.get("job_hash")
            observed_id = row.get("job_id")
            status = row.get("status")
            allowed = {"COMPLETED", "REVIEW"} if state == "done" else {"FAILED"}
            if status not in allowed or not isinstance(observed_hash, str) or len(observed_hash) != 64 or any(c not in "0123456789abcdef" for c in observed_hash):
                raise LifecycleError("UV_TERMINAL_RECEIPT_INVALID")
        if observed_id != job_id:
            raise LifecycleError("UV_STATE_IDENTITY_INVALID")
        if expected_hash is not None and observed_hash != expected_hash:
            raise IntakeError("job id already exists with different input", error_code="UV_INTAKE_IDENTITY_CONFLICT")
        states.append((state, status, observed_hash, row))
    results = spool_root / "results"
    result_dir = results / job_id
    result_exists = result_dir.exists() or result_dir.is_symlink()
    if results.exists() or results.is_symlink():
        directory(results)
    if result_exists:
        directory(result_dir)
    attempts = attempt_records(spool_root, job_id)
    if not states:
        if attempts or retained_job_evidence(spool_root, job_id) or result_exists or (spool_root / "progress" / f"{job_id}.json").exists():
            raise LifecycleError("UV_STATE_INCOMPLETE")
        return {"job_id": job_id, "status": "NOT_FOUND"}
    if len({item[2] for item in states}) != 1:
        raise LifecycleError("UV_STATE_IDENTITY_INVALID")
    terminal = [item for item in states if item[0] in {"done", "failed"}]
    if len(terminal) > 1:
        raise LifecycleError("UV_TERMINAL_RECEIPT_CONFLICT")
    selected = terminal[0] if terminal else next((item for item in states if item[0] == "running"), states[0])
    state, status, observed_hash, row = selected
    if any(row.get("job_hash") != observed_hash for row in attempts):
        raise LifecycleError("UV_ATTEMPT_IDENTITY_INVALID")
    if not terminal and attempts and attempts[-1].get("state") in {"COMPLETED", "REVIEW", "FAILED"}:
        raise LifecycleError("UV_TERMINAL_RECEIPT_MISSING")
    result = {"job_id": job_id, "job_hash": observed_hash, "status": status,
              "attempts": attempts,
              "pedagogical_status": "NOT_EVALUATED", "canonical_promotion_allowed": False}
    if status == "COMPLETED":
        # Never trust a stored PASS alone, nor a result path supplied by a receipt.
        from .result_conformance import verify_result
        verification = verify_result(result_dir, expected_job_id=job_id,
                                     expected_profile=str(row.get("profile") or ""),
                                     expected_job_hash=observed_hash,
                                     evidence_phase="REUSE_OBSERVATION", require_server_review=True)
        if verification.get("state") != "PASS":
            raise LifecycleError("UV_RESULT_CONFORMANCE_FAILED")
        result["result_conformance"] = verification
    if status in {"COMPLETED", "REVIEW"}:
        result["result_locator"] = {"kind": "local_directory", "path": str(result_dir)}
    if status == "FAILED":
        result["error_code"] = row.get("error_code")
    return result


def read_status(job_id: str, *, spool_root: Path) -> dict:
    """Read the same bounded job identity, including verified completed results."""
    try:
        with lifecycle_lock(spool_root):
            return _status_locked(job_id, spool_root)
    except (RuntimeError, VideoContractError, ValueError) as exc:
        raise IntakeError("existing job state is invalid", error_code="UV_INTAKE_STATE_INVALID") from exc


def submit(payload: dict, *, spool_root: Path, staging_root: Path) -> str:
    try:
        with lifecycle_lock(spool_root):
            return _submit_locked(payload, spool_root=spool_root, staging_root=staging_root)
    except IntakeError:
        raise
    except (RuntimeError, VideoContractError) as exc:
        raise IntakeError("job id already exists with invalid state or unsafe input", error_code="UV_INTAKE_STATE_INVALID") from exc


def _recover_staging_alias(payload: dict, *, target: Path, staging_root: Path) -> None:
    """Repair only our own interrupted hardlink publication on a submit retry.

    A linked payload is otherwise unsafe. Never scan outside the explicit
    trusted staging directory, and never let the worker unlink privileged data.
    """
    if not target.exists() or target.is_symlink():
        return
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 2:
        return
    import re
    job = validate_job(payload)
    pattern = re.compile(r"\." + re.escape(job.job_id) + r"\.\d+\.\d+\.json$")
    for candidate in staging_root.iterdir():
        if not pattern.fullmatch(candidate.name):
            continue
        other = candidate.lstat()
        if not stat.S_ISREG(other.st_mode) or (other.st_dev, other.st_ino) != (info.st_dev, info.st_ino):
            continue
        if other.st_nlink != 2 or other.st_size > MAX_JOB_BYTES or other.st_uid != os.geteuid():
            raise LifecycleError("UV_STAGING_ALIAS_UNSAFE")
        fd = os.open(candidate, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            current = os.fstat(handle.fileno())
            if (current.st_dev, current.st_ino, current.st_nlink) != (info.st_dev, info.st_ino, 2):
                raise LifecycleError("UV_STAGING_ALIAS_UNSAFE")
            original = json.loads(handle.read(MAX_JOB_BYTES + 1))
        if canonical_job_hash(validate_job(original)) != canonical_job_hash(job):
            raise IntakeError("job id already exists with different input", error_code="UV_INTAKE_IDENTITY_CONFLICT")
        _unlink_staged(candidate)
        for parent in (staging_root, target.parent):
            descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return


def _submit_locked(payload: dict, *, spool_root: Path, staging_root: Path) -> str:
    job = validate_job(payload)
    if job.source["kind"] != "google_drive":
        raise IntakeError(
            "server intake accepts explicit Google Drive sources only",
            error_code="UV_INTAKE_SOURCE_UNSUPPORTED",
        )
    if not spool_root.is_dir() or spool_root.is_symlink() or not staging_root.is_dir() or staging_root.is_symlink():
        raise IntakeError("unsafe spool or staging directory", error_code="UV_INTAKE_ROOT_UNSAFE")
    inbox = spool_root / "inbox"
    if not inbox.is_dir() or inbox.is_symlink():
        raise IntakeError("unsafe spool inbox", error_code="UV_INTAKE_INBOX_UNSAFE")
    target = inbox / f"{job.job_id}.json"
    directory(staging_root)
    _recover_staging_alias(payload, target=target, staging_root=staging_root)
    status = _status_locked(job.job_id, spool_root, canonical_job_hash(job))
    if status["status"] != "NOT_FOUND":
        return job.job_id
    temporary = staging_root / f".{job.job_id}.{os.getpid()}.{time.time_ns()}.json"
    _write_new(temporary, payload, worker_gid=inbox.stat().st_gid)
    try:
        os.link(temporary, target, follow_symlinks=False)
        directory_fd = os.open(inbox, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        _unlink_staged(temporary)
    return job.job_id


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "status":
        print(json.dumps(read_status(argv[2], spool_root=Path(argv[3])), sort_keys=True))
        return 0
    if len(argv) != 4 or argv[1] != "submit":
        raise IntakeError(
            "usage: server_intake submit JOB_JSON SPOOL_ROOT | status JOB_ID SPOOL_ROOT",
            error_code="UV_INTAKE_USAGE_INVALID",
        )
    payload_path, spool = Path(argv[2]), Path(argv[3])
    if payload_path.is_symlink() or not stat.S_ISREG(payload_path.stat().st_mode):
        raise IntakeError("job payload must be a regular file")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    staging = Path(os.environ.get("UNIVERSAL_VIDEO_STAGING_ROOT", "/opt/bridge-school/.universal-video-staging"))
    job_id = submit(payload, spool_root=spool, staging_root=staging)
    print("UV_JOB_ID=" + job_id)
    print("UV_JOB_STATUS=" + read_status(job_id, spool_root=spool)["status"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except (OSError, ValueError, VideoContractError, IntakeError) as exc:
        print("UV_STATE=REJECTED")
        print("UV_ERROR_CODE=" + _error_code(exc))
        raise SystemExit(1)
