"""Fail-closed temporal visibility state for cards observed during play.

A disappearance is never treated as a play by itself.  PLAYED requires an
explicit, verified play event for a card already observed in that seat on the
same stable deal track.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from bridge_contracts.video_deal import SEATS, canonicalize_video_deal

VISIBILITY_VERSION = "bridge-card-temporal-visibility-v1"
VISIBLE = "VISIBLE"
VISIBLE_FN = "VISIBLE_FN"
PLAYED = "PLAYED_NO_LONGER_VISIBLE"
OCCLUDED = "OCCLUDED"
AMBIGUOUS = "AMBIGUOUS"
NOT_EXPECTED = "NOT_EXPECTED_VISIBLE"


class TemporalVisibilityError(ValueError):
    pass


def _pairs(hands: Mapping[str, Any]) -> set[tuple[str, str]]:
    canonical = canonicalize_video_deal({"hands": dict(hands)}).to_dict()["hands"]
    return {
        (seat, card)
        for seat in SEATS
        for card in canonical[seat]["cards"]
    }


def _pair(raw: Mapping[str, Any], field: str) -> tuple[str, str]:
    if not isinstance(raw, Mapping):
        raise TemporalVisibilityError(f"{field} must be an object")
    seat = str(raw.get("seat") or "").upper()
    if seat not in SEATS:
        raise TemporalVisibilityError(f"invalid {field} seat")
    try:
        canonical = canonicalize_video_deal({"hands": {seat: [raw.get("card")]}}).to_dict()
    except Exception as exc:
        raise TemporalVisibilityError(f"invalid {field} card") from exc
    return seat, canonical["hands"][seat]["cards"][0]


@dataclass
class _DealState:
    observed: set[tuple[str, str]] = field(default_factory=set)
    played: set[tuple[str, str]] = field(default_factory=set)
    frame_ids: set[str] = field(default_factory=set)


class TemporalCardVisibilityTracker:
    def __init__(self):
        self._deals: dict[str, _DealState] = {}

    def observe(
        self,
        *,
        deal_key: str,
        frame_id: str,
        visible_hands: Mapping[str, Any],
        expected_hands: Mapping[str, Any] | None = None,
        play_events: Sequence[Mapping[str, Any]] = (),
        occluded: Sequence[Mapping[str, Any]] = (),
        ambiguous: Sequence[Mapping[str, Any]] = (),
        not_expected_visible: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        key = str(deal_key or "").strip()
        identity = str(frame_id or "").strip()
        if not key or not identity:
            raise TemporalVisibilityError("stable deal_key and frame_id are required")
        state = self._deals.setdefault(key, _DealState())
        if identity in state.frame_ids:
            raise TemporalVisibilityError("duplicate frame evidence")
        state.frame_ids.add(identity)

        visible = _pairs(visible_hands)
        expected = _pairs(expected_hands or visible_hands)
        occluded_pairs = {_pair(item, "occluded") for item in occluded}
        ambiguous_pairs = {_pair(item, "ambiguous") for item in ambiguous}
        not_expected_pairs = {_pair(item, "not_expected_visible") for item in not_expected_visible}

        events: list[dict[str, Any]] = []
        for raw in play_events:
            pair = _pair(raw, "play event")
            if raw.get("verified") is not True:
                raise TemporalVisibilityError("play event must be explicitly verified")
            locator = str(raw.get("evidence_locator") or "").strip()
            if not locator:
                raise TemporalVisibilityError("play event evidence locator is required")
            if pair not in state.observed and pair not in visible:
                raise TemporalVisibilityError("played card was not previously observed in that seat")
            state.played.add(pair)
            events.append({"seat": pair[0], "card": pair[1], "evidence_locator": locator})

        state.observed |= visible
        universe = expected | visible | state.played | occluded_pairs | ambiguous_pairs | not_expected_pairs
        rows: list[dict[str, Any]] = []
        counts = {status: 0 for status in (VISIBLE, VISIBLE_FN, PLAYED, OCCLUDED, AMBIGUOUS, NOT_EXPECTED)}
        for seat, card in sorted(universe):
            pair = (seat, card)
            if pair in visible:
                status = VISIBLE
            elif pair in state.played:
                status = PLAYED
            elif pair in ambiguous_pairs:
                status = AMBIGUOUS
            elif pair in occluded_pairs:
                status = OCCLUDED
            elif pair in not_expected_pairs:
                status = NOT_EXPECTED
            elif pair in expected:
                status = VISIBLE_FN
            else:
                status = NOT_EXPECTED
            counts[status] += 1
            rows.append({"seat": seat, "card": card, "status": status})

        return {
            "version": VISIBILITY_VERSION,
            "deal_key": key,
            "frame_id": identity,
            "cards": rows,
            "counts": counts,
            "verified_play_events": events,
            "observed_union_count": len(state.observed),
            "played_count": len(state.played),
            "canonical_promotion_allowed": False,
        }


__all__ = [
    "AMBIGUOUS",
    "NOT_EXPECTED",
    "OCCLUDED",
    "PLAYED",
    "TemporalCardVisibilityTracker",
    "TemporalVisibilityError",
    "VISIBLE",
    "VISIBLE_FN",
    "VISIBILITY_VERSION",
]
