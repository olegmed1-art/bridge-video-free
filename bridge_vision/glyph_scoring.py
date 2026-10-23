"""Deterministic rank/suit glyph scoring with absolute and separation gates."""
from __future__ import annotations

from typing import Mapping, Sequence

Mask = Sequence[Sequence[bool]]
MIN_GLYPH_SCORE = 0.75
MIN_GLYPH_MARGIN = 0.08


def mask_iou(a: Mask, b: Mask) -> float:
    if not a or not b or len(a) != len(b) or any(len(x) != len(y) for x, y in zip(a, b)):
        raise ValueError("glyph masks must have identical non-empty dimensions")
    intersection = union = 0
    for row_a, row_b in zip(a, b):
        if not row_a:
            raise ValueError("glyph masks must have identical non-empty dimensions")
        for value_a, value_b in zip(row_a, row_b):
            value_a, value_b = bool(value_a), bool(value_b)
            intersection += value_a and value_b
            union += value_a or value_b
    return 1.0 if union == 0 else intersection / union


def classify_mask(
    mask: Mask, templates: Mapping[str, Mask], *,
    min_score: float = MIN_GLYPH_SCORE, min_margin: float = MIN_GLYPH_MARGIN,
) -> dict:
    if min_score < MIN_GLYPH_SCORE or min_score > 1 or min_margin < MIN_GLYPH_MARGIN or min_margin > 1:
        raise ValueError("glyph thresholds cannot be lowered")
    if not templates:
        return {"label": None, "reason": "NO_TEMPLATES", "scores": {}}
    scores = {str(label): mask_iou(mask, template) for label, template in templates.items()}
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    best_label, best = ordered[0]
    second = ordered[1][1] if len(ordered) > 1 else 0.0
    if best < min_score:
        return {"label": None, "reason": "LOW_GLYPH_SCORE", "best": best, "margin": best - second, "scores": scores}
    if best - second < min_margin:
        return {"label": None, "reason": "AMBIGUOUS_GLYPH", "best": best, "margin": best - second, "scores": scores}
    return {"label": best_label, "confidence": best, "margin": best - second, "scores": scores}


__all__ = ["MIN_GLYPH_MARGIN", "MIN_GLYPH_SCORE", "classify_mask", "mask_iou"]
