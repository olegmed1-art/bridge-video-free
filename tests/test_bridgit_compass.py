import copy

import pytest

from bridge_vision.bridgit_compass import BridgitCompassError, parse_bridgit_compass

FRAME = "a" * 64
ANCHOR = FRAME
REGION = {"x": 700, "y": 20, "w": 250, "h": 220}
SIZE = {"width": 1000, "height": 800}


def _field(value, locator, source="VISUAL_TEXT", frame=FRAME):
    return {
        "value": value, "confidence": 0.99, "source": source,
        "frame_sha256": frame, "evidence_locator": locator,
    }


def compass(**updates):
    raw = {
        "interface": "BRIDGIT", "human_verified_profile": True,
        "frame_sha256": FRAME, "deal_anchor_frame_sha256": ANCHOR,
        "timestamp_ms": 1000, "source_id": "drive:file-1:v1",
        "scope": "lesson-session-1", "deal_instance_id": "instance-16-a",
        "region": REGION,
        "seat_labels": {
            "top": _field("W", "compass#top"), "right": _field("N", "compass#right"),
            "bottom": _field("E", "compass#bottom"), "left": _field("S", "compass#left"),
        },
        "board_number": _field(16, "compass#board"),
        "dealer_marker": _field("top", "compass#dealer", source="VISUAL_MARKER"),
        "vulnerability": _field("EW", "compass#vulnerability", source="VISUAL_MARKER"),
    }
    raw.update(updates)
    return raw


def parse(raw=None):
    return parse_bridgit_compass(raw or compass(), expected_region=REGION, reference_size=SIZE)


def test_source_bound_compass_confirms_board_dealer_vulnerability_and_rotation():
    result = parse()
    assert result["board_metadata"]["board_number"] == 16
    assert result["board_metadata"]["dealer"] == "W"
    assert result["board_metadata"]["vulnerability"] == "EW"
    assert result["rotation_degrees_clockwise"] == 90
    assert result["deal_identity"]["instance_id"] == "instance-16-a"
    assert result["production_activation_allowed"] is False


def test_speech_missing_or_wrong_visual_fields_fail_closed():
    raw = compass()
    raw["board_number"]["source"] = "TEACHER_SPEECH"
    with pytest.raises(BridgitCompassError, match="visual evidence"):
        parse(raw)
    raw = compass()
    del raw["vulnerability"]
    with pytest.raises(BridgitCompassError, match="missing vulnerability"):
        parse(raw)
    raw = compass()
    raw["dealer_marker"]["value"] = "right"
    with pytest.raises(BridgitCompassError, match="dealer marker conflicts"):
        parse(raw)


def test_every_field_must_be_bound_to_the_same_frame():
    raw = compass()
    raw["seat_labels"]["left"]["frame_sha256"] = "b" * 64
    with pytest.raises(BridgitCompassError, match="not bound"):
        parse(raw)


def test_threshold_cannot_be_lowered_and_region_is_profile_bound():
    with pytest.raises(BridgitCompassError, match="cannot be lowered"):
        parse_bridgit_compass(compass(), expected_region=REGION, reference_size=SIZE, min_confidence=0.5)
    raw = compass(region={**REGION, "x": 650})
    with pytest.raises(BridgitCompassError, match="verified region"):
        parse(raw)


def test_noncyclic_seat_mapping_is_rejected():
    raw = copy.deepcopy(compass())
    raw["seat_labels"]["right"]["value"] = "S"
    with pytest.raises(BridgitCompassError, match="complete compass rotation"):
        parse(raw)
