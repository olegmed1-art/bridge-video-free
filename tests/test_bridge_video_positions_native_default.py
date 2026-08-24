import json
from pathlib import Path

from bridge_vision import BridgeVisionEngine
from tools.bridge_video_positions import process_job_frames


def test_video_positions_uses_native_engine_and_keeps_legacy_off(tmp_path: Path):
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frame.jpg").write_bytes(b"frame")
    (tmp_path / "manifest.json").write_text(
        json.dumps({
            "job_id": "job-1",
            "source_fingerprint": "src-1",
            "frames": [{"time": 12.5, "file": "frame.jpg", "sha256": "a" * 64}],
        }),
        encoding="utf-8",
    )
    engine = BridgeVisionEngine({
        "school-native-test": lambda _: {
            "hands": {"N": ["AS", "KH"], "S": ["QD", "JC"]},
            "confidence": 0.95,
            "evidence": {"kind": "native-test"},
        }
    })
    summary = process_job_frames(tmp_path, engine=engine)
    assert summary["vision_engine"] == "native"
    assert summary["legacy_old_bbo_enabled"] is False
    assert summary["detectors"] == ["school-native-test"]
    record = json.loads((tmp_path / "bridge_positions.jsonl").read_text(encoding="utf-8"))
    assert record["engine_version"] == "bridge-vision-native-v1"
    assert record["deal"]["hands"]["E"]["cards"] == []
    assert record["deal"]["hands"]["W"]["cards"] == []
