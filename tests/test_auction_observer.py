import copy

import pytest

from bridge_vision.auction_observer import AuctionObserverError, aggregate_auction_observations, observe_bridgit_auction

SOURCE = "drive:file-1:v1"
ANCHOR = "f" * 64


def identity(instance="instance-a", board=1):
    return {"kind": "SOURCE_BOUND_BOARD_INSTANCE", "scope": "lesson-1", "instance_id": instance, "board_number": board, "anchor_frame_sha256": ANCHOR}


def raw(frame, calls=("1S", "PASS", "PASS", "PASS"), *, instance="instance-a", complete=True):
    seats = "NESW"
    return {
        "source": "BRIDGIT_AUCTION_TABLE", "source_id": SOURCE, "frame_sha256": frame,
        "deal_instance_id": instance, "board_number": 1, "dealer": "N", "complete": complete,
        "calls": [{
            "seat": seats[index % 4], "row": index // 4,
            "ocr": {"value": call, "confidence": 0.99, "channel_id": "ocr-v1", "source": "VISUAL_OCR", "frame_sha256": frame},
            "reference_match": {"value": call, "confidence": 0.98, "channel_id": "template-v1", "source": "VISUAL_REFERENCE", "frame_sha256": frame},
            "box": {"x": index * 10, "y": 0, "w": 8, "h": 8}, "evidence_locator": f"auction#cell-{index}",
        } for index, call in enumerate(calls)],
    }


def observe(frame, payload=None, *, instance="instance-a", status="CONFIRMED"):
    return observe_bridgit_auction(
        payload or raw(frame, instance=instance), board_number=1, dealer="N", frame_sha256=frame,
        source_id=SOURCE, deal_identity=identity(instance), board_context_status=status,
    )


def test_visual_channels_cell_order_and_legality_create_observation():
    result = observe("a" * 64)
    assert result["status"] == "PASS"
    assert result["calls"] == ["1S", "PASS", "PASS", "PASS"]
    assert result["contract"] == "1S"
    assert result["declarer"] == "N"
    assert result["production_activation_allowed"] is False


def test_visual_cell_rows_follow_non_north_dealer():
    frame = "d" * 64
    payload = raw(frame)
    payload.update(board_number=2, dealer="E", deal_instance_id="instance-e")
    for item, seat, row in zip(payload["calls"], ("E", "S", "W", "N"), (0, 0, 0, 1)):
        item["seat"], item["row"] = seat, row
    result = observe_bridgit_auction(
        payload, board_number=2, dealer="E", frame_sha256=frame, source_id=SOURCE,
        deal_identity=identity("instance-e", board=2), board_context_status="CONFIRMED",
    )
    assert result["status"] == "PASS"
    assert [cell["seat"] for cell in result["cells"]] == ["E", "S", "W", "N"]
    assert [cell["row"] for cell in result["cells"]] == [0, 0, 0, 1]


def test_speech_unbound_channel_or_unconfirmed_board_stays_review():
    frame = "a" * 64
    payload = raw(frame)
    payload["calls"][0]["ocr"]["source"] = "TEACHER_SPEECH"
    assert observe(frame, payload)["status"] == "REVIEW"
    payload = raw(frame)
    payload["calls"][0]["ocr"]["frame_sha256"] = "b" * 64
    assert observe(frame, payload)["status"] == "REVIEW"
    assert observe(frame, raw(frame), status="PARTIAL_VISUAL_EVIDENCE")["reason"] == "BOARD_CONTEXT_NOT_CONFIRMED"


def test_missing_cell_or_illegal_sequence_is_not_reconstructed():
    frame = "a" * 64
    payload = raw(frame, calls=("1S", "PASS", "PASS"), complete=False)
    del payload["calls"][1]
    result = observe(frame, payload)
    assert result["status"] == "REVIEW"
    assert result["accepted_as_observation"] is False
    illegal = raw(frame, calls=("1S", "PASS", "1H"), complete=False)
    assert observe(frame, illegal)["status"] == "REVIEW"


def test_two_frames_confirm_complete_auction_and_instances_never_mix():
    first, second = observe("a" * 64), observe("b" * 64)
    aggregate = aggregate_auction_observations([first, second])
    assert aggregate["status"] == "COMPLETE_CONFIRMED"
    assert aggregate["independent_frame_support_floor"] == 2
    assert aggregate["accepted_as_standard_pbn"] is True
    other = observe("c" * 64, instance="instance-b")
    conflict = aggregate_auction_observations([first, other])
    assert conflict["status"] == "CONFLICT"
    assert conflict["reason"] == "AUCTION_DEAL_IDENTITY_CONFLICT"


def test_conflicting_visual_sequences_and_same_frame_variants_fail_closed():
    first = observe("a" * 64)
    second = observe("b" * 64, raw("b" * 64, calls=("1NT", "PASS", "PASS", "PASS")))
    assert aggregate_auction_observations([first, second])["reason"] == "AUCTION_SEQUENCE_CONFLICT"
    forged = copy.deepcopy(first)
    forged["calls"] = ["1NT", "PASS", "PASS", "PASS"]
    assert aggregate_auction_observations([first, forged])["reason"] == "ONE_FRAME_HAS_MULTIPLE_AUCTIONS"


def test_temporal_support_cannot_be_lowered():
    with pytest.raises(AuctionObserverError, match="cannot be lowered"):
        aggregate_auction_observations([], min_independent_frames=1)
