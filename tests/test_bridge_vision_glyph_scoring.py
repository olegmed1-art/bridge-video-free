import pytest

from bridge_vision.glyph_scoring import classify_mask, mask_iou


def test_glyph_requires_absolute_score_and_runner_up_margin():
    mask = [[True, False], [True, True]]
    result = classify_mask(mask, {"A": mask, "K": [[True, False], [False, True]]})
    assert result["label"] == "A"
    ambiguous = classify_mask(mask, {"A": mask, "K": mask})
    assert ambiguous["label"] is None
    assert ambiguous["reason"] == "AMBIGUOUS_GLYPH"


def test_empty_or_mismatched_masks_fail_closed():
    with pytest.raises(ValueError, match="non-empty"):
        mask_iou([], [])
    with pytest.raises(ValueError, match="dimensions"):
        mask_iou([[True]], [[True, False]])


def test_glyph_thresholds_cannot_be_lowered():
    with pytest.raises(ValueError, match="cannot be lowered"):
        classify_mask([[True]], {"A": [[True]]}, min_score=0.1)
