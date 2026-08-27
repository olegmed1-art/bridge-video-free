import pytest
from bridge_vision.gold_manifest import canonical_gold_manifest
SHA="a"*64
def test_gold_manifest_is_deterministic_and_requires_explicit_labels():
 entry={"frame_sha256":SHA,"kind":"rank","label":"A","x":10,"y":20,"w":8,"h":16};one=canonical_gold_manifest([entry]);two=canonical_gold_manifest([dict(reversed(list(entry.items())))]);assert one==two;assert len(one["manifest_sha256"])==64;assert one["entries"][0]["label"]=="A"
def test_gold_manifest_refuses_missing_label_instead_of_inferring_from_order():
 with pytest.raises(ValueError,match="label must be explicit"):canonical_gold_manifest([{"frame_sha256":SHA,"kind":"suit","x":1,"y":2,"w":3,"h":4}])
def test_gold_manifest_refuses_duplicate_crop_with_conflicting_label():
 base={"frame_sha256":SHA,"kind":"rank","x":1,"y":2,"w":3,"h":4}
 with pytest.raises(ValueError,match="duplicate gold crop"):canonical_gold_manifest([{**base,"label":"A"},{**base,"label":"K"}])
