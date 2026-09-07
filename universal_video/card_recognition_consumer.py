"""Fail-closed Video 3.1 consumer for external card-recognition JSON.

This module deliberately does not import a recognizer implementation.  It treats
recognizer output as untrusted evidence, validates it, and preserves unknown
slots.  It never derives missing cards from the deck complement.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any


CONTRACT_SCHEMA = "video31-card-recognition-result/v1"
DEAL_EVIDENCE_SCHEMA = "bridge-video-deal-evidence/v1"
SEATS = ("N", "E", "S", "W")
SUITS = ("H", "C", "D", "S")
RANKS = tuple("AKQJT98765432")
KNOWN_SOURCES = frozenset({"VISUAL", "TEMPORAL_CONSENSUS"})
ALLOWED_STATUSES = frozenset(
    {
        "COMPLETE_VISUAL",
        "PENDING_TEMPORAL_CONSENSUS",
        "PARTIAL_VISUAL",
        "NEEDS_REVIEW",
    }
)
MAX_RESULT_BYTES = 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{2,127}$")


class CardRecognitionContractError(ValueError):
    """Recognition evidence cannot safely enter Video 3.1."""


def canonical_sha256(value: Mapping[str, Any]) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CardRecognitionContractError(f"{field} must be an object")
    return dict(value)


def _sequence(value: Any, field: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise CardRecognitionContractError(f"{field} must be an array")
    return list(value)


def _confidence(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise CardRecognitionContractError(f"invalid {field}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CardRecognitionContractError(f"invalid {field}") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise CardRecognitionContractError(f"invalid {field}")
    return number


def _sha(value: Any, field: str) -> str:
    text = str(value or "").lower()
    if not _SHA256.fullmatch(text):
        raise CardRecognitionContractError(f"invalid {field}")
    return text


def _unknown(seat: str, slot: int, version: str) -> dict[str, Any]:
    return {
        "seat": seat,
        "suit": None,
        "rank": None,
        "source": "UNKNOWN",
        "frame_sha256": None,
        "confidence": 0.0,
        "recognizer_version": version,
        "unknown_slot": slot,
    }


def _validate_record(raw: Any, index: int, version: str) -> dict[str, Any]:
    record = _mapping(raw, f"card_records[{index}]")
    seat = str(record.get("seat") or "").upper()
    if seat not in SEATS:
        raise CardRecognitionContractError(f"invalid card_records[{index}].seat")
    source = str(record.get("source") or "")
    record_version = str(record.get("recognizer_version") or "")
    if record_version != version:
        raise CardRecognitionContractError("mixed recognizer versions")
    if source == "UNKNOWN":
        if (
            record.get("rank") is not None
            or record.get("suit") is not None
            or record.get("frame_sha256") is not None
            or _confidence(record.get("confidence"), f"card_records[{index}].confidence") != 0
        ):
            raise CardRecognitionContractError("UNKNOWN slot contains inferred evidence")
        slot = record.get("unknown_slot")
        if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= 13:
            raise CardRecognitionContractError("invalid UNKNOWN slot")
        return _unknown(seat, slot, version)
    if source not in KNOWN_SOURCES:
        raise CardRecognitionContractError("unsupported or inferred card provenance")
    raw_rank = record.get("rank")
    raw_suit = record.get("suit")
    if not isinstance(raw_rank, str) or not isinstance(raw_suit, str):
        raise CardRecognitionContractError(f"invalid card_records[{index}] card types")
    rank = raw_rank.upper().replace("10", "T")
    suit = raw_suit.upper()
    if rank not in RANKS or suit not in SUITS:
        raise CardRecognitionContractError(f"invalid card_records[{index}] card")
    confidence = _confidence(record.get("confidence"), f"card_records[{index}].confidence")
    return {
        "seat": seat,
        "suit": suit,
        "rank": rank,
        "source": source,
        "frame_sha256": _sha(
            record.get("frame_sha256"), f"card_records[{index}].frame_sha256"
        ),
        "confidence": confidence,
        "recognizer_version": version,
    }


def validate_recognition_result(payload: Any) -> dict[str, Any]:
    """Validate and normalize a hash-bound recognizer result.

    The returned object contains exactly thirteen slots per seat.  Missing
    information must already be represented as UNKNOWN by the producer.
    """

    envelope = _mapping(payload, "recognition result")
    if envelope.get("schema") != CONTRACT_SCHEMA:
        raise CardRecognitionContractError("unsupported recognition contract")
    result = _mapping(envelope.get("result"), "result")
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        raise CardRecognitionContractError("recognition result exceeds size limit")
    if _sha(envelope.get("result_sha256"), "result_sha256") != canonical_sha256(result):
        raise CardRecognitionContractError("recognition result hash mismatch")
    if result.get("schema") != DEAL_EVIDENCE_SCHEMA:
        raise CardRecognitionContractError("unsupported deal evidence schema")
    version = str(result.get("recognizer_version") or "")
    if not _VERSION.fullmatch(version):
        raise CardRecognitionContractError("invalid recognizer_version")
    if result.get("status") not in ALLOWED_STATUSES:
        raise CardRecognitionContractError("unsupported recognition status")
    if list(result.get("suit_order") or ()) != list(SUITS):
        raise CardRecognitionContractError("suit order must be H,C,D,S")
    logical = _mapping(result.get("logical_inference"), "logical_inference")
    if logical.get("requested") is not False or logical.get("performed") is not False:
        raise CardRecognitionContractError("logical card inference is prohibited")
    if (
        result.get("canonical_promotion_allowed") is not False
        or result.get("school_canon_write_performed") is not False
    ):
        raise CardRecognitionContractError("recognizer crossed its authority boundary")

    records = [
        _validate_record(item, index, version)
        for index, item in enumerate(_sequence(result.get("card_records"), "card_records"))
    ]
    if len(records) != 52:
        raise CardRecognitionContractError("card_records must contain exactly 52 seat slots")
    counts = {seat: sum(record["seat"] == seat for record in records) for seat in SEATS}
    if any(count != 13 for count in counts.values()):
        raise CardRecognitionContractError("every seat must contain exactly 13 slots")
    known_cards: set[str] = set()
    unknown_slots: dict[str, set[int]] = {seat: set() for seat in SEATS}
    for record in records:
        seat = record["seat"]
        if record["source"] == "UNKNOWN":
            slot = int(record["unknown_slot"])
            if slot in unknown_slots[seat]:
                raise CardRecognitionContractError("duplicate UNKNOWN slot")
            unknown_slots[seat].add(slot)
            continue
        card = record["rank"] + record["suit"]
        if card in known_cards:
            raise CardRecognitionContractError("duplicate recognized card")
        known_cards.add(card)
    if result["status"] == "COMPLETE_VISUAL" and len(known_cards) != 52:
        raise CardRecognitionContractError(
            "COMPLETE_VISUAL requires 52 recognized cards and no UNKNOWN slots"
        )
    normalized = {
        "schema": CONTRACT_SCHEMA,
        "status": str(result["status"]),
        "recognizer_version": version,
        "cards": records,
        "known_card_count": len(known_cards),
        "unknown_slot_count": 52 - len(known_cards),
        "complete": len(known_cards) == 52 and result["status"] == "COMPLETE_VISUAL",
        "logical_inference_performed": False,
        "canonical_promotion_allowed": False,
    }
    normalized["consumer_sha256"] = canonical_sha256(normalized)
    return normalized


def adapt_legacy_hands(hands: Any, *, recognizer_version: str = "legacy-video31") -> dict[str, Any]:
    """Preserve old H/C/D/S hand data without completing it from the deck."""

    if not _VERSION.fullmatch(recognizer_version):
        raise CardRecognitionContractError("invalid recognizer_version")
    source = _mapping(hands, "legacy hands")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for seat in SEATS:
        hand = _mapping(source.get(seat), f"legacy hands.{seat}")
        known = 0
        for suit in SUITS:
            raw_ranks = hand.get(suit)
            if raw_ranks is None:
                raw_ranks = ""
            if not isinstance(raw_ranks, str):
                raise CardRecognitionContractError(f"invalid legacy hands.{seat}.{suit}")
            ranks = raw_ranks.upper().replace("10", "T").replace("-", "")
            if any(rank not in RANKS for rank in ranks) or len(set(ranks)) != len(ranks):
                raise CardRecognitionContractError(f"invalid legacy hands.{seat}.{suit}")
            for rank in ranks:
                card = rank + suit
                if card in seen:
                    raise CardRecognitionContractError("duplicate legacy card")
                seen.add(card)
                known += 1
                records.append(
                    {
                        "seat": seat,
                        "suit": suit,
                        "rank": rank,
                        "source": "VISUAL",
                        "frame_sha256": None,
                        "confidence": None,
                        "recognizer_version": recognizer_version,
                        "legacy_unverified": True,
                    }
                )
        if known > 13:
            raise CardRecognitionContractError("legacy hand exceeds 13 cards")
        records.extend(_unknown(seat, slot, recognizer_version) for slot in range(1, 14 - known))
    return {
        "schema": CONTRACT_SCHEMA,
        "status": "LEGACY_UNVERIFIED",
        "recognizer_version": recognizer_version,
        "cards": records,
        "known_card_count": len(seen),
        "unknown_slot_count": 52 - len(seen),
        "complete": False,
        "logical_inference_performed": False,
        "canonical_promotion_allowed": False,
    }


__all__ = [
    "CONTRACT_SCHEMA",
    "CardRecognitionContractError",
    "adapt_legacy_hands",
    "canonical_sha256",
    "validate_recognition_result",
]
