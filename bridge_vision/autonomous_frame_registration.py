"""Cheap fail-closed reuse of an acquired Bridgit interface-anchor lock."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Any

from bridge_vision.anchor_registration import (
    AnchorRegistrationError,
    validate_anchor_spec,
)


def _runtime():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover - runtime dependent
        raise RuntimeError("anchor lock requires OpenCV and NumPy") from exc
    return cv2, np


def _number(value: Any, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise AnchorRegistrationError(f"invalid {field}")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AnchorRegistrationError(f"invalid {field}") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise AnchorRegistrationError(f"invalid {field}")
    return result


def _rectangle(
    raw: Any,
    *,
    frame_width: int,
    frame_height: int,
    field: str,
) -> tuple[int, int, int, int]:
    if not isinstance(raw, Mapping) or raw.get("coordinate_space") != (
        "NORMALIZED_INPUT_FRAME"
    ):
        raise AnchorRegistrationError(f"invalid locked {field}")
    values = {
        name: _number(raw.get(name), f"locked {field}.{name}", 0.0, 1.0)
        for name in ("x", "y", "width", "height")
    }
    if values["width"] <= 0 or values["height"] <= 0:
        raise AnchorRegistrationError(f"invalid locked {field}")
    left = round(values["x"] * frame_width)
    top = round(values["y"] * frame_height)
    right = round((values["x"] + values["width"]) * frame_width)
    bottom = round((values["y"] + values["height"]) * frame_height)
    if (
        left < 0
        or top < 0
        or right <= left
        or bottom <= top
        or right > frame_width
        or bottom > frame_height
    ):
        raise AnchorRegistrationError(f"locked {field} leaves observed frame")
    return left, top, right - left, bottom - top


def apply_registered_game_window(
    reference: Any,
    observed: Any,
    spec: Mapping[str, Any],
    registration: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    """Reuse a static transform only after rechecking its anchor in this frame."""

    cv2, np = _runtime()
    checked = validate_anchor_spec(spec)
    if (
        not hasattr(reference, "shape")
        or not hasattr(observed, "shape")
        or not isinstance(registration, Mapping)
    ):
        raise AnchorRegistrationError("invalid locked registration inputs")
    reference_height, reference_width = reference.shape[:2]
    observed_height, observed_width = observed.shape[:2]
    if registration.get("input_size") != {
        "width": observed_width,
        "height": observed_height,
    }:
        raise AnchorRegistrationError("locked registration input size changed")
    scale = _number(registration.get("scale"), "locked scale", 0.25, 4.0)
    if not any(math.isclose(scale, item, abs_tol=1e-6) for item in checked["scales"]):
        raise AnchorRegistrationError("locked registration scale is not allowed")

    anchor_left, anchor_top, anchor_width, anchor_height = _rectangle(
        registration.get("anchor_region"),
        frame_width=observed_width,
        frame_height=observed_height,
        field="anchor region",
    )
    window_left, window_top, window_width, window_height = _rectangle(
        registration.get("game_window"),
        frame_width=observed_width,
        frame_height=observed_height,
        field="game window",
    )
    region = checked["reference_region"]
    reference_anchor = (
        round(region["x"] * reference_width),
        round(region["y"] * reference_height),
        round((region["x"] + region["width"]) * reference_width),
        round((region["y"] + region["height"]) * reference_height),
    )
    ref_left, ref_top, ref_right, ref_bottom = reference_anchor
    expected = (
        round(ref_right * scale) - round(ref_left * scale),
        round(ref_bottom * scale) - round(ref_top * scale),
        round(reference_width * scale),
        round(reference_height * scale),
        round(ref_left * scale),
        round(ref_top * scale),
    )
    actual = (
        anchor_width,
        anchor_height,
        window_width,
        window_height,
        anchor_left - window_left,
        anchor_top - window_top,
    )
    if any(abs(value - wanted) > 1 for value, wanted in zip(actual, expected)):
        raise AnchorRegistrationError("locked registration geometry changed")

    template = cv2.cvtColor(
        reference[ref_top:ref_bottom, ref_left:ref_right], cv2.COLOR_BGR2GRAY
    )
    scaled = cv2.resize(
        template,
        (expected[0], expected[1]),
        interpolation=cv2.INTER_NEAREST,
    )
    observed_anchor = cv2.cvtColor(
        observed[
            anchor_top : anchor_top + anchor_height,
            anchor_left : anchor_left + anchor_width,
        ],
        cv2.COLOR_BGR2GRAY,
    )
    if scaled.shape != observed_anchor.shape or float(scaled.std()) < 8.0:
        raise AnchorRegistrationError("locked interface anchor is invalid")
    score = abs(
        float(cv2.matchTemplate(observed_anchor, scaled, cv2.TM_CCOEFF_NORMED).item())
    )
    if not math.isfinite(score) or score < checked["minimum_score"]:
        raise AnchorRegistrationError("locked interface anchor score is too low")

    crop = observed[
        window_top : window_top + window_height,
        window_left : window_left + window_width,
    ]
    registered = cv2.resize(
        crop, (reference_width, reference_height), interpolation=cv2.INTER_AREA
    )
    if registered.shape[:2] != (reference_height, reference_width):
        raise AnchorRegistrationError("locked game window has invalid dimensions")
    return registered, {
        **dict(registration),
        "mode": "UPPER_RIGHT_ANCHOR_LOCKED",
        "score": round(score, 6),
        "registered_pixel_sha256": hashlib.sha256(
            np.ascontiguousarray(registered)
        ).hexdigest(),
    }


__all__ = ["apply_registered_game_window"]
