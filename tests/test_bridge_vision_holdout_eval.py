import pytest
from bridge_vision.holdout_eval import evaluate_labelled_outcomes


def test_holdout_pass_requires_zero_wrong_and_nonzero_coverage():
    r = evaluate_labelled_outcomes([
        {"gold": "A", "predicted": "A"},
        {"gold": "K", "predicted": None, "reason": "AMBIGUOUS_GLYPH"},
        {"gold": "Q", "predicted": None, "reason": "LOW_GLYPH_SCORE"},
    ])
    assert r["status"] == "PASS"
    assert r["accepted_precision"] == 1.0
    assert r["coverage"] == pytest.approx(1 / 3)


def test_one_accepted_wrong_fails_and_is_reported_in_confusion():
    r = evaluate_labelled_outcomes([{"gold": "H", "predicted": "D"}])
    assert r["status"] == "FAIL"
    assert r["counts"]["accepted_wrong"] == 1
    assert r["confusion"] == {"H": {"D": 1}}


def test_all_rejected_fails_useful_coverage_gate():
    r = evaluate_labelled_outcomes([{"gold": "A", "predicted": None, "reason": "LOW_GLYPH_SCORE"}])
    assert r["status"] == "FAIL"
    assert r["coverage"] == 0.0


def test_missing_human_gold_is_never_inferred():
    with pytest.raises(ValueError, match="gold label must be explicit"):
        evaluate_labelled_outcomes([{"predicted": "A"}])
