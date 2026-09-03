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
            "source_sha256": "c" * 64,
            "public_context": {"auction": ["1NT", "3NT"], "played_cards": []},
            "evidence_refs": ["segment-7"],
        },
        "full_deal_evidence": {
            "board_evidence_id": "board-proof-7",
            "deal_pbn_sha256": "a" * 64, "source_refs": ["frame-52"],
            "verified_full_board": True,
        },
        "dds_result": {
            "engine": "DDS3", "engine_version": DDS_UPSTREAM, "fallback_used": False,
            "operation": "position_all_moves",
            "deal_pbn_sha256": "a" * 64, "request_sha256": "b" * 64,
            "moves": [
                {"card": "SA", "tricks": 10, "regret": 0, "optimal": True},
                {"card": "SK", "tricks": 9, "regret": 1, "optimal": False},
            ],
        },
    }


def _board_evidence():
    return {
        "status": "VERIFIED_FULL_BOARD", "board_evidence_id": "board-proof-7",
        "deal_pbn_sha256": "a" * 64, "card_count": 52, "unique_card_count": 52,
        "source_refs": ["frame-52"], "evidence_sha256": "d" * 64,
    }


def _logic_evidence():
    return {
        "status": "SOURCE_BOUND", "logic_candidate_id": "why:rule-7:segment-7",
        "source_sha256": "c" * 64, "evidence_refs": ["segment-7"],
    }


def test_binds_logic_to_dds_offline_without_hidden_cards():
    result = build_offline_dds_comparison(_observation(), _board_evidence(), _logic_evidence())
    assert result["offline_only"] is True
    assert result["live_resolver_input_allowed"] is False
    assert result["canon_evidence_allowed"] is False
    assert "deal_pbn_sha256" not in result["full_deal_evidence"]
    assert len(result["comparison_sha256"]) == 64


@pytest.mark.parametrize("mutate, match", [
    (lambda value: value["decision"]["public_context"].update(partner_hand="AKQ"), "fields invalid"),
    (lambda value: value["full_deal_evidence"].update(verified_full_board=False), "verified full board"),
    (lambda value: value["dds_result"].update(fallback_used=True), "fallback"),
    (lambda value: value["dds_result"]["moves"].clear(), "no moves"),
])
def test_fails_closed_on_leak_or_unproven_dds(mutate, match):
    value = _observation()
    mutate(value)
    with pytest.raises(VideoDDSComparisonError, match=match):
        build_offline_dds_comparison(value, _board_evidence(), _logic_evidence())


def test_rejects_fabricated_alternatives_unverified_board_and_unresolved_logic():
    value = _observation()
    value["alternatives"] = [{"action": "H2", "value": 13}]
    with pytest.raises(VideoDDSComparisonError, match="fields mismatch"):
        build_offline_dds_comparison(value, _board_evidence(), _logic_evidence())

    board = _board_evidence(); board["unique_card_count"] = 51
    with pytest.raises(VideoDDSComparisonError, match="verified reconstruction"):
        build_offline_dds_comparison(_observation(), board, _logic_evidence())

    logic = _logic_evidence(); logic["logic_candidate_id"] = "why:missing"
    with pytest.raises(VideoDDSComparisonError, match="source-bound logic"):
        build_offline_dds_comparison(_observation(), _board_evidence(), logic)


def test_public_context_and_refs_are_allowlisted_against_hidden_card_payloads():
    value = _observation(); value["decision"]["public_context"]["notes"] = "N:AKQ..."
    with pytest.raises(VideoDDSComparisonError, match="fields invalid"):
        build_offline_dds_comparison(value, _board_evidence(), _logic_evidence())
    value = _observation(); value["decision"]["evidence_refs"] = ["N:AKQJ.T98.765.432"]
    with pytest.raises(VideoDDSComparisonError, match="invalid decision evidence ref"):
        build_offline_dds_comparison(value, _board_evidence(), _logic_evidence())


def test_extended_analysis_stages_valid_dds_comparison_and_gaps_invalid_one():
    master = {"job_id": "job-dds", "dds_decision_evaluations": [_observation()]}
    quality = {
        "authority": {"canon_activation": "DENY"},
        "verified_full_board_evidence": [_board_evidence()],
        "source_bound_logic_evidence": [_logic_evidence()],
    }
    result = build_extended_extraction(master, quality)
    assert any(row["candidate_type"] == "DDS_DECISION_COMPARISON" for row in result["candidate_records"])
    bad = _observation(); bad["dds_result"]["fallback_used"] = True
    result = build_extended_extraction({"job_id": "job-dds", "dds_decision_evaluations": [bad]}, quality)
    assert any(row["payload"].get("gap_type") == "DDS_DECISION_EVALUATION_INVALID" for row in result["candidate_records"])
