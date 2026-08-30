import pytest

from bridge_vision.card_eval import evaluate_frame, summarize_reports

SHA = "c" * 64


def test_exact_card_and_seat_tp_fp_fn_are_reported():
    report = evaluate_frame(
        frame_sha256=SHA,
        expected_hands={"N": ["AS", "KH"], "E": ["QD"]},
        detector_result={
            "hands": {"N": ["AS"], "E": ["KH", "JC"]},
            "evidence": {"rejected": [
                {"reason": "AMBIGUOUS_SUIT_SHAPE"}, {"reason": "LOW_RANK_CONFIDENCE"},
            ]},
        },
    )
    assert report["counts"]["tp"] == 1
    assert report["counts"]["fp"] == 2
    assert report["counts"]["fn"] == 2
    assert report["seat_errors"] == [{"card": "KH", "expected_seat": "N", "predicted_seat": "E"}]
    assert report["counts"]["rejected_ambiguous"] == 1
    assert report["counts"]["rejected_low_confidence"] == 1
    assert report["production_activation_allowed"] is False


def test_summary_enforces_go_thresholds_and_duplicate_frame_guard():
    perfect = evaluate_frame(
        frame_sha256=SHA, expected_hands={"S": ["AS"]},
        detector_result={"hands": {"S": ["AS"]}},
    )
    summary = summarize_reports([perfect])
    assert summary["status"] == "PASS"
    assert summary["quality_gate_passed"] is True
    assert summary["production_activation_allowed"] is False
    with pytest.raises(ValueError, match="duplicate"):
        summarize_reports([perfect, perfect])


def test_summary_is_inconclusive_without_holdout_and_fail_on_seat_error():
    assert summarize_reports([])["status"] == "INCONCLUSIVE"
    report = evaluate_frame(
        frame_sha256=SHA, expected_hands={"N": ["AS"]},
        detector_result={"hands": {"E": ["AS"]}},
    )
    summary = summarize_reports([report])
    assert summary["counts"]["seat_errors"] == 1
    assert summary["status"] == "FAIL"


def test_thresholds_cannot_be_lowered_to_manufacture_pass():
    with pytest.raises(ValueError, match="cannot be lowered"):
        summarize_reports([], min_precision=0.9)
