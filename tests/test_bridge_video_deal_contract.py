import pytest

from bridge_contracts.video_deal import (
    BRIDGE_VIDEO_DEAL_CONTRACT_VERSION,
    BridgeVideoDealContractError,
    canonicalize_video_deal,
)


def test_partial_video_deal_preserves_only_observed_cards():
    deal = canonicalize_video_deal(
        {"hands": {"N": ["AS", "10h"], "E": ["2C"], "S": []}}
    ).to_dict()

    assert deal["contract_version"] == BRIDGE_VIDEO_DEAL_CONTRACT_VERSION
    assert deal["hands"]["N"] == {"cards": ["AS", "TH"], "unknown_count": 11}
    assert deal["hands"]["E"] == {"cards": ["2C"], "unknown_count": 12}
    assert deal["hands"]["S"] == {"cards": [], "unknown_count": 13}
    assert deal["hands"]["W"] == {"cards": [], "unknown_count": 13}


def test_missing_fourth_hand_is_not_completed_from_remaining_deck():
    payload = {
        "hands": {
            "N": ["AS", "KS", "QS", "JS", "TS", "9S", "8S", "7S", "6S", "5S", "4S", "3S", "2S"],
            "E": ["AH", "KH", "QH", "JH", "TH", "9H", "8H", "7H", "6H", "5H", "4H", "3H", "2H"],
            "S": ["AD", "KD", "QD", "JD", "TD", "9D", "8D", "7D", "6D", "5D", "4D", "3D", "2D"],
        }
    }

    deal = canonicalize_video_deal(payload).to_dict()

    assert deal["hands"]["W"] == {"cards": [], "unknown_count": 13}
    known = {
        card
        for seat in ("N", "E", "S", "W")
        for card in deal["hands"][seat]["cards"]
    }
    assert len(known) == 39
    assert "AC" not in known


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
