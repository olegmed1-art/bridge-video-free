import json
from pathlib import Path

import pytest

from bridge_vision.card_eval import evaluate_frame, summarize_reports


SHA_A = "a" * 64
SHA_B = "b" * 64
CORPUS = Path(__file__).with_name("fixtures") / "diana14_card_frames_v1.json"


def test_real_frame_corpus_is_hash_bound_complete_and_shadow_only():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert corpus["schema"] == "bridge-real-card-frame-corpus-v1"
    assert corpus["coordinate_system"] == "SCREEN_TOP_N_RIGHT_E_BOTTOM_S_LEFT_W"
    assert corpus["inference_allowed"] is False
    assert corpus["result_scope"] == "SHADOW_ONLY"
    assert corpus["canonical_promotion_allowed"] is False

    frames = corpus["frames"]
    assert [row["label_status"] for row in frames] == ["GOLD_VISIBLE", "AMBIGUOUS", "NEGATIVE"]
    assert len({row["sha256"] for row in frames}) == len(frames)
    assert all(len(row["sha256"]) == 64 for row in frames)

    gold = frames[0]
    pairs = {(seat, card) for seat, cards in gold["hands"].items() for card in cards}
    cards = {card for _, card in pairs}
    assert set(gold["hands"]) == {"N", "E", "S", "W"}
    assert all(len(cards) == 13 for cards in gold["hands"].values())
    assert len(pairs) == len(cards) == 52


def test_real_frame_gold_labels_are_accepted_by_canonical_contract():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    gold = corpus["frames"][0]
    report = evaluate_frame(
        frame_sha256=gold["sha256"],
        expected_hands=gold["hands"],
        detector_result={"hands": {}},
    )
    assert report["counts"] == {"tp": 0, "fp": 0, "fn": 52, "ambiguous": 0}
    assert report["status"] == "REVIEW"


def test_report_counts_exact_pairs_and_never_completes_missing_hand():
    report = evaluate_frame(
        frame_sha256=SHA_A,
        expected_hands={"N": ["AS", "KH"], "E": ["QC"]},
        detector_result={
            "hands": {"N": ["AS"], "E": ["KH", "JD"]},
            "evidence": {
                "rejected": [
                    {"index": 3, "reason": "LOW_SUIT_CONFIDENCE", "rank": "Q"},
                    {"index": 4, "reason": "DUPLICATE_OBSERVATION", "card": "AS"},
                ]
            },
        },
    )
    assert report["counts"] == {"tp": 1, "fp": 2, "fn": 2, "ambiguous": 1}
    assert report["true_positives"] == [{"seat": "N", "card": "AS"}]
    assert {tuple(row.values()) for row in report["false_negatives"]} == {("N", "KH"), ("E", "QC")}
    assert report["seat_errors"] == [{"card": "KH", "expected_seat": "N", "predicted_seat": "E"}]
    assert report["canonical_promotion_allowed"] is False
    assert report["result_scope"] == "SHADOW_ONLY"


def test_duplicate_or_invalid_report_inputs_fail_closed():
    report = evaluate_frame(frame_sha256=SHA_A, expected_hands={}, detector_result={"hands": {}})
    with pytest.raises(ValueError, match="duplicate frame"):
        summarize_reports([report, report])
    with pytest.raises(ValueError, match="sha256"):
        evaluate_frame(frame_sha256="not-a-hash", expected_hands={}, detector_result={"hands": {}})


def test_summary_is_deterministic_and_includes_ambiguous():
    one = evaluate_frame(
        frame_sha256=SHA_A,
        expected_hands={"S": ["TH"]},
        detector_result={"hands": {"S": ["TH"]}},
    )
    two = evaluate_frame(
        frame_sha256=SHA_B,
        expected_hands={"W": ["2C"]},
        detector_result={
            "hands": {},
            "evidence": {"pending": [{"reason": "AMBIGUOUS_GLYPH", "candidates": ["2C", "2S"]}]},
        },
    )
    summary = summarize_reports([one, two])
    assert summary["counts"] == {"tp": 1, "fp": 0, "fn": 1, "ambiguous": 1, "seat_errors": 0}
    assert summary["precision"] == 1.0
    assert summary["recall"] == 0.5
    assert summary["status"] == "SHADOW_REVIEW"
