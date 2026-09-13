from __future__ import annotations

from pathlib import Path
from typing import Any


def directory_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def build_video_finops_observation(
    *,
    status: str,
    elapsed_seconds: float,
    input_bytes: int | None = None,
    output_bytes: int | None = None,
    video_seconds: float | None = None,
    whisper_model: str | None = None,
    source_kind: str | None = None,
    error_class: str | None = None,
) -> dict[str, Any]:
    """Return observed video usage without inventing a monetary cost."""

    observation: dict[str, Any] = {
        "category": "VIDEO",
        "provider": "oracle",
        "workload_kind": "UNIVERSAL_VIDEO",
        "status": status,
        "wall_time_ms": max(0, round(float(elapsed_seconds) * 1000)),
        "input_bytes": max(0, int(input_bytes)) if input_bytes is not None else None,
        "output_bytes": max(0, int(output_bytes)) if output_bytes is not None else None,
        "video_seconds": max(0.0, float(video_seconds)) if video_seconds is not None else None,
        "whisper_model": whisper_model or None,
        "source_kind": source_kind or None,
        "error_class": error_class or None,
        "pricing_basis": "runtime_observed_cost_pending",
        "estimated_cost_usd": None,
    }
    return observation
