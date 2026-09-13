from __future__ import annotations

import re
from typing import Any, Sequence


class AuctionLegalityError(ValueError):
    pass


_SEATS = ("N", "E", "S", "W")
_STRAINS = ("C", "D", "H", "S", "NT")
_BID_RE = re.compile(r"^([1-7])(C|D|H|S|N|NT)$")


def _side(seat: str) -> str:
    return "NS" if seat in {"N", "S"} else "EW"


def _normalize_dealer(dealer: str) -> str:
    seat = str(dealer).strip().upper()
    if seat not in _SEATS:
        raise AuctionLegalityError(f"invalid dealer: {dealer!r}")
    return seat


def normalize_call(call: str) -> str:
    text = str(call).strip().upper().replace(" ", "")
    if text in {"P", "PASS"}:
        return "PASS"
    if text in {"X", "DBL", "DOUBLE"}:
        return "X"
    if text in {"XX", "RDBL", "REDOUBLE"}:
        return "XX"
    match = _BID_RE.fullmatch(text)
    if not match:
        raise AuctionLegalityError(f"unsupported auction call: {call!r}")
    strain = "NT" if match.group(2) in {"N", "NT"} else match.group(2)
    return f"{match.group(1)}{strain}"


def _bid_parts(call: str) -> tuple[int, str, int]:
    match = re.fullmatch(r"([1-7])(C|D|H|S|NT)", call)
    if not match:
        raise AuctionLegalityError(f"not a normalized bid: {call!r}")
    level = int(match.group(1))
    strain = match.group(2)
    rank = (level - 1) * 5 + _STRAINS.index(strain)
    return level, strain, rank


def validate_auction(calls: Sequence[str], *, dealer: str) -> dict[str, Any]:
    """Validate duplicate auction mechanics and derive final contract/declarer.

    This is only Laws-level auction structure. It does not evaluate whether a bid
    is correct under the school's system.
    """
    if isinstance(calls, (str, bytes)) or not isinstance(calls, Sequence):
        raise AuctionLegalityError("calls must be a sequence")
    if not calls:
        raise AuctionLegalityError("auction cannot be empty")

    dealer_seat = _normalize_dealer(dealer)
    dealer_index = _SEATS.index(dealer_seat)
    normalized = [normalize_call(call) for call in calls]

    history: list[dict[str, Any]] = []
    last_bid: dict[str, Any] | None = None
    double_state = ""
    consecutive_passes = 0
    first_bidder: dict[tuple[str, str], str] = {}
    terminated = False
    termination = None

    for index, call in enumerate(normalized):
        if terminated:
            raise AuctionLegalityError("auction contains calls after legal termination")
        seat = _SEATS[(dealer_index + index) % 4]
        side = _side(seat)
        entry: dict[str, Any] = {"index": index, "seat": seat, "side": side, "call": call}

        if call == "PASS":
            consecutive_passes += 1
            if last_bid is None and consecutive_passes == 4:
                terminated = True
                termination = "PASSOUT"
            elif last_bid is not None and consecutive_passes == 3:
                terminated = True
                termination = "CONTRACT"
        elif call == "X":
            if last_bid is None:
                raise AuctionLegalityError("double without a preceding bid")
            if double_state:
                raise AuctionLegalityError("contract is already doubled or redoubled")
            if side == last_bid["side"]:
                raise AuctionLegalityError("a side cannot double its own contract")
            double_state = "X"
            consecutive_passes = 0
        elif call == "XX":
            if last_bid is None or double_state != "X":
                raise AuctionLegalityError("redouble requires an existing double")
            if side != last_bid["side"]:
                raise AuctionLegalityError("only the declaring side may redouble")
            double_state = "XX"
            consecutive_passes = 0
        else:
            level, strain, rank = _bid_parts(call)
            if last_bid is not None and rank <= last_bid["rank"]:
                raise AuctionLegalityError(
                    f"insufficient bid {call}: must outrank {last_bid['call']}"
                )
            if (side, strain) not in first_bidder:
                first_bidder[(side, strain)] = seat
            last_bid = {
                "call": call,
                "level": level,
                "strain": strain,
                "rank": rank,
                "seat": seat,
                "side": side,
            }
            double_state = ""
            consecutive_passes = 0

        entry["double_state_after"] = double_state
        entry["consecutive_passes_after"] = consecutive_passes
        history.append(entry)

    if not terminated:
        if last_bid is None:
            raise AuctionLegalityError("passout requires exactly four terminal passes")
        raise AuctionLegalityError("contract auction must end with three passes")

    if termination == "PASSOUT":
        return {
            "schema": "tournament-auction-validation-v1",
            "valid": True,
            "dealer": dealer_seat,
            "normalized_calls": normalized,
            "history": history,
            "termination": "PASSOUT",
            "final_contract": None,
            "declarer": None,
            "contract_side": None,
        }

    assert last_bid is not None
    declarer = first_bidder[(last_bid["side"], last_bid["strain"])]
    final_contract = last_bid["call"] + double_state
    return {
        "schema": "tournament-auction-validation-v1",
        "valid": True,
        "dealer": dealer_seat,
        "normalized_calls": normalized,
        "history": history,
        "termination": "CONTRACT",
        "final_contract": final_contract,
        "final_bid": last_bid["call"],
        "double_state": double_state,
        "contract_side": last_bid["side"],
        "declarer": declarer,
        "declarer_rule": "first player of contract side who named final denomination",
    }
