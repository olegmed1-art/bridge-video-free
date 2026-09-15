from __future__ import annotations

import hashlib

import pytest

from bridge_vision.gambler_classic_reference import RANKS, SUITS, validate_sprite_bytes

from bridge_vision.bridgit_unresolved_card_recovery import (
    UnresolvedRecoveryError,
    bind_location_to_seat,
    recover_unresolved_deal,
    scan_unresolved_in_registered_frame,
    unresolved_cards,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _empty():
    return {seat: [] for seat in "NESW"}


def test_unresolved_cards_is_full_deck_for_empty_primary():
    assert len(unresolved_cards(_empty())) == 52


def test_full_primary_skips_retry():
    hands = {
        "N": [r + "S" for r in "AKQJT98765432"],
        "E": [r + "H" for r in "AKQJT98765432"],
        "S": [r + "D" for r in "AKQJT98765432"],
        "W": [r + "C" for r in "AKQJT98765432"],
    }
    result = recover_unresolved_deal(hands, [])
    assert result["status"] == "COMPLETE_OBSERVED"
    assert result["retry_performed"] is False
    assert result["deck_complement_used"] is False


def test_high_confidence_retry_recovers_direct_card():
    hands = _empty()
    hands["N"] = [r + "S" for r in "KQJT98765432"]
    candidate = {
        "card": "AS",
        "seat": "N",
        "confidence": 0.97,
        "frame_sha256": _sha("f1"),
        "decoded_pixel_sha256": _sha("p1"),
        "source": "GAMBLER_PRIMARY_RETRY",
    }
    result = recover_unresolved_deal(hands, [candidate], allow_exact_complement=False)
    assert "AS" in result["hands"]["N"]
    assert result["recovered_direct"][0]["card"] == "AS"
    assert result["deck_complement_used"] is False


def test_borderline_retry_needs_two_independent_pixels():
    hands = _empty()
    hands["N"] = [r + "S" for r in "KQJT98765432"]
    one = {
        "card": "AS",
        "seat": "N",
        "confidence": 0.94,
        "frame_sha256": _sha("f1"),
        "decoded_pixel_sha256": _sha("p1"),
        "source": "GAMBLER_OCCLUSION_RETRY",
    }
    result = recover_unresolved_deal(hands, [one], allow_exact_complement=False)
    assert "AS" not in result["hands"]["N"]

    two = {
        **one,
        "frame_sha256": _sha("f2"),
        "decoded_pixel_sha256": _sha("p2"),
    }
    result = recover_unresolved_deal(hands, [one, two], allow_exact_complement=False)
    assert "AS" in result["hands"]["N"]


def test_duplicate_pixels_do_not_multiply_borderline_support():
    hands = _empty()
    hands["N"] = [r + "S" for r in "KQJT98765432"]
    first = {
        "card": "AS",
        "seat": "N",
        "confidence": 0.94,
        "frame_sha256": _sha("f1"),
        "decoded_pixel_sha256": _sha("same"),
        "source": "GAMBLER_OCCLUSION_RETRY",
    }
    second = {**first, "frame_sha256": _sha("f2"), "confidence": 0.945}
    result = recover_unresolved_deal(hands, [first, second], allow_exact_complement=False)
    assert "AS" not in result["hands"]["N"]


def test_cross_seat_retry_conflict_is_not_accepted():
    hands = _empty()
    candidates = [
        {
            "card": "AS",
            "seat": "N",
            "confidence": 0.99,
            "frame_sha256": _sha("f1"),
            "decoded_pixel_sha256": _sha("p1"),
            "source": "GAMBLER_PRIMARY_RETRY",
        },
        {
            "card": "AS",
            "seat": "E",
            "confidence": 0.99,
            "frame_sha256": _sha("f2"),
            "decoded_pixel_sha256": _sha("p2"),
            "source": "GAMBLER_PRIMARY_RETRY",
        },
    ]
    result = recover_unresolved_deal(hands, candidates, allow_exact_complement=False)
    assert "AS" in result["ambiguous_retry_cards"]
    assert all("AS" not in result["hands"][seat] for seat in "NESW")


def test_exact_complement_happens_only_after_retry_and_only_for_one_incomplete_seat():
    hands = {
        "N": [r + "S" for r in "AKQJT98765432"],
        "E": [r + "H" for r in "AKQJT98765432"],
        "S": [r + "D" for r in "AKQJT98765432"],
        "W": [],
    }
    result = recover_unresolved_deal(hands, [], allow_exact_complement=True)
    assert result["status"] == "COMPLETE_DERIVED_EXACT"
    assert len(result["hands"]["W"]) == 13
    assert result["deck_complement_used"] is True
    assert all(item["seat"] == "W" for item in result["derived_exact_complement"])


def test_multiple_incomplete_hands_never_guess_split():
    hands = {
        "N": [r + "S" for r in "AKQJT98765432"],
        "E": [r + "H" for r in "AKQJT98765432"],
        "S": [],
        "W": [],
    }
    result = recover_unresolved_deal(hands, [], allow_exact_complement=True)
    assert result["status"] == "PARTIAL_AFTER_RETRY"
    assert result["deck_complement_used"] is False
    assert len(result["remaining_unresolved"]) == 26


def test_normalized_seat_binding_is_resolution_independent():
    assert bind_location_to_seat(500, 50, frame_width=1000, frame_height=800) == "N"
    assert bind_location_to_seat(500, 700, frame_width=1000, frame_height=800) == "S"
    assert bind_location_to_seat(50, 400, frame_width=1000, frame_height=800) == "W"
    assert bind_location_to_seat(900, 400, frame_width=1000, frame_height=800) == "E"


def test_invalid_primary_duplicate_owner_rejected():
    hands = _empty()
    hands["N"] = ["AS"]
    hands["E"] = ["AS"]
    with pytest.raises(UnresolvedRecoveryError, match="multiple owners"):
        recover_unresolved_deal(hands, [])


def _synthetic_sprite_and_frame(card: str):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    width, height = 1417, 588
    image = np.full((height, width, 4), 255, dtype=np.uint8)
    image[:, :, 3] = 255
    card_width, card_height = 109, 147
    for suit_index, suit in enumerate("CDHS"):
        for rank_index, rank in enumerate("AKQJT98765432"):
            x0, y0 = rank_index * card_width, suit_index * card_height
            # Shared rank structure dominates the upper corner; suit-specific
            # structure is deliberately smaller, mirroring the real ambiguity.
            cv2.rectangle(
                image,
                (x0 + 2, y0 + 2),
                (x0 + 5 + rank_index, y0 + 19),
                (0, 0, 0, 255),
                -1,
            )
            cv2.rectangle(
                image,
                (x0 + 3 + suit_index * 3, y0 + 23),
                (x0 + 7 + suit_index * 3, y0 + 34),
                (0, 0, 0, 255),
                -1,
            )
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    payload = encoded.tobytes()
    sprite = validate_sprite_bytes(
        payload,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_variant=5,
    )
    row = "CDHS".index(card[1])
    column = "AKQJT98765432".index(card[0])
    source = image[
        row * card_height : (row + 1) * card_height,
        column * card_width : (column + 1) * card_width,
        :3,
    ]
    frame = np.full((800, 1000, 3), (30, 120, 70), dtype=np.uint8)
    frame[600 : 600 + card_height, 400 : 400 + card_width] = source
    return sprite, frame


def test_scanner_requires_rank_and_suit_to_win_at_same_location():
    sprite, frame = _synthetic_sprite_and_frame("4C")
    frame_sha = _sha("frame")
    pixel_sha = _sha("pixels")

    correct = scan_unresolved_in_registered_frame(
        frame,
        sprite,
        ["4C"],
        frame_sha256=frame_sha,
        decoded_pixel_sha256=pixel_sha,
        high_confidence=0.0,
        borderline_confidence=0.0,
    )
    assert [item["card"] for item in correct] == ["4C"]
    assert correct[0]["comparison_method"] == "SAME_LOCATION_RANK_SUIT_COMPETITION_V1"
    assert correct[0]["rank_margin"] >= 0.02
    assert correct[0]["suit_margin"] >= 0.02

    wrong_suit = scan_unresolved_in_registered_frame(
        frame,
        sprite,
        ["4S"],
        frame_sha256=frame_sha,
        decoded_pixel_sha256=pixel_sha,
        high_confidence=0.0,
        borderline_confidence=0.0,
    )
    wrong_rank = scan_unresolved_in_registered_frame(
        frame,
        sprite,
        ["5C"],
        frame_sha256=frame_sha,
        decoded_pixel_sha256=pixel_sha,
        high_confidence=0.0,
        borderline_confidence=0.0,
    )
    assert wrong_suit == []
    assert wrong_rank == []
