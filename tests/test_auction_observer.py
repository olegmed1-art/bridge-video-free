from bridge_vision.auction_observer import (
    aggregate_auction_observations,
    observe_bridgit_auction,
    validate_auction_prefix,
)
from bridge_vision.shadow_pbn import render_shadow_pbn
from bridge_school_api.tournament_auction_validation_v3 import validate_auction


def visual_auction(calls, *, complete, mismatch_at=None):
    seats = ("N", "E", "S", "W")
    cells = []
    for index, call in enumerate(calls):
        seat = seats[index % 4]
        reference = "2H" if mismatch_at == index else call
        cells.append({
            "seat": seat,
            "column": seat,
            "row": index // 4,
            "box": {"x": 600 + 40 * (index % 4), "y": 500 + 24 * (index // 4), "w": 32, "h": 18},
            "ocr": {"value": call, "confidence": 0.97, "channel_id": "paddleocr-v1"},
            "reference_match": {
                "value": reference,
                "confidence": 0.96,
                "channel_id": "bridgit-cell-template-v1",
            },
            "evidence_locator": f"frame.jpg#auction-cell={index}",
        })
    return {
        "source": "BRIDGIT_AUCTION_TABLE",
        "board_number": 1,
        "dealer": "N",
        "calls": cells,
        "complete": complete,
        "evidence_locator": "frame.jpg#auction-table",
    }


def observation(calls, *, frame, complete):
    return observe_bridgit_auction(
        visual_auction(calls, complete=complete),
        board_number=1,
        dealer="N",
        frame_sha256=frame,
        board_confirmed=True,
    )


def pbn_record(auction, *, frame):
    return {
        "status": "PARTIAL_BOARD_OBSERVATION",
        "frame_file": frame,
        "candidates": [{
            "hands": {},
            "confidence": 0.99,
            "evidence": {
                "canonical_promotion_allowed": False,
                "deal_identity": {"kind": "EXPLICIT_BOARD", "scope": "lesson", "value": "board-1"},
                "board_metadata": {
                    "status": "CONFIRMED",
                    "board_number": 1,
                    "dealer": "N",
                    "vulnerability": "NONE",
                },
                "auction_observation": auction,
            },
        }],
        "diagnostics": [],
    }


def test_legal_partial_prefix_and_complete_auction_are_distinguished():
    partial = validate_auction_prefix(["1H", "P", "2H"], dealer="N")
    complete = validate_auction_prefix(["1H", "P", "2H", "P", "P", "P"], dealer="N")
    assert partial["terminated"] is False
    assert complete["terminated"] is True
    assert complete["termination"] == "CONTRACT"


def test_complete_auction_legality_matches_independent_tournament_validator():
    examples = [
        ("N", ["P", "P", "P", "P"]),
        ("E", ["1D", "P", "1H", "P", "2H", "P", "P", "P"]),
        ("S", ["1S", "X", "XX", "P", "P", "P"]),
        ("W", ["2NT", "P", "3NT", "P", "P", "P"]),
    ]
    for dealer, calls in examples:
        observed = validate_auction_prefix(calls, dealer=dealer)
        independent = validate_auction(calls, dealer=dealer)
        assert observed["terminated"] is True
        assert observed["termination"] == independent["termination"]
        assert observed["normalized_calls"] == independent["normalized_calls"]
        assert [item["seat"] for item in observed["history"]] == [
            item["seat"] for item in independent["history"]
        ]


def test_two_90_percent_channels_and_cell_order_are_required():
    accepted = observation(["1H", "P", "2H"], frame="a" * 64, complete=False)
    assert accepted["status"] == "PARTIAL"
    assert accepted["confidence_floor"] == 0.96
    assert accepted["accepted_as_observation"] is True

    rejected = observe_bridgit_auction(
        visual_auction(["1H"], complete=False, mismatch_at=0),
        board_number=1,
        dealer="N",
        frame_sha256="b" * 64,
        board_confirmed=True,
    )
    assert rejected["status"] == "REVIEW"
    assert rejected["accepted_as_observation"] is False
    assert "channels disagree" in rejected["detail"]


def test_longest_compatible_prefix_wins_and_each_call_needs_two_frames_for_standard_pbn():
    calls = ["1H", "P", "2H", "P", "P", "P"]
    first = observation(calls[:3], frame="a" * 64, complete=False)
    second = observation(calls, frame="b" * 64, complete=True)
    review = aggregate_auction_observations([first, second])
    assert review["status"] == "COMPLETE_NEEDS_TEMPORAL_CONFIRMATION"
    assert review["call_frame_support"] == [2, 2, 2, 1, 1, 1]
    assert review["accepted_as_standard_pbn"] is False

    third = observation(calls, frame="c" * 64, complete=True)
    confirmed = aggregate_auction_observations([first, second, third])
    assert confirmed["status"] == "COMPLETE_CONFIRMED"
    assert confirmed["accepted_as_standard_pbn"] is True


def test_pbn_writes_only_temporally_confirmed_complete_auction_as_standard_block():
    calls = ["1H", "P", "2H", "P", "P", "P"]
    one = observation(calls, frame="a" * 64, complete=True)
    two = observation(calls, frame="b" * 64, complete=True)
    text = render_shadow_pbn([
        pbn_record(one, frame="one.jpg"),
        pbn_record(two, frame="two.jpg"),
    ])
    assert '[X-AuctionStatus "COMPLETE_CONFIRMED"]' in text
    assert '[Auction "N"]' in text
    assert "1H Pass 2H Pass\nPass Pass" in text
    assert '[Deal "' not in text


def test_conflicting_visual_sequences_remain_custom_review_data():
    one = observation(["1H", "P"], frame="a" * 64, complete=False)
    two = observation(["1S", "P"], frame="b" * 64, complete=False)
    text = render_shadow_pbn([
        pbn_record(one, frame="one.jpg"),
        pbn_record(two, frame="two.jpg"),
    ])
    assert '[X-AuctionStatus "CONFLICT"]' in text
    assert '[X-AuctionVariants "' in text
    assert '[Auction "' not in text
