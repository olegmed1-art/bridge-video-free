import pytest

from bridge_vision.suit_shape import classify_suit


def test_colour_filters_family_but_shape_selects_suit():
    result = classify_suit({"H": 0.93, "D": 0.20, "S": 1.0}, colour_family="red")
    assert result["suit"] == "H"
    assert result["evidence"]["colour_role"] == "FAMILY_FILTER_ONLY"


def test_colour_alone_or_ambiguous_shape_never_names_suit():
    assert classify_suit({}, colour_family="black")["suit"] is None
    result = classify_suit({"S": 0.95, "C": 0.91}, colour_family="black")
    assert result["suit"] is None
    assert result["reason"] == "AMBIGUOUS_SUIT_SHAPE"


def test_suit_thresholds_cannot_be_lowered():
    with pytest.raises(ValueError, match="cannot be lowered"):
        classify_suit({"S": 0.5, "C": 0.0}, colour_family="black", min_score=0.5)
