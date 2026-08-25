import pytest

from bridge_school_api.tournament_auction_stepwise_model_v3 import (
    AuctionModelError,
    board15_verified_prefix,
    build_stepwise_auction_model,
)


HANDS = {
    "N": "Q3.Q9.A82.J98753",
    "E": "KJ6.AJ87632.43.6",
    "S": "AT94.5.9765.KQ42",
    "W": "8752.KT4.KQJT.AT",
}


def test_board15_prefix_is_forward_and_hidden_hand_safe():
    out = board15_verified_prefix()
    assert out["schema"] == "tournament-auction-stepwise-model-v1"
    assert out["public_auction"] == ["P", "1D", "P", "1H"]
    assert out["policy"]["build_direction"] == "FORWARD_ONE_CALL_AT_A_TIME"
    assert out["policy"]["use_final_contract_to_backsolve"] is False
    for step in out["steps"]:
        ctx = step["decision_context"]
        assert ctx["hidden_hand_access_allowed"] is False
        assert "partner_hand" not in ctx
        assert "opponent_hands" not in ctx
    east = out["steps"][3]
    assert east["provenance"] == "CANON"
    assert east["call"] == "1H"
    assert east["decision_context"]["actor_lengths"]["H"] == 7
    assert east["decision_context"]["actor_hcp"] == 9


def test_public_history_grows_one_call_at_a_time():
    out = board15_verified_prefix()
    assert [s["decision_context"]["public_auction_before_call"] for s in out["steps"]] == [
        [], ["P"], ["P", "1D"], ["P", "1D", "P"]
    ]


def test_canon_requires_explicit_rule_and_evidence():
    with pytest.raises(AuctionModelError, match="CANON step requires"):
        build_stepwise_auction_model(
            dealer="S",
            hands=HANDS,
            canon_revision="x",
            steps=[{"seat": "S", "call": "P", "provenance": "CANON"}],
        )


def test_model_cannot_claim_canon_rule():
    with pytest.raises(AuctionModelError, match="non-CANON"):
        build_stepwise_auction_model(
            dealer="S",
            hands=HANDS,
            canon_revision="x",
            steps=[{
                "seat": "S", "call": "P", "provenance": "MODEL",
                "canon_rule_id": "MADE_UP_RULE"
            }],
        )


def test_actor_order_is_enforced():
    with pytest.raises(AuctionModelError, match="wrong actor order"):
        build_stepwise_auction_model(
            dealer="S",
            hands=HANDS,
            canon_revision="x",
            steps=[{"seat": "W", "call": "1D", "provenance": "MODEL"}],
        )
