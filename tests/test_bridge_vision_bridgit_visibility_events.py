from __future__ import annotations

import hashlib

import pytest

from bridge_vision.bridgit_visibility_events import (
    EventDrivenRecognitionGate,
    VisibilityEventError,
    normalize_four_seat_state,
    normalize_seat_state,
    registered_seat_region,
)


def sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def seat(
    name: str,
    *,
    observed: bool = True,
    closed: bool = False,
    face_up: bool = False,
    ambiguous: bool = False,
    cards: list[str] | None = None,
) -> dict:
    return {
        "seat": name,
        "region_observed": observed,
        "closed_hand_visible": closed,
        "face_up_cards_visible": face_up,
        "recognition_ambiguous": ambiguous,
        "recognized_cards": cards or [],
    }


def test_closed_and_not_observed_are_normal_not_errors() -> None:
    closed = normalize_seat_state(seat("E", closed=True))
    missing = normalize_seat_state(seat("W", observed=False))
    assert closed["state"] == "CLOSED"
    assert missing["state"] == "NOT_OBSERVED"
    assert not closed["is_error"]
    assert not missing["is_error"]
    assert closed["deck_complement_used"] is False
    assert missing["hidden_hand_inference_used"] is False


def test_visible_ambiguous_is_the_error_state() -> None:
    result = normalize_seat_state(
        seat("N", face_up=True, ambiguous=True, cards=["AS", "KS"])
    )
    assert result["state"] == "VISIBLE_AMBIGUOUS"
    assert result["is_error"] is True


def test_visible_partial_is_normal_and_never_completed() -> None:
    result = normalize_seat_state(seat("S", face_up=True, cards=["AS", "KH"]))
    assert result["state"] == "VISIBLE_PARTIAL"
    assert result["recognized_cards"] == ["AS", "KH"]
    assert result["unknown_count"] == 11
    assert result["deck_complement_used"] is False


def test_four_seat_state_supports_any_open_hand() -> None:
    result = normalize_four_seat_state(
        [
            seat("N", closed=True),
            seat("E", face_up=True, cards=["AS"]),
            seat("S", observed=False),
            seat("W", closed=True),
        ]
    )
    assert result["seats"]["E"]["state"] == "VISIBLE_PARTIAL"
    assert result["seats"]["N"]["state"] == "CLOSED"
    assert result["seats"]["S"]["state"] == "NOT_OBSERVED"
    assert result["deck_complement_used"] is False


def test_four_seat_state_requires_exact_coverage() -> None:
    with pytest.raises(VisibilityEventError, match="cover N/E/S/W"):
        normalize_four_seat_state(
            [seat("N", closed=True), seat("E", closed=True), seat("S", closed=True), seat("S", closed=True)]
        )


def test_registered_regions_scale_and_translate_without_theme_dependency() -> None:
    small = registered_seat_region(
        "E", game_window_x=100, game_window_y=50, game_window_width=1000, game_window_height=600
    )
    large = registered_seat_region(
        "E", game_window_x=200, game_window_y=100, game_window_width=2000, game_window_height=1200
    )
    assert large["x"] == 2 * small["x"]
    assert large["y"] == 2 * small["y"]
    assert large["width"] == 2 * small["width"]
    assert large["height"] == 2 * small["height"]
    assert small["orientation"] == "VERTICAL"
    assert registered_seat_region(
        "N", game_window_x=0, game_window_y=0, game_window_width=1000, game_window_height=600
    )["orientation"] == "HORIZONTAL"


def test_event_gate_waits_for_two_independent_frames() -> None:
    gate = EventDrivenRecognitionGate()
    state = sha("state-a")
    first = gate.observe(state_sha256=state, frame_sha256=sha("f1"), decoded_pixel_sha256=sha("p1"))
    second = gate.observe(state_sha256=state, frame_sha256=sha("f2"), decoded_pixel_sha256=sha("p2"))
    assert first["action"] == "WAIT_FOR_STABILITY"
    assert first["run_heavy_observer"] is False
    assert second["action"] == "STABLE_STATE_CHANGE"
    assert second["run_heavy_observer"] is True


def test_reencoded_or_duplicate_pixels_do_not_confirm_change() -> None:
    gate = EventDrivenRecognitionGate()
    state = sha("state-a")
    gate.observe(state_sha256=state, frame_sha256=sha("f1"), decoded_pixel_sha256=sha("same-pixels"))
    duplicate_pixels = gate.observe(
        state_sha256=state,
        frame_sha256=sha("f2-different-bytes"),
        decoded_pixel_sha256=sha("same-pixels"),
    )
    assert duplicate_pixels["action"] == "WAIT_FOR_STABILITY"
    assert duplicate_pixels["support"] == 1


def test_unchanged_stable_state_never_retriggers_heavy_observer() -> None:
    gate = EventDrivenRecognitionGate()
    state = sha("state-a")
    gate.observe(state_sha256=state, frame_sha256=sha("f1"), decoded_pixel_sha256=sha("p1"))
    gate.observe(state_sha256=state, frame_sha256=sha("f2"), decoded_pixel_sha256=sha("p2"))
    again = gate.observe(state_sha256=state, frame_sha256=sha("f3"), decoded_pixel_sha256=sha("p3"))
    assert again == {"action": "NO_CHANGE", "run_heavy_observer": False, "support": 0}


def test_candidate_change_resets_when_state_changes_again() -> None:
    gate = EventDrivenRecognitionGate()
    a = sha("state-a")
    b = sha("state-b")
    gate.observe(state_sha256=a, frame_sha256=sha("a1"), decoded_pixel_sha256=sha("ap1"))
    result = gate.observe(state_sha256=b, frame_sha256=sha("b1"), decoded_pixel_sha256=sha("bp1"))
    assert result["action"] == "WAIT_FOR_STABILITY"
    assert result["support"] == 1


def test_no_timer_or_polling_field_exists() -> None:
    gate = EventDrivenRecognitionGate()
    assert not hasattr(gate, "poll_interval")
    assert not hasattr(gate, "timer")
