"""Local job transitions and bounded evidence retention; no external providers.

The lifecycle lock is short-lived. Workers acquire workload -> lifecycle, never
in reverse order, and release lifecycle before media work. Spool directories
must be private/trusted, as for the existing resident worker.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .contract import ID_RE, MAX_JOB_BYTES, RESERVED_PATH_IDS

MAX_RECEIPT_BYTES = 16 * 1024 * 1024
MAX_ATTEMPTS = 2  # initial attempt and one restart, never an unlimited retry loop
MAX_ARCHIVED_RESULTS = 2
MAX_ARCHIVE_FILES = 10000
MAX_ARCHIVE_BYTES = 16 * 1024**3


class LifecycleError(RuntimeError):
    def __init__(self, code: str):
        self.error_code = code
        super().__init__(code)


def valid_id(job_id: str) -> None:
    if not isinstance(job_id, str) or not ID_RE.fullmatch(job_id) or job_id in RESERVED_PATH_IDS:
        raise LifecycleError("UV_JOB_ID_INVALID")


def directory(path: Path, *, create: bool = False) -> Path:
    """Reject symlink ancestors; do not resolve them into a different trust root."""
    path = path.absolute()
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if create and part == path:
                try:
                    part.mkdir(mode=0o750)
                except FileExistsError:
                    pass
                info = part.lstat()
            else:
                raise LifecycleError("UV_DIRECTORY_MISSING") from None
        if not stat.S_ISDIR(info.st_mode):
            raise LifecycleError("UV_DIRECTORY_UNSAFE")
    return path


def read_json(path: Path, *, max_bytes: int = MAX_RECEIPT_BYTES) -> dict[str, Any]:
    directory(path.parent)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= max_bytes:
                raise LifecycleError("UV_STATE_FILE_UNSAFE")
            raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise LifecycleError("UV_STATE_FILE_UNSAFE")
        value = json.loads(raw)
    except (OSError, ValueError, UnicodeError):
        raise LifecycleError("UV_STATE_FILE_INVALID") from None
    if not isinstance(value, dict):
        raise LifecycleError("UV_STATE_FILE_INVALID")
    return value


def atomic_json(path: Path, value: dict[str, Any], *, replace: bool = True) -> None:
    directory(path.parent)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0), 0o640)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            if path.exists() or path.is_symlink():
                read_json(path)
            os.replace(temporary, path)
        else:
            os.link(temporary, path, follow_symlinks=False)
            temporary.unlink()
        fd_dir = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd_dir)
        finally:
            os.close(fd_dir)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def lifecycle_lock(spool_root: Path) -> Iterator[None]:
    """Lock the installed directory inode; no new privileged lock file exists.

    Linux flock works on directories. This avoids publishing a partially
    initialized root-owned inode under a restrictive umask, while both the
    privileged intake and resident use their existing spool read permissions.
    """
    root = directory(spool_root)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise LifecycleError("UV_LIFECYCLE_LOCK_UNSAFE")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def retained_job_evidence(spool_root: Path, job_id: str) -> bool:
    """Retained crash/quarantine bytes prevent accidental reuse of a job ID."""
    valid_id(job_id)
    root = spool_root / "attempts"
    if not root.exists() and not root.is_symlink():
        return False
    directory(root)
    pattern = re.compile(re.escape(job_id) + r"\.(?:"
        r"\d+\.(?:inbox|running|done|failed|progress)\.quarantined|"
        r"(?:terminal|terminal-inbox|exhausted|duplicate)-\d+\.request|"
        r"conflict-\d+(?:\.inbox\.request|\.receipt\.json)?)$")
    return any(pattern.fullmatch(path.name) for path in root.iterdir())


def attempt_records(spool_root: Path, job_id: str) -> list[dict[str, Any]]:
    valid_id(job_id)
    root = spool_root / "attempts"
    if not root.exists() and not root.is_symlink():
        return []
    directory(root)
    prefix = re.compile(re.escape(job_id) + r"\.(\d+)\.json$")
    for path in root.iterdir():
        match = prefix.fullmatch(path.name)
        if match and not 1 <= int(match.group(1)) <= MAX_ATTEMPTS:
            raise LifecycleError("UV_ATTEMPT_SEQUENCE_INVALID")
    records = []
    for index in range(1, MAX_ATTEMPTS + 1):
        path = root / f"{job_id}.{index}.json"
        if path.exists() or path.is_symlink():
            row = read_json(path, max_bytes=MAX_JOB_BYTES)
            if row.get("job_id") != job_id or row.get("attempt") != index:
                raise LifecycleError("UV_ATTEMPT_IDENTITY_INVALID")
            records.append(row)
    if [row["attempt"] for row in records] != list(range(1, len(records) + 1)):
        raise LifecycleError("UV_ATTEMPT_SEQUENCE_INVALID")
    return records


def start_attempt(spool_root: Path, job_id: str, job_hash: str) -> int:
    directory(spool_root / "attempts", create=True)
    records = attempt_records(spool_root, job_id)
    if records and any(row.get("job_hash") != job_hash for row in records):
        raise LifecycleError("UV_ATTEMPT_IDENTITY_INVALID")
    if len(records) >= MAX_ATTEMPTS:
        raise LifecycleError("UV_RECOVERY_EXHAUSTED")
    index = len(records) + 1
    atomic_json(spool_root / "attempts" / f"{job_id}.{index}.json", {
        "schema": "universal-video-attempt-v1", "job_id": job_id,
        "job_hash": job_hash, "attempt": index, "state": "RUNNING",
        "started_at_unix": time.time(),
    }, replace=False)
    return index


def finish_attempt(spool_root: Path, job_id: str, index: int, state: str, code: str | None = None) -> None:
    path = spool_root / "attempts" / f"{job_id}.{index}.json"
    row = read_json(path, max_bytes=MAX_JOB_BYTES)
    if row.get("job_id") != job_id or row.get("attempt") != index or row.get("state") != "RUNNING":
        raise LifecycleError("UV_ATTEMPT_TRANSITION_INVALID")
    row.update(state=state, finished_at_unix=time.time())
    if code:
        row["error_code"] = code
    atomic_json(path, row)


def check_archive_state(output_root: Path, job_id: str) -> None:
    """A partial archive transaction requires explicit reconciliation, not rerun."""
    valid_id(job_id)
    root = output_root / ".attempts" / job_id
    if not root.exists() and not root.is_symlink():
        return
    directory(root)
    for path in root.glob("attempt-*.json"):
        record = read_json(path)
        if record.get("state") != "PRESERVED":
            raise LifecycleError("UV_RESULT_PRESERVATION_PENDING")


def archive_result(output_root: Path, job_id: str) -> Path | None:
    """Rename an old result intact, with bounded inventory and SHA evidence.

    Never delete a previous generation, even when its source/model has changed.
    A full retention budget is a stop, not permission to evict evidence.
    """
    valid_id(job_id)
    root = directory(output_root)
    check_archive_state(root, job_id)
    source = root / job_id
    if not source.exists() and not source.is_symlink():
        return None
    directory(source)
    archive = directory(root / ".attempts", create=True)
    job_archive = directory(archive / job_id, create=True)
    entries = list(job_archive.iterdir())
    generations = [p for p in entries if re.fullmatch(r"attempt-\d+", p.name)]
    intents = [p for p in entries if re.fullmatch(r"attempt-\d+\.json", p.name)]
    # An interrupted intent must be reconciled, never silently forgotten or
    # accumulated as unlimited new attempts.
    for record in intents:
        prior = read_json(record)
        target = job_archive / record.stem
        if prior.get("state") == "PRESERVE_INTENT":
            if target.is_dir() and not target.is_symlink() and not source.exists():
                prior["state"] = "PRESERVED"
                atomic_json(record, prior)
            else:
                raise LifecycleError("UV_RESULT_PRESERVATION_PENDING")
    if len(intents) >= MAX_ARCHIVED_RESULTS or len(generations) >= MAX_ARCHIVED_RESULTS:
        raise LifecycleError("UV_RESULT_RETENTION_LIMIT")
    inventory = []
    total = 0
    for base, dirs, files in os.walk(source, followlinks=False):
        for name in dirs:
            directory(Path(base) / name)
        for name in files:
            path = Path(base) / name
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise LifecycleError("UV_RESULT_ARCHIVE_UNSAFE")
                total += info.st_size
                if len(inventory) >= MAX_ARCHIVE_FILES or total > MAX_ARCHIVE_BYTES:
                    raise LifecycleError("UV_RESULT_RETENTION_LIMIT")
                digest = hashlib.sha256()
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            inventory.append({"path": path.relative_to(source).as_posix(), "bytes": info.st_size, "sha256": digest.hexdigest()})
    destination = job_archive / f"attempt-{time.time_ns()}"
    # Record first: a crash leaves a recoverable pending intent, never lost bytes.
    record = destination.with_suffix(".json")
    atomic_json(record, {"schema": "universal-video-preserved-result-v1", "job_id": job_id,
                         "directory": destination.name, "state": "PRESERVE_INTENT",
                         "bytes": total, "files": inventory}, replace=False)
    source.rename(destination)
    for parent in (root, job_archive):
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    row = read_json(record)
    row["state"] = "PRESERVED"
    atomic_json(record, row)
    return destination
