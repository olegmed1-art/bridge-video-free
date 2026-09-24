from pathlib import Path
from unittest.mock import patch

import pytest

from bridge_vision.gold import evaluate_card_detector, passes_card_gold_gate
from bridge_vision.native_cards import (
    NativeCardDetectorError,
    NativeFourSeatCardDetector,
    observations_from_backend,
)


def payload(cards):
    return {
        "table_region": {"x": 0, "y": 0, "w": 1000, "h": 1000},
        "cards": cards,
    }


def card(card, x, y, confidence=0.99):
    return {"card": card, "confidence": confidence, "box": {"x": x, "y": y, "w": 20, "h": 20}}


def test_four_seat_assignment_is_geometry_based():
    hands, evidence = observations_from_backend(payload([
        card("AS", 490, 50),
        card("KH", 900, 490),
        card("QD", 490, 900),
        card("JC", 50, 490),
    ]))
    assert hands == {"N": ["AS"], "E": ["KH"], "S": ["QD"], "W": ["JC"]}
    assert len(evidence["accepted"]) == 4


def test_low_confidence_and_center_dead_zone_remain_unknown():
    hands, evidence = observations_from_backend(payload([
        card("AS", 490, 490),
        card("KH", 900, 490, confidence=0.30),
    ]))
    assert hands == {}
    assert {item["reason"] for item in evidence["rejected"]} == {"AMBIGUOUS_SEAT", "LOW_CONFIDENCE"}


def test_cross_seat_duplicate_fails_closed():
    with pytest.raises(NativeCardDetectorError, match="assigned to both"):
        observations_from_backend(payload([
            card("AS", 490, 50),
            card("AS", 900, 490),
        ]))


def test_invalid_card_is_rejected_without_promoting_it_to_evidence():
    hands, evidence = observations_from_backend(payload([card("not-a-card", 490, 50)]))
    assert hands == {}
    assert evidence["accepted"] == []
    assert evidence["rejected"] == [{"index": 0, "reason": "INVALID_CARD"}]


def test_unexpected_normalization_failure_is_not_hidden_as_invalid_card():
    with patch("bridge_vision.native_cards.canonicalize_video_deal", side_effect=RuntimeError("broken normalizer")):
        with pytest.raises(RuntimeError, match="broken normalizer"):
            observations_from_backend(payload([card("AS", 490, 50)]))


def test_detector_returns_bridge_vision_candidate_shape():
    detector = NativeFourSeatCardDetector(lambda _: payload([
        card("AS", 490, 50, 0.97),
        card("KS", 490, 70, 0.96),
    ]))
    result = detector(Path("frame.jpg"))
    assert result["hands"] == {"N": ["AS", "KS"]}
    assert result["confidence"] == 0.96
    assert result["evidence"]["detector_version"] == "bridge-native-cards-v1"


def test_gold_gate_requires_zero_seat_errors_and_high_precision_recall():
    detector = NativeFourSeatCardDetector(lambda _: payload([
        card("AS", 490, 50), card("KH", 900, 490), card("QD", 490, 900), card("JC", 50, 490)
    ]))
    metrics = evaluate_card_detector(detector, [{
        "frame": "f.jpg",
        "hands": {"N": ["AS"], "E": ["KH"], "S": ["QD"], "W": ["JC"]},
    }])
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.seat_errors == 0
    assert passes_card_gold_gate(metrics)
