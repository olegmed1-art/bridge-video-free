"""Bounded, fail-closed BEN auction rollout over complete deal worlds.

This module deliberately stops at final contracts.  It does not attach scores or
call the double-dummy engine: DDS3 evaluation is a separate downstream stage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .ai_worlds import parse_hand_pbn


SEATS = ("N", "E", "S", "W")
STRAINS = ("C", "D", "H", "S", "N")
_BID = re.compile(r"^([1-7])(C|D|H|S|N|NT)$")


class AuctionRolloutError(ValueError):
    pass


def normalize_call(raw: Any) -> str:
    value = str(raw or "").strip().upper().replace(" ", "")
    aliases = {
        "P": "PASS", "--": "PASS", "-": "PASS",
        "DBL": "X", "DOUBLE": "X", "DB": "X",
        "XX": "XX", "RDBL": "XX", "REDOUBLE": "XX", "RD": "XX",
    }
    value = aliases.get(value, value)
    match = _BID.fullmatch(value)
    if match:
        return match.group(1) + ("N" if match.group(2) == "NT" else match.group(2))
    if value in {"PASS", "X", "XX"}:
        return value
    raise AuctionRolloutError(f"invalid call: {raw!r}")


def _side(seat: str) -> int:
    return SEATS.index(seat) % 2


def _bid_rank(call: str) -> int:
    return (int(call[0]) - 1) * 5 + STRAINS.index(call[1])


@dataclass(frozen=True)
class AuctionState:
    dealer: str
    calls: tuple[str, ...]
    complete: bool
    next_seat: str | None
    contract: str | None
    declarer: str | None


def analyze_auction(dealer: str, calls: list[Any] | tuple[Any, ...]) -> AuctionState:
    dealer = str(dealer or "").upper()
    if dealer not in SEATS:
        raise AuctionRolloutError("dealer is invalid")
    normalized: list[str] = []
    last_bid: str | None = None
    last_bid_seat: str | None = None
    doubling = ""
    first_strain_bidder: dict[tuple[int, str], str] = {}
    complete = False
    for index, raw in enumerate(calls):
        if complete:
            raise AuctionRolloutError("auction contains calls after completion")
        call = normalize_call(raw)
        seat = SEATS[(SEATS.index(dealer) + index) % 4]
        if call[0:1].isdigit():
            if last_bid is not None and _bid_rank(call) <= _bid_rank(last_bid):
                raise AuctionRolloutError("bid does not outrank current contract")
            last_bid, last_bid_seat, doubling = call, seat, ""
            first_strain_bidder.setdefault((_side(seat), call[1]), seat)
        elif call == "X":
            if last_bid is None or doubling or _side(seat) == _side(last_bid_seat or seat):
                raise AuctionRolloutError("illegal double")
            doubling = "X"
        elif call == "XX":
            if last_bid is None or doubling != "X" or _side(seat) != _side(last_bid_seat or seat):
                raise AuctionRolloutError("illegal redouble")
            doubling = "XX"
        normalized.append(call)
        trailing_passes = 0
        for previous in reversed(normalized):
            if previous != "PASS":
                break
            trailing_passes += 1
        complete = (last_bid is None and trailing_passes == 4) or (
            last_bid is not None and trailing_passes == 3
        )

    declarer = None
    contract = None
    if complete and last_bid is not None and last_bid_seat is not None:
        declarer = first_strain_bidder[(_side(last_bid_seat), last_bid[1])]
        contract = last_bid + doubling
    next_seat = None if complete else SEATS[(SEATS.index(dealer) + len(normalized)) % 4]
    return AuctionState(dealer, tuple(normalized), complete, next_seat, contract, declarer)


BenBidder = Callable[[str, str, tuple[str, ...]], dict[str, Any]]


def _validated_ben_call(result: Any) -> str:
    if not isinstance(result, dict):
        raise AuctionRolloutError("BEN returned a non-object response")
    selected = normalize_call(result.get("bid") or result.get("call"))
    candidates = result.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise AuctionRolloutError("BEN returned no candidates")
    actions = {
        normalize_call(item.get("call") or item.get("bid") or item.get("action"))
        for item in candidates if isinstance(item, dict)
    }
    if selected not in actions:
        raise AuctionRolloutError("BEN selected call is absent from candidates")
    return selected


def rollout_worlds(
    *,
    worlds: list[dict[str, Any]],
    dealer: str,
    auction: list[Any] | tuple[Any, ...],
    decision_seat: str,
    candidate_call: Any,
    ben_bidder: BenBidder,
    max_worlds: int = 200,
    max_calls_per_world: int = 40,
) -> dict[str, Any]:
    """Complete one candidate auction in every world using the supplied BEN adapter."""
    if not isinstance(worlds, list) or not worlds or len(worlds) > max_worlds:
        raise AuctionRolloutError("world count is outside the bounded rollout limit")
    if not 1 <= max_calls_per_world <= 64:
        raise AuctionRolloutError("max_calls_per_world is invalid")
    initial = analyze_auction(dealer, auction)
    seat = str(decision_seat or "").upper()
    if initial.complete or initial.next_seat != seat:
        raise AuctionRolloutError("decision seat is not next to call")
    candidate = normalize_call(candidate_call)
    seeded = analyze_auction(dealer, [*initial.calls, candidate])

    results: list[dict[str, Any]] = []
    for index, world in enumerate(worlds):
        if not isinstance(world, dict) or not isinstance(world.get("hands"), dict):
            raise AuctionRolloutError("world is missing complete hands")
        hands = world["hands"]
        if set(hands) != set(SEATS) or any(not isinstance(hands[s], str) for s in SEATS):
            raise AuctionRolloutError("world hands are invalid")
        try:
            parsed_hands = {candidate: parse_hand_pbn(hands[candidate]) for candidate in SEATS}
        except ValueError as exc:
            raise AuctionRolloutError("world hands are invalid") from exc
        all_cards = [card for cards in parsed_hands.values() for card in cards]
        if len(set(all_cards)) != 52:
            raise AuctionRolloutError("world does not contain one complete unique deck")
        state = seeded
        generated = 0
        while not state.complete:
            if generated >= max_calls_per_world:
                raise AuctionRolloutError("BEN auction rollout exceeded call limit")
            acting = state.next_seat
            if acting is None:
                raise AuctionRolloutError("auction state has no next seat")
            selected = _validated_ben_call(ben_bidder(acting, hands[acting], state.calls))
            state = analyze_auction(dealer, [*state.calls, selected])
            generated += 1
        results.append({
            "world_index": world.get("world_index", index),
            "world_fingerprint": world.get("fingerprint"),
            "auction": list(state.calls),
            "contract": state.contract,
            "declarer": state.declarer,
            "passed_out": state.contract is None,
            "ben_calls_generated": generated,
        })
    return {
        "engine": "BEN",
        "fallback_used": False,
        "evidence_class": "BEN_AUCTION_ROLLOUT",
        "candidate_call": candidate,
        "requested_worlds": len(worlds),
        "completed_worlds": len(results),
        "complete": True,
        "dds_evaluated": False,
        "worlds": results,
    }


__all__ = [
    "AuctionRolloutError", "AuctionState", "analyze_auction", "normalize_call", "rollout_worlds",
]
