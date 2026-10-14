from bridge_vision.glyph_scoring import classify_mask, mask_iou


def test_exact_template_is_accepted():
    a = [[1,0],[1,1]]
    b = [[0,1],[1,0]]
    result = classify_mask(a, {"A": a, "B": b}, min_score=.8, min_margin=.1)
    assert result["label"] == "A"
    assert result["confidence"] == 1.0


def test_weak_match_is_rejected():
    result = classify_mask([[1,0],[0,0]], {"A": [[1,1],[1,1]]}, min_score=.75)
    assert result["label"] is None
    assert result["reason"] == "LOW_GLYPH_SCORE"


def test_close_runner_up_is_rejected():
    mask = [[1,1,1],[1,0,0]]
    templates = {"H": [[1,1,1],[1,0,0]], "D": [[1,1,1],[1,1,0]]}
    result = classify_mask(mask, templates, min_score=.5, min_margin=.25)
    assert result["label"] is None
    assert result["reason"] == "AMBIGUOUS_GLYPH"


def test_dimension_mismatch_fails_closed():
    try:
        mask_iou([[1]], [[1,0]])
    except ValueError as exc:
        assert "identical dimensions" in str(exc)
    else:
        raise AssertionError("expected ValueError")
