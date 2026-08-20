from bridge_school_api.ai_policy import _rank_distribution


def test_policy_distribution_ranking_and_margin():
    top, margin = _rank_distribution({"1S": 0.72, "PASS": 0.18, "1N": 0.10})
    assert top == "1S"
    assert str(margin) == "0.54"


def test_policy_single_candidate_has_no_fabricated_margin():
    top, margin = _rank_distribution({"1S": 0.998})
    assert top == "1S"
    assert margin is None


def test_policy_ignores_non_numeric_scores():
    top, margin = _rank_distribution({"1S": "bad", "PASS": 0.2, "1N": 0.1})
    assert top == "PASS"
    assert str(margin) == "0.1"
