import hashlib
import json
from pathlib import Path

import pytest

from bridge_contracts.video_dds_decision_comparison import (
    VideoDDSComparisonError,
    build_offline_dds_comparison,
)
from bridge_school_api.dds3.service import DDS_UPSTREAM
from bridge_contracts.video_extended_extraction import build_extended_extraction


PBN = "N:AKQJ.T98.765.432 T987.654.32.AKQ 6543.AKQ.JT98.76 2.J732.AKQ4.JT985"
DEAL_SHA = hashlib.sha256(PBN.encode()).hexdigest()
ROOT = Path(__file__).parents[1]


def _observation():
    return {
        "dds_request": {
            "operation": "position_all_moves",
            "position": {"pbn": PBN, "trump": "NT", "first": "S"},
        },
        "decision": {
            "decision_id": "play-7", "domain": "PLAY", "selected_action": "SA",
            "logic_candidate_id": "why:rule-7:segment-7",
            "source_sha256": "c" * 64,
            "public_context": {
                "auction": ["1NT", "3NT"], "played_cards": [],
                "contract": "3NT", "seat_to_play": "S",
            },
            "evidence_refs": ["segment-7"],
        },
        "full_deal_evidence": {
            "board_evidence_id": "board-proof-7",
            "deal_pbn_sha256": DEAL_SHA, "source_refs": ["frame-52"],
            "verified_full_board": True,
        },
    }


def _dds_result():
    return {
        "engine": "DDS3", "engine_version": DDS_UPSTREAM, "fallback_used": False,
        "operation": "position_all_moves",
        "binary_sha256": "e" * 64,
        "moves": [
            {"card": "SA", "tricks": 10, "regret": 0, "optimal": True},
            {"card": "SK", "tricks": 9, "regret": 1, "optimal": False},
        ],
    }


def _board_evidence():
    return {
        "status": "VERIFIED_FULL_BOARD", "board_evidence_id": "board-proof-7",
        "deal_pbn_sha256": DEAL_SHA, "card_count": 52, "unique_card_count": 52,
        "source_refs": ["frame-52"], "evidence_sha256": "d" * 64,
    }


def _logic_evidence():
    return {
        "status": "SOURCE_BOUND", "logic_candidate_id": "why:rule-7:segment-7",
        "source_sha256": "c" * 64, "evidence_refs": ["segment-7"],
    }


def _executor(result=None):
    result = result or _dds_result()
    return lambda payload: result


def test_binds_logic_to_dds_offline_without_hidden_cards():
    observation = _observation()
    result = build_offline_dds_comparison(
        observation, _board_evidence(), _logic_evidence(),
        dds_request_executor=_executor(),
    )
    assert result["offline_only"] is True
    assert result["live_resolver_input_allowed"] is False
    assert result["canon_evidence_allowed"] is False
    assert "deal_pbn_sha256" not in result["full_deal_evidence"]
    assert result["dds_provenance"]["verification_mode"] == "PINNED_DDS_RERUN"
    assert len(result["comparison_sha256"]) == 64


def test_dds_opening_position_requires_contract_and_actor_context():
    for missing in ("contract", "seat_to_play"):
        value = _observation()
        value["decision"]["public_context"].pop(missing)
        with pytest.raises(VideoDDSComparisonError, match="requires public contract"):
            build_offline_dds_comparison(
                value, _board_evidence(), _logic_evidence(),
                dds_request_executor=_executor(),
            )


@pytest.mark.parametrize("context_patch,position_patch", [
    ({"seat_to_play": "E"}, {}),
    ({"contract": "3H"}, {}),
    ({"declarer": "N"}, {}),
])
def test_dds_position_must_match_supplied_public_context(context_patch, position_patch):
    value = _observation()
    value["decision"]["public_context"].update(context_patch)
    value["dds_request"]["position"].update(position_patch)
    with pytest.raises(VideoDDSComparisonError, match="does not match public"):
        build_offline_dds_comparison(
            value, _board_evidence(), _logic_evidence(),
            dds_request_executor=_executor(),
        )


