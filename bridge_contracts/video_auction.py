"""Fail-closed mechanics contract for auctions observed in bridge video.

This module validates only the order and legality of calls.  It deliberately
does not assign bidding meanings, teaching conclusions, or School Canon
status.  An upstream observer remains responsible for proving that the calls
were actually visible in the source video.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

SEATS = ("N", "E", "S", "W")
STRAINS = ("C", "D", "H", "S", "NT")
MAX_AUCTION_CALLS = 80
_BID_RE = re.compile(r"^([1-7])(C|D|H|S|N|NT)$")


class VideoAuctionContractError(ValueError):
    pass


def normalize_call(value: Any) -> str:
    text = str(value or "").strip().upper().replace(" ", "")
    if text in {"P", "PASS"}:
        return "PASS"
    if text in {"X", "DBL", "DOUBLE"}:
        return "X"
    if text in {"XX", "RDBL", "REDOUBLE"}:
        return "XX"
    match = _BID_RE.fullmatch(text)
    if not match:
        raise VideoAuctionContractError(f"unsupported auction call: {value!r}")
    strain = "NT" if match.group(2) in {"N", "NT"} else match.group(2)
    return f"{match.group(1)}{strain}"


def _side(seat: str) -> str:
    return "NS" if seat in {"N", "S"} else "EW"


def _bid_rank(call: str) -> tuple[str, int]:
    match = re.fullmatch(r"([1-7])(C|D|H|S|NT)", call)
    if not match:
        raise VideoAuctionContractError("invalid normalized bid")
    strain = match.group(2)
    return strain, (int(match.group(1)) - 1) * 5 + STRAINS.index(strain)


def validate_auction_prefix(calls: Sequence[Any], *, dealer: str) -> dict[str, Any]:
    """Validate Laws-level call mechanics while allowing a partial prefix."""
    if isinstance(calls, (str, bytes)) or not isinstance(calls, Sequence) or not calls:
        raise VideoAuctionContractError("auction calls must be a non-empty array")
    if len(calls) > MAX_AUCTION_CALLS:
        raise VideoAuctionContractError("auction call limit exceeded")
    dealer_seat = str(dealer or "").strip().upper()
    if dealer_seat not in SEATS:
        raise VideoAuctionContractError("dealer must be N, E, S, or W")

    normalized = [normalize_call(call) for call in calls]
    dealer_index = SEATS.index(dealer_seat)
    last_bid: dict[str, Any] | None = None
    double_state = ""
    passes = 0
    terminated = False
    termination = None
    history: list[dict[str, Any]] = []

    for index, call in enumerate(normalized):
        if terminated:
            raise VideoAuctionContractError("auction contains calls after termination")
        seat = SEATS[(dealer_index + index) % 4]
        side = _side(seat)
        if call == "PASS":
            passes += 1
            if last_bid is None and passes == 4:
                terminated, termination = True, "PASSOUT"
            elif last_bid is not None and passes == 3:
                terminated, termination = True, "CONTRACT"
        elif call == "X":
            if last_bid is None:
                raise VideoAuctionContractError("double without a preceding bid")
            if double_state:
                raise VideoAuctionContractError("contract is already doubled or redoubled")
            if side == last_bid["side"]:
                raise VideoAuctionContractError("a side cannot double its own contract")
            double_state, passes = "X", 0
        elif call == "XX":
            if last_bid is None or double_state != "X":
                raise VideoAuctionContractError("redouble requires an existing double")
            if side != last_bid["side"]:
                raise VideoAuctionContractError("only the declaring side may redouble")
            double_state, passes = "XX", 0
        else:
            strain, rank = _bid_rank(call)
            if last_bid is not None and rank <= last_bid["rank"]:
                raise VideoAuctionContractError(f"insufficient bid {call}")
            last_bid = {"call": call, "strain": strain, "rank": rank, "side": side}
            double_state, passes = "", 0
        history.append({"index": index, "seat": seat, "call": call})

    return {
        "dealer": dealer_seat,
        "normalized_calls": normalized,
        "history": history,
        "terminated": terminated,
        "termination": termination,
    }


__all__ = [
    "MAX_AUCTION_CALLS",
    "SEATS",
    "STRAINS",
    "VideoAuctionContractError",
    "normalize_call",
    "validate_auction_prefix",
]
