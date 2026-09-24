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
            "frame_evidence": {
                "schema": "universal-video-frame-evidence-v1",
                "strategy": "anchor-neighbors-v1",
                "regions": {
                    "N": {"x": 0.15, "y": 0.0, "width": 0.7, "height": 0.3},
                    "E": {"x": 0.7, "y": 0.15, "width": 0.3, "height": 0.7},
                    "S": {"x": 0.15, "y": 0.7, "width": 0.7, "height": 0.3},
                    "W": {"x": 0.0, "y": 0.15, "width": 0.3, "height": 0.7},
                    "CENTER": {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5},
                },
                "bundles": [{
                    "bundle_id": "evidence-0001",
                    "anchor_time": 12.5,
                    "members": [{"role": "CENTER", "time": 12.5, "offset_seconds": 0, "file": "frame.jpg"}],
                }],
            },
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
    assert record["engine_version"] == "bridge-vision-native-v2"
    assert record["deal"]["hands"]["E"]["cards"] == []
    assert record["deal"]["hands"]["W"]["cards"] == []
    assert record["frame_evidence"]["memberships"][0]["bundle_id"] == "evidence-0001"
    assert record["frame_evidence"]["memberships"][0]["role"] == "CENTER"
    assert record["frame_evidence"]["crop_regions"]["N"]["width"] == 0.7
    assert summary["frame_evidence_bundle_count"] == 1
    assert summary["canonical_promotion_allowed"] is False
