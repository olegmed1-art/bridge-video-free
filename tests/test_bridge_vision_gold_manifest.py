import pytest

from bridge_vision.gold_manifest import canonical_gold_manifest

SHA = "b" * 64


def _entry(**updates):
    row = {
        "frame_sha256": SHA, "timestamp_ms": 1234, "source_id": "drive:file:version",
        "partition": "HOLDOUT", "kind": "card", "label": "AS", "seat": "N",
        "visibility": "VISIBLE", "x": 10, "y": 20, "w": 30, "h": 40,
        "human_verified": True, "annotator": "human-a", "reviewer": "human-b",
    }
    row.update(updates)
    return row


def test_manifest_is_deterministic_and_source_bound():
    one = canonical_gold_manifest([_entry()])
    two = canonical_gold_manifest([dict(reversed(list(_entry().items())))])
    assert one == two
    assert len(one["manifest_sha256"]) == 64
    assert one["entries"][0]["timestamp_ms"] == 1234
    assert one["entries"][0]["seat"] == "N"


def test_frame_has_one_source_timestamp_and_partition():
    with pytest.raises(ValueError, match="one source"):
        canonical_gold_manifest([
            _entry(partition="TEMPLATE"),
            _entry(partition="HOLDOUT", kind="rank", label="A", x=50),
        ])


def test_hidden_or_unknown_card_is_not_labelled_by_inference():
    with pytest.raises(ValueError, match="cannot carry"):
        canonical_gold_manifest([_entry(visibility="HIDDEN", label="KS")])
    hidden = canonical_gold_manifest([_entry(visibility="HIDDEN", label=None)])
    assert hidden["entries"][0]["label"] is None


def test_visible_label_and_independent_reviewer_are_required():
    with pytest.raises(ValueError, match="label must be explicit"):
        canonical_gold_manifest([_entry(label=None)])
    with pytest.raises(ValueError, match="independent reviewer"):
        canonical_gold_manifest([_entry(reviewer="human-a")])


def test_duplicate_crop_is_rejected_even_if_label_conflicts():
    with pytest.raises(ValueError, match="duplicate gold crop"):
        canonical_gold_manifest([_entry(), _entry(label="KS")])


def test_channel_labels_are_valid_and_card_cannot_move_seats():
    with pytest.raises(ValueError, match="invalid for its independent channel"):
        canonical_gold_manifest([_entry(kind="suit", label="RED")])
    with pytest.raises(ValueError, match="two seats"):
        canonical_gold_manifest([_entry(), _entry(seat="E", x=50)])
