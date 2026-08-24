import json

from bridge_contracts.video_deal import canonicalize_video_deal


def test_recognizer_payload_to_canonical_json_never_invents_cards():
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
    assert tuple(artifact["hands"]) == ("E", "N", "S", "W") or set(artifact["hands"]) == {"N", "E", "S", "W"}
    assert artifact["hands"]["W"] == {"cards": [], "unknown_count": 13}
    assert sum(hand["unknown_count"] for hand in artifact["hands"].values()) == 46