@pytest.mark.parametrize("context_patch,position_patch", [
    ({"lead": "AH"}, {"current_trick": ["SA"]}),
    ({"trick_no": 1, "played_cards": ["AS"]}, {}),
    ({"trick_no": 2}, {}),
])
def test_midplay_dds_is_fail_closed_without_remaining_deal_proof(context_patch, position_patch):
    value = _observation()
    value["decision"]["public_context"].update(context_patch)
    value["dds_request"]["position"].update(position_patch)
    with pytest.raises(VideoDDSComparisonError, match="verified opening position"):
        build_offline_dds_comparison(
            value, _board_evidence(), _logic_evidence(),
            dds_request_executor=_executor(),
        )


@pytest.mark.parametrize("mutate, match", [
    (lambda value: value["decision"]["public_context"].update(partner_hand="AKQ"), "fields invalid"),
    (lambda value: value["full_deal_evidence"].update(verified_full_board=False), "verified full board"),
])
def test_fails_closed_on_leak_or_unproven_dds(mutate, match):
    value = _observation()
    mutate(value)
    with pytest.raises(VideoDDSComparisonError, match=match):
        build_offline_dds_comparison(
            value, _board_evidence(), _logic_evidence(),
            dds_request_executor=_executor(),
        )


@pytest.mark.parametrize("result, match", [
    (dict(_dds_result(), fallback_used=True), "fallback"),
    (dict(_dds_result(), moves=[]), "no moves"),
])
def test_fails_closed_on_unproven_pinned_dds_result(result, match):
    with pytest.raises(VideoDDSComparisonError, match=match):
        build_offline_dds_comparison(
            _observation(), _board_evidence(), _logic_evidence(),
            dds_request_executor=_executor(result),
        )


def test_rejects_fabricated_alternatives_unverified_board_and_unresolved_logic():
    value = _observation()
    value["alternatives"] = [{"action": "H2", "value": 13}]
    with pytest.raises(VideoDDSComparisonError, match="fields mismatch"):
        build_offline_dds_comparison(
            value, _board_evidence(), _logic_evidence(),
            dds_request_executor=_executor(),
        )

    board = _board_evidence(); board["unique_card_count"] = 51
    with pytest.raises(VideoDDSComparisonError, match="verified reconstruction"):
        value = _observation()
        build_offline_dds_comparison(
            value, board, _logic_evidence(), dds_request_executor=_executor()
        )

    logic = _logic_evidence(); logic["logic_candidate_id"] = "why:missing"
    with pytest.raises(VideoDDSComparisonError, match="source-bound logic"):
        value = _observation()
        build_offline_dds_comparison(
            value, _board_evidence(), logic, dds_request_executor=_executor()
        )


def test_public_context_and_refs_are_allowlisted_against_hidden_card_payloads():
    value = _observation(); value["decision"]["public_context"]["notes"] = "N:AKQ..."
    with pytest.raises(VideoDDSComparisonError, match="fields invalid"):
        build_offline_dds_comparison(
            value, _board_evidence(), _logic_evidence(), dds_request_executor=_executor()
        )
    value = _observation(); value["decision"]["evidence_refs"] = ["N:AKQJ.T98.765.432"]
    with pytest.raises(VideoDDSComparisonError, match="invalid decision evidence ref"):
        build_offline_dds_comparison(
            value, _board_evidence(), _logic_evidence(), dds_request_executor=_executor()
        )
    for hidden_ref in (
        "AKQJ.T98.765.432", "AKQJ/T98/765/432", "AKQx.Txx.xxx.xxx",
        "N:AK", "N:AKx", "N:10", "N:A10", "N:AS", "north:10S",
        "partner:AKQ", "partner:AS", "opponent:QH",
        "partner/AS", "north=10S", "opponent.QH",
        "partner-hand:AKQ", "partnerHand:AKQ",
        "opponent/cards/SA", "partnerDeal:AKQ", "opponent-deal:AKQ",
        "north/deal:AKQ",
    ):
        value = _observation(); value["decision"]["evidence_refs"] = [hidden_ref]
        with pytest.raises(VideoDDSComparisonError, match="invalid decision evidence ref"):
            build_offline_dds_comparison(
                value, _board_evidence(), _logic_evidence(), dds_request_executor=_executor()
            )
    for public_ref in ("N:table-1", "frame:N:task-7"):
        value = _observation(); value["decision"]["evidence_refs"] = [public_ref]
        logic = _logic_evidence(); logic["evidence_refs"] = [public_ref]
        result = build_offline_dds_comparison(
            value, _board_evidence(), logic, dds_request_executor=_executor()
        )
        assert result["decision"]["evidence_refs"] == [public_ref]
    value = _observation(); value["decision"]["evidence_refs"] = ["frame:N:AKQJ.T98.765.432"]
    with pytest.raises(VideoDDSComparisonError, match="invalid decision evidence ref"):
        build_offline_dds_comparison(
            value, _board_evidence(), _logic_evidence(), dds_request_executor=_executor()
        )

    value = _observation()
    value["decision"]["public_context"]["contract"] = "N:AKQJ.T98.765.432 E:..."
    with pytest.raises(VideoDDSComparisonError, match="public contract invalid"):
        build_offline_dds_comparison(
            value, _board_evidence(), _logic_evidence(), dds_request_executor=_executor()
        )


