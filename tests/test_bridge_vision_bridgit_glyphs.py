import pytest
from bridge_vision.bridgit_glyphs import glyph_boxes_for_start
def test_glyph_boxes_stay_inside_exposed_corner():
 b=glyph_boxes_for_start(316,60,visible_width=29,visible_height=116);assert b is not None;assert b.rank.x>=316 and b.rank.x+b.rank.w<=345;assert b.suit.x>=316 and b.suit.x+b.suit.w<=345;assert b.rank.y<b.suit.y
def test_too_narrow_or_short_card_fails_closed():
 assert glyph_boxes_for_start(10,20,visible_width=17,visible_height=100) is None;assert glyph_boxes_for_start(10,20,visible_width=29,visible_height=37) is None
def test_invalid_geometry_is_rejected():
 with pytest.raises(ValueError):glyph_boxes_for_start(-1,20,visible_width=29,visible_height=100)
