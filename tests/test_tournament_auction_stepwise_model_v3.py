import pytest

from bridge_school_api.tournament_auction_stepwise_model_v3 import (
    AuctionModelError,
    PublicSuitPromise,
    board15_verified_prefix,
    build_stepwise_auction_model,
    guaranteed_fit_state,
)


HANDS = {
    "N": "Q3.Q9.A82.J98753",
    "E": "KJ6.AJ87632.43.6",
    "S": "AT94.5.9765.KQ42",
    "W": "8752.KT4.KQJT.AT",
}


def heart_promise(minimum_length: int = 4) -> PublicSuitPromise:
    return PublicSuitPromise(
        suit="H",
        minimum_length=minimum_length,
        source_call="1H",
        canon_rule_id="RESP_NEW_SUIT_LEVEL1_4PLUS",
        evidence_ref="teacher-confirmed-in-chat-2026-08-25",
    )


def test_board15_prefix_is_forward_and_hidden_hand_safe():
    out = board15_verified_prefix()
    assert out["schema"] == "tournament-auction-stepwise-model-v1"
    assert out["public_auction"] == ["P", "1D", "P", "1H"]
    assert out["policy"]["build_direction"] == "FORWARD_ONE_CALL_AT_A_TIME"
    assert out["policy"]["use_final_contract_to_backsolve"] is False
    assert out["policy"]["fit_definition"] == "GUARANTEED_COMBINED_LENGTH_AT_LEAST_8"
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


def test_board15_west_does_not_have_known_heart_fit_after_1h():
    out = board15_verified_prefix()
    fit = out["next_actor_fit_check"]
    assert fit["seat"] == "W"
    assert fit["suit"] == "H"
    assert fit["actor_length"] == 3
    assert fit["partner_promised_minimum"] == 4
    assert fit["guaranteed_combined_length"] == 7
    assert fit["fit_established"] is False
    assert fit["support_as_known_fit_allowed"] is False
    assert fit["uses_partner_hidden_actual_length"] is False


def test_fit_is_established_at_guaranteed_eight_cards():
    fit = guaranteed_fit_state(actor_length=4, partner_promise=heart_promise())
    assert fit["guaranteed_combined_length"] == 8
    assert fit["fit_established"] is True
    assert fit["support_as_known_fit_allowed"] is True


def test_fit_api_rejects_unproven_partner_length():
    with pytest.raises(AuctionModelError, match="evidenced PublicSuitPromise"):
        guaranteed_fit_state(actor_length=3, partner_promise=7)


def test_hidden_partner_length_cannot_change_public_fit_result():
    promise = heart_promise()
    results = [
        guaranteed_fit_state(actor_length=3, partner_promise=promise)
        for _hidden_partner_actual_length in (4, 7)
    ]
    assert results[0] == results[1]
    assert results[0]["guaranteed_combined_length"] == 7
    assert results[0]["fit_established"] is False
    assert results[0]["partner_promise_source_call"] == "1H"
    assert results[0]["partner_promise_canon_rule_id"] == "RESP_NEW_SUIT_LEVEL1_4PLUS"


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
