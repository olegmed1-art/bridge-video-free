import pytest

from bridge_contracts.video_deal import FULL_DECK
from bridge_vision.deal_pbn import DealPbnError, render_deals_pbn

ANCHOR = "f" * 64
IDENTITY = {"kind": "SOURCE_BOUND_BOARD_INSTANCE", "scope": "lesson-1", "instance_id": "instance-a", "board_number": 1, "anchor_frame_sha256": ANCHOR}
SOURCE = "drive:file-1:v1"
BOARD = {"status": "CONFIRMED", "source_id": SOURCE, "board_number": 1, "dealer": "N", "vulnerability": "NONE"}


def base_deal(hands, **updates):
    value = {
        "source_id": SOURCE, "deal_identity": IDENTITY, "board_context": BOARD, "hands": hands,
        "deal_evidence": {"result_scope": "SHADOW_ONLY", "canonical_promotion_allowed": False, "production_activation_allowed": False},
    }
    value.update(updates)
    return value


def complete_hands():
    cards = sorted(FULL_DECK)
    return {seat: cards[index * 13:(index + 1) * 13] for index, seat in enumerate("NESW")}


def confirmed_auction(identity_value=IDENTITY):
    return {
        "status": "COMPLETE_CONFIRMED", "dealer": "N",
        "calls": ["1S", "PASS", "PASS", "PASS"],
        "accepted_as_standard_pbn": True, "deal_identity": identity_value, "source_id": SOURCE,
    }


def test_partial_and_39_card_deals_never_reconstruct_fourth_hand():
    hands = complete_hands()
    hands["W"] = []
    text, report = render_deals_pbn([base_deal(hands)], source_name="Diana13", algorithm_revision="test-r1")
    assert '[X-ObservedCardCount "39"]' in text
    assert "[Deal " not in text
    assert "X-Derived" not in text
    assert report["derived_deal_count"] == 0
    assert report["hidden_or_fourth_hand_reconstruction_performed"] is False


def test_full_human_verified_deal_and_confirmed_auction_emit_standard_tags():
    deal = base_deal(
        complete_hands(),
        verification={"status": "HUMAN_VERIFIED", "verified_seats": list("NESW"), "reference_frame_sha256": "a" * 64},
        auction=confirmed_auction(),
    )
    text, report = render_deals_pbn([deal], source_name="Diana13", algorithm_revision="test-r1")
    assert '[Deal "N:' in text
    assert '[Auction "N"]' in text
    assert '[Vulnerable "None"]' in text
    assert report["standard_deal_count"] == 1
    assert report["confirmed_auction_count"] == 1


def test_full_visual_deal_needs_every_card_and_three_independent_channels():
    hands = complete_hands()
    observations = [
        {
            "seat": seat, "card": card, "evidence_class": "OBSERVED_VISUAL",
            "frame_sha256s": ["a" * 64, "b" * 64],
            "channels": {"rank": "rank-v1", "suit": "suit-v1", "full_card": "card-v1"},
        }
        for seat, cards in hands.items() for card in cards
    ]
    text, report = render_deals_pbn(
        [base_deal(hands, card_observations=observations)],
        source_name="Diana13", algorithm_revision="test-r1",
    )
    assert "[Deal " in text
    assert report["standard_deal_count"] == 1
    observations[0]["channels"]["full_card"] = "rank-v1"
    text, report = render_deals_pbn(
        [base_deal(hands, card_observations=observations)],
        source_name="Diana13", algorithm_revision="test-r1",
    )
    assert "[Deal " not in text
    assert report["standard_deal_count"] == 0


def test_complete_claim_without_card_provenance_is_rejected():
    deal = base_deal(complete_hands())
    deal["deal_evidence"]["complete_without_derivation"] = True
    with pytest.raises(DealPbnError, match="lacks direct"):
        render_deals_pbn([deal], source_name="Diana13", algorithm_revision="test-r1")


def test_auction_from_another_instance_is_rejected():
    other = {**IDENTITY, "instance_id": "instance-b", "anchor_frame_sha256": "b" * 64}
    deal = base_deal({}, auction=confirmed_auction(other))
    with pytest.raises(DealPbnError, match="different deal instance"):
        render_deals_pbn([deal], source_name="Diana13", algorithm_revision="test-r1")


def test_partial_auction_is_preserved_only_in_x_tags():
    auction = {"status": "PARTIAL_OBSERVED", "dealer": "N", "calls": ["1S", "PASS"], "deal_identity": IDENTITY, "source_id": SOURCE}
    text, report = render_deals_pbn([base_deal({}, auction=auction)], source_name="Diana13", algorithm_revision="test-r1")
    assert "[Auction " not in text
    assert '[X-AuctionCalls "1S PASS"]' in text
    assert report["confirmed_auction_count"] == 0
