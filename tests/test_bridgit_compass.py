import pytest

from bridge_vision.bridgit_compass import BridgitCompassError, guard_recognizer_result, parse_bridgit_compass


REGION = {"x": 1228, "y": 31, "w": 99, "h": 101}
SIZE = {"width": 1680, "height": 1010}


def field(value, locator="frame.jpg#compass", confidence=0.99):
    return {"value": value, "confidence": confidence, "evidence_locator": locator}


def compass(board=1, labels=("N", "E", "S", "W"), dealer_position="top"):
    return {
        "interface": "BRIDGIT",
        "human_verified_profile": True,
        "region": dict(REGION),
        "scope": "diana-14",
        "board_number": field(board, "frame.jpg#compass-center"),
        "seat_labels": {position: field(value) for position, value in zip(("top", "right", "bottom", "left"), labels)},
        "dealer_marker": field(dealer_position, "frame.jpg#compass-D"),
        "vulnerability": field("NONE", "frame.jpg#compass-colour"),
    }


@pytest.mark.parametrize(("labels", "rotation"), [
    (("N", "E", "S", "W"), 0),
    (("W", "N", "E", "S"), 90),
    (("S", "W", "N", "E"), 180),
    (("E", "S", "W", "N"), 270),
])
def test_accepts_only_four_bridge_compass_rotations(labels, rotation):
    dealer_position = ("top", "right", "bottom", "left")[labels.index("N")]
    raw = compass(labels=labels, dealer_position=dealer_position)
    parsed = parse_bridgit_compass(raw, expected_region=REGION, reference_size=SIZE)
    assert parsed["rotation_degrees_clockwise"] == rotation
    assert parsed["seat_positions"] == dict(zip(("top", "right", "bottom", "left"), labels))


def test_board_number_becomes_stable_deal_track_and_metadata():
    parsed = parse_bridgit_compass(compass(), expected_region=REGION, reference_size=SIZE)
    assert parsed["deal_identity"] == {"kind": "EXPLICIT_BOARD", "scope": "diana-14", "value": "board-1"}
    assert parsed["board_metadata"]["board_number"]["value"] == 1
    assert parsed["board_metadata"]["dealer"]["value"] == "N"


def test_board_change_splits_deal_identity():
    first = parse_bridgit_compass(compass(), expected_region=REGION, reference_size=SIZE)
    second_raw = compass(board=2, dealer_position="right")
    second_raw["vulnerability"] = field("NS")
    second = parse_bridgit_compass(second_raw, expected_region=REGION, reference_size=SIZE)
    assert first["deal_identity"]["value"] != second["deal_identity"]["value"]


@pytest.mark.parametrize("mutation, message", [
    (lambda raw: raw.update(region={"x": 500, "y": 31, "w": 99, "h": 101}), "verified region"),
    (lambda raw: raw.update(seat_labels={position: field("N") for position in ("top", "right", "bottom", "left")}), "rotation"),
    (lambda raw: raw.update(dealer_marker=field("right")), "dealer marker conflicts"),
    (lambda raw: raw.update(vulnerability=field("EW")), "vulnerability conflicts"),
])
def test_conflicts_fail_closed(mutation, message):
    raw = compass()
    mutation(raw)
    with pytest.raises(BridgitCompassError, match=message):
        parse_bridgit_compass(raw, expected_region=REGION, reference_size=SIZE)


def test_guard_rejects_profile_rotation_disagreement():
    result = {"cards": [], "ordering_prior": {"seat_positions": {"top": "S", "right": "W", "bottom": "N", "left": "E"}}}
    with pytest.raises(BridgitCompassError, match="profile"):
        guard_recognizer_result(result, compass(), expected_region=REGION, reference_size=SIZE)
