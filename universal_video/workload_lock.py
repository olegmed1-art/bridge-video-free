"""Cross-runtime claim fence shared by source and container workers."""
from __future__ import annotations

import fcntl
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


LOCK_FILE_NAME = ".workload.lock"


def configured_spool_root() -> Path:
    return Path(
        os.getenv(
            "UNIVERSAL_VIDEO_SPOOL_ROOT",
            "/opt/bridge-school/universal-video/spool",
        )
    )


@contextmanager
def shared_workload_lock(
    spool_root: Path | None = None,
    *,
    blocking: bool = True,
    exclusive: bool = False,
) -> Iterator[Path]:
    """Hold the shared claim lock while a worker can mutate or claim work.

    The metadata-only pre-canary attestation holds the same inode exclusively.
    Consequently a source worker, a container worker, and the Neon claim path
    cannot begin work during the complete attestation window.
    """

    root = spool_root or configured_spool_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / LOCK_FILE_NAME
    flags = os.O_RDONLY | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o640)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError("UV_WORKLOAD_LOCK_INVALID")
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if not blocking:
            operation |= fcntl.LOCK_NB
        fcntl.flock(fd, operation)
        yield path
    finally:
        os.close(fd)


__all__ = ["LOCK_FILE_NAME", "configured_spool_root", "shared_workload_lock"]
