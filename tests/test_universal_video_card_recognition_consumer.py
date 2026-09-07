from __future__ import annotations

import copy

import pytest

from universal_video.card_recognition_consumer import (
    CONTRACT_SCHEMA,
    CardRecognitionContractError,
    adapt_legacy_hands,
    canonical_sha256,
    validate_recognition_result,
)


def _unknown(seat: str, slot: int) -> dict:
    return {
        "seat": seat,
        "suit": None,
        "rank": None,
        "source": "UNKNOWN",
        "frame_sha256": None,
        "confidence": 0.0,
        "recognizer_version": "recognizer-v1",
        "unknown_slot": slot,
    }


def _envelope() -> dict:
    result = {
        "schema": "bridge-video-deal-evidence/v1",
        "status": "PARTIAL_VISUAL",
        "recognizer_version": "recognizer-v1",
        "suit_order": ["H", "C", "D", "S"],
        "card_records": [_unknown(seat, slot) for seat in "NESW" for slot in range(1, 14)],
        "logical_inference": {"requested": False, "performed": False},
        "canonical_promotion_allowed": False,
        "school_canon_write_performed": False,
    }
    return {
        "schema": CONTRACT_SCHEMA,
        "result": result,
        "result_sha256": canonical_sha256(result),
    }


def _rehash(envelope: dict) -> None:
    envelope["result_sha256"] = canonical_sha256(envelope["result"])


def test_accepts_hash_bound_partial_result_and_preserves_unknowns() -> None:
    envelope = _envelope()
    record = envelope["result"]["card_records"][0]
    record.update(
        suit="S",
        rank="A",
        source="TEMPORAL_CONSENSUS",
        frame_sha256="a" * 64,
        confidence=0.97,
    )
    record.pop("unknown_slot")
    _rehash(envelope)

    consumed = validate_recognition_result(envelope)

    assert consumed["known_card_count"] == 1
    assert consumed["unknown_slot_count"] == 51
    assert consumed["complete"] is False
    assert consumed["logical_inference_performed"] is False


def test_rejects_changed_result_after_hashing() -> None:
    envelope = _envelope()
    envelope["result"]["status"] = "COMPLETE_VISUAL"
    with pytest.raises(CardRecognitionContractError, match="hash mismatch"):
        validate_recognition_result(envelope)


def test_rejects_complete_status_with_unknown_slots() -> None:
    envelope = _envelope()
    envelope["result"]["status"] = "COMPLETE_VISUAL"
    _rehash(envelope)
    with pytest.raises(CardRecognitionContractError, match="requires 52 recognized cards"):
        validate_recognition_result(envelope)


@pytest.mark.parametrize("confidence", [True, -0.01, 1.01, float("nan"), float("inf")])
def test_rejects_invalid_confidence(confidence) -> None:
    envelope = _envelope()
    record = envelope["result"]["card_records"][0]
    record.update(
        suit="H",
        rank="A",
        source="VISUAL",
        frame_sha256="b" * 64,
        confidence=confidence,
    )
    record.pop("unknown_slot")
    _rehash(envelope)
    with pytest.raises(CardRecognitionContractError, match="confidence"):
        validate_recognition_result(envelope)


def test_rejects_duplicate_card_across_seats() -> None:
    envelope = _envelope()
    for ordinal, index in enumerate((0, 13), start=1):
        record = envelope["result"]["card_records"][index]
        record.update(
            suit="D",
            rank="K",
            source="VISUAL",
            frame_sha256=str(ordinal) * 64,
            confidence=0.9,
        )
        record.pop("unknown_slot")
    _rehash(envelope)
    with pytest.raises(CardRecognitionContractError, match="duplicate recognized card"):
        validate_recognition_result(envelope)


def test_rejects_more_than_thirteen_slots_for_one_seat() -> None:
    envelope = _envelope()
    envelope["result"]["card_records"][13]["seat"] = "N"
    _rehash(envelope)
    with pytest.raises(CardRecognitionContractError, match="exactly 13 slots"):
        validate_recognition_result(envelope)


def test_rejects_logical_inference_even_when_hash_is_valid() -> None:
    envelope = _envelope()
    envelope["result"]["logical_inference"]["performed"] = True
    _rehash(envelope)
    with pytest.raises(CardRecognitionContractError, match="inference is prohibited"):
        validate_recognition_result(envelope)


def test_rejects_teacher_pointer_as_card_source() -> None:
    envelope = _envelope()
    record = envelope["result"]["card_records"][0]
    record.update(
        suit="C",
        rank="Q",
        source="TEACHER_POINTER",
        frame_sha256="c" * 64,
        confidence=1.0,
    )
    record.pop("unknown_slot")
    _rehash(envelope)
    with pytest.raises(CardRecognitionContractError, match="provenance"):
        validate_recognition_result(envelope)


@pytest.mark.parametrize("rank", [10, 2])
def test_rejects_non_string_recognizer_rank(rank) -> None:
    envelope = _envelope()
    record = envelope["result"]["card_records"][0]
    record.update(
        suit="S",
        rank=rank,
        source="VISUAL",
        frame_sha256="d" * 64,
        confidence=0.9,
    )
    record.pop("unknown_slot")
    _rehash(envelope)
    with pytest.raises(CardRecognitionContractError, match="card types"):
        validate_recognition_result(envelope)


def test_legacy_adapter_does_not_mutate_input_or_complete_fourth_hand() -> None:
    hands = {
        "N": {"H": "AKQ", "C": "", "D": "", "S": ""},
        "E": {"H": "", "C": "JT", "D": "", "S": ""},
        "S": {"H": "", "C": "", "D": "9", "S": ""},
        "W": {"H": "", "C": "", "D": "", "S": ""},
    }
    original = copy.deepcopy(hands)

    adapted = adapt_legacy_hands(hands)

    assert hands == original
    assert adapted["known_card_count"] == 6
    assert adapted["unknown_slot_count"] == 46
    assert adapted["complete"] is False
    assert sum(card["source"] == "UNKNOWN" for card in adapted["cards"] if card["seat"] == "W") == 13


def test_legacy_adapter_rejects_duplicate_and_hand_overflow() -> None:
    duplicate = {seat: {suit: "" for suit in "HCDS"} for seat in "NESW"}
    duplicate["N"]["S"] = "A"
    duplicate["E"]["S"] = "A"
    with pytest.raises(CardRecognitionContractError, match="duplicate legacy card"):
        adapt_legacy_hands(duplicate)

    overflow = {seat: {suit: "" for suit in "HCDS"} for seat in "NESW"}
    overflow["N"]["H"] = "AKQJT98765432"
    overflow["N"]["S"] = "A"
    with pytest.raises(CardRecognitionContractError, match="exceeds 13 cards"):
        adapt_legacy_hands(overflow)


def test_legacy_adapter_rejects_non_string_ranks() -> None:
    hands = {seat: {suit: "" for suit in "HCDS"} for seat in "NESW"}
    hands["N"]["S"] = 10
    with pytest.raises(CardRecognitionContractError, match="legacy hands.N.S"):
        adapt_legacy_hands(hands)
