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
CONFIDENCE_GATES = {"video31-card-consumer-v1": 0.80}
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
_IDENTITY_TEXT = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")


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
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CardRecognitionContractError(f"invalid {field}")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise CardRecognitionContractError(f"invalid {field}")
    return number


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise CardRecognitionContractError(f"invalid {field}")
    text = value.lower()
    if not _SHA256.fullmatch(text):
        raise CardRecognitionContractError(f"invalid {field}")
    return text


def _deal_identity(
    value: Any, *, visual_anchor_gates: tuple[int, float] | None
) -> dict[str, Any]:
    identity = _mapping(value, "deal_identity")
    kind = str(identity.get("kind") or "").upper()
    if kind == "EXPLICIT_BOARD":
        if set(identity) != {"kind", "scope", "value"}:
            raise CardRecognitionContractError("invalid explicit deal_identity fields")
        raw_scope = identity.get("scope")
        raw_board = identity.get("value")
        if (
            not isinstance(raw_scope, str)
            or not isinstance(raw_board, str)
        ):
            raise CardRecognitionContractError("invalid explicit deal_identity")
        scope = raw_scope.strip()
        board = raw_board.strip()
        if (
            not scope
            or not board
            or not _IDENTITY_TEXT.fullmatch(scope)
            or not _IDENTITY_TEXT.fullmatch(board)
        ):
            raise CardRecognitionContractError("invalid explicit deal_identity")
        return {"kind": kind, "scope": scope, "value": board}
    if kind == "VISUAL_ANCHOR":
        if visual_anchor_gates is None:
            raise CardRecognitionContractError(
                "VISUAL_ANCHOR requires approved profile-bound gates"
            )
        if set(identity) != {
            "kind",
            "anchor_frame_sha256",
            "inliers",
            "inlier_ratio",
        }:
            raise CardRecognitionContractError("invalid visual deal_identity fields")
        inliers = identity.get("inliers")
        if isinstance(inliers, bool) or not isinstance(inliers, int) or inliers <= 0:
            raise CardRecognitionContractError("invalid visual deal_identity inliers")
        inlier_ratio = _confidence(
            identity.get("inlier_ratio"), "deal_identity.inlier_ratio"
        )
        minimum_inliers, minimum_inlier_ratio = visual_anchor_gates
        if inliers < minimum_inliers or inlier_ratio < minimum_inlier_ratio:
            raise CardRecognitionContractError("visual deal_identity below approved gates")
        return {
            "kind": kind,
            "anchor_frame_sha256": _sha(
                identity.get("anchor_frame_sha256"),
                "deal_identity.anchor_frame_sha256",
            ),
            "inliers": inliers,
            "inlier_ratio": inlier_ratio,
        }
    raise CardRecognitionContractError("unsupported deal_identity")


