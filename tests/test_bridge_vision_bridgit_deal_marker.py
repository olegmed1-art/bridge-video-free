from __future__ import annotations

import pytest

from bridge_vision.bridgit_deal_marker import (
    DealMarkerError,
    assign_stable_deal_markers,
    hamming_distance,
)


def marked(index: int, bits: str) -> dict:
    return {
        "timestamp_ms": index * 1000,
        "deal_marker_fingerprint": bits,
        "cards": [],
    }


def test_debounces_two_stable_visual_deals_without_mixing_transition() -> None:
    first = "0f" * 64
    near_first = "0f" * 63 + "0e"
    transient = "aa" * 64
    second = "f0" * 64
    frames = [
        marked(1, first),
        marked(2, near_first),
        marked(3, transient),
        marked(4, first),
        marked(5, second),
        marked(6, second),
        marked(7, second),
    ]

    result = assign_stable_deal_markers(frames, max_hamming_distance=2)

    accepted = result["accepted_frames"]
    assert [frame["timestamp_ms"] for frame in accepted] == [
        1000,
        2000,
        4000,
        5000,
        6000,
        7000,
    ]
    assert accepted[0]["deal_marker_sha256"] == accepted[2]["deal_marker_sha256"]
    assert accepted[3]["deal_marker_sha256"] == accepted[5]["deal_marker_sha256"]
    assert accepted[0]["deal_marker_sha256"] != accepted[3]["deal_marker_sha256"]
    assert result["rejected_frames"] == [
        {
            "frame_index": 2,
            "reason": "UNSTABLE_DEAL_MARKER_TRANSITION",
            "deal_marker_fingerprint": transient,
        }
    ]


def test_one_frame_board_never_becomes_a_deal() -> None:
    result = assign_stable_deal_markers([marked(1, "0" * 128), marked(2, "f" * 128)])

    assert result["accepted_frames"] == []
    assert len(result["rejected_frames"]) == 2


def test_marker_hamming_input_and_bounds_are_strict() -> None:
    assert hamming_distance("0" * 128, "0" * 127 + "1") == 1
    with pytest.raises(DealMarkerError, match="512 lowercase bits"):
        hamming_distance("A" * 128, "0" * 128)
    with pytest.raises(DealMarkerError, match="outside 0..64"):
        assign_stable_deal_markers([marked(1, "0" * 128)], max_hamming_distance=65)