def test_rejects_missing_or_invalid_pinned_dds_rerun():
    value = _observation()
    with pytest.raises(VideoDDSComparisonError, match="pinned DDS"):
        build_offline_dds_comparison(value, _board_evidence(), _logic_evidence())
    with pytest.raises(VideoDDSComparisonError, match="invalid result"):
        build_offline_dds_comparison(
            value, _board_evidence(), _logic_evidence(),
            dds_request_executor=lambda _: "forged",
        )


def test_extended_analysis_stages_valid_dds_comparison_and_gaps_invalid_one():
    master = {"job_id": "job-dds", "dds_decision_evaluations": [_observation()]}
    quality = {
        "authority": {"canon_activation": "DENY"},
        "verified_full_board_evidence": [_board_evidence()],
        "source_bound_logic_evidence": [_logic_evidence()],
    }
    result = build_extended_extraction(master, quality, dds_request_executor=_executor())
    assert any(row["candidate_type"] == "DDS_DECISION_COMPARISON" for row in result["candidate_records"])
    bad = _observation()
    rejected = dict(_dds_result(), fallback_used=True)
    result = build_extended_extraction(
        {"job_id": "job-dds", "dds_decision_evaluations": [bad]}, quality,
        dds_request_executor=_executor(rejected),
    )
    assert any(row["payload"].get("gap_type") == "DDS_DECISION_EVALUATION_INVALID" for row in result["candidate_records"])


def test_rejected_dds_gap_never_echoes_hidden_evidence_reference():
    hidden_ref = "N:AKQJ.T98.765.432"
    bad = _observation()
    bad["decision"]["evidence_refs"] = [hidden_ref]
    quality = {
        "authority": {"canon_activation": "DENY"},
        "verified_full_board_evidence": [_board_evidence()],
        "source_bound_logic_evidence": [_logic_evidence()],
    }

    result = build_extended_extraction(
        {"job_id": "job-dds", "dds_decision_evaluations": [bad]},
        quality,
        dds_request_executor=_executor(),
    )

    gap = next(
        row for row in result["candidate_records"]
        if row["payload"].get("gap_type") == "DDS_DECISION_EVALUATION_INVALID"
    )
    assert gap["evidence_refs"] == []
    assert gap["payload"]["evidence_refs"] == []
    assert hidden_ref not in json.dumps(result, ensure_ascii=False)


def test_real_diana_postprocessor_wires_the_pinned_dds_executor():
    source = (ROOT / "diana_longitudinal_postprocess_v4_2.py").read_text()
    assert "from bridge_contracts.video_dds_pinned_executor import execute_digest_pinned_dds3" in source
    assert "dds_request_executor=execute_digest_pinned_dds3" in source
    assert "correction_receipt_resolver=_trusted_correction_receipt_resolver()" in source
