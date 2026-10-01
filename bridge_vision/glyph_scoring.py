"""Small deterministic scorer for already-cropped Bridgit rank/suit glyph masks.

Templates are data, not decisions: a label is emitted only when the best match
clears both an absolute score gate and a separation margin from runner-up.
"""
from __future__ import annotations
from typing import Mapping, Sequence

Mask = Sequence[Sequence[bool]]


def mask_iou(a: Mask, b: Mask) -> float:
    if len(a) != len(b) or any(len(x) != len(y) for x, y in zip(a, b)):
        raise ValueError("glyph masks must have identical dimensions")
    intersection = union = 0
    for ra, rb in zip(a, b):
        if len(ra) != len(rb):
            raise ValueError("glyph masks must have identical dimensions")
        for va, vb in zip(ra, rb):
            va, vb = bool(va), bool(vb)
            intersection += va and vb
            union += va or vb
    return 1.0 if union == 0 else intersection / union


def classify_mask(mask: Mask, templates: Mapping[str, Mask], *, min_score: float = .75, min_margin: float = .08) -> dict:
    if not templates:
        return {"label": None, "reason": "NO_TEMPLATES", "scores": {}}
    scores = {label: mask_iou(mask, template) for label, template in templates.items()}
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_label, best = ordered[0]
    second = ordered[1][1] if len(ordered) > 1 else 0.0
    if best < min_score:
        return {"label": None, "reason": "LOW_GLYPH_SCORE", "best": best, "margin": best-second, "scores": scores}
    if best - second < min_margin:
        return {"label": None, "reason": "AMBIGUOUS_GLYPH", "best": best, "margin": best-second, "scores": scores}
    return {"label": best_label, "confidence": best, "margin": best-second, "scores": scores}

__all__ = ["mask_iou", "classify_mask"]
