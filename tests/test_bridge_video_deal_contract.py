import pytest

from bridge_contracts.video_deal import (
    BRIDGE_VIDEO_DEAL_CONTRACT_VERSION,
    BridgeVideoDealContractError,
    FULL_DECK,
    canonicalize_video_deal,
)


def three_complete_suit_hands():
    return {
        "N": ["AS", "KS", "QS", "JS", "TS", "9S", "8S", "7S", "6S", "5S", "4S", "3S", "2S"],
        "E": ["AH", "KH", "QH", "JH", "TH", "9H", "8H", "7H", "6H", "5H", "4H", "3H", "2H"],
        "S": ["AD", "KD", "QD", "JD", "TD", "9D", "8D", "7D", "6D", "5D", "4D", "3D", "2D"],
    }


def test_partial_video_deal_preserves_only_observed_cards():
    deal = canonicalize_video_deal(
        {"hands": {"N": ["AS", "10h"], "E": ["2C"], "S": []}}
    ).to_dict()

    assert deal["contract_version"] == BRIDGE_VIDEO_DEAL_CONTRACT_VERSION
    assert deal["hands"]["N"] == {"cards": ["AS", "TH"], "unknown_count": 11}
    assert deal["hands"]["E"] == {"cards": ["2C"], "unknown_count": 12}
    assert deal["hands"]["S"] == {"cards": [], "unknown_count": 13}
    assert deal["hands"]["W"] == {"cards": [], "unknown_count": 13}
    assert deal["derivations"] == []


def test_missing_fourth_hand_stays_unknown_without_explicit_derivation():
    payload = {"hands": three_complete_suit_hands()}

    deal = canonicalize_video_deal(payload).to_dict()

    assert deal["hands"]["W"] == {"cards": [], "unknown_count": 13}
    assert deal["derivations"] == []
    known = {
        card
        for seat in ("N", "E", "S", "W")
        for card in deal["hands"][seat]["cards"]
    }
    assert len(known) == 39
    assert "AC" not in known


def test_explicit_fourth_hand_derivation_complements_exactly_three_complete_hands():
    payload = {"hands": three_complete_suit_hands()}

    deal = canonicalize_video_deal(payload, derive_fourth_hand=True).to_dict()

    assert deal["hands"]["W"] == {
        "cards": ["AC", "KC", "QC", "JC", "TC", "9C", "8C", "7C", "6C", "5C", "4C", "3C", "2C"],
        "unknown_count": 0,
    }
    assert deal["card_provenance"]["W"]["observed_cards"] == []
    assert deal["card_provenance"]["W"]["derived_cards"] == deal["hands"]["W"]["cards"]
    assert deal["derivations"] == [{
        "type": "DECK_COMPLEMENT_FROM_THREE_VISUAL_HANDS",
        "seat": "W",
        "source_seats": ["N", "E", "S"],
        "observed_card_count": 39,
        "computed_cards": deal["hands"]["W"]["cards"],
        "provenance": "INFERRED_DECK_COMPLEMENT",
    }]


def test_partial_fourth_hand_observation_is_preserved_without_completion():
    hands = three_complete_suit_hands()
    hands["W"] = ["AC", "2C"]

    deal = canonicalize_video_deal({"hands": hands}).to_dict()

    assert deal["derivations"] == []
    assert deal["hands"]["W"]["unknown_count"] == 11
    assert deal["card_provenance"]["W"]["observed_cards"] == ["AC", "2C"]
    assert deal["card_provenance"]["W"]["derived_cards"] == []


def test_derivation_fails_closed_without_three_complete_hands():
    with pytest.raises(BridgeVideoDealContractError, match="requires exactly three complete hands"):
        canonicalize_video_deal(
            {"hands": {"N": ["AS"], "E": ["KH"], "S": ["QD"]}},
            derive_fourth_hand=True,
        )


def test_duplicate_card_across_hands_fails_closed():
    with pytest.raises(BridgeVideoDealContractError, match="appears in both"):
        canonicalize_video_deal({"hands": {"N": ["AS"], "E": ["AS"]}})


def test_invalid_or_overfull_hand_fails_closed():
    with pytest.raises(BridgeVideoDealContractError, match="invalid card"):
        canonicalize_video_deal({"hands": {"N": ["1S"]}})

    with pytest.raises(BridgeVideoDealContractError, match="more than 13"):
        canonicalize_video_deal({"hands": {"N": ["AS"] * 14}})


def test_card_order_is_deterministic_without_adding_cards():
    deal = canonicalize_video_deal(
        {"hands": {"N": ["2c", "A♠", "10D", "KH"]}}
    ).to_dict()

    assert deal["hands"]["N"]["cards"] == ["AS", "KH", "TD", "2C"]
    assert set(deal["hands"]["N"]["cards"]) == {"AS", "KH", "TD", "2C"}


def test_every_missing_seat_stays_unknown_even_when_complement_is_unique():
    seats = ("N", "E", "S", "W")
    deck = sorted(FULL_DECK)
    complete = {seat: deck[index * 13 : (index + 1) * 13] for index, seat in enumerate(seats)}

    for missing in seats:
        observed = {seat: cards for seat, cards in complete.items() if seat != missing}
        deal = canonicalize_video_deal({"hands": observed}).to_dict()
        emitted = {card for hand in deal["hands"].values() for card in hand["cards"]}
        assert emitted == set(FULL_DECK) - set(complete[missing])
        assert deal["hands"][missing] == {"cards": [], "unknown_count": 13}
        assert deal["derivations"] == []


def test_every_wholly_absent_fourth_seat_can_be_derived_with_provenance():
    seats = ("N", "E", "S", "W")
    deck = sorted(FULL_DECK)
    complete = {seat: deck[index * 13 : (index + 1) * 13] for index, seat in enumerate(seats)}

    for missing in seats:
        observed = {seat: cards for seat, cards in complete.items() if seat != missing}
        deal = canonicalize_video_deal(
            {"hands": observed}, derive_fourth_hand=True
        ).to_dict()
        assert deal["hands"][missing]["cards"] == sorted(
            complete[missing], key=lambda card: ("SHDC".index(card[1]), "AKQJT98765432".index(card[0]))
        )
        assert deal["card_provenance"][missing]["observed_cards"] == []
        assert len(deal["card_provenance"][missing]["derived_cards"]) == 13


def test_partial_fourth_hand_must_not_be_completed():
    hands = three_complete_suit_hands()
    hands["W"] = ["AC"]
    with pytest.raises(BridgeVideoDealContractError, match="requires exactly three complete hands"):
        canonicalize_video_deal({"hands": hands}, derive_fourth_hand=True)
