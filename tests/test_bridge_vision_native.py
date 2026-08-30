from pathlib import Path

import pytest

from bridge_contracts.video_deal import BridgeVideoDealContractError
from bridge_vision import BridgeVisionEngine


THREE_COMPLETE_HANDS = {
    "N": [f"{rank}S" for rank in "AKQJT98765432"],
    "E": [f"{rank}H" for rank in "AKQJT98765432"],
    "S": [f"{rank}D" for rank in "AKQJT98765432"],
}


def test_native_engine_fuses_non_conflicting_observations_without_inference():
    engine = BridgeVisionEngine(
        {
            "north-detector": lambda _: {"hands": {"N": ["AS", "KH"]}, "confidence": 0.9, "evidence": {"roi": "north"}},
            "south-detector": lambda _: {"hands": {"S": ["QD", "JC"]}, "confidence": 0.8, "evidence": {"roi": "south"}},
        }
    )
    out = engine.analyze_frame(Path("frame.jpg")).to_dict()
    assert out["status"] == "PARTIAL_BOARD_OBSERVATION"
    assert out["deal"]["hands"]["N"]["cards"] == ["AS", "KH"]
    assert out["deal"]["hands"]["S"]["cards"] == ["QD", "JC"]
    assert out["deal"]["hands"]["E"]["cards"] == []
    assert out["deal"]["hands"]["W"]["cards"] == []
    assert out["deal"]["derivations"] == []


def test_native_engine_fails_closed_on_cross_seat_conflict():
    engine = BridgeVisionEngine(
        {
            "a": lambda _: {"hands": {"N": ["AS"]}, "confidence": 0.9},
            "b": lambda _: {"hands": {"E": ["AS"]}, "confidence": 0.9},
        }
    )
    out = engine.analyze_frame(Path("frame.jpg")).to_dict()
    assert out["status"] == "CONFLICT"
    assert out["deal"] is None
    assert out["conflicts"][0]["card"] == "AS"


def test_low_confidence_observations_do_not_become_facts():
    engine = BridgeVisionEngine(
        {"weak": lambda _: {"hands": {"N": ["AS", "KS", "QS", "JS"]}, "confidence": 0.2}},
        min_confidence=0.6,
    )
    out = engine.analyze_frame(Path("frame.jpg")).to_dict()
    assert out["status"] == "UNAVAILABLE"
    assert out["deal"] is None


def test_no_detector_means_unknown_not_legacy_fallback():
    out = BridgeVisionEngine().analyze_frame(Path("frame.jpg")).to_dict()
    assert out["status"] == "UNAVAILABLE"
    assert out["candidates"] == []


def test_three_complete_recognized_hands_derive_the_fourth_with_provenance():
    engine = BridgeVisionEngine(
        {"generic-card-detector": lambda _: {"hands": THREE_COMPLETE_HANDS, "confidence": 0.93}}
    )
    out = engine.analyze_frame(Path("frame.jpg")).to_dict()

    assert out["status"] == "PARTIAL_BOARD_OBSERVATION"
    assert out["deal"]["hands"]["W"]["unknown_count"] == 0
    assert len(out["deal"]["hands"]["W"]["cards"]) == 13
    derivation = out["deal"]["derivations"][0]
    assert derivation["provenance_class"] == "DERIVED"
    assert derivation["evidence_basis"] == "39_unique_cards_in_three_complete_observed_hands"
    assert derivation["confidence"] == {
        "logical_complement": 1.0,
        "source_observation_floor": 0.93,
    }


def test_38_cards_do_not_derive_but_one_exposed_play_card_allows_exact_40_card_reconstruction():
    incomplete = {seat: list(cards) for seat, cards in THREE_COMPLETE_HANDS.items()}
    incomplete["S"].pop()
    out_38 = BridgeVisionEngine(
        {"generic-card-detector": lambda _: {"hands": incomplete, "confidence": 0.99}}
    ).analyze_frame(Path("frame.jpg")).to_dict()
    assert out_38["deal"]["derivations"] == []
    assert out_38["deal"]["hands"]["W"]["unknown_count"] == 13

    with_partial_fourth = {seat: list(cards) for seat, cards in THREE_COMPLETE_HANDS.items()}
    # The hidden fourth hand has played AC, so that one card is now observed.
    with_partial_fourth["W"] = ["AC"]
    out_40 = BridgeVisionEngine(
        {"generic-card-detector": lambda _: {"hands": with_partial_fourth, "confidence": 0.97}}
    ).analyze_frame(Path("frame.jpg")).to_dict()
    derivation = out_40["deal"]["derivations"][0]
    assert derivation["observed_cards_preserved"] == ["AC"]
    assert len(derivation["computed_cards"]) == 12
    assert len(out_40["deal"]["hands"]["W"]["cards"]) == 13
    assert out_40["deal"]["card_provenance"]["W"]["observed_cards"] == ["AC"]
    assert len(out_40["deal"]["card_provenance"]["W"]["derived_cards"]) == 12


def test_conflicting_partial_fourth_hand_fails_closed():
    conflicting = {seat: list(cards) for seat, cards in THREE_COMPLETE_HANDS.items()}
    conflicting["W"] = ["AS"]
    engine = BridgeVisionEngine(
        {"generic-card-detector": lambda _: {"hands": conflicting, "confidence": 0.99}}
    )
    with pytest.raises(BridgeVideoDealContractError, match="appears in both"):
        engine.analyze_frame(Path("frame.jpg"))
