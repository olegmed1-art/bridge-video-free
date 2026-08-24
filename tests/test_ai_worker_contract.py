import os

import bridge_ai_compute_worker as worker
from bridge_school_api.ai_worker import SearchCompletion


def test_missing_credentials_fail_closed():
    old_base = os.environ.pop("BRIDGE_API_BASE_URL", None)
    old_token = os.environ.pop("BRIDGE_API_TOKEN", None)
    try:
        try:
            worker.load_config()
        except RuntimeError:
            pass
        else:
            raise AssertionError("worker accepted missing credentials")
    finally:
        if old_base is not None:
            os.environ["BRIDGE_API_BASE_URL"] = old_base
        if old_token is not None:
            os.environ["BRIDGE_API_TOKEN"] = old_token


def test_no_engine_fails_closed():
    config = worker.Config("https://example.invalid", "token", None, None, 5.0)
    try:
        worker.choose_engine(config, {"position": {}})
    except RuntimeError:
        pass
    else:
        raise AssertionError("worker fabricated a result without an engine")


def test_ben_context_normalization():
    assert worker._ben_context("1H – 1S") == "1H1S"
    assert worker._ben_context("1NT PASS 2C X") == "1N--2CDb"
    assert worker._ben_context(["PASS", "PASS"]) == "----"


def test_policy_score_is_not_promoted_to_search_ev():
    job = {"candidates": [{"candidate_id": "c1", "action": "2H"}]}
    result = {"bid": "2H", "candidates": [{"call": "2H", "insta_score": 0.61}]}
    assert worker.search_evaluations(job, "ben", result) == []
    teacher = worker.teacher_payload("ben", result)
    assert teacher["action"] == "2H"
    assert teacher["candidate_scores"]["2H"] == 0.61
    policy = worker.policy_payload("ben", teacher)
    assert policy["distribution"]["2H"] == 0.61
    assert policy["model_version"] == "NOT_SPECIFIED"


def test_explicit_ben_simulation_metrics_become_search_evidence():
    job = {"candidates": [{"candidate_id": "c1", "action": "2H"}]}
    result = {
        "bid": "2H",
        "candidates": [{
            "call": "2H",
            "insta_score": 0.61,
            "expected_score_sd": 118,
            "expected_tricks_sd": 8.4,
            "p_make_contract": 0.73,
        }],
    }
    evaluations = worker.search_evaluations(job, "ben", result)
    assert len(evaluations) == 1
    assert evaluations[0]["raw_score_ev"] == 118
    assert evaluations[0]["make_probability"] == 0.73
    assert evaluations[0]["metrics_json"]["evidence_class"] == "BEN_SIMULATION"


def test_ben_policy_only_is_terminal_without_fabricated_search_evidence():
    completion = SearchCompletion(status="NO_SEARCH_EVIDENCE", evaluations=[])
    assert completion.status == "NO_SEARCH_EVIDENCE"
    assert completion.evaluations == []
