"""Conservative, label-free card boundary measurements for the primary pass.

This is not the experimental Geometry recognizer. It measures exposed card
faces in the existing profile's horizontal hand regions before any template
matching. Fan lengths, card identities and deck completion are never inputs.
Occluded strips can establish height, but cannot establish card width.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from bridge_vision.gambler_classic_reference import VARIANT_CARD_SIZE

SCALE_VERSION = "gambler-pixel-boundaries-v1"
MAX_BOUNDARY_ERROR_PX = 1.0
MAX_ASPECT_ERROR = 0.035


@dataclass(frozen=True)
class CardScale:
    status: str
    reason: str
    width_px: float | None = None
    height_px: float | None = None
    width_state: str = "unknown"
    height_state: str = "unknown"
    boundaries: tuple[tuple[int, int, int, int], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"version": SCALE_VERSION, **asdict(self),
                "coordinate_space": "INPUT_FRAME_PIXELS",
                "boundary_error_px": MAX_BOUNDARY_ERROR_PX,
                "selection_eligible": self.status == "MEASURED"}


def compatible_variants(width: float, height: float) -> tuple[tuple[float, int], ...]:
    """One size ranking, independent of rank scores, with no aspect stretching."""
    try:
        width, height = float(width), float(height)
    except (TypeError, ValueError, OverflowError):
        return ()
    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
        return ()
    scored = []
    for variant, (nw, nh) in VARIANT_CARD_SIZE.items():
        if abs((width / height) / (nw / nh) - 1) > MAX_ASPECT_ERROR:
            continue
        if not (0.5 <= width / nw <= 2.0 and 0.5 <= height / nh <= 2.0):
            continue
        scored.append((max(abs(width - nw) / nw, abs(height - nh) / nh), variant))
    return tuple(sorted(scored))


def scales_agree(first: CardScale, second: CardScale) -> bool:
    return (first.status == second.status == "MEASURED"
            and abs(first.width_px - second.width_px) <= MAX_BOUNDARY_ERROR_PX
            and abs(first.height_px - second.height_px) <= MAX_BOUNDARY_ERROR_PX)


def measure_card_scale(image: Any, profile: Any) -> CardScale:
    """Require two exposed, ink-bearing, closed rectangular card boundaries.

    Components are found at a single fixed brightness threshold. Contrasting
    outside pixels must surround their white face; zero to three pixels of
    neutral dark border are measured on each side. Artwork holes are allowed.
    Blank panels, clipped components and
    narrow overlap strips cannot become full-width measurements. Conflicting
    eligible components cause abstention, even if one group is more numerous.
    """
    import cv2
    import numpy as np

    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        return CardScale("UNKNOWN", "invalid_raster")
    height, width = image.shape[:2]
    if width != profile.width or not profile.height <= height <= profile.height + 120:
        return CardScale("UNKNOWN", "unsupported_viewport")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    neutral = image.max(axis=2).astype(int) - image.min(axis=2).astype(int) <= 30
    white = (gray > 200) & neutral
    dark = (gray < 200) & neutral
    boxes: set[tuple[int, int, int, int]] = set()
    partial_heights: list[int] = []
    padding = height - profile.height
    for x_min, x_max, anchor_y in profile.horizontal_search.values():
        x0, x1 = max(0, x_min - 16), min(width, x_max + 192)
        y0, y1 = max(0, anchor_y - 24), min(height, anchor_y + padding + 380)
        mask = white[y0:y1, x0:x1].astype("uint8")
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        faces = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if h < 30 or w < 8:
                continue
            # Compressed video can leave a few white pixels bridging a black
            # card seam. Split only on an observed almost-full-height seam,
            # never at a hypothesized overlap step or expected card count.
            margin_y = max(3, round(h * 0.08))
            columns = dark[y0 + y + margin_y:y0 + y + h - margin_y,
                           x0 + x:x0 + x + w].mean(axis=0) >= 0.90
            start = 0
            for index in range(w):
                if columns[index]:
                    if index - start >= 8:
                        faces.append((x + start, y, index - start, h))
                    start = index + 1
            if w - start >= 8:
                faces.append((x + start, y, w - start, h))
        for x, y, w, h in faces:
            # A face touching the search boundary has no proven outside edge.
            if x < 2 or y < 2 or x + w + 2 >= mask.shape[1] or y + h + 2 >= mask.shape[0]:
                continue
            x, y = x + x0, y + y0
            if not anchor_y - 16 <= y <= anchor_y + padding + 8:
                continue
            if not (8 <= w <= 380 and 30 <= h <= 360):
                continue
            # Ignore rounded corners. All four straight borders need support.
            margin = max(3, round(min(w, h) * 0.08))
            if w <= 2 * margin or h <= 2 * margin:
                continue
            def edge(array, side, offset):
                if side == 0:
                    return array[y - offset, x + margin:x + w - margin]
                if side == 1:
                    return array[y + h - 1 + offset, x + margin:x + w - margin]
                if side == 2:
                    return array[y + margin:y + h - margin, x - offset]
                return array[y + margin:y + h - margin, x + w - 1 + offset]

            if min(x, y, width - x - w, height - y - h) < 4:
                continue
            border_widths = []
            for side in range(4):
                thickness = 0
                for offset in range(1, 5):
                    if float(edge(dark, side, offset).mean()) < 0.90:
                        break
                    thickness += 1
                # Thick dark panels have no proven outside card edge. Without
                # a dark border, require a direct white/background transition.
                if thickness == 4 or (thickness == 0 and float(edge(white, side, 1).mean()) > 0.10):
                    break
                border_widths.append(thickness)
            if len(border_widths) != 4:
                continue
            inner_edges = (white[y + 1, x + margin:x + w - margin],
                           white[y + h - 2, x + margin:x + w - margin],
                           white[y + margin:y + h - margin, x + 1],
                           white[y + margin:y + h - margin, x + w - 2])
            if any(float(edge.mean()) < 0.80 for edge in inner_edges):
                continue
            face = gray[y:y + h, x:x + w]
            # Ink is required, but its identity/recognition score is not used.
            corner = face[2:max(4, round(h * 0.25)), 2:max(4, round(w * 0.28))]
            ink_fraction = float((corner < 170).mean())
            if not 0.02 <= ink_fraction <= 0.65 or float(white[y:y + h, x:x + w].mean()) < 0.60:
                continue
            top, bottom, left, right = border_widths
            full_w, full_h = w + left + right, h + top + bottom
            if any(float(edge(white, side, thickness + 1).mean()) > 0.10
                   for side, thickness in ((0, top), (1, bottom))):
                continue
            if 0.30 <= full_w / full_h <= 0.82:
                partial_heights.append(full_h)
            # The profile's N/S fans run left to right. A seam followed by
            # another white face bounds an overlap strip, not a complete card.
            # Only the terminal card has a proven outside right edge. Without
            # it even a strip with a plausible aspect ratio remains UNKNOWN.
            terminal = float(edge(white, 3, right + 1).mean()) <= 0.10
            if terminal and compatible_variants(full_w, full_h):
                boxes.add((x - left, y - top, full_w, full_h))
    ordered = tuple(sorted(boxes))
    if len(ordered) < 2:
        if len(partial_heights) >= 2 and max(partial_heights) - min(partial_heights) <= 2:
            measured_height = float(np.median(partial_heights))
            return CardScale("UNKNOWN", "width_occluded_or_insufficient_boundaries",
                             height_px=measured_height, height_state="measured", boundaries=ordered)
        return CardScale("UNKNOWN", "insufficient_closed_boundaries", boundaries=ordered)
    widths = [box[2] for box in ordered]
    heights = [box[3] for box in ordered]
    if max(widths) - min(widths) > 2 or max(heights) - min(heights) > 2:
        return CardScale("TIE", "conflicting_pixel_scales", boundaries=ordered)
    return CardScale("MEASURED", "repeated_closed_card_boundaries",
                     float(np.median(widths)), float(np.median(heights)),
                     "measured", "measured", ordered)
