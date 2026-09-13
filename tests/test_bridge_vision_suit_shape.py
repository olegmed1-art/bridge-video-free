from bridge_vision.suit_shape import classify_suit


def test_red_colour_only_limits_family_and_shape_selects_heart():
    result = classify_suit({"H": .97, "D": .21, "S": .99}, colour_family="red")
    assert result["suit"] == "H"
    assert result["confidence"] == .97


def test_black_colour_only_limits_family_and_shape_selects_club():
    result = classify_suit({"S": .12, "C": .96, "H": .99}, colour_family="black")
    assert result["suit"] == "C"


def test_weak_shape_fails_closed():
    result = classify_suit({"H": .72, "D": .10}, colour_family="red")
    assert result["suit"] is None
    assert result["reason"] == "LOW_SUIT_SCORE"


def test_close_heart_diamond_scores_are_ambiguous_not_guessed():
    result = classify_suit({"H": .95, "D": .91}, colour_family="red")
    assert result["suit"] is None
    assert result["reason"] == "AMBIGUOUS_SUIT_SHAPE"
