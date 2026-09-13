from __future__ import annotations

import copy
import hashlib

import pytest

from bridge_vision.bridgit_visible_timeline import (
    VisibleTimelineError,
    fuse_visible_timeline,
)


IDENTITY = {"kind": "EXPLICIT_BOARD", "scope": "session-a", "value": "board-7"}


def frame(index: int, cards: list[dict], *, pixel: int | None = None) -> dict:
    frame_cards = copy.deepcopy(cards)
    for card in frame_cards:
        card.setdefault(
            "evidence_pixel_sha256",
            hashlib.sha256(
                f"{index}|{card.get('card')}|{card.get('seat')}".encode()
            ).hexdigest(),
        )
    return {
        "frame_sha256": f"{index:064x}",
        "decoded_pixel_sha256": f"{pixel if pixel is not None else index + 100:064x}",
        "timestamp_ms": index * 1000,
        "deal_identity": dict(IDENTITY),
        "cards": frame_cards,
    }


def seen(card: str, seat: str, source: str = "HAND", confidence: float = 0.995) -> dict:
    return {"card": card, "seat": seat, "source": source, "confidence": confidence}


def test_fuses_visible_hand_and_played_card_without_hidden_completion() -> None:
    cards = [seen("AS", "N"), seen("KH", "S"), seen("2D", "W", "PLAYED")]
    result = fuse_visible_timeline([frame(1, cards), frame(2, cards)])

    assert result["status"] == "SHADOW_PARTIAL_TEMPORAL_OBSERVATION"
    assert result["observed_card_count"] == 3
    assert result["unknown_slots"] == {"N": 12, "E": 13, "S": 12, "W": 12}
    assert result["deal"]["hands"]["N"]["cards"] == ["AS"]
    assert result["deal"]["hands"]["S"]["cards"] == ["KH"]
    assert result["deal"]["hands"]["W"]["cards"] == ["2D"]
    assert result["hidden_hand_inference_used"] is False
    assert result["deck_complement_used"] is False
    assert result["canonical_promotion_allowed"] is False
    assert result["result_scope"] == "SHADOW_ONLY"
    assert len(result["receipt_sha256"]) == 64


