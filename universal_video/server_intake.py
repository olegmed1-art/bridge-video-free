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

from .contract import VideoContractError, validate_job


_INTAKE_ERROR_CODES = frozenset(
    {
        "UV_INTAKE_COLLISION",
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
    }
)


class IntakeError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "UV_INTAKE_REJECTED") -> None:
        self.error_code = error_code
        super().__init__(message)


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
    except Exception:
        if fd >= 0:
            os.close(fd)
        path.unlink(missing_ok=True)
        raise


def submit(payload: dict, *, spool_root: Path, staging_root: Path) -> str:
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
    collisions = [
        spool_root / state / f"{job.job_id}.json"
        for state in ("inbox", "running", "done", "failed", "progress")
    ] + [spool_root / "results" / job.job_id]
    if any(path.exists() or path.is_symlink() for path in collisions):
        raise IntakeError(
            "job id already exists; use status or a new id",
            error_code="UV_INTAKE_JOB_EXISTS",
        )
    temporary = staging_root / f".{job.job_id}.{os.getpid()}.{time.time_ns()}.json"
    _write_new(temporary, payload, worker_gid=inbox.stat().st_gid)
    try:
        os.link(temporary, target, follow_symlinks=False)
        directory = os.open(inbox, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return job.job_id


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] != "submit":
        raise IntakeError(
            "usage: server_intake submit JOB_JSON SPOOL_ROOT",
            error_code="UV_INTAKE_USAGE_INVALID",
        )
    payload_path, spool = Path(argv[2]), Path(argv[3])
    if payload_path.is_symlink() or not stat.S_ISREG(payload_path.stat().st_mode):
        raise IntakeError("job payload must be a regular file")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    staging = Path(os.environ.get("UNIVERSAL_VIDEO_STAGING_ROOT", "/opt/bridge-school/.universal-video-staging"))
    print("UV_STATE=DOWNLOAD_QUEUED")
    print("UV_JOB_ID=" + submit(payload, spool_root=spool, staging_root=staging))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except (OSError, ValueError, VideoContractError, IntakeError) as exc:
        print("UV_STATE=REJECTED")
        print("UV_ERROR_CODE=" + _error_code(exc))
        raise SystemExit(1)
