"""Bridge School DDS3 service.

Thin fail-closed wrapper around the deterministic dds_pbn_cli executable.
No alternative solver or heuristic fallback is permitted.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any


class DDSUnavailable(RuntimeError):
    """DDS3 executable is unavailable or failed."""


@dataclass(frozen=True)
class DDS3Config:
    executable: str = os.getenv("DDS3_CLI", "/opt/bridge-school-dds3/dds_pbn_cli")
    timeout_seconds: float = float(os.getenv("DDS3_TIMEOUT_SECONDS", "15"))


def solve_table(*, pbn: str, dealer: str = "N", vulnerability: str = "None", config: DDS3Config | None = None) -> dict[str, Any]:
    cfg = config or DDS3Config()
    if not pbn or not pbn.strip():
        raise ValueError("pbn is required")
    try:
        proc = subprocess.run(
            [cfg.executable, dealer, vulnerability, pbn],
            check=False,
            capture_output=True,
            text=True,
            timeout=cfg.timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DDSUnavailable("DDS_UNAVAILABLE") from exc
    if proc.returncode != 0:
        raise DDSUnavailable("DDS_UNAVAILABLE")
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise DDSUnavailable("DDS_UNAVAILABLE") from exc
    result["engine"] = "DDS3"
    result["fallback_used"] = False
    return result
