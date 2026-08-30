"""Fail-closed startup gate for the Oracle universal-video container.

The gate intentionally emits only structured, non-secret facts.  It proves
the image identity, runtime tools, writable isolated mounts and an already
available ASR model before starting the resident worker.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Sequence

from .runner import _load_model
from .runtime_preflight import validate_video_runtime


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
WRITABLE_ROOT_ENV = (
    "UNIVERSAL_VIDEO_OUTPUT_ROOT",
    "UNIVERSAL_VIDEO_MEDIA_ROOT",
    "HF_HOME",
)
SPOOL_LEAVES = ("inbox", "running", "done", "failed", "results", "progress")


class ContainerRuntimeUnavailable(RuntimeError):
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


def _require_directory(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ContainerRuntimeUnavailable("UV_CONTAINER_MOUNT_UNAVAILABLE")
    return path


def _write_probe(path: Path) -> None:
    marker = path / f".container-write-check-{os.getpid()}"
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    marker.unlink()


def _require_writable_directory(value: str) -> Path:
    path = _require_directory(value)
    try:
        _write_probe(path)
        return path
    except ContainerRuntimeUnavailable:
        raise
    except OSError as exc:
        raise ContainerRuntimeUnavailable("UV_CONTAINER_MOUNT_UNAVAILABLE") from exc


def _require_spool_directory(value: str) -> Path:
    """Validate the protected spool root and each worker-owned state leaf."""

    root = _require_directory(value)
    for leaf in SPOOL_LEAVES:
        _require_writable_directory(str(root / leaf))
    return root


def validate_container_runtime() -> dict[str, object]:
    """Validate all container-only prerequisites without processing media."""

    commit = os.getenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "").strip().lower()
    if not COMMIT_RE.fullmatch(commit):
        raise ContainerRuntimeUnavailable("UV_CONTAINER_PROVENANCE_INVALID")
    mounts = {
        "UNIVERSAL_VIDEO_SPOOL_ROOT": _require_spool_directory(os.getenv("UNIVERSAL_VIDEO_SPOOL_ROOT", "")),
        **{name: _require_writable_directory(os.getenv(name, "")) for name in WRITABLE_ROOT_ENV},
    }
    try:
        runtime = validate_video_runtime()
        # ``HF_HUB_OFFLINE`` is exported by the entrypoint.  Loading the model
        # here proves it is present locally and prevents a network fallback.
        _load_model()
    except ContainerRuntimeUnavailable:
        raise
    except Exception as exc:
        raise ContainerRuntimeUnavailable("UV_CONTAINER_MODEL_UNAVAILABLE") from exc
    return {
        "schema": "universal-video-container-readiness-v1",
        "status": "READY",
        "source_commit": commit,
        "python": ".".join(map(str, sys.version_info[:3])),
        "runtime": runtime,
        "mounts": sorted(str(path) for path in mounts.values()),
        "fallback_used": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    command = list(sys.argv[1:] if argv is None else argv)
    if not command:
        command = ["python", "-m", "universal_video.spool_worker"]
    try:
        readiness = validate_container_runtime()
    except ContainerRuntimeUnavailable as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.error_code}, sort_keys=True), file=sys.stderr)
        return 78
    print(json.dumps(readiness, sort_keys=True), flush=True)
    os.execvp(command[0], command)
    return 127


if __name__ == "__main__":  # pragma: no cover - exercised by image startup
    raise SystemExit(main())
