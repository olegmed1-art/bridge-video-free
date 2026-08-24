import pytest

from bridge_school_api.ai_auction_scoring import (
    AuctionScoringError,
    normalize_vulnerability,
    score_duplicate_contract,
    score_rollout_with_dds3,
)


def test_duplicate_scoring_covers_game_slam_doubles_and_ns_sign():
    assert score_duplicate_contract(
        contract="4S", declarer="N", tricks=10, vulnerability="NONE"
    )["score_ns"] == 420
    assert score_duplicate_contract(
        contract="4S", declarer="N", tricks=10, vulnerability="NS"
    )["score_ns"] == 620
    assert score_duplicate_contract(
        contract="3NT", declarer="E", tricks=9, vulnerability="EW"
    )["score_ns"] == -600
    assert score_duplicate_contract(
        contract="4SX", declarer="S", tricks=8, vulnerability="NONE"
    )["score_ns"] == -300
    assert score_duplicate_contract(
        contract="4SXX", declarer="S", tricks=8, vulnerability="NS"
    )["score_ns"] == -1000
    assert score_duplicate_contract(
        contract="6H", declarer="W", tricks=12, vulnerability="NONE"
    )["score_ns"] == -980


def test_vulnerability_and_contract_inputs_fail_closed():
    assert normalize_vulnerability("all") == "BOTH"
    assert normalize_vulnerability("n-s") == "NS"
    with pytest.raises(AuctionScoringError, match="vulnerability"):
        normalize_vulnerability("dealer")
    with pytest.raises(AuctionScoringError, match="contract"):
        score_duplicate_contract(contract="8S", declarer="N", tricks=13, vulnerability="NONE")
    with pytest.raises(AuctionScoringError, match="trick"):
        score_duplicate_contract(contract="1S", declarer="N", tricks=True, vulnerability="NONE")


def _rollout(*, passed_out=False, vulnerability="NONE"):
    fingerprint = "a" * 64
    return {
        "engine": "BEN",
        "fallback_used": False,
        "evidence_class": "BEN_AUCTION_ROLLOUT",
        "candidate_call": "4S",
        "vulnerability": vulnerability,
        "requested_worlds": 1,
        "completed_worlds": 1,
        "complete": True,
        "dds_evaluated": False,
        "worlds": [{
            "world_index": 0,
            "world_fingerprint": fingerprint,
            "deal_pbn_sha256": "b" * 64,
            "auction": ["4S", "PASS", "PASS", "PASS"],
            "contract": None if passed_out else "4S",
            "declarer": None if passed_out else "N",
            "passed_out": passed_out,
            "ben_calls_generated": 3,
        }],
    }


def _dds(*, fallback=False):
    return {
        "engine": "DDS3",
        "engine_version": "v3-test",
        "operation": "dd_table",
        "fallback_used": fallback,
        "hand_order": ["N", "E", "S", "W"],
        "strain_order": ["S", "H", "D", "C", "NT"],
        "dd_table": {
            "S": [10, 3, 10, 3],
            "H": [9, 4, 9, 4],
            "D": [8, 5, 8, 5],
            "C": [7, 6, 7, 6],
            "NT": [9, 4, 9, 4],
        },
    }


def _dds_envelope(*, fallback=False, fingerprint=None, deal_pbn_sha256=None):
    return {
        "world_fingerprint": fingerprint or "a" * 64,
        "deal_pbn_sha256": deal_pbn_sha256 or "b" * 64,
        "result": _dds(fallback=fallback),
    }


def test_rollout_binding_preserves_ben_and_dds3_provenance():
    result = score_rollout_with_dds3(
        rollout=_rollout(),
        dds3_results={"a" * 64: _dds_envelope()},
        vulnerability="NONE",
    )
    assert result["complete"] is True
    assert result["fallback_used"] is False
    assert result["dds_evaluated"] is True
    assert result["dds_required_worlds"] == 1
    assert result["evidence_class"] == "BEN_AUCTION_ROLLOUT_WITH_DDS3_SCORING"
    assert result["worlds"][0]["score_ns"] == 420
    assert result["worlds"][0]["dds3_tricks"] == 10
    assert len(result["worlds"][0]["dds3_sha256"]) == 64
    assert len(result["rollout_sha256"]) == 64


def test_passed_out_world_needs_no_dds3_and_scores_zero():
    result = score_rollout_with_dds3(
        rollout=_rollout(passed_out=True, vulnerability="BOTH"),
        dds3_results={},
        vulnerability="BOTH",
    )
    assert result["dds_evaluated"] is False
    assert result["dds_required_worlds"] == 0
    assert result["worlds"][0]["score_ns"] == 0


@pytest.mark.parametrize("dds_results", [
    {},
    {"a" * 64: _dds_envelope(fallback=True)},
    {"c" * 64: _dds_envelope(fingerprint="c" * 64)},
])
def test_rollout_binding_rejects_missing_fallback_or_mismatched_dds3(dds_results):
    with pytest.raises(AuctionScoringError):
        score_rollout_with_dds3(
            rollout=_rollout(),
            dds3_results=dds_results,
            vulnerability="NONE",
        )


def test_rollout_binding_rejects_duplicate_worlds():
    rollout = _rollout()
    rollout["worlds"].append(dict(rollout["worlds"][0]))
    rollout["requested_worlds"] = rollout["completed_worlds"] = 2
    with pytest.raises(AuctionScoringError, match="duplicate"):
        score_rollout_with_dds3(
            rollout=rollout,
            dds3_results={"a" * 64: _dds_envelope()},
            vulnerability="NONE",
        )


def test_rollout_binding_rejects_vulnerability_mismatch():
    with pytest.raises(AuctionScoringError, match="vulnerability"):
        score_rollout_with_dds3(
            rollout=_rollout(vulnerability="NS"),
            dds3_results={"a" * 64: _dds_envelope()},
            vulnerability="EW",
        )


def test_rollout_binding_rejects_mislabeled_dds3_deal():
    with pytest.raises(AuctionScoringError, match="not bound"):
        score_rollout_with_dds3(
            rollout=_rollout(),
            dds3_results={
                "a" * 64: _dds_envelope(deal_pbn_sha256="c" * 64),
            },
            vulnerability="NONE",
        )
