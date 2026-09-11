"""Template-based observer for cards visibly played on a Bridgit table.

Rank templates come from the existing Oleg-reviewed visible-hand profile. Suit
templates are derived automatically from the same reference frames: the hand
observer has already established the fixed H/C/D/S run order, so no new labels
or screenshot review are needed. The observer reports direct pixel evidence
only; reconstruction lives in ``bridgit_autonomous_deals``.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from typing import Any

from bridge_vision.bridgit_visible_hand_observer import (
    RANKS,
    SUITS,
    ObserverProfile,
    VisibleHandObserverError,
    _run_color,
    _suit_indices,
    _white_runs,
)

PLAYED_OBSERVER_VERSION = "bridgit-played-card-observer-v1"


def _pixel_runtime():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover - runtime dependent
        raise RuntimeError("opencv-python-headless and numpy are required") from exc
    return cv2, np


def _binary(image: Any, threshold: int):
    cv2, np = _pixel_runtime()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return (gray < threshold).astype(np.uint8) * 255


def build_suit_bank(
    profile: ObserverProfile, reference_images: Mapping[str, Any]
) -> dict[str, tuple[Any, ...]]:
    """Derive H/C/D/S glyph samples from already reviewed hand references."""

    if set(reference_images) != set(profile.references):
        raise VisibleHandObserverError("reference images do not match profile")
    bank: dict[str, list[Any]] = {suit: [] for suit in SUITS}
    owners: dict[str, str] = {}
    for items in profile.rank_templates.values():
        for reference_id, x, y in items:
            image = reference_images[reference_id]
            if tuple(image.shape[:2]) != (profile.height, profile.width):
                raise VisibleHandObserverError(
                    "reference dimensions do not match profile"
                )
            seat = min(profile.rows, key=lambda key: abs(profile.rows[key][0] - y))
            row_y, x_min, x_max = profile.rows[seat]
            if abs(row_y - y) > 8:
                raise VisibleHandObserverError(
                    "rank template is not bound to a reviewed hand row"
                )
            runs = _white_runs(image, row_y, x_min, x_max, profile)
            colors = [_run_color(image, row_y, run, profile) for run in runs]
            suit_indices = _suit_indices(colors)
            matches = [
                index
                for index, (run_x, width) in enumerate(runs)
                if run_x - 5 <= x < run_x + width
            ]
            if suit_indices is None or len(matches) != 1:
                raise VisibleHandObserverError(
                    "rank template suit cannot be derived from reviewed geometry"
                )
            suit = SUITS[suit_indices[matches[0]]]
            x0 = max(0, x - 2)
            y0 = max(0, y + profile.rank_height)
            x1 = min(profile.width, x + profile.rank_width + 4)
            y1 = min(profile.height, y + profile.rank_height + 28)
            sample = _binary(image[y0:y1, x0:x1], profile.binary_threshold)
            digest = hashlib.sha256(sample.tobytes()).hexdigest()
            previous = owners.get(digest)
            if previous is not None and previous != suit:
                raise VisibleHandObserverError(
                    "one derived suit template has conflicting labels"
                )
            owners[digest] = suit
            if all(not (sample == existing).all() for existing in bank[suit]):
                bank[suit].append(sample)
    if any(not samples for samples in bank.values()):
        raise VisibleHandObserverError("derived suit bank does not cover H/C/D/S")
    return {suit: tuple(samples) for suit, samples in bank.items()}


def _white_card_rectangles(image: Any, profile: ObserverProfile):
    cv2, np = _pixel_runtime()
    top = min(profile.rows["N"][0] + 2 * profile.rank_height, profile.height - 1)
    bottom = max(profile.rows["S"][0] - profile.rank_height, top + 1)
    right = min(profile.width, round(profile.width * 0.75))
    table = image[top:bottom, :right]
    hsv = cv2.cvtColor(table, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(
        hsv,
        np.array([0, 0, 165], dtype=np.uint8),
        np.array([180, 90, 255], dtype=np.uint8),
    )
    count, _, stats, _ = cv2.connectedComponentsWithStats(white)
    minimum_width = max(48, profile.rank_width * 4)
    maximum_width = min(256, profile.rank_width * 8)
    minimum_height = max(72, profile.rank_height * 5)
    maximum_height = min(320, profile.rank_height * 9)
    rectangles = []
    for x, relative_y, width, height, area in stats[1:count]:
        y = int(relative_y) + top
        fill = float(area) / float(width * height)
        ratio = float(height) / float(width)
        if (
            minimum_width <= width <= maximum_width
            and minimum_height <= height <= maximum_height
            and 1.15 <= ratio <= 1.75
            and fill >= 0.55
        ):
            rectangles.append((int(x), y, int(width), int(height), fill))
    return rectangles


def _rank(
    image: Any,
    rectangle: tuple[int, int, int, int, float],
    rank_bank: Mapping[str, Sequence[Any]],
    profile: ObserverProfile,
):
    cv2, _ = _pixel_runtime()
    x, y, width, _, _ = rectangle
    search = _binary(
        image[
            y : y + profile.rank_height + 14,
            x : x + min(width, profile.rank_width + 18),
        ],
        profile.binary_threshold,
    )
    matches = []
    for rank in RANKS:
        samples = rank_bank.get(rank) or ()
        if not samples:
            raise VisibleHandObserverError("rank bank does not cover A through 2")
        score = -1.0
        position = (0, 0)
        for sample in samples:
            matrix = cv2.matchTemplate(search, sample, cv2.TM_CCOEFF_NORMED)
            _, current, _, location = cv2.minMaxLoc(matrix)
            if current > score:
                score = float(current)
                position = location
        matches.append((score, rank, position))
    matches.sort(reverse=True)
    best, second = matches[:2]
    margin = best[0] - second[0]
    if best[0] < profile.min_rank_score or margin < profile.min_rank_margin:
        return None
    return best[1], x + best[2][0], y + best[2][1], best[0], margin


def _suit_color(image: Any, x: int, y: int, profile: ObserverProfile) -> str:
    crop = image[
        y + profile.rank_height : y + profile.rank_height + 30,
        max(0, x - 2) : x + profile.rank_width + 6,
    ]
    blue = crop[:, :, 0]
    green = crop[:, :, 1]
    red = crop[:, :, 2]
    red_pixels = ((red > 130) & (red > green * 1.35) & (red > blue * 1.35)).sum()
    dark_pixels = ((red < 120) & (green < 120) & (blue < 120)).sum()
    return "R" if red_pixels > max(2, dark_pixels * profile.red_dark_ratio) else "B"


def _suit(
    image: Any,
    rank_x: int,
    rank_y: int,
    suit_bank: Mapping[str, Sequence[Any]],
    profile: ObserverProfile,
):
    cv2, _ = _pixel_runtime()
    x0 = max(0, rank_x - 4)
    y0 = max(0, rank_y + profile.rank_height - 4)
    search = _binary(
        image[
            y0 : rank_y + profile.rank_height + 34,
            x0 : rank_x + profile.rank_width + 10,
        ],
        profile.binary_threshold,
    )
    color = _suit_color(image, rank_x, rank_y, profile)
    allowed = "HD" if color == "R" else "CS"
    matches = []
    for suit in allowed:
        samples = suit_bank.get(suit) or ()
        if not samples:
            raise VisibleHandObserverError("suit bank does not cover H/C/D/S")
        score = max(
            float(cv2.matchTemplate(search, sample, cv2.TM_CCOEFF_NORMED).max())
            for sample in samples
        )
        matches.append((score, suit))
    matches.sort(reverse=True)
    margin = matches[0][0] - matches[1][0]
    if matches[0][0] < 0.82 or margin < 0.06:
        return None
    return matches[0][1], matches[0][0], margin


def _seat(rectangle: tuple[int, int, int, int, float], profile: ObserverProfile):
    x, y, width, height, _ = rectangle
    center_x = profile.width * 0.375
    center_y = (profile.rows["N"][0] + profile.rows["S"][0]) / 2
    delta_x = x + width / 2 - center_x
    delta_y = y + height / 2 - center_y
    if abs(delta_x) > abs(delta_y):
        seat = "E" if delta_x > 0 else "W"
    else:
        seat = "S" if delta_y > 0 else "N"
    dominance = abs(abs(delta_x) - abs(delta_y)) / max(
        1.0, math.hypot(delta_x, delta_y)
    )
    return seat, dominance


def observe_played_cards(
    image: Any,
    rank_bank: Mapping[str, Sequence[Any]],
    suit_bank: Mapping[str, Sequence[Any]],
    profile: ObserverProfile,
) -> dict[str, Any]:
    """Observe the current trick, assigning ownership by verified table geometry."""

    if tuple(image.shape[:2]) != (profile.height, profile.width):
        raise VisibleHandObserverError("frame dimensions do not match profile")
    candidates = []
    rejected = []
    for rectangle in _white_card_rectangles(image, profile):
        rank = _rank(image, rectangle, rank_bank, profile)
        if rank is None:
            rejected.append({"region": rectangle[:4], "reason": "RANK_AMBIGUOUS"})
            continue
        rank_value, rank_x, rank_y, rank_score, rank_margin = rank
        suit = _suit(image, rank_x, rank_y, suit_bank, profile)
        if suit is None:
            rejected.append({"region": rectangle[:4], "reason": "SUIT_AMBIGUOUS"})
            continue
        suit_value, suit_score, suit_margin = suit
        seat, geometry_margin = _seat(rectangle, profile)
        if geometry_margin < 0.12:
            rejected.append({"region": rectangle[:4], "reason": "SEAT_AMBIGUOUS"})
            continue
        x, y, width, height, fill = rectangle
        evidence = image[y : y + height, x : x + width]
        candidates.append(
            {
                "card": rank_value + suit_value,
                "seat": seat,
                "source": "PLAYED",
                "confidence": round(min(rank_score, suit_score, fill), 6),
                "rank_margin": round(rank_margin, 6),
                "suit_margin": round(suit_margin, 6),
                "geometry_margin": round(geometry_margin, 6),
                "evidence_pixel_sha256": hashlib.sha256(evidence.tobytes()).hexdigest(),
                "region": {"x": x, "y": y, "width": width, "height": height},
            }
        )

    by_seat: dict[str, list[dict[str, Any]]] = {}
    for item in candidates:
        by_seat.setdefault(item["seat"], []).append(item)
    conflicts = [seat for seat, cards in by_seat.items() if len(cards) > 1]
    if conflicts:
        return {
            "status": "CONFLICT",
            "cards": [],
            "rejected": rejected,
            "conflicts": [
                {"seat": seat, "reason": "MULTIPLE_CURRENT_TRICK_CARDS"}
                for seat in sorted(conflicts)
            ],
        }
    owners: dict[str, set[str]] = {}
    for item in candidates:
        owners.setdefault(item["card"], set()).add(item["seat"])
    repeated = [card for card, seats in owners.items() if len(seats) > 1]
    if repeated:
        return {
            "status": "CONFLICT",
            "cards": [],
            "rejected": rejected,
            "conflicts": [
                {"card": card, "reason": "CROSS_SEAT_CARD_CONFLICT"}
                for card in sorted(repeated)
            ],
        }
    return {
        "status": "SHADOW_PLAYED_CARDS" if candidates else "REVIEW",
        "cards": sorted(candidates, key=lambda item: item["seat"]),
        "rejected": rejected,
        "conflicts": [],
        "observer_version": PLAYED_OBSERVER_VERSION,
    }


__all__ = [
    "PLAYED_OBSERVER_VERSION",
    "build_suit_bank",
    "observe_played_cards",
]
