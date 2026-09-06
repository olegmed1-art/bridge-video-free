from __future__ import annotations

import pytest

import bridge_vision.anchor_registration as anchor_registration
from bridge_vision.anchor_registration import (
    AnchorRegistrationError,
    estimate_anchor_work_units,
    register_from_upper_right_anchor,
    validate_anchor_spec,
    validate_anchor_job_budget,
)

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


def reference_frame() -> np.ndarray:
    frame = np.full((120, 200, 3), 35, dtype=np.uint8)
    cv2.rectangle(frame, (5, 5), (194, 114), (65, 65, 65), 2)
    cv2.line(frame, (20, 60), (180, 60), (90, 90, 90), 2)
    cv2.rectangle(frame, (150, 8), (181, 29), (245, 245, 245), 2)
    cv2.circle(frame, (166, 18), 7, (20, 20, 20), 2)
    cv2.line(frame, (157, 18), (175, 18), (220, 220, 220), 1)
    cv2.line(frame, (166, 9), (166, 27), (220, 220, 220), 1)
    return frame


def anchor_spec(**changes) -> dict:
    spec = {
        "type": "UPPER_RIGHT_TEMPLATE",
        "reference_region": {"x": 0.74, "y": 0.05, "width": 0.18, "height": 0.22},
        "scales": [1.0, 1.25, 1.5],
        "minimum_score": 0.65,
        "minimum_margin": 0.04,
    }
    spec.update(changes)
    return spec


def place(frame: np.ndarray, *, scale: float, x: int, y: int, invert: bool = False):
    resized = cv2.resize(
        frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST
    )
    if invert:
        resized = 255 - resized
    canvas = np.full((300, 450, 3), 120, dtype=np.uint8)
    canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    return canvas


@pytest.mark.parametrize("invert", [False, True])
def test_upper_right_anchor_registers_scale_translation_and_theme(invert: bool):
    reference = reference_frame()
    observed = place(reference, scale=1.5, x=70, y=40, invert=invert)

    registered, evidence = register_from_upper_right_anchor(
        reference, observed, anchor_spec()
    )

    assert registered.shape == reference.shape
    assert evidence["mode"] == "UPPER_RIGHT_ANCHOR"
    assert evidence["appearance"] == "INTENSITY_INVERSION_INVARIANT"
    assert evidence["scale"] == 1.5
    assert evidence["score"] >= 0.65
    assert evidence["game_window"] == {
        "coordinate_space": "NORMALIZED_INPUT_FRAME",
        "x": pytest.approx(70 / 450, abs=0.005),
        "y": pytest.approx(40 / 300, abs=0.005),
        "width": pytest.approx(300 / 450, abs=0.005),
        "height": pytest.approx(180 / 300, abs=0.005),
    }


def test_missing_and_ambiguous_anchor_fail_closed():
    reference = reference_frame()
    missing = np.full((300, 450, 3), 120, dtype=np.uint8)
    with pytest.raises(AnchorRegistrationError, match="score is too low"):
        register_from_upper_right_anchor(reference, missing, anchor_spec())

    one = cv2.resize(reference, None, fx=1.0, fy=1.0, interpolation=cv2.INTER_NEAREST)
    ambiguous = np.full((300, 450, 3), 120, dtype=np.uint8)
    ambiguous[20:140, 20:220] = one
    ambiguous[160:280, 230:430] = one
    with pytest.raises(AnchorRegistrationError, match="ambiguous"):
        register_from_upper_right_anchor(
            reference, ambiguous, anchor_spec(scales=[1.0])
        )


def test_anchor_profile_is_normalized_bounded_and_upper_right():
    checked = validate_anchor_spec(anchor_spec())
    assert checked["reference_region"]["x"] == 0.74
    assert checked["scales"] == [1.0, 1.25, 1.5]

    invalid = anchor_spec()
    invalid["reference_region"] = {"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1}
    with pytest.raises(AnchorRegistrationError, match="right half"):
        validate_anchor_spec(invalid)


def test_different_scale_or_window_size_is_a_competing_anchor():
    best = {
        "scale": 1.0,
        "window_x": 20,
        "window_y": 10,
        "window_width": 200,
        "window_height": 120,
    }
    same = dict(best)
    different_scale = {
        **best,
        "scale": 1.25,
        "window_width": 250,
        "window_height": 150,
    }

    assert anchor_registration._implies_distinct_window(best, same) is False
    assert anchor_registration._implies_distinct_window(best, different_scale) is True


def test_equivalent_peak_suppression_exposes_a_third_distinct_window():
    scores = np.zeros((80, 120), dtype=np.float32)
    scores[20, 20] = 0.99
    scores[20, 40] = 0.98
    scores[20, 70] = 0.97

    anchor_registration._suppress_equivalent_window_peak(
        scores,
        (20, 20),
        game_width=1000,
        game_height=600,
    )

    assert scores[20, 40] == -1.0
    assert cv2.minMaxLoc(scores)[3] == (70, 20)


def test_anchor_work_is_template_weighted_and_bounded_for_whole_job():
    small_spec = anchor_spec()
    large_spec = anchor_spec()
    large_spec["reference_region"] = {
        "x": 0.70,
        "y": 0.02,
        "width": 0.20,
        "height": 0.30,
    }
    small_work, _ = estimate_anchor_work_units((1000, 720), [(1920, 1080)], small_spec)
    large_work, _ = estimate_anchor_work_units((1000, 720), [(1920, 1080)], large_spec)
    assert large_work > small_work

    with pytest.raises(AnchorRegistrationError, match="job exceeds work budget"):
        validate_anchor_job_budget(
            (1000, 720),
            [(1920, 1080)] * 3,
            {
                **small_spec,
                "reference_region": {
                    "x": 0.72,
                    "y": 0.02,
                    "width": 0.08,
                    "height": 0.10,
                },
            },
        )
