from bridge_contracts.video_extended_extraction import build_extended_extraction


def test_rule_without_why_creates_explanation_gap():
    quality = {
        "canon_candidates": [{
            "canon_observation_id": "rule-7",
            "classification": "RULE_PARAPHRASE_MATCH",
            "evidence_refs": ["segment-7"],
        }],
        "authority": {"canon_activation": "DENY"},
    }
    result = build_extended_extraction({"job_id": "job-1"}, quality)
    gaps = [
        row for row in result["candidate_records"]
        if row["candidate_type"] == "GAP_OR_CONFLICT"
        and row["payload"].get("gap_type") == "EXPLANATION_MISSING"
    ]
    assert len(gaps) == 1
    assert gaps[0]["payload"]["rule_stable_key"] == "rule-7"
    assert gaps[0]["promotion_allowed"] is False


def test_source_bound_why_is_a_separate_explanation_candidate():
    master = {
        "job_id": "job-1",
        "explanation_observations": [{
            "stable_key": "why:rule-7",
            "rule_stable_key": "rule-7",
            "why_chain": ["premise", "conclusion"],
            "rejected_alternatives": [{"action": "PASS", "reason": "forcing"}],
            "example": {"auction": ["1H", "2C"]},
            "counterexample": {"auction": ["1H", "X"]},
            "evidence_refs": ["segment-7"],
            "status": "REVIEW_REQUIRED",
        }],
    }
    quality = {
        "canon_candidates": [{
            "canon_observation_id": "rule-7",
            "classification": "RULE_PARAPHRASE_MATCH",
            "evidence_refs": ["segment-7"],
        }],
        "authority": {"canon_activation": "DENY"},
    }
    result = build_extended_extraction(master, quality)
    assert sum(row["candidate_type"] == "EXPLANATION_CANDIDATE" for row in result["candidate_records"]) == 1
    assert not any(
        row["payload"].get("gap_type") == "EXPLANATION_MISSING"
        for row in result["candidate_records"]
    )
