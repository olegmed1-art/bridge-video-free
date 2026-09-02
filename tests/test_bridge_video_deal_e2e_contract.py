import json

from bridge_contracts.video_deal import canonicalize_video_deal


def test_recognizer_payload_to_canonical_json_never_invents_cards_by_default():
    recognizer_output = {
        "hands": {
            "N": ["AS", "KH", "7D"],
            "E": ["QC", "2S"],
            "S": ["JD"],
        }
    }
    observed = {
        card
        for cards in recognizer_output["hands"].values()
        for card in cards
    }

    canonical_json = json.dumps(
        canonicalize_video_deal(recognizer_output).to_dict(),
        sort_keys=True,
    )
    artifact = json.loads(canonical_json)

    emitted = {
        card
        for hand in artifact["hands"].values()
        for card in hand["cards"]
    }
    assert emitted == observed
    assert set(artifact["hands"]) == {"N", "E", "S", "W"}
    assert artifact["hands"]["W"] == {"cards": [], "unknown_count": 13}
    assert artifact["derivations"] == []
    assert sum(hand["unknown_count"] for hand in artifact["hands"].values()) == 46


def test_reconstruction_request_is_rejected_instead_of_serialized():
    payload = {
        "hands": {
            "N": ["AS", "KS", "QS", "JS", "TS", "9S", "8S", "7S", "6S", "5S", "4S", "3S", "2S"],
            "E": ["AH", "KH", "QH", "JH", "TH", "9H", "8H", "7H", "6H", "5H", "4H", "3H", "2H"],
            "S": ["AD", "KD", "QD", "JD", "TD", "9D", "8D", "7D", "6D", "5D", "4D", "3D", "2D"],
        }
    }
    import pytest
    from bridge_contracts.video_deal import BridgeVideoDealContractError

    with pytest.raises(BridgeVideoDealContractError, match="hidden cards must remain UNKNOWN"):
        canonicalize_video_deal(payload, derive_fourth_hand=True)
