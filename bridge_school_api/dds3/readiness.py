from __future__ import annotations

import json
import os
import subprocess

from .position_runtime import DDS3PositionConfig, PositionWorker, PositionWorkerUnavailable, solve_position_all_moves
from .service import DDS3Config, DDS_UPSTREAM


def engine_readiness(config: DDS3Config | None = None) -> dict:
    cfg = config or DDS3Config()
    path = cfg.executable
    if not os.path.isfile(path) or not os.access(path, os.X_OK):
        return {
            "status": "unavailable",
            "engine": "DDS3",
            "engine_version": DDS_UPSTREAM,
            "executable": path,
            "reason": "DDS3_EXECUTABLE_MISSING",
            "fallback_used": False,
        }
    deal = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"
    try:
        proc = subprocess.run(
            [path, "N", "None", deal],
            capture_output=True,
            text=True,
            timeout=min(cfg.timeout_seconds, 5),
            check=False,
        )
        if proc.returncode != 0:
            return {
                "status": "unavailable",
                "engine": "DDS3",
                "engine_version": DDS_UPSTREAM,
                "reason": "DDS3_SELFTEST_FAILED",
                "fallback_used": False,
            }
        data = json.loads(proc.stdout)
        if data.get("par_score_ns") != -110:
            return {
                "status": "unavailable",
                "engine": "DDS3",
                "engine_version": DDS_UPSTREAM,
                "reason": "DDS3_SELFTEST_MISMATCH",
                "fallback_used": False,
            }
    except Exception:
        return {
            "status": "unavailable",
            "engine": "DDS3",
            "engine_version": DDS_UPSTREAM,
            "reason": "DDS3_SELFTEST_ERROR",
            "fallback_used": False,
        }

    position_cfg = DDS3PositionConfig(timeout_seconds=min(float(os.getenv("DDS3_POSITION_TIMEOUT_SECONDS", "20")), 5.0))
    position_path = position_cfg.executable
    if not os.path.isfile(position_path) or not os.access(position_path, os.X_OK):
        return {
            "status": "unavailable",
            "engine": "DDS3",
            "engine_version": DDS_UPSTREAM,
            "reason": "DDS3_POSITION_WORKER_MISSING",
            "fallback_used": False,
        }
    worker = PositionWorker(position_cfg)
    try:
        position = solve_position_all_moves(
            {"pbn": deal, "trump": "NT", "first": "N", "current_trick": []},
            worker=worker,
        )
        if not position.get("moves") or position.get("fallback_used") is not False:
            raise PositionWorkerUnavailable("DDS3_POSITION_SELFTEST_MISMATCH")
    except Exception:
        return {
            "status": "unavailable",
            "engine": "DDS3",
            "engine_version": DDS_UPSTREAM,
            "reason": "DDS3_POSITION_SELFTEST_ERROR",
            "fallback_used": False,
        }
    finally:
        worker.close()

    return {
        "status": "ready",
        "engine": "DDS3",
        "engine_version": DDS_UPSTREAM,
        "executable": path,
        "position_worker": position_path,
        "position_solver": "ready",
        "fallback_used": False,
    }
