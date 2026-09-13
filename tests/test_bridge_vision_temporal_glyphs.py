import pytest

from bridge_vision.temporal_glyphs import stable_consensus

SHA1, SHA2 = "a" * 64, "b" * 64
MASK = [[True, False], [True, True]]


def test_consensus_requires_stable_distinct_source_frames():
    result = stable_consensus([
        {"frame_sha256": SHA1, "mask": MASK}, {"frame_sha256": SHA2, "mask": MASK},
    ])
    assert result["status"] == "STABLE"
    assert result["stable_frames"] == [SHA1, SHA2]
    with pytest.raises(ValueError, match="distinct frames"):
        stable_consensus([
            {"frame_sha256": SHA1, "mask": MASK}, {"frame_sha256": SHA1, "mask": MASK},
        ])


def test_insufficient_or_unstable_evidence_does_not_form_template():
    assert stable_consensus([{"frame_sha256": SHA1, "mask": MASK}])["status"] == "INSUFFICIENT_SUPPORT"
    result = stable_consensus([
        {"frame_sha256": SHA1, "mask": [[True, False], [False, False]]},
        {"frame_sha256": SHA2, "mask": [[False, True], [False, False]]},
    ])
    assert result["status"] == "UNSTABLE"
    assert result["template"] is None


def test_temporal_thresholds_cannot_be_lowered():
    with pytest.raises(ValueError, match="cannot be lowered"):
        stable_consensus([], min_pair_iou=0.5)
    with pytest.raises(ValueError, match="cannot be lowered"):
        stable_consensus([], min_support=1)
