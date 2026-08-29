import pytest

from bridge_vision.card_eval import evaluate_frame, summarize_reports


SHA_A = "a" * 64
SHA_B = "b" * 64


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
