import json
from pathlib import Path

from tools.bridge_video_positions import process_job_frames


def test_universal_video_frames_flow_into_existing_recognizer_contract(tmp_path: Path):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    frame = frames_dir / "frame-0000-000000.jpg"
    frame.write_bytes(b"fake-frame-for-contract-test")
    manifest = {
        "job_id": "video-job-1",
        "source_fingerprint": "source-fp",
        "frames": [
            {
                "time": 0.0,
                "file": frame.name,
                "sha256": "a" * 64,
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    seen = []

    def fake_existing_parser(path: Path):
        seen.append(path)
        return {
            "status": "PARTIAL_BOARD_OBSERVATION",
            "hands": {"N": ["AS", "KS"], "S": ["QH", "JH"]},
            "recognized_card_count": 4,
            "state_fingerprint": "0123456789abcdefabcd",
        }

    summary = process_job_frames(tmp_path, parser=fake_existing_parser)

    assert seen == [frame.resolve()]
    assert summary == {
        "status": "COMPLETED",
        "job_id": "video-job-1",
        "source_fingerprint": "source-fp",
        "input_frames": 1,
        "output_records": 1,
        "recognized_frames": 1,
        "conflict_frames": 0,
        "derive_fourth_hand": False,
        "output": "bridge_positions.jsonl",
    }
    record = json.loads((tmp_path / "bridge_positions.jsonl").read_text(encoding="utf-8"))
    assert record["frame_file"] == frame.name
    assert record["time"] == 0.0
    assert record["deal"]["hands"]["N"]["cards"] == ["AS", "KS"]
    assert record["deal"]["hands"]["S"]["cards"] == ["QH", "JH"]
    assert record["deal"]["hands"]["E"]["unknown_count"] == 13
    assert record["deal"]["hands"]["W"]["unknown_count"] == 13
    assert record["deal"]["derivations"] == []


def test_adapter_keeps_insufficient_frames_as_evidence_records(tmp_path: Path):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    frame = frames_dir / "frame-0000-000000.jpg"
    frame.write_bytes(b"fake")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"job_id": "j", "frames": [{"time": 0, "file": frame.name, "sha256": "b" * 64}]}),
        encoding="utf-8",
    )

    summary = process_job_frames(
        tmp_path,
        parser=lambda _: {
            "status": "INSUFFICIENT",
            "hands": {"N": ["AS", "KS"]},
            "recognized_card_count": 2,
            "state_fingerprint": "fedcba9876543210abcd",
        },
    )

    assert summary["recognized_frames"] == 1
    record = json.loads((tmp_path / "bridge_positions.jsonl").read_text(encoding="utf-8"))
    assert record["parser_status"] == "INSUFFICIENT"
    assert record["deal"]["hands"]["N"]["cards"] == ["AS", "KS"]
    assert record["deal"]["hands"]["N"]["unknown_count"] == 11
