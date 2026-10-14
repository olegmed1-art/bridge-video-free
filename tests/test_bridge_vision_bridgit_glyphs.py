import pytest

from bridge_vision.bridgit_glyphs import glyph_boxes_for_start


def test_glyph_boxes_stay_inside_exposed_corner():
    boxes = glyph_boxes_for_start(316, 60, visible_width=29, visible_height=116)
    assert boxes is not None
    assert boxes.rank.x >= 316 and boxes.rank.x + boxes.rank.w <= 345
    assert boxes.suit.x >= 316 and boxes.suit.x + boxes.suit.w <= 345
    assert boxes.rank.y < boxes.suit.y


def test_too_narrow_or_short_card_fails_closed():
    assert glyph_boxes_for_start(10, 20, visible_width=17, visible_height=100) is None
    assert glyph_boxes_for_start(10, 20, visible_width=29, visible_height=37) is None


def test_invalid_geometry_is_rejected():
    with pytest.raises(ValueError):
        glyph_boxes_for_start(-1, 20, visible_width=29, visible_height=100)
