from pathlib import Path

import pytest

from bridge_vision.independent_card_channels import IndependentCardChannelBackend
from bridge_vision.native_cards import NativeFourSeatCardDetector

SHA1, SHA2 = "a" * 64, "b" * 64
TABLE = {"x": 0, "y": 0, "w": 1000, "h": 800}


def _candidate(**updates):
    row = {
        "rank": "A", "rank_confidence": 0.99, "rank_source": "rank-model-v1",
        "suit": "S", "suit_confidence": 0.98, "suit_source": "suit-shape-v1",
        "full_card": "AS", "full_card_confidence": 0.97, "full_card_source": "card-model-v1",
        "channel_frames": {
            "rank": [SHA1, SHA2], "suit": [SHA1, SHA2], "full_card": [SHA1, SHA2],
        },
        "box": {"x": 450, "y": 40, "w": 30, "h": 50},
    }
    row.update(updates)
    return row


def _backend(candidate):
    return IndependentCardChannelBackend(lambda _: {"table_region": TABLE, "candidates": [candidate]})


def test_card_requires_three_independent_agreeing_visual_channels():
    payload = _backend(_candidate())(Path("unused.jpg"))
    assert payload["cards"] == [{
        "card": "AS", "confidence": 0.97,
        "box": {"x": 450, "y": 40, "w": 30, "h": 50}, "channel_evidence_index": 0,
    }]
    assert payload["channel_evidence"]["production_activation_allowed"] is False
    assert payload["channel_evidence"]["result_scope"] == "SHADOW_ONLY"


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"full_card": None}, "MISSING_OR_INVALID_CHANNEL"),
        ({"full_card": "AH"}, "CHANNEL_CONFLICT"),
        ({"full_card_source": "rank-model-v1"}, "NON_INDEPENDENT_CHANNEL_PROVENANCE"),
        ({"full_card_confidence": 0.89}, "LOW_CHANNEL_CONFIDENCE"),
        ({"channel_frames": {"rank": [SHA1], "suit": [SHA1, SHA2], "full_card": [SHA1, SHA2]}}, "INSUFFICIENT_TEMPORAL_EVIDENCE"),
    ],
)
def test_missing_weak_conflicting_or_nonindependent_signal_stays_unknown(updates, reason):
    payload = _backend(_candidate(**updates))(Path("unused.jpg"))
    assert payload["cards"] == []
    assert payload["channel_evidence"]["rejected"][0]["reason"] == reason


def test_thresholds_and_temporal_support_cannot_be_lowered():
    with pytest.raises(ValueError, match="cannot be lowered"):
        IndependentCardChannelBackend(lambda _: {}, min_full_card_confidence=0.5)
    with pytest.raises(ValueError, match="cannot be lowered"):
        IndependentCardChannelBackend(lambda _: {}, min_temporal_support=1)


def test_native_geometry_assigns_seat_and_preserves_channel_evidence():
    detector = NativeFourSeatCardDetector(_backend(_candidate()), min_card_confidence=0.90)
    result = detector(Path("unused.jpg"))
    assert result["hands"] == {"N": ["AS"]}
    assert result["evidence"]["channel_evidence"]["accepted_count"] == 1
    assert result["evidence"]["accepted"][0]["seat"] == "N"


def test_speech_or_colour_fields_cannot_create_missing_visual_card():
    candidate = _candidate(rank=None, transcript_card="AS", colour="black")
    payload = _backend(candidate)(Path("unused.jpg"))
    assert payload["cards"] == []
    assert payload["channel_evidence"]["rejected"][0]["reason"] == "MISSING_OR_INVALID_CHANNEL"
