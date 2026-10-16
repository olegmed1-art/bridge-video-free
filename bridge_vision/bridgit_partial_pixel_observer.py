"""Deterministic, shadow-only partial card observer using original Gambler pixels.

Geometry is caller supplied. Only a visible, unoccluded top-left card corner is
classified; an uncertain slot is returned as unknown, never completed by deck
logic. The input sprite must have passed the pinned loader in the adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge_vision.gambler_classic_reference import (
    CARDS, GamblerClassicSprite, decode_card_cells,
)
from bridge_vision.native_cards import Box, TableRegion, _seat_for

PIXEL_OBSERVER_VERSION = "bridgit-gambler-partial-pixels-v3"


class PartialPixelObservationError(ValueError):
    pass


@dataclass(frozen=True)
class CardCorner:
    x: int
    y: int
    seat: str


@dataclass(frozen=True)
class PartialPixelGeometry:
    width: int
    height: int
    card_width: int
    card_height: int
    table_region: tuple[int, int, int, int]
    corners: tuple[CardCorner, ...]
    auto_discover: bool = False


def _normalized_bank(crops: list[Any]):
    import numpy as np  # type: ignore

    variants = np.stack(crops).astype(np.float32).reshape(len(crops), -1)
    variants -= variants.mean(axis=1, keepdims=True)
    variants /= np.maximum(np.linalg.norm(variants, axis=1, keepdims=True), 1e-6)
    return variants


def _corner_size(sprite: GamblerClassicSprite) -> tuple[int, int]:
    # Both rank and suit must fit; proportions follow the actual pinned cell.
    return max(8, round(sprite.card_width * 0.30)), max(12, round(sprite.card_height * 0.32))


def _bgr_cell(cell: Any):
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    unit = float(np.iinfo(cell.dtype).max)
    if cell.ndim == 3 and cell.shape[2] == 4:
        alpha = cell[:, :, 3:4].astype(np.float32) / unit
        colour = cell[:, :, :3].astype(np.float32) / unit
        return np.rint(255.0 * (colour * alpha + 1.0 - alpha)).astype(np.uint8)
    if cell.ndim == 3 and cell.shape[2] == 3:
        return np.rint(cell.astype(np.float32) * 255.0 / unit).astype(np.uint8)
    gray = np.rint(cell.astype(np.float32) * 255.0 / unit).astype(np.uint8)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def _discover_corners(image: Any, sprite: GamblerClassicSprite,
                      geometry: PartialPixelGeometry) -> tuple[CardCorner, ...]:
    """Match the common pinned card-border cue in bounded four-seat bands.

    This follows the existing anchor registration's local template search and
    peak suppression. The 52-card identity matcher remains in this observer.
    """
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    tx, ty, tw, th = geometry.table_region
    cw, ch = sprite.card_width, sprite.card_height
    white = (image[:, :, :3] >= 225).all(axis=2).astype(np.float32)
    cue_w, cue_h = max(6, round(cw * .18)), max(6, round(ch * .12))
    cells = decode_card_cells(sprite)
    cue = np.stack([(_bgr_cell(cells[card])[:cue_h, :cue_w].min(axis=2) >= 225)
                    .astype(np.float32) for card in CARDS]).mean(axis=0)
    if float(cue.std()) < .08:
        raise PartialPixelObservationError("CARD_BORDER_CUE_UNPROVEN")
    zones = {
        "N": (tx + round(tw * .15), tx + round(tw * .85), ty, ty + round(th * .22)),
        "S": (tx + round(tw * .15), tx + round(tw * .85),
              ty + th - ch - round(th * .18), ty + th),
        "W": (tx, tx + round(tw * .25), ty + round(th * .18), ty + round(th * .72)),
        "E": (tx + round(tw * .72), tx + tw, ty + round(th * .18),
              ty + round(th * .72)),
    }
    found: list[CardCorner] = []
    for seat, (left, right, top, bottom) in zones.items():
        left, right = max(tx, left), min(tx + tw, right)
        top, bottom = max(ty, top), min(ty + th, bottom + cue_h)
        if right - left < cue_w or bottom - top < cue_h:
            continue
        scores = cv2.matchTemplate(white[top:bottom, left:right], cue,
                                   cv2.TM_CCOEFF_NORMED)
        for _ in range(13):  # A seat cannot expose more than 13 distinct cards.
            _, score, _, location = cv2.minMaxLoc(scores)
            if score < .55:
                break
            lx, ly = location
            x, y = left + lx, top + ly
            radius_x, radius_y = max(2, round(cw * .055)), max(2, round(ch * .025))
            scores[max(0, ly - radius_y):ly + radius_y + 1,
                   max(0, lx - radius_x):lx + radius_x + 1] = -1
            if x + cw > tx + tw or y + ch > ty + th:
                continue
            mapped = _seat_for(Box(x, y, cw, ch), TableRegion(tx, ty, tw, th),
                               dead_zone=.08)
            if mapped == seat:
                found.append(CardCorner(x, y, seat))
    return tuple(sorted(found, key=lambda item: (item.seat, item.y, item.x)))


class GamblerPartialPixelObserver:
    """Score explicitly positioned native-size card corners; report unknowns."""

    def __init__(self, geometry: PartialPixelGeometry, *, minimum_score: float = 0.78,
                 minimum_margin: float = 0.08):
        if (not 0.0 < minimum_score <= 1.0 or not 0.0 < minimum_margin <= 1.0):
            raise PartialPixelObservationError("INVALID_SCORE_GATE")
        if (type(geometry.width) is not int or type(geometry.height) is not int
                or type(geometry.card_width) is not int or type(geometry.card_height) is not int
                or min(geometry.width, geometry.height, geometry.card_width, geometry.card_height) < 1
                or len(geometry.corners) > 52
                or (geometry.auto_discover and geometry.corners)
                or (not geometry.auto_discover and not geometry.corners)):
            raise PartialPixelObservationError("INVALID_GEOMETRY")
        tx, ty, tw, th = geometry.table_region
        if min(tx, ty) < 0 or min(tw, th) <= 0 or tx + tw > geometry.width or ty + th > geometry.height:
            raise PartialPixelObservationError("INVALID_TABLE_REGION")
        seen: set[tuple[int, int]] = set()
        for corner in geometry.corners:
            if (corner.seat not in "NESW" or type(corner.x) is not int or type(corner.y) is not int
                    or corner.x < tx or corner.y < ty
                    or corner.x >= tx + tw or corner.y >= ty + th
                    or (corner.x, corner.y) in seen):
                raise PartialPixelObservationError("INVALID_CARD_CORNER")
            if (corner.x + geometry.card_width <= tx + tw
                    and corner.y + geometry.card_height <= ty + th):
                mapped = _seat_for(
                    Box(corner.x, corner.y, geometry.card_width, geometry.card_height),
                    TableRegion(tx, ty, tw, th), dead_zone=0.08,
                )
                if mapped != corner.seat:
                    raise PartialPixelObservationError("CARD_SEAT_GEOMETRY_MISMATCH")
            seen.add((corner.x, corner.y))
        self.geometry = geometry
        self.minimum_score = minimum_score
        self.minimum_margin = minimum_margin
        self._bank_cache: dict[str, tuple[tuple[int, int], Any, list[Any], dict[bytes, str | None]]] = {}

    def __call__(self, frame_path: Path, sprite: GamblerClassicSprite) -> dict[str, Any]:
        import cv2  # type: ignore
        geometry = self.geometry
        if (geometry.card_width, geometry.card_height) != (sprite.card_width, sprite.card_height):
            raise PartialPixelObservationError("CARD_SCALE_UNSUPPORTED")
        image = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (geometry.height, geometry.width):
            raise PartialPixelObservationError("FRAME_GEOMETRY_MISMATCH")
        corners = (_discover_corners(image, sprite, geometry) if geometry.auto_discover
                   else geometry.corners)
        if sprite.sprite_sha256 not in self._bank_cache:
            size = _corner_size(sprite)
            cw, ch = size
            cells = decode_card_cells(sprite)
            templates = [_bgr_cell(cells[card])[:ch, :cw].copy() for card in CARDS]
            exact_lookup: dict[bytes, str | None] = {}
            for card, template in zip(CARDS, templates):
                key = template.tobytes()
                exact_lookup[key] = card if key not in exact_lookup else None
            self._bank_cache[sprite.sprite_sha256] = (
                size, _normalized_bank(templates), templates, exact_lookup)
        (roi_w, roi_h), bank, templates, exact_lookup = self._bank_cache[sprite.sprite_sha256]

        cards: list[dict[str, Any]] = []
        unknown: list[dict[str, Any]] = []
        if geometry.auto_discover and not corners:
            unknown.append({"index": 0, "reason": "NO_CORNERS_FOUND"})
        for index, corner in enumerate(corners):
            x, y = corner.x, corner.y
            if (x + geometry.card_width > geometry.width
                    or y + geometry.card_height > geometry.height
                    or x + roi_w > geometry.width or y + roi_h > geometry.height):
                unknown.append({"index": index, "reason": "CORNER_INCOMPLETE", "seat": corner.seat})
                continue
            radius = min(4, max(2, round(sprite.card_width * .03)))
            local_patches = []
            exact: set[str] = set()
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    xx, yy = x + dx, y + dy
                    if xx < 0 or yy < 0 or xx + roi_w > geometry.width or yy + roi_h > geometry.height:
                        continue
                    patch = image[yy:yy + roi_h, xx:xx + roi_w]
                    local_patches.append(patch)
                    key = patch.tobytes()
                    if key in exact_lookup:
                        bound_card = exact_lookup[key]
                        if bound_card is not None:
                            exact.add(bound_card)
            if len(exact) == 1:
                cards.append({"card": next(iter(exact)), "confidence": 1.0,
                              "box": {"x": x, "y": y, "w": geometry.card_width,
                                      "h": geometry.card_height}})
                continue
            if len(exact) > 1:
                unknown.append({"index": index, "reason": "CARD_AMBIGUOUS", "seat": corner.seat})
                continue
            best_by_card: dict[str, tuple[float, Any]] = {}
            for patch in local_patches:
                vector = patch.astype("float32").ravel()
                vector -= vector.mean()
                vector /= max(float(cv2.norm(vector)), 1e-6)
                scores = bank @ vector
                for card_index, score in enumerate(scores):
                    card = CARDS[card_index]
                    if card not in best_by_card or score > best_by_card[card][0]:
                        best_by_card[card] = (float(score), patch)
            if not best_by_card:
                unknown.append({"index": index, "reason": "CORNER_OUT_OF_BOUNDS", "seat": corner.seat})
                continue
            # Each identity gets its best local-registration score. Margin is
            # against a *different* card, not another offset of the same card.
            ordered = sorted(best_by_card.items(), key=lambda item: (-item[1][0], item[0]))
            card, (score, patch) = ordered[0]
            margin = score - ordered[1][1][0]
            expected = templates[CARDS.index(card)]
            white_mask = (expected >= 235).all(axis=2)
            white_retention = (float(((patch >= 220).all(axis=2))[white_mask].mean())
                               if white_mask.any() else 1.0)
            if white_retention < 0.85:
                unknown.append({"index": index, "reason": "CORNER_OCCLUDED", "seat": corner.seat})
                continue
            if score < self.minimum_score or margin < self.minimum_margin:
                unknown.append({"index": index, "reason": "CARD_UNCERTAIN", "seat": corner.seat,
                                "best_score": round(score, 4), "margin": round(margin, 4)})
                continue
            cards.append({"card": card, "confidence": round(score, 4),
                          "box": {"x": x, "y": y, "w": geometry.card_width,
                                  "h": geometry.card_height}})
        return {"version": PIXEL_OBSERVER_VERSION,
                "table_region": dict(zip(("x", "y", "w", "h"), geometry.table_region)),
                "cards": cards, "unknown": unknown,
                "corner_detection": {"mode": "PINNED_BORDER_SEAT_BANDS" if geometry.auto_discover
                                     else "EXPLICIT_TEST_CORNERS", "candidate_count": len(corners)}}


__all__ = ["PIXEL_OBSERVER_VERSION", "PartialPixelObservationError",
           "CardCorner", "PartialPixelGeometry", "GamblerPartialPixelObserver"]
