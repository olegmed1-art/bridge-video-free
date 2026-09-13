"""Retry-first recovery for unresolved cards using original Gambler assets.

This shadow-only successor runs only after primary direct recognition leaves
unknown cards. It rechecks only unresolved identities against source-bound
Gambler classic artwork on independent registered frames, then optionally uses
an exact deck complement *only* when one seat is the sole incomplete hand.

It never uses cursor position, never promotes Canon, and never guesses a split
between two or more incomplete hands.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from bridge_vision.gambler_classic_reference import (
    GamblerClassicReferenceError,
    GamblerClassicSprite,
    decode_card_cells,
)
from bridge_vision.bridgit_visibility_events import SEAT_REGIONS

RANKS = tuple("AKQJT98765432")
SUITS = tuple("SHDC")
SEATS = tuple("NESW")
FULL_DECK = frozenset(rank + suit for suit in SUITS for rank in RANKS)
RECOVERY_VERSION = "bridgit-unresolved-gambler-recovery-v1"
HIGH_CONFIDENCE = 0.95
BORDERLINE_CONFIDENCE = 0.92
BORDERLINE_MIN_INDEPENDENT_FRAMES = 2
MAX_RETRY_FRAMES = 32

_PRIMARY_CROPS = (
    (14 / 109, 40 / 147),
    (20 / 109, 30 / 147),
    (25 / 109, 35 / 147),
)
_OCCLUSION_CROPS = (
    (12 / 109, 24 / 147),
    (14 / 109, 26 / 147),
)

_ALLOWED_SOURCES = frozenset(
    {
        "GAMBLER_PRIMARY_RETRY",
        "GAMBLER_OCCLUSION_RETRY",
        "PLAYED_CURRENT_TRICK",
        "TEACHER_SPEECH_CORROBORATED",
    }
)


class UnresolvedRecoveryError(ValueError):
    """Recovery input cannot be accepted without weakening evidence rules."""


@dataclass(frozen=True)
class RetryCandidate:
    card: str
    seat: str
    confidence: float
    frame_sha256: str
    decoded_pixel_sha256: str
    source: str
    x: int | None = None
    y: int | None = None


def _card(value: Any) -> str:
    text = str(value or "").upper().replace("10", "T")
    if len(text) != 2 or text[0] not in RANKS or text[1] not in SUITS:
        raise UnresolvedRecoveryError("invalid card")
    return text


def _seat(value: Any) -> str:
    text = str(value or "").upper()
    if text not in SEATS:
        raise UnresolvedRecoveryError("invalid seat")
    return text


def _sha(value: Any, field: str) -> str:
    text = str(value or "").lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise UnresolvedRecoveryError(f"invalid {field}")
    return text


def _confidence(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise UnresolvedRecoveryError("confidence must be numeric") from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise UnresolvedRecoveryError("confidence outside [0,1]")
    return result


def _normalized_primary(primary_by_seat: Mapping[str, Sequence[str]]) -> dict[str, set[str]]:
    if not isinstance(primary_by_seat, Mapping):
        raise UnresolvedRecoveryError("primary_by_seat must be an object")
    if set(primary_by_seat) != set(SEATS):
        raise UnresolvedRecoveryError("primary_by_seat must cover N,E,S,W")
    result: dict[str, set[str]] = {}
    owners: dict[str, str] = {}
    for seat in SEATS:
        cards = {_card(card) for card in primary_by_seat[seat]}
        if len(cards) != len(primary_by_seat[seat]) or len(cards) > 13:
            raise UnresolvedRecoveryError("primary hand is invalid")
        for card in cards:
            previous = owners.get(card)
            if previous is not None and previous != seat:
                raise UnresolvedRecoveryError("primary card has multiple owners")
            owners[card] = seat
        result[seat] = cards
    return result


def unresolved_cards(primary_by_seat: Mapping[str, Sequence[str]]) -> list[str]:
    primary = _normalized_primary(primary_by_seat)
    observed = {card for cards in primary.values() for card in cards}
    return sorted(FULL_DECK - observed, key=lambda c: (SUITS.index(c[1]), RANKS.index(c[0])))


def bind_location_to_seat(
    x: int,
    y: int,
    *,
    frame_width: int,
    frame_height: int,
    game_window: Mapping[str, float] | None = None,
) -> str | None:
    """Bind a detected card corner to N/E/S/W using normalized table geometry."""
    if min(frame_width, frame_height) <= 0:
        raise UnresolvedRecoveryError("invalid frame size")
    if game_window is None:
        gx = gy = 0.0
        gw, gh = float(frame_width), float(frame_height)
    else:
        try:
            gx = float(game_window.get("x", 0.0))
            gy = float(game_window.get("y", 0.0))
            gw = float(game_window.get("width", frame_width))
            gh = float(game_window.get("height", frame_height))
        except (TypeError, ValueError, OverflowError) as exc:
            raise UnresolvedRecoveryError("invalid game_window") from exc
        if 0.0 <= gx <= 1.0 and 0.0 <= gy <= 1.0 and 0.0 < gw <= 1.0 and 0.0 < gh <= 1.0:
            gx, gy, gw, gh = gx * frame_width, gy * frame_height, gw * frame_width, gh * frame_height
    if gw <= 0 or gh <= 0:
        raise UnresolvedRecoveryError("invalid game_window")
    xn, yn = (x - gx) / gw, (y - gy) / gh
    matches = []
    for seat, (rx, ry, rw, rh) in SEAT_REGIONS.items():
        if rx <= xn <= rx + rw and ry <= yn <= ry + rh:
            cx, cy = rx + rw / 2, ry + rh / 2
            matches.append(((xn - cx) ** 2 + (yn - cy) ** 2, seat))
    return min(matches)[1] if matches else None


def _flatten_bgr(cell: Any) -> Any:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("opencv-python-headless and numpy are required") from exc
    if cell.ndim == 3 and cell.shape[2] == 4:
        alpha = cell[:, :, 3:4].astype(np.float32) / 255.0
        return (cell[:, :, :3].astype(np.float32) * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8)
    if cell.ndim == 3 and cell.shape[2] == 3:
        return cell
    if cell.ndim == 2:
        return cv2.cvtColor(cell, cv2.COLOR_GRAY2BGR)
    raise UnresolvedRecoveryError("unsupported card raster")


def _scaled_crop(sprite: GamblerClassicSprite, fractions: tuple[float, float]) -> tuple[int, int]:
    return (
        max(4, min(sprite.card_width, round(sprite.card_width * fractions[0]))),
        max(4, min(sprite.card_height, round(sprite.card_height * fractions[1]))),
    )


def scan_unresolved_in_registered_frame(
    frame: Any,
    sprite: GamblerClassicSprite,
    cards: Sequence[str],
    *,
    frame_sha256: str,
    decoded_pixel_sha256: str,
    game_window: Mapping[str, float] | None = None,
    high_confidence: float = HIGH_CONFIDENCE,
    borderline_confidence: float = BORDERLINE_CONFIDENCE,
) -> list[dict[str, Any]]:
    """Search only unresolved card identities in one already registered frame.

    Pass 1 uses three native corner crops. Pass 2 is an occlusion-aware
    smaller-corner retry, but remains only a candidate until recovery
    aggregation sees independent-frame support.
    """
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("opencv-python-headless is required") from exc
    if frame is None or not hasattr(frame, "shape") or len(frame.shape) < 2:
        raise UnresolvedRecoveryError("frame is not a raster")
    height, width = int(frame.shape[0]), int(frame.shape[1])
    fsha = _sha(frame_sha256, "frame_sha256")
    psha = _sha(decoded_pixel_sha256, "decoded_pixel_sha256")
    cells = decode_card_cells(sprite)
    requested = [_card(card) for card in cards]
    results = []

    for card in requested:
        source = _flatten_bgr(cells[card])
        primary_scores = []
        best_location = None
        for fractions in _PRIMARY_CROPS:
            cw, ch = _scaled_crop(sprite, fractions)
            template = source[:ch, :cw]
            response = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(response)
            primary_scores.append(float(score))
            if best_location is None:
                best_location = location
        primary_confidence = min(primary_scores)
        accepted_source = "GAMBLER_PRIMARY_RETRY"
        confidence = primary_confidence

        if primary_confidence < high_confidence:
            small_scores = []
            small_location = None
            for fractions in _OCCLUSION_CROPS:
                cw, ch = _scaled_crop(sprite, fractions)
                template = source[:ch, :cw]
                response = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
                _, score, _, location = cv2.minMaxLoc(response)
                small_scores.append(float(score))
                if small_location is None:
                    small_location = location
            small_confidence = min(small_scores)
            if small_confidence > confidence:
                confidence = small_confidence
                best_location = small_location
                accepted_source = "GAMBLER_OCCLUSION_RETRY"

        if confidence < borderline_confidence or best_location is None:
            continue
        seat = bind_location_to_seat(
            int(best_location[0]),
            int(best_location[1]),
            frame_width=width,
            frame_height=height,
            game_window=game_window,
        )
        if seat is None:
            continue
        results.append(
            {
                "card": card,
                "seat": seat,
                "confidence": round(confidence, 6),
                "frame_sha256": fsha,
                "decoded_pixel_sha256": psha,
                "source": accepted_source,
                "x": int(best_location[0]),
                "y": int(best_location[1]),
                "mouse_cursor_used": False,
            }
        )
    return results


def _normalize_retry_candidate(raw: Mapping[str, Any]) -> RetryCandidate:
    if not isinstance(raw, Mapping):
        raise UnresolvedRecoveryError("retry candidate must be an object")
    source = str(raw.get("source") or "").upper()
    if source not in _ALLOWED_SOURCES:
        raise UnresolvedRecoveryError("unsupported retry candidate source")
    return RetryCandidate(
        card=_card(raw.get("card")),
        seat=_seat(raw.get("seat")),
        confidence=_confidence(raw.get("confidence")),
        frame_sha256=_sha(raw.get("frame_sha256"), "frame_sha256"),
        decoded_pixel_sha256=_sha(raw.get("decoded_pixel_sha256"), "decoded_pixel_sha256"),
        source=source,
        x=int(raw["x"]) if raw.get("x") is not None else None,
        y=int(raw["y"]) if raw.get("y") is not None else None,
    )


def recover_unresolved_deal(
    primary_by_seat: Mapping[str, Sequence[str]],
    retry_candidates: Sequence[Mapping[str, Any]],
    *,
    allow_exact_complement: bool = True,
    high_confidence: float = HIGH_CONFIDENCE,
    borderline_confidence: float = BORDERLINE_CONFIDENCE,
    borderline_min_independent_frames: int = BORDERLINE_MIN_INDEPENDENT_FRAMES,
) -> dict[str, Any]:
    """Retry unknown cards first, then complete only mathematically unique residue."""
    if not isinstance(allow_exact_complement, bool):
        raise UnresolvedRecoveryError("allow_exact_complement must be boolean")
    if not (0.0 <= borderline_confidence <= high_confidence <= 1.0):
        raise UnresolvedRecoveryError("invalid retry thresholds")
    if borderline_min_independent_frames < 2:
        raise UnresolvedRecoveryError("borderline retry requires independent frames")

    hands = _normalized_primary(primary_by_seat)
    observed_owner = {card: seat for seat, cards in hands.items() for card in cards}
    initial_unresolved = FULL_DECK - set(observed_owner)
    if not initial_unresolved:
        return {
            "version": RECOVERY_VERSION,
            "status": "COMPLETE_OBSERVED",
            "hands": {seat: sorted(hands[seat]) for seat in SEATS},
            "initial_unresolved_count": 0,
            "recovered_direct": [],
            "derived_exact_complement": [],
            "remaining_unresolved": [],
            "retry_performed": False,
            "deck_complement_used": False,
            "mouse_cursor_used": False,
            "canonical_promotion_allowed": False,
        }

    normalized = [
        _normalize_retry_candidate(item)
        for item in retry_candidates[:MAX_RETRY_FRAMES * len(FULL_DECK)]
        if _card(item.get("card")) in initial_unresolved
    ]
    grouped: dict[tuple[str, str], dict[str, RetryCandidate]] = defaultdict(dict)
    raw_card_seats: dict[str, set[str]] = defaultdict(set)
    for item in normalized:
        if item.card not in initial_unresolved:
            continue
        raw_card_seats[item.card].add(item.seat)
        previous = grouped[(item.card, item.seat)].get(item.decoded_pixel_sha256)
        if previous is None or item.confidence > previous.confidence:
            grouped[(item.card, item.seat)][item.decoded_pixel_sha256] = item

    accepted_retry = []
    ambiguous_cards = {card for card, seats in raw_card_seats.items() if len(seats) > 1}
    for (card, seat), by_pixel in sorted(grouped.items()):
        if card in ambiguous_cards:
            continue
        votes = list(by_pixel.values())
        if card in observed_owner or len(hands[seat]) >= 13:
            continue
        high = [item for item in votes if item.confidence >= high_confidence]
        borderline = [item for item in votes if item.confidence >= borderline_confidence]
        if not (high or len(borderline) >= borderline_min_independent_frames):
            continue
        chosen = max(high or borderline, key=lambda item: item.confidence)
        hands[seat].add(card)
        observed_owner[card] = seat
        accepted_retry.append(
            {
                "card": card,
                "seat": seat,
                "confidence": chosen.confidence,
                "source": chosen.source,
                "independent_pixel_support": len(borderline),
                "evidence_frame_sha256s": sorted({item.frame_sha256 for item in borderline}),
                "evidence_pixel_sha256s": sorted({item.decoded_pixel_sha256 for item in borderline}),
            }
        )

    if any(len(hands[seat]) > 13 for seat in SEATS):
        raise UnresolvedRecoveryError("retry exceeded hand capacity")
    if sum(len(hands[seat]) for seat in SEATS) != len(
        {card for cards in hands.values() for card in cards}
    ):
        raise UnresolvedRecoveryError("retry created duplicate ownership")

    derived = []
    deck_complement_used = False
    remaining = FULL_DECK - {card for cards in hands.values() for card in cards}
    incomplete = [seat for seat in SEATS if len(hands[seat]) < 13]
    if remaining and allow_exact_complement and len(incomplete) == 1:
        seat = incomplete[0]
        capacity = 13 - len(hands[seat])
        if capacity == len(remaining) and all(len(hands[other]) == 13 for other in SEATS if other != seat):
            for card in sorted(remaining):
                hands[seat].add(card)
                derived.append(
                    {
                        "card": card,
                        "seat": seat,
                        "source": "EXACT_DECK_COMPLEMENT_AFTER_RETRY",
                        "confidence_kind": "MATHEMATICALLY_UNIQUE_NOT_VISUAL",
                    }
                )
            deck_complement_used = True
            remaining = set()

    status = (
        "COMPLETE_OBSERVED"
        if not remaining and not derived
        else "COMPLETE_DERIVED_EXACT"
        if not remaining and derived
        else "PARTIAL_AFTER_RETRY"
    )
    return {
        "version": RECOVERY_VERSION,
        "status": status,
        "hands": {seat: sorted(hands[seat]) for seat in SEATS},
        "initial_unresolved_count": len(initial_unresolved),
        "recovered_direct": accepted_retry,
        "derived_exact_complement": derived,
        "remaining_unresolved": sorted(remaining),
        "ambiguous_retry_cards": sorted(ambiguous_cards),
        "retry_performed": True,
        "deck_complement_used": deck_complement_used,
        "completion_policy": "RETRY_UNRESOLVED_FIRST_THEN_EXACT_COMPLEMENT_ONLY_IF_UNIQUE",
        "mouse_cursor_used": False,
        "canonical_promotion_allowed": False,
    }


__all__ = [
    "BORDERLINE_CONFIDENCE",
    "BORDERLINE_MIN_INDEPENDENT_FRAMES",
    "HIGH_CONFIDENCE",
    "RECOVERY_VERSION",
    "RetryCandidate",
    "UnresolvedRecoveryError",
    "bind_location_to_seat",
    "recover_unresolved_deal",
    "scan_unresolved_in_registered_frame",
    "unresolved_cards",
]
