"""Fail-closed visual auction observations for the profiled SHADOW pipeline.

The pixel/OCR backend is injected by the profiled challenger.  This boundary
accepts a call only when two independently named recognition channels agree,
both clear the 0.90 confidence gate, the visual table cell identifies the
expected dealer-relative seat and row, and the resulting auction is legal.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA = "bridge-visual-auction-observer/v1"
AGGREGATE_SCHEMA = "bridge-shadow-auction-consensus/v1"
SEATS = ("N", "E", "S", "W")
STRAINS = ("C", "D", "H", "S", "NT")
MAX_AUCTION_CALLS = 80
MIN_CHANNEL_CONFIDENCE = 0.90
_BID_RE = re.compile(r"^([1-7])(C|D|H|S|N|NT)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AuctionObserverError(ValueError):
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
        raise AuctionObserverError(f"unsupported auction call: {value!r}")
    strain = "NT" if match.group(2) in {"N", "NT"} else match.group(2)
    return f"{match.group(1)}{strain}"


def _probability(value: Any, name: str) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise AuctionObserverError(f"invalid {name}") from exc
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise AuctionObserverError(f"{name} outside [0,1]")
    return confidence


def _seat(value: Any, name: str) -> str:
    seat = str(value or "").strip().upper()
    if seat not in SEATS:
        raise AuctionObserverError(f"invalid {name}")
    return seat


def _positive_row(value: Any) -> int:
    if isinstance(value, bool):
        raise AuctionObserverError("invalid auction cell row")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AuctionObserverError("invalid auction cell row") from exc
    if not math.isfinite(number) or not number.is_integer():
        raise AuctionObserverError("invalid auction cell row")
    row = int(number)
    if row < 0 or row > 40:
        raise AuctionObserverError("auction cell row outside [0,40]")
    return row


def _box(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "w", "h"}:
        raise AuctionObserverError("auction cell box must contain x,y,w,h")
    box: dict[str, float] = {}
    for key in ("x", "y", "w", "h"):
        try:
            number = float(value[key])
        except (TypeError, ValueError) as exc:
            raise AuctionObserverError("invalid auction cell box") from exc
        if not math.isfinite(number) or abs(number) > 1_000_000:
            raise AuctionObserverError("invalid auction cell box")
        box[key] = number
    if box["w"] <= 0 or box["h"] <= 0:
        raise AuctionObserverError("auction cell box must have positive size")
    return box


def _side(seat: str) -> str:
    return "NS" if seat in {"N", "S"} else "EW"


def _bid_rank(call: str) -> tuple[str, int]:
    match = re.fullmatch(r"([1-7])(C|D|H|S|NT)", call)
    if not match:
        raise AuctionObserverError("invalid normalized bid")
    strain = match.group(2)
    return strain, (int(match.group(1)) - 1) * 5 + STRAINS.index(strain)


def validate_auction_prefix(calls: Sequence[str], *, dealer: str) -> dict[str, Any]:
    """Validate Laws-level mechanics while allowing a visible partial prefix."""
    if isinstance(calls, (str, bytes)) or not isinstance(calls, Sequence) or not calls:
        raise AuctionObserverError("auction calls must be a non-empty array")
    if len(calls) > MAX_AUCTION_CALLS:
        raise AuctionObserverError("auction call limit exceeded")
    dealer_seat = _seat(dealer, "dealer")
    dealer_index = SEATS.index(dealer_seat)
    normalized = [normalize_call(call) for call in calls]
    last_bid: dict[str, Any] | None = None
    double_state = ""
    passes = 0
    terminated = False
    termination = None
    history: list[dict[str, Any]] = []
    for index, call in enumerate(normalized):
        if terminated:
            raise AuctionObserverError("auction contains calls after termination")
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
                raise AuctionObserverError("double without a preceding bid")
            if double_state:
                raise AuctionObserverError("contract is already doubled or redoubled")
            if side == last_bid["side"]:
                raise AuctionObserverError("a side cannot double its own contract")
            double_state, passes = "X", 0
        elif call == "XX":
            if last_bid is None or double_state != "X":
                raise AuctionObserverError("redouble requires an existing double")
            if side != last_bid["side"]:
                raise AuctionObserverError("only the declaring side may redouble")
            double_state, passes = "XX", 0
        else:
            strain, rank = _bid_rank(call)
            if last_bid is not None and rank <= last_bid["rank"]:
                raise AuctionObserverError(f"insufficient bid {call}")
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


def _channel(raw: Any, name: str) -> tuple[str, float, str]:
    if not isinstance(raw, Mapping):
        raise AuctionObserverError(f"{name} channel is missing")
    call = normalize_call(raw.get("value"))
    confidence = _probability(raw.get("confidence"), f"{name} confidence")
    if confidence < MIN_CHANNEL_CONFIDENCE:
        raise AuctionObserverError(f"{name} confidence below 0.90 gate")
    channel_id = str(raw.get("channel_id") or "").strip()
    if not channel_id or len(channel_id) > 128:
        raise AuctionObserverError(f"invalid {name} channel id")
    return call, confidence, channel_id


def observe_bridgit_auction(
    raw: Any,
    *,
    board_number: int,
    dealer: str,
    frame_sha256: str,
    board_confirmed: bool,
) -> dict[str, Any] | None:
    """Normalize one recognizer result; invalid visual data remains REVIEW."""
    if raw is None:
        return None
    base = {
        "schema": SCHEMA,
        "frame_sha256": str(frame_sha256),
        "board_number": board_number,
        "dealer": str(dealer).upper(),
        "provenance_class": "OBSERVED_VISUAL_AUCTION",
        "canonical_promotion_allowed": False,
    }
    try:
        if not isinstance(raw, Mapping):
            raise AuctionObserverError("auction observation must be an object")
        if not _SHA256.fullmatch(str(frame_sha256).lower()):
            raise AuctionObserverError("invalid auction frame sha256")
        if str(raw.get("source") or "").upper() != "BRIDGIT_AUCTION_TABLE":
            raise AuctionObserverError("unsupported auction observation source")
        if raw.get("board_number") is not None and int(raw["board_number"]) != int(board_number):
            raise AuctionObserverError("auction board number disagrees with compass")
        if raw.get("dealer") is not None and _seat(raw["dealer"], "auction dealer") != _seat(dealer, "dealer"):
            raise AuctionObserverError("auction dealer disagrees with board cycle")
        locator = str(raw.get("evidence_locator") or "").strip()
        if not locator or len(locator) > 256:
            raise AuctionObserverError("invalid auction evidence locator")
        calls_raw = raw.get("calls")
        if isinstance(calls_raw, (str, bytes)) or not isinstance(calls_raw, Sequence) or not calls_raw:
            raise AuctionObserverError("auction calls must be a non-empty array")
        if len(calls_raw) > MAX_AUCTION_CALLS:
            raise AuctionObserverError("auction call limit exceeded")
        dealer_seat = _seat(dealer, "dealer")
        dealer_index = SEATS.index(dealer_seat)
        calls: list[str] = []
        cells: list[dict[str, Any]] = []
        confidence_floor = 1.0
        for index, item in enumerate(calls_raw):
            if not isinstance(item, Mapping):
                raise AuctionObserverError("auction cell must be an object")
            primary_call, primary_confidence, primary_channel = _channel(item.get("ocr"), "OCR")
            reference_call, reference_confidence, reference_channel = _channel(
                item.get("reference_match"), "reference"
            )
            if primary_channel == reference_channel:
                raise AuctionObserverError("auction channels must be independent")
            if primary_call != reference_call:
                raise AuctionObserverError("auction recognition channels disagree")
            expected_seat = SEATS[(dealer_index + index) % 4]
            expected_row = (dealer_index + index) // 4
            observed_seat = _seat(item.get("seat") or item.get("column"), "auction cell seat")
            observed_column = _seat(item.get("column") or observed_seat, "auction cell column")
            observed_row = _positive_row(item.get("row"))
            if observed_seat != expected_seat or observed_column != expected_seat or observed_row != expected_row:
                raise AuctionObserverError("auction cell order disagrees with dealer-relative sequence")
            cell_locator = str(item.get("evidence_locator") or "").strip()
            if not cell_locator or len(cell_locator) > 256:
                raise AuctionObserverError("invalid auction cell evidence locator")
            calls.append(primary_call)
            confidence_floor = min(confidence_floor, primary_confidence, reference_confidence)
            cells.append({
                "index": index,
                "seat": expected_seat,
                "row": expected_row,
                "column": expected_seat,
                "call": primary_call,
                "box": _box(item.get("box")),
                "confidence_floor": min(primary_confidence, reference_confidence),
                "channels": [primary_channel, reference_channel],
                "evidence_locator": cell_locator,
            })
        legality = validate_auction_prefix(calls, dealer=dealer_seat)
        complete = raw.get("complete") is True
        if complete != legality["terminated"]:
            raise AuctionObserverError(
                "complete flag disagrees with legal auction termination"
                if complete
                else "terminated auction must be marked complete"
            )
        if not board_confirmed:
            return {
                **base,
                "status": "REVIEW",
                "reason": "BOARD_AND_COMPASS_NOT_TEMPORALLY_CONFIRMED",
                "accepted_as_observation": False,
                "calls": calls,
                "complete": complete,
            }
        return {
            **base,
            "status": "PASS" if complete else "PARTIAL",
            "accepted_as_observation": True,
            "calls": calls,
            "cells": cells,
            "complete": complete,
            "termination": legality["termination"],
            "confidence_floor": confidence_floor,
            "evidence_locator": locator,
        }
    except (AuctionObserverError, TypeError, ValueError) as exc:
        return {
            **base,
            "status": "REVIEW",
            "reason": "AUCTION_OBSERVATION_REJECTED",
            "detail": str(exc)[:160],
            "accepted_as_observation": False,
        }


def aggregate_auction_observations(
    observations: Sequence[Mapping[str, Any]],
    *,
    min_independent_frames: int = 2,
) -> dict[str, Any]:
    """Choose the longest prefix-compatible sequence and measure frame support."""
    if min_independent_frames < 1:
        raise ValueError("min independent frames must be positive")
    accepted: list[Mapping[str, Any]] = []
    for observation in observations:
        if not isinstance(observation, Mapping):
            raise AuctionObserverError("auction observation must be an object")
        if observation.get("accepted_as_observation") is not True:
            continue
        if (
            observation.get("schema") != SCHEMA
            or observation.get("provenance_class") != "OBSERVED_VISUAL_AUCTION"
            or observation.get("canonical_promotion_allowed") is not False
        ):
            raise AuctionObserverError("invalid accepted auction provenance")
        accepted.append(observation)
    if not accepted:
        return {
            "schema": AGGREGATE_SCHEMA,
            "status": "UNAVAILABLE",
            "accepted_as_standard_pbn": False,
            "canonical_promotion_allowed": False,
        }
    dealers = {_seat(item.get("dealer"), "dealer") for item in accepted}
    boards = {int(item.get("board_number")) for item in accepted}
    if len(dealers) != 1 or len(boards) != 1:
        return {
            "schema": AGGREGATE_SCHEMA,
            "status": "CONFLICT",
            "reason": "AUCTION_BOARD_OR_DEALER_CONFLICT",
            "accepted_as_standard_pbn": False,
            "canonical_promotion_allowed": False,
        }
    variants: dict[tuple[str, ...], set[str]] = {}
    complete_variants: set[tuple[str, ...]] = set()
    confidence_by_variant: dict[tuple[str, ...], list[float]] = {}
    for item in accepted:
        frame_sha256 = str(item.get("frame_sha256") or "").lower()
        if not _SHA256.fullmatch(frame_sha256):
            raise AuctionObserverError("accepted auction has invalid frame sha256")
        calls = tuple(normalize_call(call) for call in item.get("calls") or [])
        if not calls:
            raise AuctionObserverError("accepted auction has no calls")
        validate_auction_prefix(calls, dealer=next(iter(dealers)))
        variants.setdefault(calls, set()).add(frame_sha256)
        confidence_by_variant.setdefault(calls, []).append(
            _probability(item.get("confidence_floor"), "accepted auction confidence")
        )
        if item.get("complete") is True:
            complete_variants.add(calls)
    ordered = sorted(variants, key=lambda calls: (len(calls), calls), reverse=True)
    longest = ordered[0]
    incompatible = [calls for calls in ordered[1:] if longest[: len(calls)] != calls]
    if incompatible:
        return {
            "schema": AGGREGATE_SCHEMA,
            "status": "CONFLICT",
            "reason": "AUCTION_SEQUENCE_CONFLICT",
            "dealer": next(iter(dealers)),
            "board_number": next(iter(boards)),
            "variants": [list(calls) for calls in ordered],
            "accepted_as_standard_pbn": False,
            "canonical_promotion_allowed": False,
        }
    frame_support: list[int] = []
    for length in range(1, len(longest) + 1):
        supporting_frames: set[str] = set()
        prefix = longest[:length]
        for calls, frames in variants.items():
            if len(calls) >= length and calls[:length] == prefix:
                supporting_frames.update(frames)
        frame_support.append(len(supporting_frames))
    complete = longest in complete_variants
    support_floor = min(frame_support)
    standard = complete and support_floor >= min_independent_frames
    return {
        "schema": AGGREGATE_SCHEMA,
        "status": (
            "COMPLETE_CONFIRMED"
            if standard
            else "COMPLETE_NEEDS_TEMPORAL_CONFIRMATION"
            if complete
            else "PARTIAL_OBSERVED"
        ),
        "board_number": next(iter(boards)),
        "dealer": next(iter(dealers)),
        "calls": list(longest),
        "complete": complete,
        "call_frame_support": frame_support,
        "independent_frame_support_floor": support_floor,
        "confidence_floor": min(
            confidence
            for calls, confidences in confidence_by_variant.items()
            if longest[: len(calls)] == calls
            for confidence in confidences
        ),
        "accepted_as_standard_pbn": standard,
        "canonical_promotion_allowed": False,
    }


__all__ = [
    "AGGREGATE_SCHEMA",
    "AuctionObserverError",
    "MIN_CHANNEL_CONFIDENCE",
    "SCHEMA",
    "aggregate_auction_observations",
    "normalize_call",
    "observe_bridgit_auction",
    "validate_auction_prefix",
]
