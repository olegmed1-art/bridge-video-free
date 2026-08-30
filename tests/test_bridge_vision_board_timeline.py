import copy

import pytest

from bridge_vision.board_timeline import BoardTimelineError, confirm_board_timeline
from bridge_vision.bridgit_compass import parse_bridgit_compass

REGION = {"x": 700, "y": 20, "w": 250, "h": 220}
SIZE = {"width": 1000, "height": 800}


def _field(value, locator, frame, source="VISUAL_TEXT"):
    return {"value": value, "confidence": 0.99, "source": source, "frame_sha256": frame, "evidence_locator": locator}


def observation(frame, timestamp, instance, *, anchor, source="drive:file-1:v1", board=1):
    dealer = "NESW"[(board - 1) % 4]
    vulnerability = ("NONE", "NS", "EW", "BOTH", "NS", "EW", "BOTH", "NONE", "EW", "BOTH", "NONE", "NS", "BOTH", "NONE", "NS", "EW")[(board - 1) % 16]
    dealer_position = {"N": "top", "E": "right", "S": "bottom", "W": "left"}[dealer]
    raw = {
        "interface": "BRIDGIT", "human_verified_profile": True,
        "frame_sha256": frame, "deal_anchor_frame_sha256": anchor, "timestamp_ms": timestamp,
        "source_id": source, "scope": "lesson-session-1", "deal_instance_id": instance,
        "region": REGION,
        "seat_labels": {
            "top": _field("N", "#top", frame), "right": _field("E", "#right", frame),
            "bottom": _field("S", "#bottom", frame), "left": _field("W", "#left", frame),
        },
        "board_number": _field(board, "#board", frame),
        "dealer_marker": _field(dealer_position, "#dealer", frame, "VISUAL_MARKER"),
        "vulnerability": _field(vulnerability, "#vul", frame, "VISUAL_MARKER"),
    }
    return parse_bridgit_compass(raw, expected_region=REGION, reference_size=SIZE)


def test_two_source_bound_frames_confirm_one_board_instance():
    a, b = "a" * 64, "b" * 64
    result = confirm_board_timeline([
        observation(a, 1000, "instance-a", anchor=a),
        observation(b, 2000, "instance-a", anchor=a),
    ])
    assert result["status"] == "PASS"
    assert result["confirmed_segment_count"] == 1
    assert result["segments"][0]["frame_sha256s"] == [a, b]


def test_same_board_number_in_two_instances_is_never_merged():
    frames = [character * 64 for character in "abcd"]
    result = confirm_board_timeline([
        observation(frames[0], 1000, "instance-a", anchor=frames[0]),
        observation(frames[1], 2000, "instance-a", anchor=frames[0]),
        observation(frames[2], 3000, "instance-b", anchor=frames[2]),
        observation(frames[3], 4000, "instance-b", anchor=frames[2]),
    ])
    assert result["status"] == "PASS"
    assert result["segment_count"] == 2
    assert {segment["deal_identity"]["instance_id"] for segment in result["segments"]} == {"instance-a", "instance-b"}


def test_noncontiguous_instance_reappearance_is_review_not_merge():
    frames = [character * 64 for character in "abcdef"]
    rows = [
        observation(frames[0], 1000, "instance-a", anchor=frames[0]),
        observation(frames[1], 2000, "instance-a", anchor=frames[0]),
        observation(frames[2], 3000, "instance-b", anchor=frames[2], board=2),
        observation(frames[3], 4000, "instance-b", anchor=frames[2], board=2),
        observation(frames[4], 5000, "instance-a", anchor=frames[0]),
        observation(frames[5], 6000, "instance-a", anchor=frames[0]),
    ]
    result = confirm_board_timeline(rows)
    assert result["status"] == "REVIEW"
    assert "NON_CONTIGUOUS_INSTANCE_REAPPEARANCE" in result["segments"][2]["review_reasons"]


def test_context_disagreement_and_missing_anchor_stay_review():
    a, b = "a" * 64, "b" * 64
    first = observation(a, 1000, "instance-a", anchor="c" * 64)
    second = observation(b, 2000, "instance-a", anchor="c" * 64)
    second = copy.deepcopy(second)
    second["seat_positions"] = {"top": "W", "right": "N", "bottom": "E", "left": "S"}
    second["rotation_degrees_clockwise"] = 90
    result = confirm_board_timeline([first, second])
    assert result["status"] == "REVIEW"
    assert set(result["segments"][0]["review_reasons"]) == {"ANCHOR_FRAME_MISSING", "BOARD_CONTEXT_DISAGREEMENT"}


def test_source_chronology_duplicates_and_support_are_fail_closed():
    a, b = "a" * 64, "b" * 64
    first = observation(a, 1000, "instance-a", anchor=a)
    with pytest.raises(BoardTimelineError, match="sources"):
        confirm_board_timeline([first, observation(b, 2000, "instance-a", anchor=a, source="drive:file-2:v1")])
    with pytest.raises(BoardTimelineError, match="chronological"):
        confirm_board_timeline([first, observation(b, 500, "instance-a", anchor=a)])
    with pytest.raises(BoardTimelineError, match="cannot be lowered"):
        confirm_board_timeline([], min_support=1)


def test_tampered_cycle_or_unbound_provenance_is_rejected():
    a = "a" * 64
    row = observation(a, 1000, "instance-a", anchor=a)
    tampered = copy.deepcopy(row)
    tampered["board_metadata"]["dealer"] = "E"
    with pytest.raises(BoardTimelineError, match="mechanics"):
        confirm_board_timeline([tampered])
    tampered = copy.deepcopy(row)
    tampered["board_metadata"]["provenance"]["dealer"]["frame_sha256"] = "b" * 64
    with pytest.raises(BoardTimelineError, match="frame-bound"):
        confirm_board_timeline([tampered])
