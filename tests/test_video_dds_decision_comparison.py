import pytest

from bridge_contracts.video_dds_decision_comparison import (
    VideoDDSComparisonError,
    build_offline_dds_comparison,
)
from bridge_school_api.dds3.service import DDS_UPSTREAM
from bridge_contracts.video_extended_extraction import build_extended_extraction


def _observation():
    return {
        "decision": {
            "decision_id": "play-7", "domain": "PLAY", "selected_action": "SA",
            "logic_candidate_id": "why:rule-7:segment-7",
            "public_context": {"auction": ["1NT", "3NT"], "played_cards": []},
            "evidence_refs": ["segment-7"],
        },
        "full_deal_evidence": {
            "deal_pbn_sha256": "a" * 64, "source_refs": ["frame-52"],
            "verified_full_board": True,
        },
        "dds_result": {
            "engine": "DDS3", "engine_version": DDS_UPSTREAM, "fallback_used": False,
            "operation": "dd_table", "input_validated": True,
            "hand_order": ["N", "E", "S", "W"], "strain_order": ["S", "H", "D", "C", "NT"],
            "deal_pbn_sha256": "a" * 64, "request_sha256": "b" * 64,
        },
        "alternatives": [
            {"action": "SA", "metric": "tricks", "value": 10, "selected": True},
            {"action": "SK", "metric": "tricks", "value": 9, "selected": False},
        ],
    }


def test_binds_logic_to_dds_offline_without_hidden_cards():
    result = build_offline_dds_comparison(_observation())
    assert result["offline_only"] is True
    assert result["live_resolver_input_allowed"] is False
    assert result["canon_evidence_allowed"] is False
    assert "deal_pbn_sha256" not in result["full_deal_evidence"]
    assert len(result["comparison_sha256"]) == 64


@pytest.mark.parametrize("mutate, match", [
    (lambda value: value["decision"]["public_context"].update(partner_hand="AKQ"), "hidden"),
    (lambda value: value["full_deal_evidence"].update(verified_full_board=False), "verified full board"),
    (lambda value: value["dds_result"].update(fallback_used=True), "fallback"),
    (lambda value: value["alternatives"][0].update(selected=False), "exactly one"),
])
def test_fails_closed_on_leak_or_unproven_dds(mutate, match):
    value = _observation()
    mutate(value)
    with pytest.raises(VideoDDSComparisonError, match=match):
        build_offline_dds_comparison(value)


def test_extended_analysis_stages_valid_dds_comparison_and_gaps_invalid_one():
    master = {"job_id": "job-dds", "dds_decision_evaluations": [_observation()]}
    quality = {"authority": {"canon_activation": "DENY"}}
    result = build_extended_extraction(master, quality)
    assert any(row["candidate_type"] == "DDS_DECISION_COMPARISON" for row in result["candidate_records"])
    bad = _observation(); bad["dds_result"]["fallback_used"] = True
    result = build_extended_extraction({"job_id": "job-dds", "dds_decision_evaluations": [bad]}, quality)
    assert any(row["payload"].get("gap_type") == "DDS_DECISION_EVALUATION_INVALID" for row in result["candidate_records"])
