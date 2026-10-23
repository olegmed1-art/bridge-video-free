"""Pixels and independent dimensions are the oracle; never recognizer labels."""
import os
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from bridge_vision.gambler_card_scale import CardScale, compatible_variants, measure_card_scale, scales_agree
from bridge_vision.gambler_classic_reference import VARIANT_CARD_SIZE, decode_card_cells, validate_sprite_bytes
from bridge_vision.gambler_reference_authority import pinned_sprite_sha256


PROFILE = SimpleNamespace(width=1200, height=800,
                          horizontal_search={"N": (20, 1000, 36), "S": (20, 1000, 436)})


def card(width, height, *, ink=True):
    cell = np.zeros((height, width, 3), dtype=np.uint8)
    cell[1:-1, 1:-1] = 255
    if ink:
        cell[5:max(8, height // 7), 5:max(7, width // 9)] = 0
    return cell


def scene(width=109, height=147, *, overlap=False, ink=True):
    image = np.full((800, 1200, 3), (40, 100, 40), dtype=np.uint8)
    for x in (70, 640):
        for shift in ((0, width // 4, width // 2) if overlap else (0,)):
            image[31:31 + height, x + shift:x + shift + width] = card(width, height, ink=ink)
    return image


@pytest.mark.parametrize("variant,size", VARIANT_CARD_SIZE.items())
@pytest.mark.parametrize("overlap", [False, True])
def test_eight_pixel_scales(variant, size, overlap):
    result = measure_card_scale(scene(*size, overlap=overlap), PROFILE)
    assert result.status == "MEASURED", result
    assert (result.width_px, result.height_px) == size
    assert result.width_state == result.height_state == "measured"
    assert compatible_variants(*size)[0][1] == variant


@pytest.mark.parametrize("kind", ["blank", "panel", "borderless", "clipped", "one", "wrong_aspect"])
def test_false_or_insufficient_boundaries_abstain(kind):
    image = scene()
    if kind == "blank":
        image[:] = 50
    elif kind == "panel":
        image = scene(ink=False)
    elif kind == "borderless":
        image[31:178, 70:179] = 255
        image[31:178, 640:749] = 255
    elif kind == "clipped":
        image[170:] = (40, 100, 40)
    elif kind == "one":
        image[:, 600:] = (40, 100, 40)
    elif kind == "wrong_aspect":
        image = scene(109, 200)
    assert measure_card_scale(image, PROFILE).status == "UNKNOWN"


def test_mixed_scales_abstain_even_with_a_majority():
    image = scene()
    image[31:127, 300:371] = card(71, 96)
    result = measure_card_scale(image, PROFILE)
    assert result.status == "TIE"
    assert result.width_px is None


def test_height_does_not_imply_width_or_native_variant():
    result = measure_card_scale(scene(65, 147), PROFILE)
    assert result.status == "UNKNOWN"
    assert result.height_state == "measured"
    assert result.width_state == "unknown"
    assert result.width_px is None


def test_video_zoom_drift_is_not_stable():
    a = measure_card_scale(scene(), PROFILE)
    b = measure_card_scale(scene(89, 120), PROFILE)
    assert scales_agree(a, a)
    assert not scales_agree(a, b)
    assert not scales_agree(a, CardScale("UNKNOWN", "no_edges"))


def test_fan_completion_is_not_a_scale_oracle():
    observed = [109, 109, 109, 334]
    assert [109 + 25 * (n - 1) for n in [1, 1, 1, 10]] == observed
    assert [64 + 45 * (n - 1) for n in [2, 2, 2, 7]] == observed
    # No pixels is still UNKNOWN, irrespective of either 13-card fit.
    assert measure_card_scale(np.zeros((800, 1200, 3), np.uint8), PROFILE).status == "UNKNOWN"


def test_clipped_terminal_card_does_not_turn_plausible_overlap_strips_into_width():
    image = np.full((800, 1200, 3), (40, 100, 40), dtype=np.uint8)
    cell = card(109, 147)
    for x in range(70, 1200, 98):
        visible = min(109, 1200 - x)
        image[31:178, x:x + visible] = cell[:, :visible]
    result = measure_card_scale(image, PROFILE)
    assert result.status == "UNKNOWN"
    assert result.width_px is None


@pytest.mark.parametrize("variant,size", VARIANT_CARD_SIZE.items())
def test_original_pinned_sprites_as_pixel_fixtures(variant, size):
    root = os.environ.get("GAMBLER_CLASSIC_FIXTURE_ROOT")
    if not root:
        pytest.skip("set GAMBLER_CLASSIC_FIXTURE_ROOT for original pinned sprites")
    sprite = validate_sprite_bytes((Path(root) / str(variant) / "all.png").read_bytes(),
                                   expected_sha256=pinned_sprite_sha256(variant), expected_variant=variant)
    cell = decode_card_cells(sprite)["AC"]
    if cell.dtype == np.uint16:
        cell = np.rint(cell.astype(np.float64) * (255 / 65535)).astype(np.uint8)
    assert cell.dtype == np.uint8
    image = scene(*size)
    w, h = size
    for x in (70, 640):
        for shift in (0, w // 4, w // 2):
            target = image[31:31 + h, x + shift:x + shift + w]
            if cell.shape[2] == 4:
                alpha = cell[:, :, 3:4].astype(float) / 255
                target[:] = np.rint(cell[:, :, :3] * alpha + target * (1 - alpha)).astype(np.uint8)
            else:
                target[:] = cell
    result = measure_card_scale(image, PROFILE)
    assert result.status == "MEASURED", result
    assert abs(result.width_px - w) <= 1
    assert abs(result.height_px - h) <= 1
