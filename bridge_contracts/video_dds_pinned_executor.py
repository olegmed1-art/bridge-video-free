"""Digest-pinned DDS3 position executor for video post-analysis."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
from typing import Any, Mapping

from bridge_school_api.dds3.position_runtime import (
    DDS3PositionConfig,
    PositionWorker,
    solve_position_all_moves,
)
from bridge_school_api.dds3.service import DDS_UPSTREAM


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PinnedDDSExecutorError(RuntimeError):
    pass


def execute_digest_pinned_dds3(request: Mapping[str, Any]) -> Mapping[str, Any]:
    """Run one isolated DDS position request after verifying executable bytes."""
    if not isinstance(request, Mapping) or request.get("operation") != "position_all_moves":
        raise PinnedDDSExecutorError("bounded position_all_moves request required")
    position = request.get("position")
    if not isinstance(position, Mapping):
        raise PinnedDDSExecutorError("DDS position required")
    expected = os.environ.get("DDS3_POSITION_WORKER_SHA256", "").strip().lower()
    if not _SHA256.fullmatch(expected):
        raise PinnedDDSExecutorError("DDS3_POSITION_WORKER_SHA256 is required")
    config = DDS3PositionConfig()
    executable = Path(config.executable)
    if not executable.is_absolute() or not executable.is_file():
        raise PinnedDDSExecutorError("pinned DDS3 executable is unavailable")
    actual = hashlib.sha256(executable.read_bytes()).hexdigest()
    if actual != expected:
        raise PinnedDDSExecutorError("pinned DDS3 executable digest mismatch")

    worker = PositionWorker(config)
    try:
        result = solve_position_all_moves(position, worker=worker)
    finally:
        worker.close()
    result["engine_version"] = DDS_UPSTREAM
    result["binary_sha256"] = actual
    return result


__all__ = ["PinnedDDSExecutorError", "execute_digest_pinned_dds3"]