def _trusted_tuple(
    result: Mapping[str, Any], approved_recognizers: Sequence[Mapping[str, Any]]
) -> tuple[str, str, str, tuple[int, float] | None]:
    version = result["recognizer_version"]
    profile_id = result["recognition_profile_id"]
    verification_sha = _sha(
        result.get("profile_verification_sha256"), "profile_verification_sha256"
    )
    candidate = (version, profile_id, verification_sha)
    approved: dict[tuple[str, str, str], tuple[int, float] | None] = {}
    for index, raw in enumerate(approved_recognizers):
        item = _mapping(raw, f"approved_recognizers[{index}]")
        required_fields = {
            "recognizer_version",
            "recognition_profile_id",
            "profile_verification_sha256",
        }
        optional_anchor_fields = {
            "min_deal_match_inliers",
            "min_deal_match_inlier_ratio",
        }
        if not required_fields.issubset(item) or set(item) - required_fields not in (
            set(),
            optional_anchor_fields,
        ):
            raise CardRecognitionContractError("invalid approved recognizer tuple")
        approved_version = item["recognizer_version"]
        approved_profile = item["recognition_profile_id"]
        if not isinstance(approved_version, str) or not _VERSION.fullmatch(
            approved_version
        ):
            raise CardRecognitionContractError("invalid approved recognizer version")
        if not isinstance(approved_profile, str) or approved_profile not in CONFIDENCE_GATES:
            raise CardRecognitionContractError("invalid approved recognizer profile")
        approved_candidate = (
                approved_version,
                approved_profile,
                _sha(
                    item["profile_verification_sha256"],
                    f"approved_recognizers[{index}].profile_verification_sha256",
                ),
            )
        anchor_gates = None
        if optional_anchor_fields.issubset(item):
            minimum_inliers = item["min_deal_match_inliers"]
            if (
                isinstance(minimum_inliers, bool)
                or not isinstance(minimum_inliers, int)
                or minimum_inliers <= 0
            ):
                raise CardRecognitionContractError("invalid approved anchor inlier gate")
            anchor_gates = (
                minimum_inliers,
                _confidence(
                    item["min_deal_match_inlier_ratio"],
                    f"approved_recognizers[{index}].min_deal_match_inlier_ratio",
                ),
            )
        if approved_candidate in approved and approved[approved_candidate] != anchor_gates:
            raise CardRecognitionContractError("conflicting approved recognizer policy")
        approved[approved_candidate] = anchor_gates
    if candidate not in approved:
        raise CardRecognitionContractError("recognizer/profile tuple is not approved")
    return (*candidate, approved[candidate])


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


