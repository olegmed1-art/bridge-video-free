"""Scale/translation registration from a verified upper-right UI anchor."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from typing import Any

MAX_ANCHOR_TEMPLATE_PIXELS = 512 * 512
MAX_ANCHOR_SEARCH_PIXELS = 250_000_000


class AnchorRegistrationError(ValueError):
    """The anchor is absent, ambiguous or cannot define a full game window."""


def _runtime():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover - worker dependency
        raise RuntimeError("anchor registration requires OpenCV and NumPy") from exc
    return cv2, np


def _number(value: Any, field: str, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AnchorRegistrationError(f"invalid {field}") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise AnchorRegistrationError(f"invalid {field}")
    return result


def validate_anchor_spec(raw: Any) -> dict[str, Any]:
    """Validate a bounded profile fragment using normalized coordinates."""

    if not isinstance(raw, Mapping) or raw.get("type") != "UPPER_RIGHT_TEMPLATE":
        raise AnchorRegistrationError("unsupported interface anchor")
    region = raw.get("reference_region")
    if not isinstance(region, Mapping):
        raise AnchorRegistrationError("interface anchor reference_region is required")
    normalized = {
        name: _number(region.get(name), f"reference_region.{name}", 0.0, 1.0)
        for name in ("x", "y", "width", "height")
    }
    if normalized["width"] <= 0 or normalized["height"] <= 0:
        raise AnchorRegistrationError("interface anchor region is empty")
    if normalized["x"] + normalized["width"] > 1.0 + 1e-9:
        raise AnchorRegistrationError("interface anchor leaves reference width")
    if normalized["y"] + normalized["height"] > 1.0 + 1e-9:
        raise AnchorRegistrationError("interface anchor leaves reference height")
    if normalized["x"] + normalized["width"] / 2 < 0.5:
        raise AnchorRegistrationError("interface anchor is not in the right half")
    if normalized["y"] + normalized["height"] / 2 > 0.5:
        raise AnchorRegistrationError("interface anchor is not in the upper half")
    scales_raw = raw.get("scales")
    if (
        not isinstance(scales_raw, Sequence)
        or isinstance(scales_raw, (str, bytes))
        or not 1 <= len(scales_raw) <= 33
    ):
        raise AnchorRegistrationError("interface anchor scales are invalid")
    scales = sorted(
        {_number(value, "interface anchor scale", 0.25, 4.0) for value in scales_raw}
    )
    if len(scales) != len(scales_raw):
        raise AnchorRegistrationError("interface anchor scales must be unique")
    return {
        "type": "UPPER_RIGHT_TEMPLATE",
        "reference_region": normalized,
        "scales": scales,
        "minimum_score": _number(raw.get("minimum_score"), "minimum_score", 0.0, 1.0),
        "minimum_margin": _number(
            raw.get("minimum_margin"), "minimum_margin", 0.0, 1.0
        ),
        "appearance": "INTENSITY_INVERSION_INVARIANT",
    }


def _appearance(image: Any):
    cv2, _ = _runtime()
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def register_from_upper_right_anchor(
    reference: Any,
    observed: Any,
    spec: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    """Return ``observed`` warped to reference pixels plus registration evidence."""

    cv2, np = _runtime()
    checked = validate_anchor_spec(spec)
    if not hasattr(reference, "shape") or not hasattr(observed, "shape"):
        raise AnchorRegistrationError("registration inputs must be decoded images")
    reference_height, reference_width = reference.shape[:2]
    observed_height, observed_width = observed.shape[:2]
    region = checked["reference_region"]
    anchor_x = round(region["x"] * reference_width)
    anchor_y = round(region["y"] * reference_height)
    anchor_width = max(8, round(region["width"] * reference_width))
    anchor_height = max(8, round(region["height"] * reference_height))
    if (
        anchor_x + anchor_width > reference_width
        or anchor_y + anchor_height > reference_height
    ):
        raise AnchorRegistrationError("rounded interface anchor leaves reference")
    if anchor_width * anchor_height > MAX_ANCHOR_TEMPLATE_PIXELS:
        raise AnchorRegistrationError("interface anchor exceeds template budget")
    if (
        observed_width * observed_height * len(checked["scales"])
        > MAX_ANCHOR_SEARCH_PIXELS
    ):
        raise AnchorRegistrationError("interface anchor search exceeds work budget")
    template = _appearance(
        reference[
            anchor_y : anchor_y + anchor_height, anchor_x : anchor_x + anchor_width
        ]
    )
    if float(template.std()) < 8.0:
        raise AnchorRegistrationError("interface anchor has insufficient visual detail")
    observed_appearance = _appearance(observed)
    candidates: list[dict[str, Any]] = []
    for scale in checked["scales"]:
        width = max(8, round(anchor_width * scale))
        height = max(8, round(anchor_height * scale))
        game_width = round(reference_width * scale)
        game_height = round(reference_height * scale)
        if width > observed_width or height > observed_height:
            continue
        scaled = cv2.resize(template, (width, height), interpolation=cv2.INTER_NEAREST)
        scores = np.abs(
            cv2.matchTemplate(observed_appearance, scaled, cv2.TM_CCOEFF_NORMED)
        )
        x_min = max(0, math.ceil(anchor_x * scale))
        y_min = max(0, math.ceil(anchor_y * scale))
        x_max = min(
            scores.shape[1] - 1,
            math.floor(observed_width - (reference_width - anchor_x) * scale),
        )
        y_max = min(
            scores.shape[0] - 1,
            math.floor(observed_height - (reference_height - anchor_y) * scale),
        )
        if x_min > x_max or y_min > y_max:
            continue
        valid = scores[y_min : y_max + 1, x_min : x_max + 1]
        for _ in range(2):
            _, maximum, _, location = cv2.minMaxLoc(valid)
            x = x_min + location[0]
            y = y_min + location[1]
            left = round(x - anchor_x * scale)
            top = round(y - anchor_y * scale)
            candidates.append(
                {
                    "score": float(maximum),
                    "scale": float(scale),
                    "anchor_x": x,
                    "anchor_y": y,
                    "anchor_width": width,
                    "anchor_height": height,
                    "window_x": left,
                    "window_y": top,
                    "window_width": game_width,
                    "window_height": game_height,
                }
            )
            local_x = location[0]
            local_y = location[1]
            radius = max(4, min(width, height) // 3)
            valid[
                max(0, local_y - radius) : min(valid.shape[0], local_y + radius + 1),
                max(0, local_x - radius) : min(valid.shape[1], local_x + radius + 1),
            ] = -1.0
    if not candidates:
        raise AnchorRegistrationError("upper-right interface anchor was not found")
    candidates.sort(
        key=lambda item: (
            -item["score"],
            item["scale"],
            item["window_y"],
            item["window_x"],
        )
    )
    best = candidates[0]
    if best["score"] < checked["minimum_score"]:
        raise AnchorRegistrationError("upper-right interface anchor score is too low")
    distant = next(
        (
            item
            for item in candidates[1:]
            if abs(item["window_x"] - best["window_x"])
            > max(4, best["window_width"] * 0.03)
            or abs(item["window_y"] - best["window_y"])
            > max(4, best["window_height"] * 0.03)
        ),
        None,
    )
    runner_up_score = float(distant["score"]) if distant is not None else -1.0
    if runner_up_score >= best["score"] - checked["minimum_margin"]:
        raise AnchorRegistrationError("upper-right interface anchor is ambiguous")
    left = int(best["window_x"])
    top = int(best["window_y"])
    right = left + int(best["window_width"])
    bottom = top + int(best["window_height"])
    if left < 0 or top < 0 or right > observed_width or bottom > observed_height:
        raise AnchorRegistrationError("registered game window leaves observed frame")
    crop = observed[top:bottom, left:right]
    registered = cv2.resize(
        crop, (reference_width, reference_height), interpolation=cv2.INTER_AREA
    )
    if registered.shape[:2] != (reference_height, reference_width):
        raise AnchorRegistrationError("registered game window has invalid dimensions")
    return registered, {
        "mode": "UPPER_RIGHT_ANCHOR",
        "anchor_type": checked["type"],
        "appearance": checked["appearance"],
        "score": round(float(best["score"]), 6),
        "runner_up_score": round(runner_up_score, 6),
        "score_margin": round(float(best["score"] - runner_up_score), 6),
        "scale": round(float(best["scale"]), 6),
        "anchor_region": {
            "coordinate_space": "NORMALIZED_INPUT_FRAME",
            "x": round(best["anchor_x"] / observed_width, 8),
            "y": round(best["anchor_y"] / observed_height, 8),
            "width": round(best["anchor_width"] / observed_width, 8),
            "height": round(best["anchor_height"] / observed_height, 8),
        },
        "game_window": {
            "coordinate_space": "NORMALIZED_INPUT_FRAME",
            "x": round(left / observed_width, 8),
            "y": round(top / observed_height, 8),
            "width": round((right - left) / observed_width, 8),
            "height": round((bottom - top) / observed_height, 8),
        },
        "input_size": {"width": observed_width, "height": observed_height},
        "registered_size": {"width": reference_width, "height": reference_height},
        "registered_pixel_sha256": hashlib.sha256(
            np.ascontiguousarray(registered)
        ).hexdigest(),
    }


__all__ = [
    "AnchorRegistrationError",
    "MAX_ANCHOR_SEARCH_PIXELS",
    "MAX_ANCHOR_TEMPLATE_PIXELS",
    "register_from_upper_right_anchor",
    "validate_anchor_spec",
]
