"""Suit-shape classifier where colour is only a family constraint."""
from __future__ import annotations

from typing import Any, Mapping

SUIT_SHAPE_VERSION = "bridge-suit-shape-v2"
MIN_SUIT_SCORE = 0.90
MIN_SUIT_MARGIN = 0.08
_FAMILIES = {"red": ("H", "D"), "black": ("S", "C")}


def classify_suit(
    scores: Mapping[str, Any], *, colour_family: str,
    min_score: float = MIN_SUIT_SCORE, min_margin: float = MIN_SUIT_MARGIN,
) -> dict[str, Any]:
    if colour_family not in _FAMILIES:
        raise ValueError("colour_family must be red or black")
    if min_score < MIN_SUIT_SCORE or min_score > 1 or min_margin < MIN_SUIT_MARGIN or min_margin > 1:
        raise ValueError("suit thresholds cannot be lowered")
    parsed = []
    for suit in _FAMILIES[colour_family]:
        try:
            value = float(scores.get(suit))
        except (TypeError, ValueError):
            value = -1.0
        parsed.append((suit, value if 0.0 <= value <= 1.0 else -1.0))
    parsed.sort(key=lambda item: item[1], reverse=True)
    best, second = parsed
    evidence = {
        "schema": SUIT_SHAPE_VERSION, "colour_role": "FAMILY_FILTER_ONLY",
        "colour_family": colour_family, "shape_scores": dict(parsed),
        "min_score": min_score, "min_margin": min_margin,
    }
    if best[1] < min_score:
        return {"suit": None, "confidence": 0.0, "reason": "LOW_SUIT_SCORE", "evidence": evidence}
    if best[1] - second[1] < min_margin:
        return {"suit": None, "confidence": 0.0, "reason": "AMBIGUOUS_SUIT_SHAPE", "evidence": evidence}
    return {"suit": best[0], "confidence": best[1], "reason": None, "evidence": evidence}


__all__ = ["MIN_SUIT_MARGIN", "MIN_SUIT_SCORE", "SUIT_SHAPE_VERSION", "classify_suit"]