def _validate_record(
    raw: Any, index: int, version: str, minimum_confidence: float
) -> dict[str, Any]:
    record = _mapping(raw, f"card_records[{index}]")
    seat = str(record.get("seat") or "").upper()
    if seat not in SEATS:
        raise CardRecognitionContractError(f"invalid card_records[{index}].seat")
    source = str(record.get("source") or "")
    record_version = record.get("recognizer_version")
    if not isinstance(record_version, str):
        raise CardRecognitionContractError("invalid card recognizer_version type")
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
    if confidence < minimum_confidence:
        raise CardRecognitionContractError("recognized card is below confidence gate")
    normalized = {
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
    if source == "VISUAL":
        raise CardRecognitionContractError(
            "single-frame VISUAL evidence remains pending until temporal consensus"
        )
    if source == "TEMPORAL_CONSENSUS":
        frame_hashes = [
            _sha(item, f"card_records[{index}].frame_sha256s")
            for item in _sequence(
                record.get("frame_sha256s"),
                f"card_records[{index}].frame_sha256s",
            )
        ]
        support_count = record.get("support_count")
        if (
            isinstance(support_count, bool)
            or not isinstance(support_count, int)
            or support_count != len(frame_hashes)
            or len(frame_hashes) < 2
            or len(set(frame_hashes)) != len(frame_hashes)
            or normalized["frame_sha256"] not in frame_hashes
        ):
            raise CardRecognitionContractError(
                "TEMPORAL_CONSENSUS requires at least two distinct supporting frames"
            )
        normalized["frame_sha256s"] = frame_hashes
        normalized["support_count"] = support_count
    return normalized


def validate_recognition_result(
    payload: Any,
    *,
    approved_recognizers: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Validate and normalize a hash-bound recognizer result.

    The returned object contains exactly thirteen slots per seat. Missing
    information must already be represented as UNKNOWN by the producer.
    Known cards additionally require a caller-supplied approved immutable
    recognizer/profile tuple and a stable deal identity.
    Any explicitly supplied identity is validated even for UNKNOWN-only input.
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
    version = result.get("recognizer_version")
    if not isinstance(version, str):
        raise CardRecognitionContractError("invalid recognizer_version type")
    if not _VERSION.fullmatch(version):
        raise CardRecognitionContractError("invalid recognizer_version")
    if result.get("status") not in ALLOWED_STATUSES:
        raise CardRecognitionContractError("unsupported recognition status")
    if _sequence(result.get("suit_order"), "suit_order") != list(SUITS):
        raise CardRecognitionContractError("suit order must be H,C,D,S")
    profile_id = result.get("recognition_profile_id")
    if not isinstance(profile_id, str) or profile_id not in CONFIDENCE_GATES:
        raise CardRecognitionContractError("unsupported recognition confidence profile")
    minimum_confidence = CONFIDENCE_GATES[profile_id]
    logical = _mapping(result.get("logical_inference"), "logical_inference")
    if logical.get("requested") is not False or logical.get("performed") is not False:
        raise CardRecognitionContractError("logical card inference is prohibited")
    if (
        result.get("canonical_promotion_allowed") is not False
        or result.get("school_canon_write_performed") is not False
    ):
        raise CardRecognitionContractError("recognizer crossed its authority boundary")

    records = [
        _validate_record(item, index, version, minimum_confidence)
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
    deal_identity = None
    profile_verification_sha256 = None
    visual_anchor_gates = None
    if known_cards:
        _, _, profile_verification_sha256, visual_anchor_gates = _trusted_tuple(
            result, approved_recognizers
        )
    if "deal_identity" in result:
        raw_identity = _mapping(result["deal_identity"], "deal_identity")
        if str(raw_identity.get("kind") or "").upper() == "VISUAL_ANCHOR" and not known_cards:
            (
                _,
                _,
                profile_verification_sha256,
                visual_anchor_gates,
            ) = _trusted_tuple(result, approved_recognizers)
        deal_identity = _deal_identity(
            raw_identity, visual_anchor_gates=visual_anchor_gates
        )
    elif known_cards:
        raise CardRecognitionContractError("deal_identity is required for known cards")
    if result["status"] == "COMPLETE_VISUAL" and len(known_cards) != 52:
        raise CardRecognitionContractError(
            "COMPLETE_VISUAL requires 52 recognized cards and no UNKNOWN slots"
        )
    if result["status"] == "COMPLETE_VISUAL" and any(
        record["source"] != "TEMPORAL_CONSENSUS" for record in records
    ):
        raise CardRecognitionContractError(
            "COMPLETE_VISUAL requires independent temporal support for every card"
        )
    normalized = {
        "schema": CONTRACT_SCHEMA,
        "status": str(result["status"]),
        "recognizer_version": version,
        "recognition_profile_id": profile_id,
        "minimum_card_confidence": minimum_confidence,
        "cards": records,
        "known_card_count": len(known_cards),
        "unknown_slot_count": 52 - len(known_cards),
        "complete": len(known_cards) == 52 and result["status"] == "COMPLETE_VISUAL",
        "logical_inference_performed": False,
        "canonical_promotion_allowed": False,
    }
    if profile_verification_sha256 is not None:
        normalized["profile_verification_sha256"] = profile_verification_sha256
    if deal_identity is not None:
        normalized["deal_identity"] = deal_identity
    normalized["consumer_sha256"] = canonical_sha256(normalized)
    return normalized


def adapt_legacy_hands(hands: Any, *, recognizer_version: str = "legacy-video31") -> dict[str, Any]:
    """Preserve old H/C/D/S hand data without completing it from the deck."""

    if not _VERSION.fullmatch(recognizer_version):
        raise CardRecognitionContractError("invalid recognizer_version")
    source = _mapping(hands, "legacy hands")
    unsupported_seats = set(source) - set(SEATS)
    if unsupported_seats:
        raise CardRecognitionContractError("unsupported legacy seat key")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for seat in SEATS:
        hand = {} if seat not in source else _mapping(source[seat], f"legacy hands.{seat}")
        unsupported_suits = set(hand) - set(SUITS)
        if unsupported_suits:
            raise CardRecognitionContractError(f"unsupported legacy suit key for {seat}")
        known = 0
        for suit in SUITS:
            raw_ranks = "" if suit not in hand else hand[suit]
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
                        "source": "LEGACY_UNVERIFIED",
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
