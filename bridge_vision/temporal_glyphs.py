"""Temporal consensus for repeated glyph masks from nearby video frames.

No semantic label is learned here. The module only decides whether multiple
observations are visually stable enough to form a candidate template. Unstable
frames are excluded; insufficient stable support fails closed.
"""
from __future__ import annotations
from typing import Sequence

TEMPORAL_GLYPH_VERSION = "bridgit-temporal-glyph-v1"
Mask = Sequence[Sequence[bool]]


def _shape(mask: Mask) -> tuple[int, int]:
    h = len(mask)
    w = len(mask[0]) if h else 0
    if not h or not w or any(len(row) != w for row in mask):
        raise ValueError("glyph mask must be a non-empty rectangle")
    return h, w


def mask_iou(a: Mask, b: Mask) -> float:
    if _shape(a) != _shape(b):
        raise ValueError("glyph mask dimensions differ")
    inter = union = 0
    for ra, rb in zip(a, b):
        for va, vb in zip(ra, rb):
            va, vb = bool(va), bool(vb)
            inter += va and vb
            union += va or vb
    return float(inter / union) if union else 1.0


def stable_consensus(masks: Sequence[Mask], *, min_pair_iou: float = .90, min_support: int = 2) -> dict:
    if not 0.0 <= min_pair_iou <= 1.0:
        raise ValueError("min_pair_iou outside [0,1]")
    if min_support < 2:
        raise ValueError("min_support must be at least 2")
    if len(masks) < min_support:
        return {"status": "INSUFFICIENT_SUPPORT", "template": None, "stable_indices": []}
    shape = _shape(masks[0])
    if any(_shape(mask) != shape for mask in masks[1:]):
        raise ValueError("glyph mask dimensions differ")
    stable = []
    for i, mask in enumerate(masks):
        if any(i != j and mask_iou(mask, other) >= min_pair_iou for j, other in enumerate(masks)):
            stable.append(i)
    if len(stable) < min_support:
        return {"status": "UNSTABLE", "template": None, "stable_indices": stable}
    h, w = shape
    needed = len(stable) // 2 + 1
    template = [[sum(bool(masks[i][y][x]) for i in stable) >= needed for x in range(w)] for y in range(h)]
    return {"status": "STABLE", "template": template, "stable_indices": stable,
            "support": len(stable), "min_pair_iou": min_pair_iou,
            "version": TEMPORAL_GLYPH_VERSION}


__all__ = ["TEMPORAL_GLYPH_VERSION", "mask_iou", "stable_consensus"]
