from decimal import Decimal

from bridge_school_api.ai_decision import (
    POLICY_MIN_MARGIN,
    _forced_choice,
    _metric_for_scoring,
    _policy_choice,
    _search_choice,
)


def test_scoring_metric_is_explicit():
    assert _metric_for_scoring("imps") == ("imp_ev", "IMP_EV")
    assert _metric_for_scoring("mp") == ("mp_ev", "MP_EV")
    assert _metric_for_scoring("raw") == ("raw_score_ev", "RAW_SCORE_EV")


def test_search_requires_two_real_evaluations():
    one = [{
        "search_run_id": "r1",
        "action": "2H",
        "imp_ev": 1.2,
        "robustness": 0.8,
        "sample_quality": 0.9,
        "effective_sample_size": 100,
    }]
    assert _search_choice(one, "imps") is None


def test_search_selects_best_matching_scoring_metric():
    rows = [
        {"search_run_id": "r1", "action": "2H", "imp_ev": 1.3, "mp_ev": 0.44, "robustness": 0.82, "sample_quality": 0.9, "effective_sample_size": 100},
        {"search_run_id": "r1", "action": "3H", "imp_ev": 0.8, "mp_ev": 0.61, "robustness": 0.70, "sample_quality": 0.9, "effective_sample_size": 100},
    ]
    imp_choice = _search_choice(rows, "imps")
    mp_choice = _search_choice(rows, "mp")
    assert imp_choice["chosen_action"] == "2H"
    assert mp_choice["chosen_action"] == "3H"
    assert imp_choice["confidence"] == Decimal("0.82")


def test_search_tie_does_not_finalize():
    rows = [
        {"search_run_id": "r1", "action": "2H", "imp_ev": 1.0, "robustness": None, "sample_quality": None, "effective_sample_size": None},
        {"search_run_id": "r1", "action": "3H", "imp_ev": 1.0, "robustness": None, "sample_quality": None, "effective_sample_size": None},
    ]
    assert _search_choice(rows, "imps") is None


def test_policy_requires_margin_and_allowed_candidate():
    candidates = [
        {"candidate_id": "c1", "action": "2H", "legal": True, "system_compatible": True, "hard_rule_status": None},
        {"candidate_id": "c2", "action": "3H", "legal": True, "system_compatible": True, "hard_rule_status": None},
    ]
    weak = {
        "policy_run_id": "p1", "model_key": "test", "model_version": "v1",
        "distribution_json": {"2H": 0.52, "3H": 0.48}, "top_action": "2H",
        "margin": str(POLICY_MIN_MARGIN - Decimal("0.01")), "entropy": None,
    }
    strong = dict(weak, margin=str(POLICY_MIN_MARGIN), distribution_json={"2H": 0.70, "3H": 0.30})
    assert _policy_choice(weak, candidates) is None
    assert _policy_choice(strong, candidates)["chosen_action"] == "2H"


def test_policy_cannot_choose_vetoed_candidate():
    policy = {
        "policy_run_id": "p1", "model_key": "test", "model_version": "v1",
        "distribution_json": {"2H": 0.9, "PASS": 0.1}, "top_action": "2H",
        "margin": 0.8, "entropy": None,
    }
    candidates = [
        {"candidate_id": "c1", "action": "2H", "legal": True, "system_compatible": True, "hard_rule_status": "VETO"},
        {"candidate_id": "c2", "action": "PASS", "legal": True, "system_compatible": True, "hard_rule_status": None},
    ]
    assert _policy_choice(policy, candidates) is None


def test_exactly_one_forced_candidate_wins_before_models():
    candidates = [
        {"candidate_id": "c1", "action": "2D", "legal": True, "system_compatible": True, "hard_rule_status": "FORCED"},
        {"candidate_id": "c2", "action": "PASS", "legal": True, "system_compatible": True, "hard_rule_status": None},
    ]
    choice = _forced_choice(candidates)
    assert choice["chosen_action"] == "2D"
    assert choice["decision_path"] == "HARD_RULE"