def test_thirty_nine_cards_never_create_the_missing_thirteen() -> None:
    deck = [rank + suit for suit in "HCDS" for rank in "AKQJT98765432"]
    cards = [
        seen(card, ("N", "E", "S")[index // 13]) for index, card in enumerate(deck[:39])
    ]
    result = fuse_visible_timeline([frame(1, cards), frame(2, cards)])

    assert result["observed_card_count"] == 39
    assert result["unknown_slots"]["W"] == 13
    assert result["deal"]["hands"]["W"]["cards"] == []
    assert result["status"] == "SHADOW_PARTIAL_TEMPORAL_OBSERVATION"


def test_one_frame_is_only_pending() -> None:
    result = fuse_visible_timeline([frame(1, [seen("AS", "N")])])
    assert result["status"] == "PENDING_TEMPORAL_EVIDENCE"
    assert result["observed_card_count"] == 0
    assert result["rejected"][0]["reason"] == "PENDING_TEMPORAL_SUPPORT"


def test_reencoded_duplicate_pixels_do_not_increase_support() -> None:
    first = frame(1, [seen("AS", "N")], pixel=500)
    replay = frame(2, [seen("AS", "N")], pixel=500)
    result = fuse_visible_timeline([first, replay])

    assert result["status"] == "PENDING_TEMPORAL_EVIDENCE"
    assert result["observed_card_count"] == 0
    assert {item["reason"] for item in result["rejected"]} == {
        "DUPLICATE_DECODED_PIXELS",
        "PENDING_TEMPORAL_SUPPORT",
    }


def test_duplicate_card_pixels_do_not_increase_support() -> None:
    first = frame(1, [seen("AS", "N")])
    replay = frame(2, [seen("AS", "N")])
    replay["cards"][0]["evidence_pixel_sha256"] = first["cards"][0][
        "evidence_pixel_sha256"
    ]

    result = fuse_visible_timeline([first, replay])

    assert result["status"] == "PENDING_TEMPORAL_EVIDENCE"
    assert result["observed_card_count"] == 0
    assert {item["reason"] for item in result["rejected"]} == {
        "DUPLICATE_CARD_PIXELS",
        "PENDING_TEMPORAL_SUPPORT",
    }


def test_card_pixel_hash_cannot_support_different_claims() -> None:
    first = frame(1, [seen("AS", "N")])
    second = frame(2, [seen("KH", "S")])
    second["cards"][0]["evidence_pixel_sha256"] = first["cards"][0][
        "evidence_pixel_sha256"
    ]
    with pytest.raises(VisibleTimelineError, match="reused across different claims"):
        fuse_visible_timeline([first, second])


def test_cross_seat_card_conflict_fails_closed() -> None:
    result = fuse_visible_timeline(
        [frame(1, [seen("AS", "N")]), frame(2, [seen("AS", "E", "PLAYED")])]
    )
    assert result["status"] == "CONFLICT"
    assert result["observed_card_count"] == 0
    assert result["conflicts"] == [
        {"card": "AS", "seats": ["E", "N"], "reason": "CROSS_SEAT_CONFLICT"}
    ]
    assert len(result["receipt_sha256"]) == 64


def test_cross_deal_time_proximity_is_rejected() -> None:
    other = frame(2, [seen("AS", "N")])
    other["deal_identity"]["value"] = "board-8"
    with pytest.raises(VisibleTimelineError, match="cross explicit deal identities"):
        fuse_visible_timeline([frame(1, [seen("AS", "N")]), other])


def test_low_confidence_does_not_vote() -> None:
    cards = [seen("AS", "N", confidence=0.90)]
    result = fuse_visible_timeline([frame(1, cards), frame(2, cards)])
    assert result["observed_card_count"] == 0
    assert [item["reason"] for item in result["rejected"]] == [
        "LOW_CONFIDENCE",
        "LOW_CONFIDENCE",
    ]


def test_hand_limit_conflict_does_not_emit_partial_deal() -> None:
    fourteen = [
        seen(card, "N") for card in [rank + "S" for rank in "AKQJT98765432"] + ["AH"]
    ]
    result = fuse_visible_timeline([frame(1, fourteen), frame(2, fourteen)])
    assert result["status"] == "CONFLICT"
    assert result["observed_card_count"] == 0
    assert result["conflicts"][0]["reason"] == "HAND_LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda x: x[0].update(frame_sha256="A" * 64), "64 lowercase hex"),
        (lambda x: x[0]["cards"][0].update(source="INFERRED"), "HAND or PLAYED"),
        (lambda x: x[0]["cards"][0].pop("seat"), "logical seat"),
        (lambda x: x[0].update(timestamp_ms=-1), "non-negative"),
    ],
)
def test_malformed_or_inferred_evidence_is_rejected(mutation, message: str) -> None:
    observations = [frame(1, [seen("AS", "N")])]
    mutation(observations)
    with pytest.raises(VisibleTimelineError, match=message):
        fuse_visible_timeline(observations)


def test_result_is_deterministic_under_card_order() -> None:
    cards = [seen("AS", "N"), seen("KH", "S"), seen("2D", "W", "PLAYED")]
    left = fuse_visible_timeline([frame(1, cards), frame(2, cards)])
    reversed_cards = list(reversed(copy.deepcopy(cards)))
    right = fuse_visible_timeline([frame(1, reversed_cards), frame(2, reversed_cards)])
    assert left == right


def test_same_frame_hash_cannot_change_its_observations() -> None:
    original = frame(1, [seen("AS", "N")])
    changed = copy.deepcopy(original)
    changed["cards"] = frame(1, [seen("KH", "S")])["cards"]
    with pytest.raises(VisibleTimelineError, match="inconsistent observation records"):
        fuse_visible_timeline([original, changed])


def test_one_frame_cannot_repeat_a_card() -> None:
    duplicate = frame(1, [seen("AS", "N"), seen("AS", "N", "PLAYED")])
    with pytest.raises(VisibleTimelineError, match="repeats or conflicts"):
        fuse_visible_timeline([duplicate])
