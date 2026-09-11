"""Theme-calibrated suit-colour evidence for Bridgit card crops."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

SUIT_FAMILY = {"H": "RED", "D": "RED", "C": "BLACK", "S": "BLACK"}


class SuitColorError(ValueError):
    """Suit-colour evidence is malformed."""


def _rgb(value: Sequence[float], field: str) -> tuple[float, float, float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 3
    ):
        raise SuitColorError(f"{field} must contain three RGB channels")
    try:
        channels = tuple(float(item) for item in value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SuitColorError(f"{field} contains a non-numeric channel") from exc
    if any(
        not math.isfinite(item) or item < 0.0 or item > 255.0
        for item in channels
    ):
        raise SuitColorError(f"{field} channels must be inside [0,255]")
    return channels  # type: ignore[return-value]


def classify_suit_color(
    samples: Sequence[Sequence[float]],
    *,
    red_reference: Sequence[float],
    black_reference: Sequence[float],
    background_reference: Sequence[float],
    minimum_foreground_distance: float = 20.0,
) -> dict[str, Any]:
    """Classify RED/BLACK relative to verified profile colours.

    Absolute RGB assumptions are intentionally avoided: every run supplies
    red, black and background reference colours taken from the verified Bridgit
    profile/theme. That keeps light/dark or recoloured themes explicit.
    """

    if (
        not isinstance(samples, Sequence)
        or isinstance(samples, (str, bytes))
        or not samples
    ):
        raise SuitColorError("samples must be a non-empty sequence")
    if (
        isinstance(minimum_foreground_distance, bool)
        or not isinstance(minimum_foreground_distance, (int, float))
        or not math.isfinite(float(minimum_foreground_distance))
        or float(minimum_foreground_distance) < 0.0
    ):
        raise SuitColorError("minimum_foreground_distance is invalid")

    red = _rgb(red_reference, "red_reference")
    black = _rgb(black_reference, "black_reference")
    background = _rgb(background_reference, "background_reference")

    foreground: list[tuple[float, float, float]] = []
    for index, sample in enumerate(samples):
        pixel = _rgb(sample, f"samples[{index}]")
        distance = math.sqrt(
            sum((pixel[channel] - background[channel]) ** 2 for channel in range(3))
        )
        if distance >= float(minimum_foreground_distance):
            foreground.append(pixel)

    if not foreground:
        return {
            "family": "UNKNOWN",
            "confidence": 0.0,
            "foreground_fraction": 0.0,
            "calibrated": True,
        }

    mean = tuple(
        sum(pixel[channel] for pixel in foreground) / len(foreground)
        for channel in range(3)
    )
    red_distance = math.sqrt(
        sum((mean[channel] - red[channel]) ** 2 for channel in range(3))
    )
    black_distance = math.sqrt(
        sum((mean[channel] - black[channel]) ** 2 for channel in range(3))
    )
    total = red_distance + black_distance
    if total <= 1e-9:
        family = "UNKNOWN"
        confidence = 0.0
    else:
        family = "RED" if red_distance < black_distance else "BLACK"
        confidence = min(0.995, abs(red_distance - black_distance) / total)

    return {
        "family": family,
        "confidence": round(confidence, 6),
        "foreground_fraction": round(len(foreground) / len(samples), 6),
        "calibrated": True,
        "mean_rgb": [round(value, 3) for value in mean],
    }


def expected_family(card_or_suit: str) -> str:
    text = str(card_or_suit or "").upper()
    suit = text[-1:] if len(text) > 1 else text
    if suit not in SUIT_FAMILY:
        raise SuitColorError("card_or_suit does not identify H/C/D/S")
    return SUIT_FAMILY[suit]
