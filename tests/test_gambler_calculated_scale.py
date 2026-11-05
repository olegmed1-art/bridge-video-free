"""Calculated placements and degradation of synthetic geometric card panels.

No original sprites, real frames, private annotations or holdout are included.
These tests check native-size selection/abstention, never rank/suit accuracy.
"""
from fractions import Fraction

import cv2
import numpy as np
import pytest

from bridge_vision.gambler_classic_reference import VARIANT_CARD_SIZE
from tests.test_diana20_gambler_scale import inventory
from tests.test_gambler_card_scale import card
from tools.diana20_gambler_scale import select_native_scale


def round_half_up(value):
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def calculated_scene(variant, *, uniform_height=False):
    width, height = VARIANT_CARD_SIZE[variant]
    sx, sy = Fraction(width, 109), Fraction(height, 147)
    if uniform_height:
        height = round_half_up(147 * sx)
    image = np.full((1010, 1920, 3), (40, 100, 40), dtype=np.uint8)
    boxes = []
    # Synthetic layout model, not measured coordinates from another video.
    for offset in (0, 178, 356):
        x, y = round_half_up(412 + offset * sx), 25
        image[y:y + height, x:x + width] = card(width, height)
        boxes.append((x, y, width, height))
    return image, boxes, sx, sy


@pytest.mark.parametrize("variant", range(1, 9))
def test_transferred_footprints_keep_native_dimensions(variant):
    image, boxes, sx, sy = calculated_scene(variant)
    width, height = VARIANT_CARD_SIZE[variant]
    for offset, (x, y, w, h) in zip((0, 178, 356), boxes):
        # Integer cross-products independently check four rounded corners.
        for dx, dy in ((0, 0), (109, 0), (0, 147), (109, 147)):
            nx, ny = 412 * 109 + (offset + dx) * width, 25 * 147 + dy * height
            ox, oy = x + (w if dx else 0), y + (h if dy else 0)
            assert (2 * ox - 1) * 109 <= 2 * nx < (2 * ox + 1) * 109
            assert (2 * oy - 1) * 147 <= 2 * ny < (2 * oy + 1) * 147
    assert abs(147 * sx - height) <= 1 if variant <= 5 else abs(147 * sx - height) > 18
    assert 109 * sx == width and 147 * sy == height
    result = select_native_scale(image, inventory())
    assert result.sprite.variant == variant
    assert result.evidence["width_state"] == result.evidence["height_state"] == "measured"
    assert result.evidence["matcher_attempts"] == 0


@pytest.mark.parametrize("variant", (6, 7, 8))
def test_width_only_uniform_transfer_cannot_claim_taller_native_sprite(variant):
    image, _, _, _ = calculated_scene(variant, uniform_height=True)
    result = select_native_scale(image, inventory())
    assert result.sprite is None
    assert result.evidence["selection_status"] == "UNKNOWN"
    assert result.evidence["matcher_attempts"] == 0


@pytest.mark.parametrize("variant", range(1, 9))
@pytest.mark.parametrize("stress", ("jpeg75", "half_pixel"))
def test_degraded_calculated_scene_selects_exact_size_or_abstains(variant, stress):
    image, _, _, _ = calculated_scene(variant)
    if stress == "jpeg75":
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 75])
        assert ok
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    else:
        image = cv2.warpAffine(image, np.float32([[1, 0, .5], [0, 1, .5]]),
                               (1920, 1010), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=(40, 100, 40))
    result = select_native_scale(image, inventory())
    assert result.evidence["selection_status"] in ("SELECTED", "UNKNOWN", "TIE")
    assert result.sprite is None or result.sprite.variant == variant
    assert result.evidence["matcher_attempts"] == 0
    assert result.evidence["template_transform"] == "NONE_NATIVE_ONLY"


def test_transferred_scale_sequence_does_not_reuse_previous_evidence():
    results = []
    sprites = inventory()
    for variant in (5, 1, 8, 4, 6, 2, 7, 3, 5):
        image, _, _, _ = calculated_scene(variant)
        result = select_native_scale(image, sprites)
        assert result.sprite.variant == variant
        results.append(result.evidence)
    assert results[0] == results[-1]
