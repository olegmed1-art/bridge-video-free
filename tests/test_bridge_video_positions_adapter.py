import json
from pathlib import Path

from tools.bridge_video_positions import _bind_speech_declarations, process_job_frames


def _speech(**overrides):
    value = {
        "evidence_locator": "transcript.jsonl#segment=7",
        "start": 8.0,
        "end": 12.0,
        "source_fingerprint": "source-1",
    }
    value.update(overrides)
    return value


def _frames(*times):
    return [
        {"time": float(value), "file": f"frame-{index}.jpg", "sha256": str(index + 1) * 64}
        for index, value in enumerate(times)
    ]


def test_speech_interval_binds_only_the_unique_nearest_hash_bound_frame():
    bound, unbound = _bind_speech_declarations(
        [_speech(start=8.5, end=11.5)], frames=_frames(9, 10, 11),
        source_fingerprint="source-1",
    )

    assert unbound == []
    assert set(bound) == {"frame-1.jpg"}
    _, declaration = bound["frame-1.jpg"][0]
    assert declaration["frame_sha256"] == "2" * 64
    assert declaration["frame_binding_evidence"]["single_frame_binding"] is True
    assert declaration["frame_binding_evidence"]["method"] == "NEAREST_FRAME_INSIDE_SPEECH_INTERVAL"


def test_speech_nearest_frame_tie_remains_review_instead_of_spreading():
    bound, unbound = _bind_speech_declarations(
        [_speech()], frames=_frames(9, 11), source_fingerprint="source-1",
    )

    assert bound == {}
    assert unbound == [{
        "index": 0,
        "evidence_locator": "transcript.jsonl#segment=7",
        "reason": "AMBIGUOUS_NEAREST_FRAME",
    }]


def test_speech_file_only_or_wrong_source_never_binds():
    frames = _frames(10)
    bound, unbound = _bind_speech_declarations(
        [_speech(frame_file="frame-0.jpg"), _speech(source_fingerprint="other")],
        frames=frames, source_fingerprint="source-1",
    )

    assert bound == {}
    assert [item["reason"] for item in unbound] == [
        "FRAME_FILE_WITHOUT_SHA256", "SOURCE_IDENTITY_MISMATCH",
    ]


def test_explicit_frame_hash_must_be_inside_phrase_interval():
    frames = _frames(10)
    bound, unbound = _bind_speech_declarations(
        [_speech(start=1.0, end=2.0, frame_sha256="1" * 64)],
        frames=frames, source_fingerprint="source-1",
    )

    assert bound == {}
    assert unbound[0]["reason"] == "FRAME_OUTSIDE_SPEECH_INTERVAL"


def test_repeated_pixel_hash_needs_unique_timestamp_or_filename_binding():
    frames = [
        {"time": 9.0, "file": "first.jpg", "sha256": "a" * 64},
        {"time": 11.0, "file": "second.jpg", "sha256": "a" * 64},
    ]
    bound, unbound = _bind_speech_declarations(
        [_speech(frame_sha256="a" * 64)], frames=frames,
        source_fingerprint="source-1",
    )
    assert bound == {}
    assert unbound[0]["reason"] == "FRAME_SHA256_AMBIGUOUS"

    bound, unbound = _bind_speech_declarations(
        [_speech(frame_sha256="a" * 64, frame_file="second.jpg")], frames=frames,
        source_fingerprint="source-1",
    )
    assert unbound == []
    assert set(bound) == {"second.jpg"}


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
        "derived_fourth_hand_frames": 0,
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
