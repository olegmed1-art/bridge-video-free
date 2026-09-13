"""Pure shadow contracts for four-seat visibility and event-driven recognition.

This module is intentionally lightweight.  It does not decode video and it does
not recognize cards.  It normalizes per-seat visibility for N/E/S/W after the
existing interface-registration layer, and decides when an expensive observer
is worth running.

CLOSED and NOT_OBSERVED are normal states.  VISIBLE_AMBIGUOUS is the only
visibility state here that represents a recognition failure.  No hidden hand
is ever completed from the deck complement.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

SEATS = ("N", "E", "S", "W")
NORMAL_STATES = frozenset({"CLOSED", "NOT_OBSERVED", "VISIBLE_RECOGNIZED", "VISIBLE_PARTIAL"})
ERROR_STATE = "VISIBLE_AMBIGUOUS"
ALL_STATES = NORMAL_STATES | {ERROR_STATE}

# Normalized regions inside the already registered Bridgit game window.
# These are geometry hints only; no theme colour is encoded here.
SEAT_REGIONS = {
    "N": (0.16, 0.00, 0.68, 0.29),
    "E": (0.72, 0.14, 0.28, 0.72),
    "S": (0.16, 0.71, 0.68, 0.29),
    "W": (0.00, 0.14, 0.28, 0.72),
}
SEAT_ORIENTATION = {"N": "HORIZONTAL", "S": "HORIZONTAL", "E": "VERTICAL", "W": "VERTICAL"}


class VisibilityEventError(ValueError):
    """Visibility evidence is malformed or cannot safely trigger recognition."""


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def registered_seat_region(
    seat: str,
    *,
    game_window_x: int,
    game_window_y: int,
    game_window_width: int,
    game_window_height: int,
) -> dict[str, Any]:
    """Map a normalized N/E/S/W seat region to the registered input frame.

    Because the calculation is normalized to the registered game window, the
    contract is independent of source resolution, scale and absolute window
    position.  Theme identity is deliberately not consulted here.
    """
    seat = str(seat).upper()
    if seat not in SEAT_REGIONS:
        raise VisibilityEventError("unsupported seat")
    if min(game_window_x, game_window_y) < 0 or game_window_width <= 0 or game_window_height <= 0:
        raise VisibilityEventError("invalid registered game window")
    rx, ry, rw, rh = SEAT_REGIONS[seat]
    x = game_window_x + round(rx * game_window_width)
    y = game_window_y + round(ry * game_window_height)
    width = max(1, round(rw * game_window_width))
    height = max(1, round(rh * game_window_height))
    return {
        "seat": seat,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "orientation": SEAT_ORIENTATION[seat],
    }


def normalize_seat_state(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return a fail-closed seat state without treating hidden hands as errors."""
    if not isinstance(raw, Mapping):
        raise VisibilityEventError("seat observation must be an object")
    seat = str(raw.get("seat") or "").upper()
    if seat not in SEATS:
        raise VisibilityEventError("seat observation requires N/E/S/W")
    region_observed = raw.get("region_observed") is True
    closed = raw.get("closed_hand_visible") is True
    face_up = raw.get("face_up_cards_visible") is True
    ambiguous = raw.get("recognition_ambiguous") is True
    cards = raw.get("recognized_cards") or []
    if not isinstance(cards, Sequence) or isinstance(cards, (str, bytes)) or len(cards) > 13:
        raise VisibilityEventError("recognized_cards must be an array of at most 13")
    cards = tuple(str(card).upper() for card in cards)
    if len(cards) != len(set(cards)):
        raise VisibilityEventError("recognized_cards contains duplicates")

    if not region_observed:
        state = "NOT_OBSERVED"
    elif closed and not face_up:
        state = "CLOSED"
    elif ambiguous:
        state = ERROR_STATE
    elif face_up and len(cards) == 13:
        state = "VISIBLE_RECOGNIZED"
    elif face_up:
        state = "VISIBLE_PARTIAL"
    else:
        state = "NOT_OBSERVED"
    return {
        "seat": seat,
        "state": state,
        "recognized_cards": list(cards),
        "unknown_count": 13 - len(cards),
        "is_error": state == ERROR_STATE,
        "hidden_hand_inference_used": False,
        "deck_complement_used": False,
    }


def normalize_four_seat_state(observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(observations) != 4:
        raise VisibilityEventError("exactly four seat observations are required")
    normalized = [normalize_seat_state(item) for item in observations]
    by_seat = {item["seat"]: item for item in normalized}
    if set(by_seat) != set(SEATS):
        raise VisibilityEventError("seat observations must cover N/E/S/W exactly once")
    result = {seat: by_seat[seat] for seat in SEATS}
    return {
        "seats": result,
        "visible_ambiguity_count": sum(result[s]["state"] == ERROR_STATE for s in SEATS),
        "hidden_hand_inference_used": False,
        "deck_complement_used": False,
        "state_sha256": _canonical_hash(result),
    }


@dataclass
class EventDrivenRecognitionGate:
    """Require a stable changed state before an expensive recognition run.

    A candidate change must be observed in ``required_independent_frames``
    distinct encoded and decoded-pixel frames.  Identical/re-encoded pixels do
    not add support.  Once a stable state is accepted, unchanged frames never
    request another heavy run.  There is intentionally no timer/poll cadence.
    """

    required_independent_frames: int = 2
    accepted_state_sha256: str | None = None
    candidate_state_sha256: str | None = None
    _frame_sha256s: set[str] | None = None
    _pixel_sha256s: set[str] | None = None

    def __post_init__(self) -> None:
        if self.required_independent_frames < 2:
            raise VisibilityEventError("at least two independent frames are required")
        self._frame_sha256s = set()
        self._pixel_sha256s = set()

    def observe(self, *, state_sha256: str, frame_sha256: str, decoded_pixel_sha256: str) -> dict[str, Any]:
        for value, name in ((state_sha256, "state_sha256"), (frame_sha256, "frame_sha256"), (decoded_pixel_sha256, "decoded_pixel_sha256")):
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise VisibilityEventError(f"invalid {name}")
        if state_sha256 == self.accepted_state_sha256:
            return {"action": "NO_CHANGE", "run_heavy_observer": False, "support": 0}
        if state_sha256 != self.candidate_state_sha256:
            self.candidate_state_sha256 = state_sha256
            self._frame_sha256s = set()
            self._pixel_sha256s = set()
        assert self._frame_sha256s is not None and self._pixel_sha256s is not None
        self._frame_sha256s.add(frame_sha256)
        self._pixel_sha256s.add(decoded_pixel_sha256)
        support = min(len(self._frame_sha256s), len(self._pixel_sha256s))
        if support < self.required_independent_frames:
            return {"action": "WAIT_FOR_STABILITY", "run_heavy_observer": False, "support": support}
        self.accepted_state_sha256 = state_sha256
        self.candidate_state_sha256 = None
        self._frame_sha256s = set()
        self._pixel_sha256s = set()
        return {"action": "STABLE_STATE_CHANGE", "run_heavy_observer": True, "support": support}


__all__ = [
    "ALL_STATES",
    "ERROR_STATE",
    "EventDrivenRecognitionGate",
    "NORMAL_STATES",
    "SEATS",
    "SEAT_ORIENTATION",
    "SEAT_REGIONS",
    "VisibilityEventError",
    "normalize_four_seat_state",
    "normalize_seat_state",
    "registered_seat_region",
]
