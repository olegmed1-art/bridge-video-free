"""Source-bound visual auction observations and temporal consensus."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from bridge_contracts.video_auction import MAX_AUCTION_CALLS, SEATS, normalize_call, validate_auction_prefix

SCHEMA = "bridge-source-bound-visual-auction/v2"
AGGREGATE_SCHEMA = "bridge-source-bound-auction-consensus/v2"
MIN_CHANNEL_CONFIDENCE = 0.90
MIN_INDEPENDENT_FRAMES = 2
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AuctionObserverError(ValueError):
    pass


def _probability(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AuctionObserverError(f"invalid {name}") from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise AuctionObserverError(f"{name} outside [0,1]")
    return result


def _seat(value: Any, name: str) -> str:
    result = str(value or "").strip().upper()
    if result not in SEATS:
        raise AuctionObserverError(f"invalid {name}")
    return result


def _identity(raw: Any, *, board_number: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or raw.get("kind") != "SOURCE_BOUND_BOARD_INSTANCE":
        raise AuctionObserverError("auction needs source-bound deal identity")
    if isinstance(raw.get("board_number"), bool):
        raise AuctionObserverError("auction board identity is invalid")
    identity = {
        "kind": raw["kind"], "scope": str(raw.get("scope") or ""),
        "instance_id": str(raw.get("instance_id") or ""),
        "board_number": int(raw.get("board_number")),
        "anchor_frame_sha256": str(raw.get("anchor_frame_sha256") or ""),
    }
    if not identity["scope"] or not identity["instance_id"] or identity["board_number"] != int(board_number):
        raise AuctionObserverError("auction deal identity is incomplete or mismatched")
    if not _SHA256.fullmatch(identity["anchor_frame_sha256"]):
        raise AuctionObserverError("auction deal anchor is invalid")
    return identity


def _channel(raw: Any, name: str, *, frame_sha256: str, expected_source: str) -> tuple[str, float, str]:
    if not isinstance(raw, Mapping):
        raise AuctionObserverError(f"{name} channel is missing")
    if raw.get("source") != expected_source or raw.get("frame_sha256") != frame_sha256:
        raise AuctionObserverError(f"{name} channel is not independent visual frame evidence")
    call = normalize_call(raw.get("value"))
    confidence = _probability(raw.get("confidence"), f"{name} confidence")
    if confidence < MIN_CHANNEL_CONFIDENCE:
        raise AuctionObserverError(f"{name} confidence below gate")
    channel_id = str(raw.get("channel_id") or "").strip()
    if not channel_id or len(channel_id) > 128:
        raise AuctionObserverError(f"invalid {name} channel id")
    return call, confidence, channel_id


def _box(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "w", "h"}:
        raise AuctionObserverError("auction cell box must contain x,y,w,h")
    try:
        box = {key: float(value[key]) for key in ("x", "y", "w", "h")}
    except (TypeError, ValueError) as exc:
        raise AuctionObserverError("invalid auction cell box") from exc
    if any(not math.isfinite(number) for number in box.values()) or box["w"] <= 0 or box["h"] <= 0:
        raise AuctionObserverError("invalid auction cell box")
    return box


def observe_bridgit_auction(
    raw: Any, *, board_number: int, dealer: str, frame_sha256: str,
    source_id: str, deal_identity: Mapping[str, Any], board_context_status: str,
) -> dict[str, Any] | None:
    if raw is None:
        return None
    base = {
        "schema": SCHEMA, "frame_sha256": frame_sha256, "source_id": source_id,
        "board_number": int(board_number), "dealer": str(dealer).upper(),
        "provenance_class": "OBSERVED_VISUAL_AUCTION", "production_activation_allowed": False,
    }
    try:
        if not isinstance(raw, Mapping) or raw.get("source") != "BRIDGIT_AUCTION_TABLE":
            raise AuctionObserverError("unsupported auction observation source")
        if not _SHA256.fullmatch(frame_sha256) or raw.get("frame_sha256") != frame_sha256:
            raise AuctionObserverError("auction is not bound to the frame")
        if not source_id or raw.get("source_id") != source_id:
            raise AuctionObserverError("auction is not bound to the video source")
        identity = _identity(deal_identity, board_number=board_number)
        if raw.get("deal_instance_id") != identity["instance_id"]:
            raise AuctionObserverError("auction deal instance disagrees with board context")
        if int(raw.get("board_number")) != int(board_number) or _seat(raw.get("dealer"), "auction dealer") != _seat(dealer, "dealer"):
            raise AuctionObserverError("auction board/dealer disagrees with board context")
        calls_raw = raw.get("calls")
        if isinstance(calls_raw, (str, bytes)) or not isinstance(calls_raw, Sequence) or not calls_raw:
            raise AuctionObserverError("auction calls must be a non-empty array")
        if len(calls_raw) > MAX_AUCTION_CALLS:
            raise AuctionObserverError("auction call limit exceeded")
        dealer = _seat(dealer, "dealer")
        dealer_index = SEATS.index(dealer)
        calls, cells, confidence_floor = [], [], 1.0
        for index, item in enumerate(calls_raw):
            if not isinstance(item, Mapping):
                raise AuctionObserverError("auction cell must be an object")
            ocr_call, ocr_conf, ocr_id = _channel(item.get("ocr"), "OCR", frame_sha256=frame_sha256, expected_source="VISUAL_OCR")
            ref_call, ref_conf, ref_id = _channel(item.get("reference_match"), "reference", frame_sha256=frame_sha256, expected_source="VISUAL_REFERENCE")
            if ocr_id == ref_id or ocr_call != ref_call:
                raise AuctionObserverError("auction recognition channels disagree or are not independent")
            expected_seat = SEATS[(dealer_index + index) % 4]
            expected_row = (dealer_index + index) // 4
            row = item.get("row")
            if isinstance(row, bool):
                raise AuctionObserverError("auction cell row is invalid")
            try:
                observed_row = int(row)
            except (TypeError, ValueError) as exc:
                raise AuctionObserverError("auction cell row is invalid") from exc
            if _seat(item.get("seat"), "auction cell seat") != expected_seat or observed_row != expected_row:
                raise AuctionObserverError("auction cell order disagrees with dealer-relative sequence")
            locator = str(item.get("evidence_locator") or "").strip()
            if not locator:
                raise AuctionObserverError("auction cell evidence locator is missing")
            calls.append(ocr_call)
            confidence_floor = min(confidence_floor, ocr_conf, ref_conf)
            cells.append({
                "index": index, "seat": expected_seat, "row": expected_row, "call": ocr_call,
                "box": _box(item.get("box")), "channels": [ocr_id, ref_id],
                "confidence_floor": min(ocr_conf, ref_conf), "evidence_locator": locator,
            })
        legality = validate_auction_prefix(calls, dealer=dealer)
        complete = raw.get("complete") is True
        if complete != legality["terminated"]:
            raise AuctionObserverError("complete flag disagrees with legal termination")
        if board_context_status != "CONFIRMED":
            return {**base, "deal_identity": identity, "status": "REVIEW", "reason": "BOARD_CONTEXT_NOT_CONFIRMED", "accepted_as_observation": False}
        return {
            **base, "deal_identity": identity, "status": "PASS" if complete else "PARTIAL",
            "accepted_as_observation": True, "calls": calls, "cells": cells,
            "complete": complete, "termination": legality["termination"],
            "contract": legality["contract"], "declarer": legality["declarer"],
            "confidence_floor": confidence_floor,
        }
    except Exception as exc:
        return {**base, "status": "REVIEW", "reason": "AUCTION_OBSERVATION_REJECTED", "detail": str(exc)[:160], "accepted_as_observation": False}


def _identity_key(identity: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(identity), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def aggregate_auction_observations(
    observations: Sequence[Mapping[str, Any]], *, min_independent_frames: int = MIN_INDEPENDENT_FRAMES,
) -> dict[str, Any]:
    if min_independent_frames < MIN_INDEPENDENT_FRAMES:
        raise AuctionObserverError("independent frame support cannot be lowered below two")
    if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
        raise AuctionObserverError("auction observations must be an array")
    accepted = []
    for item in observations:
        if not isinstance(item, Mapping):
            raise AuctionObserverError("auction observation must be an object")
        if item.get("accepted_as_observation") is True:
            accepted.append(item)
    if not accepted:
        return {"schema": AGGREGATE_SCHEMA, "status": "UNAVAILABLE", "accepted_as_standard_pbn": False, "production_activation_allowed": False}
    keys = set()
    for item in accepted:
        if item.get("schema") != SCHEMA or item.get("provenance_class") != "OBSERVED_VISUAL_AUCTION":
            raise AuctionObserverError("invalid accepted auction provenance")
        source_id = str(item.get("source_id") or "")
        board = item.get("board_number")
        if not source_id or isinstance(board, bool):
            raise AuctionObserverError("accepted auction lacks source/board identity")
        try:
            board = int(board)
        except (TypeError, ValueError) as exc:
            raise AuctionObserverError("accepted auction has invalid board identity") from exc
        identity = _identity(item.get("deal_identity"), board_number=board)
        keys.add((source_id, _identity_key(identity), _seat(item.get("dealer"), "dealer"), board))
    if len(keys) != 1:
        return {"schema": AGGREGATE_SCHEMA, "status": "CONFLICT", "reason": "AUCTION_DEAL_IDENTITY_CONFLICT", "accepted_as_standard_pbn": False, "production_activation_allowed": False}
    variants: dict[tuple[str, ...], set[str]] = {}
    complete_variants: set[tuple[str, ...]] = set()
    frame_variant: dict[str, tuple[str, ...]] = {}
    confidences: list[float] = []
    for item in accepted:
        frame_sha = str(item.get("frame_sha256") or "")
        if not _SHA256.fullmatch(frame_sha) or item.get("production_activation_allowed") is not False:
            raise AuctionObserverError("accepted auction is not safe source-bound evidence")
        calls = tuple(normalize_call(call) for call in item.get("calls") or [])
        validate_auction_prefix(calls, dealer=str(item.get("dealer")))
        if frame_sha in frame_variant and frame_variant[frame_sha] != calls:
            return {"schema": AGGREGATE_SCHEMA, "status": "CONFLICT", "reason": "ONE_FRAME_HAS_MULTIPLE_AUCTIONS", "accepted_as_standard_pbn": False, "production_activation_allowed": False}
        frame_variant[frame_sha] = calls
        variants.setdefault(calls, set()).add(frame_sha)
        confidences.append(_probability(item.get("confidence_floor"), "auction confidence"))
        if item.get("complete") is True:
            complete_variants.add(calls)
    ordered = sorted(variants, key=lambda value: (len(value), value), reverse=True)
    longest = ordered[0]
    if any(longest[:len(calls)] != calls for calls in ordered[1:]):
        return {"schema": AGGREGATE_SCHEMA, "status": "CONFLICT", "reason": "AUCTION_SEQUENCE_CONFLICT", "accepted_as_standard_pbn": False, "production_activation_allowed": False}
    support = [sum(1 for calls in frame_variant.values() if len(calls) > index and calls[:index + 1] == longest[:index + 1]) for index in range(len(longest))]
    legality = validate_auction_prefix(longest, dealer=str(accepted[0]["dealer"]))
    complete = longest in complete_variants and legality["terminated"]
    standard = complete and min(support) >= min_independent_frames
    source_id, _, dealer, board = next(iter(keys))
    return {
        "schema": AGGREGATE_SCHEMA, "status": "COMPLETE_CONFIRMED" if standard else "COMPLETE_NEEDS_TEMPORAL_CONFIRMATION" if complete else "PARTIAL_OBSERVED",
        "source_id": source_id, "deal_identity": dict(accepted[0]["deal_identity"]),
        "board_number": board, "dealer": dealer, "calls": list(longest), "complete": complete,
        "contract": legality["contract"], "declarer": legality["declarer"],
        "call_frame_support": support, "independent_frame_support_floor": min(support),
        "confidence_floor": min(confidences), "accepted_as_standard_pbn": standard,
        "production_activation_allowed": False,
    }


__all__ = ["AGGREGATE_SCHEMA", "AuctionObserverError", "MIN_CHANNEL_CONFIDENCE", "SCHEMA", "aggregate_auction_observations", "observe_bridgit_auction"]
