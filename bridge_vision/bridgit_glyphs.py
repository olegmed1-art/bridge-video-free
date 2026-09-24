"""Geometry-only glyph crop planning for visible Bridgit card starts.

No rank or suit is inferred here. Given a validated visible card start, this
module returns bounded rank/suit rectangles in the exposed top-left card corner.
A downstream recognizer may score those pixels; ambiguous scores remain unknown.
"""
from __future__ import annotations
from dataclasses import dataclass

BRIDGIT_GLYPH_GEOMETRY_VERSION = "bridgit-glyph-geometry-v1"

@dataclass(frozen=True)
class GlyphBox:
    x: int
    y: int
    w: int
    h: int

@dataclass(frozen=True)
class CardGlyphBoxes:
    rank: GlyphBox
    suit: GlyphBox


def glyph_boxes_for_start(x: int, y: int, *, visible_width: int, visible_height: int) -> CardGlyphBoxes | None:
    """Return conservative glyph boxes or None when too little card is exposed.

    Bridgit keeps rank and small suit mark in the top-left exposed strip. We
    require enough visible pixels for both signals; otherwise fail closed.
    """
    if min(x, y) < 0 or visible_width <= 0 or visible_height <= 0:
        raise ValueError("invalid visible card geometry")
    if visible_width < 18 or visible_height < 38:
        return None
    # Conservative boxes avoid the neighbouring overlapped card border.
    rank_w = min(17, visible_width - 2)
    suit_w = min(16, visible_width - 2)
    return CardGlyphBoxes(
        rank=GlyphBox(x + 2, y + 2, rank_w, 19),
        suit=GlyphBox(x + 2, y + 20, suit_w, 16),
    )


__all__ = ["BRIDGIT_GLYPH_GEOMETRY_VERSION", "GlyphBox", "CardGlyphBoxes", "glyph_boxes_for_start"]
