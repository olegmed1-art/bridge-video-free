import json
from pathlib import Path

import pytest

from bridge_vision.gold_corpus import GoldCorpusError, load_jsonl, to_detector_cases, validate_case


def test_gold_case_requires_human_verification_and_sha():
    with pytest.raises(GoldCorpusError, match="human_verified"):
        validate_case({"frame": "x.jpg", "frame_sha256": "a" * 64, "hands": {"N": ["AS"]}})
    case = validate_case({
        "frame": "x.jpg",
        "frame_sha256": "a" * 64,
        "human_verified": True,
        "hands": {"N": ["AS", "10h"]},
    })
    assert case["hands"]["N"] == ["AS", "TH"]


def test_gold_jsonl_rejects_duplicate_frame_sha(tmp_path: Path):
    row = {"frame": "x.jpg", "frame_sha256": "b" * 64, "human_verified": True, "hands": {"N": ["AS"]}}
    p = tmp_path / "gold.jsonl"
    p.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(GoldCorpusError, match="duplicate"):
        load_jsonl(p)


def test_gold_frames_must_exist(tmp_path: Path):
    case = validate_case({"frame": "x.jpg", "frame_sha256": "c" * 64, "human_verified": True, "hands": {"S": ["KH"]}})
    with pytest.raises(GoldCorpusError, match="missing"):
        to_detector_cases([case], tmp_path)
    (tmp_path / "x.jpg").write_bytes(b"frame")
    converted = to_detector_cases([case], tmp_path)
    assert converted[0]["hands"]["S"] == ["KH"]
