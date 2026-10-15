"""Conservative OCR for the numbered tile in the upper-right Bridgit compass."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

# Native desktop coordinates observed on the 1680x934 development frame.
# The 1920x1010 profile keeps native card coordinates; other compass layouts
# fail closed when this pale tile and its digit are not proven.
NUMBER_BOX_XYXY = (1228, 40, 1269, 78)


def _normalized(mask: Any) -> Any:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    ys, xs = np.where(mask > 0)
    if not len(xs):
        return None
    glyph = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    height, width = glyph.shape
    scale = min(20 / width, 28 / height)
    resized = cv2.resize(glyph, (max(1, round(width * scale)), max(1, round(height * scale))),
                         interpolation=cv2.INTER_NEAREST)
    canvas = np.zeros((32, 24), dtype=np.uint8)
    y = (32 - resized.shape[0]) // 2
    x = (24 - resized.shape[1]) // 2
    canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return canvas


@lru_cache(maxsize=1)
def _digit_templates() -> dict[str, tuple[Any, ...]]:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    fonts = (cv2.FONT_HERSHEY_SIMPLEX, cv2.FONT_HERSHEY_DUPLEX,
             cv2.FONT_HERSHEY_COMPLEX, cv2.FONT_HERSHEY_TRIPLEX)
    templates = {}
    for digit in "0123456789":
        variants = []
        for font in fonts:
            canvas = np.zeros((42, 38), dtype=np.uint8)
            cv2.putText(canvas, digit, (4, 34), font, 0.9, 255, 1, cv2.LINE_AA)
            normalized = _normalized((canvas >= 100).astype(np.uint8) * 255)
            variants.append(normalized)
        templates[digit] = tuple(variants)
    return templates


def read_board_number(frame: Any) -> dict[str, Any]:
    """Return a number only when digit shape and distinctness both pass.

    This is a gate, not a source of card or deal truth. Any unsupported layout,
    noisy glyph, or competing numeral yields UNREADABLE rather than a guess.
    """
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    x0, y0, x1, y1 = NUMBER_BOX_XYXY
    if frame is None or not hasattr(frame, "shape") or len(frame.shape) < 2 or (
            frame.shape[1] < x1 or frame.shape[0] < y1):
        return {"status": "UNREADABLE", "number": None, "reason": "UNSUPPORTED_FRAME"}
    region = frame[y0:y1, x0:x1]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    if float(np.median(gray)) < 165 or float(np.percentile(gray, 90)) < 180:
        return {"status": "UNREADABLE", "number": None, "reason": "NUMBER_TILE_NOT_PROVEN"}
    # Compass number is dark on the pale centre tile. Otsu would be pulled by
    # occasional coloured compass overlays; keep only near-black strokes.
    mask = (gray < 105).astype(np.uint8) * 255
    count, _, boxes, _ = cv2.connectedComponentsWithStats(mask, 8)
    parts = []
    for index in range(1, count):
        x, y, width, height, area = map(int, boxes[index])
        if area >= 5 and 6 <= height <= 27 and 1 <= width <= 19:
            parts.append((x, y, width, height))
    parts.sort()
    if not 1 <= len(parts) <= 3:
        return {"status": "UNREADABLE", "number": None, "reason": "DIGIT_SEGMENTATION"}
    templates = _digit_templates()
    digits = []
    scores = []
    for x, y, width, height in parts:
        glyph = _normalized(mask[y:y + height, x:x + width])
        if glyph is None:
            return {"status": "UNREADABLE", "number": None, "reason": "EMPTY_DIGIT"}
        ranked = sorted(((float((glyph == variant).mean()), digit)
                         for digit, variants in templates.items() for variant in variants), reverse=True)
        best_score, best_digit = ranked[0]
        runner_up = max(score for score, digit in ranked if digit != best_digit)
        if best_score < 0.78 or best_score - runner_up < 0.035:
            return {"status": "UNREADABLE", "number": None, "reason": "AMBIGUOUS_DIGIT"}
        digits.append(best_digit)
        scores.append(round(best_score, 4))
    value = int("".join(digits))
    if value < 1 or value > 999:
        return {"status": "UNREADABLE", "number": None, "reason": "NUMBER_OUT_OF_RANGE"}
    return {"status": "READ", "number": value, "digit_scores": scores,
            "region_xyxy": list(NUMBER_BOX_XYXY)}


__all__ = ["NUMBER_BOX_XYXY", "read_board_number"]
