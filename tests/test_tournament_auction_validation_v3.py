from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge_school_api.tournament_auction_validation_v3 import (
    AuctionLegalityError,
    validate_auction,
)
from bridge_school_api.tournament_preanalysis_gate_v3 import build_preanalysis_gate


FACTS = Path("data/tournaments/tournament_30041_round2_diana_facts_v1.json")
RECEIVED_AT = "2026-08-21T11:05:01Z"
RECEIPT_COMMIT = "0158e506c022fd20051898a3161ab8b576d51f9b"
ALGORITHM_REVISION = "AIroW35dhYwaAOQ1dDxMCkYjVKitrJKTII3Zx0IS7RNHuQDBE8iXBw-l2Ux9qF42DTU1gUWmWDu3kn9XVb1ne2cTls8HTLvblELsTAZRRuY"


def test_passout_is_exact_four_passes_from_dealer():
    result = validate_auction(["P", "P", "P", "P"], dealer="W")
    assert result["termination"] == "PASSOUT"
    assert result["final_contract"] is None
    assert result["declarer"] is None
    assert [x["seat"] for x in result["history"]] == ["W", "N", "E", "S"]


def test_normal_contract_finishes_with_three_passes_and_declarer_is_first_denominator_bidder():
    result = validate_auction(
        ["1H", "P", "2H", "P", "4H", "P", "P", "P"], dealer="N"
    )
    assert result["termination"] == "CONTRACT"
    assert result["final_contract"] == "4H"
    assert result["contract_side"] == "NS"
    assert result["declarer"] == "N"


def test_partner_can_be_declarer_when_partner_first_named_final_strain():
    result = validate_auction(
        ["1C", "P", "1S", "P", "2S", "P", "4S", "P", "P", "P"], dealer="N"
    )
    assert result["final_contract"] == "4S"
    assert result["contract_side"] == "NS"
    assert result["declarer"] == "S"


def test_double_and_redouble_are_carried_into_final_contract():
    result = validate_auction(["1H", "X", "XX", "P", "P", "P"], dealer="N")
    assert result["final_contract"] == "1HXX"
    assert result["double_state"] == "XX"
    assert result["declarer"] == "N"


def test_delayed_redouble_after_two_passes_is_legal():
    result = validate_auction(
        ["1H", "X", "P", "P", "XX", "P", "P", "P"], dealer="N"
    )
    assert result["final_contract"] == "1HXX"
    assert result["declarer"] == "N"


def test_new_bid_clears_old_double_state():
    result = validate_auction(
        ["1H", "X", "2H", "P", "P", "P"], dealer="N"
    )
    assert result["final_contract"] == "2H"
    assert result["double_state"] == ""


@pytest.mark.parametrize(
    "calls, dealer, error_text",
    [
        (["1H", "P", "X"], "N", "own contract"),
        (["1H", "P", "XX"], "N", "redouble requires"),
        (["1H", "P", "1D"], "N", "insufficient bid"),
        (["1H", "P", "P"], "N", "three passes"),
        (["P", "P", "P"], "N", "four terminal passes"),
        (["1H", "P", "P", "P", "P"], "N", "after legal termination"),
    ],
)
def test_illegal_auctions_fail_closed(calls, dealer, error_text):
    with pytest.raises(AuctionLegalityError, match=error_text):
        validate_auction(calls, dealer=dealer)


def test_real_30041_has_no_actual_auction_and_must_not_be_auction_attributed():
    raw = FACTS.read_bytes()
    source = json.loads(raw.decode("utf-8"))
    assert "auction" not in source["columns"]

    import hashlib

    gate = build_preanalysis_gate(
        source,
        normalized_facts_sha256=hashlib.sha256(raw).hexdigest(),
        normalized_facts_size_bytes=len(raw),
        normalized_facts_received_at=RECEIVED_AT,
        normalized_facts_commit=RECEIPT_COMMIT,
        algorithm_revision_id=ALGORITHM_REVISION,
    )
    assert gate["facts_only_analysis_ready"] is True
    assert gate["evidence_availability"]["actual_auction_boards"] == 0
    assert "AUCTION_ANALYSIS_ONLY_ON_BOARDS_WITH_ACTUAL_AUCTION" not in gate["allowed_analyses"]
    assert "BIDDING_DECISION_ATTRIBUTION_WITHOUT_ACTUAL_AUCTION" in gate["blocked_attributions"]
