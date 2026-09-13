import hashlib

import pytest

from bridge_school_api.ai_auction_scoring import (
    AuctionScoringError,
    normalize_vulnerability,
    score_duplicate_contract,
    score_rollout_with_dds3,
)
from bridge_school_api.dds3.service import (
    DDS_UPSTREAM,
    canonical_dds3_table_request,
    dds3_table_request_sha256,
)


TEST_PBN = "N:AKQJT98765432... .AKQJT98765432.. ..AKQJT98765432. ...AKQJT98765432"
TEST_PBN_SHA256 = hashlib.sha256(TEST_PBN.encode("utf-8")).hexdigest()
ALT_PBN = "N:AKQJT9876543.2.. .AKQJT9876543.2. ..AKQJT9876543.2 2..AKQJT9876543"


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


@pytest.mark.parametrize(("contract", "tricks", "vulnerability", "expected"), [
    ("1C", 7, "NONE", 70),
    ("2D", 8, "BOTH", 90),
    ("2H", 8, "NONE", 110),
    ("3S", 9, "NS", 140),
    ("1N", 7, "NONE", 90),
    ("2N", 8, "NS", 120),
    ("3N", 9, "NONE", 400),
    ("3N", 9, "NS", 600),
    ("4H", 10, "NONE", 420),
    ("4S", 10, "NS", 620),
    ("5C", 11, "NONE", 400),
    ("5D", 11, "NS", 600),
    ("6N", 12, "NONE", 990),
    ("6N", 12, "NS", 1440),
    ("7N", 13, "NONE", 1520),
    ("7N", 13, "NS", 2220),
    ("2SX", 8, "NONE", 470),
    ("2SXX", 8, "NONE", 640),
    ("4SX", 11, "NONE", 690),
    ("3N", 10, "NS", 630),
])
def test_duplicate_scoring_golden_matrix(contract, tricks, vulnerability, expected):
    assert score_duplicate_contract(
        contract=contract,
        declarer="N",
        tricks=tricks,
        vulnerability=vulnerability,
    )["score_ns"] == expected


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
            "deal_pbn_sha256": TEST_PBN_SHA256,
            "auction": ["4S", "PASS", "PASS", "PASS"],
            "contract": None if passed_out else "4S",
            "declarer": None if passed_out else "N",
            "passed_out": passed_out,
            "ben_calls_generated": 3,
        }],
    }


def _dds(*, request, fallback=False, engine_version=DDS_UPSTREAM, input_validated=True,
         strain_order=None, request_sha256=None, deal_pbn_sha256=None):
    return {
        "engine": "DDS3",
        "engine_version": engine_version,
        "operation": "dd_table",
        "input_validated": input_validated,
        "fallback_used": fallback,
        "hand_order": ["N", "E", "S", "W"],
        "strain_order": strain_order or ["S", "H", "D", "C", "NT"],
        "deal_pbn_sha256": deal_pbn_sha256 or hashlib.sha256(request["pbn"].encode("utf-8")).hexdigest(),
        "request_sha256": request_sha256 or dds3_table_request_sha256(request),
        "dd_table": {
            "S": [10, 3, 10, 3],
            "H": [9, 4, 9, 4],
            "D": [8, 5, 8, 5],
            "C": [7, 6, 7, 6],
            "NT": [9, 4, 9, 4],
        },
    }


def _dds_envelope(*, fallback=False, fingerprint=None, pbn=TEST_PBN, **dds_overrides):
    request = canonical_dds3_table_request(pbn=pbn, dealer="N", vulnerability="None")
    return {
        "world_fingerprint": fingerprint or "a" * 64,
        "request": request,
        "result": _dds(request=request, fallback=fallback, **dds_overrides),
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
                "a" * 64: _dds_envelope(pbn=ALT_PBN),
            },
            vulnerability="NONE",
        )


@pytest.mark.parametrize("overrides", [
    {"engine_version": "v3-unreviewed"},
    {"input_validated": False},
    {"strain_order": ["C", "D", "H", "S", "NT"]},
    {"request_sha256": "0" * 64},
    {"deal_pbn_sha256": "0" * 64},
])
def test_rollout_binding_rejects_incomplete_dds3_provenance(overrides):
    with pytest.raises(AuctionScoringError, match="DDS3"):
        score_rollout_with_dds3(
            rollout=_rollout(),
            dds3_results={"a" * 64: _dds_envelope(**overrides)},
            vulnerability="NONE",
        )
