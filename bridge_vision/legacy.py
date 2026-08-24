"""Explicit legacy adapters for Bridge Vision.

Legacy recognizers are opt-in compatibility tools. They are never registered by
default and their platform/layout assumptions must remain visible in provenance.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def old_bbo_report_parser(frame: Path) -> dict[str, Any]:
    from bridge_report_board_reconstruction import parse_image

    raw = parse_image(frame)
    hands = raw.get("hands") or {}
    count = sum(len(cards) for cards in hands.values())
    confidence = 1.0 if raw.get("status") == "PARTIAL_BOARD_OBSERVATION" and count else 0.0
    return {
        "hands": hands,
        "confidence": confidence,
        "evidence": {
            "adapter": "legacy:old-bbo-report-parser",
            "parser_status": raw.get("status"),
            "recognized_card_count": raw.get("recognized_card_count", count),
            "state_fingerprint": raw.get("state_fingerprint"),
            "limitations": ["old_bbo_layout", "north_south_primary"],
        },
    }


__all__ = ["old_bbo_report_parser"]
