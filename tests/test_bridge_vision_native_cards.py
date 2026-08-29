from pathlib import Path

import pytest

from bridge_vision.gold import (
    evaluate_card_detector,
    evaluate_card_detector_report,
    evaluate_temporal_card_detector_report,
    passes_card_gold_gate,
)
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


def test_gold_report_exposes_tp_fp_fn_ambiguous_per_frame():
    def detector(_):
        return {
            "hands": {"N": ["AS", "KH"], "E": ["QD"]},
            "ambiguous": [{"candidates": ["JC", "JS"]}],
        }

    report = evaluate_card_detector_report(detector, [{
        "frame": "gold.jpg",
        "hands": {"N": ["AS", "KH", "QH"], "E": ["JC"]},
    }])
    assert report["frames"] == [{
        "frame": "gold.jpg",
        "tp": 2,
        "fp": 1,
        "fn": 2,
        "ambiguous": 1,
        "seat_errors": 0,
    }]
    assert report["totals"]["precision"] == pytest.approx(2 / 3)
    assert report["totals"]["recall"] == 0.5


def test_temporal_gold_report_does_not_charge_verified_play_as_fn():
    outputs = {
        "early.jpg": {"hands": {"S": ["AS", "KS", "QS"]}},
        "later.jpg": {"hands": {"S": ["KS", "QS"]}},
    }
    report = evaluate_temporal_card_detector_report(
        lambda frame: outputs[frame.name],
        [
            {
                "frame": "early.jpg",
                "frame_id": "early",
                "deal_key": "board-8",
                "hands": {"S": ["AS", "KS", "QS"]},
            },
            {
                "frame": "later.jpg",
                "frame_id": "later",
                "deal_key": "board-8",
                "hands": {"S": ["AS", "KS", "QS"]},
                "play_events": [{
                    "seat": "S",
                    "card": "AS",
                    "verified": True,
                    "evidence_locator": "later.jpg#center",
                }],
            },
        ],
    )
    assert report["frames"][1]["played_no_longer_visible"] == 1
    assert report["frames"][1]["visible_fn"] == 0
    assert report["totals"]["visible_recall"] == 1.0
