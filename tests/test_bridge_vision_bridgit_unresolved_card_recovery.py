from __future__ import annotations

import hashlib

import pytest

from bridge_vision.bridgit_unresolved_card_recovery import (
    UnresolvedRecoveryError,
    bind_location_to_seat,
    recover_unresolved_deal,
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
