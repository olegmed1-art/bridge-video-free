import hashlib

import pytest

from bridge_vision.bridgit_autonomous_deals import (
    AutonomousDealsError,
    reconstruct_autonomous_deals,
)

RANKS = "AKQJT98765432"
SUIT_HANDS = {
    "N": [rank + "S" for rank in RANKS],
    "E": [rank + "H" for rank in RANKS],
    "S": [rank + "D" for rank in RANKS],
    "W": [rank + "C" for rank in RANKS],
}


def sha(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def frame(number, marker, hands, *, source="HAND", evidence_round=None):
    evidence_round = number if evidence_round is None else evidence_round
    cards = [
        {
            "card": card,
            "seat": seat,
            "source": source,
            "confidence": 0.999,
            "evidence_pixel_sha256": sha(f"evidence:{evidence_round}:{seat}:{card}"),
        }
        for seat, seat_cards in hands.items()
        for card in seat_cards
    ]
    return {
        "frame_sha256": sha(f"frame:{number}"),
        "decoded_pixel_sha256": sha(f"pixels:{number}"),
        "deal_marker_sha256": sha(marker),
        "timestamp_ms": number * 1000,
        "cards": cards,
    }


def test_all_observed_cards_produce_complete_valid_pbn_without_review():
    result = reconstruct_autonomous_deals(
        [frame(1, "board-1", SUIT_HANDS), frame(2, "board-1", SUIT_HANDS)],
        source_scope="diana-23.mp4:sha256",
    )

    assert result["status"] == "COMPLETE"
    assert result["status_counts"]["COMPLETE_OBSERVED"] == 1
    deal = result["deals"][0]
    assert deal["status"] == "COMPLETE_OBSERVED"
    assert deal["validation"]["status"] == "PASS"
    assert deal["validation"]["total_cards"] == 52
    assert deal["pbn"] == (
        "N:AKQJT98765432... .AKQJT98765432.. ..AKQJT98765432. ...AKQJT98765432"
    )
    assert result["uses_language_model"] is False
    assert result["requires_screenshot_review"] is False


def test_three_observed_hands_complete_only_missing_seat_by_exact_subtraction():
    visible = {seat: cards for seat, cards in SUIT_HANDS.items() if seat != "W"}
    result = reconstruct_autonomous_deals(
        [frame(1, "board-40", visible), frame(2, "board-40", visible)],
        source_scope="diana-23.mp4:sha256",
    )

    deal = result["deals"][0]
    assert deal["status"] == "COMPLETE_DERIVED_EXACT"
    assert deal["completion"] == "ONE_SEAT_EXACT_DECK_COMPLEMENT"
    assert deal["deal"]["card_provenance"]["W"]["observed_cards"] == []
    assert deal["deal"]["card_provenance"]["W"]["derived_cards"] == SUIT_HANDS["W"]
    assert deal["validation"]["full_board"] is True
    assert deal["pbn"].endswith("...AKQJT98765432")


def test_two_incomplete_hands_remain_partial_and_never_emit_pbn():
    visible = {"N": SUIT_HANDS["N"], "S": SUIT_HANDS["S"]}
    result = reconstruct_autonomous_deals(
        [frame(1, "board-2", visible), frame(2, "board-2", visible)],
        source_scope="diana-23.mp4:sha256",
    )

    deal = result["deals"][0]
    assert result["status"] == "REVIEW"
    assert deal["status"] == "PARTIAL"
    assert deal["pbn"] is None
    assert deal["deal"]["derivations"] == []
    assert deal["validation"]["full_board"] is False


def test_visual_marker_changes_create_separate_autonomous_deals():
    frames = [
        frame(1, "board-a", SUIT_HANDS),
        frame(2, "board-a", SUIT_HANDS),
        frame(3, "board-b", SUIT_HANDS),
        frame(4, "board-b", SUIT_HANDS),
    ]
    result = reconstruct_autonomous_deals(frames, source_scope="video")

    assert result["deal_count"] == 2
    assert result["status_counts"]["COMPLETE_OBSERVED"] == 2
    assert [item["deal_identity"]["value"][:4] for item in result["deals"]] == [
        "0001",
        "0002",
    ]


def test_cross_seat_card_conflict_fails_closed():
    first = {"N": ["AS"]}
    second = {"E": ["AS"]}
    frames = [
        frame(1, "board-x", first),
        frame(2, "board-x", first),
        frame(3, "board-x", second),
        frame(4, "board-x", second),
    ]
    result = reconstruct_autonomous_deals(frames, source_scope="video")

    deal = result["deals"][0]
    assert deal["status"] == "CONFLICT"
    assert deal["deal"] is None
    assert deal["pbn"] is None
    assert deal["conflicts"] == [
        {"card": "AS", "seats": ["E", "N"], "reason": "CROSS_SEAT_CONFLICT"}
    ]


def test_unsupported_cross_seat_claims_still_fail_closed() -> None:
    result = reconstruct_autonomous_deals(
        [
            frame(1, "board-x", {"N": ["AS"]}),
            frame(2, "board-x", {"E": ["AS"]}),
        ],
        source_scope="video",
    )

    assert result["deals"][0]["status"] == "CONFLICT"
    assert result["deals"][0]["deal"] is None


def test_same_card_pixels_do_not_count_as_independent_support():
    frames = [
        frame(1, "board-x", {"N": ["AS"]}, evidence_round="same"),
        frame(2, "board-x", {"N": ["AS"]}, evidence_round="same"),
    ]
    result = reconstruct_autonomous_deals(frames, source_scope="video")

    deal = result["deals"][0]
    assert deal["observed_card_count"] == 0
    assert any(item["reason"] == "DUPLICATE_CARD_PIXELS" for item in deal["rejected"])
    assert any(
        item["reason"] == "PENDING_TEMPORAL_SUPPORT" for item in deal["rejected"]
    )


def test_reencoded_duplicate_pixels_do_not_count_as_a_second_frame():
    frames = [
        frame(1, "board-x", {"N": ["AS"]}),
        frame(2, "board-x", {"N": ["AS"]}),
    ]
    frames[1]["decoded_pixel_sha256"] = frames[0]["decoded_pixel_sha256"]
    result = reconstruct_autonomous_deals(frames, source_scope="video")

    deal = result["deals"][0]
    assert deal["observed_card_count"] == 0
    assert any(
        item["reason"] == "DUPLICATE_DECODED_PIXELS" for item in deal["rejected"]
    )


def test_duplicate_source_timestamp_is_rejected_before_segmentation():
    frames = [frame(1, "board-x", {}), frame(2, "board-x", {})]
    frames[1]["timestamp_ms"] = frames[0]["timestamp_ms"]

    with pytest.raises(AutonomousDealsError, match="duplicate source timestamp"):
        reconstruct_autonomous_deals(frames, source_scope="video")
