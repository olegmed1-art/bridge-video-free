import os

import bridge_ai_compute_worker as worker


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


def test_ben_policy_only_status_does_not_claim_search_completion():
    assert worker.BEN_POLICY_ONLY_STATUS == "NO_SEARCH_EVIDENCE"


def test_ben_response_contract_rejects_missing_policy_evidence():
    for payload in ({}, {"bid": "1S"}, {"bid": "1S", "candidates": []}):
        try:
            worker._validate_ben_result(payload)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"worker accepted invalid BEN payload: {payload!r}")


def test_ben_response_contract_accepts_real_shape():
    payload = {
        "bid": "1S",
        "candidates": [{"call": "1S", "insta_score": 1.168}],
    }
    assert worker._validate_ben_result(payload) is payload


def test_ben_retries_transient_network_error(monkeypatch):
    calls = []
    payload = {"bid": "PASS", "candidates": [{"call": "PASS", "insta_score": 0.5}]}

    def fake_request(url):
        calls.append(url)
        if len(calls) == 1:
            raise worker.urllib.error.URLError("temporary")
        return payload

    monkeypatch.setattr(worker, "request_json", fake_request)
    config = worker.Config("https://api.invalid", "token", "https://ben.invalid", None, 5.0, 2, 0.0)
    assert worker._ben_request(config, "https://ben.invalid/bid") == payload
    assert len(calls) == 2


def test_ben_does_not_retry_invalid_contract(monkeypatch):
    calls = []

    def fake_request(url):
        calls.append(url)
        return {}

    monkeypatch.setattr(worker, "request_json", fake_request)
    config = worker.Config("https://api.invalid", "token", "https://ben.invalid", None, 5.0, 3, 0.0)
    try:
        worker._ben_request(config, "https://ben.invalid/bid")
    except RuntimeError:
        pass
    else:
        raise AssertionError("worker accepted invalid BEN contract")
    assert len(calls) == 1


def test_ben_rejects_selected_bid_missing_from_candidates():
    payload = {
        "bid": "2H",
        "candidates": [{"call": "PASS", "insta_score": 0.4}],
    }
    try:
        worker._validate_ben_result(payload)
    except RuntimeError:
        pass
    else:
        raise AssertionError("worker accepted a BEN bid absent from candidates")
