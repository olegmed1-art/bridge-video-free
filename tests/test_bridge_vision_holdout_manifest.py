"""Manifest checks are metadata/file-integrity tests, not recognizer accuracy."""
from __future__ import annotations

import hashlib
import json
import os
import struct
import zlib
from pathlib import Path

import pytest

from bridge_vision.bridgit_holdout_manifest import SCHEMA_VERSION, validate_holdout_manifest


def _png(value: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes([0, value, value, value])))
            + chunk(b"IEND", b""))


def _manifest(root: Path) -> dict:
    frames = []
    for index, value in enumerate((10, 20)):
        name = f"synthetic-{index}.png"
        raw = _png(value)
        (root / name).write_bytes(raw)
        read = {"status": "READ", "number": 12}
        frames.append({
            "path": name, "sha256": hashlib.sha256(raw).hexdigest(),
            "pts_ms": 1000 + 600 * index, "observation_index": index,
            "source_video_sha256": "a" * 64, "episode_id": "synthetic-episode",
            "board_epoch": 1, "board_number": 12,
            "board_number_read": dict(read), "board_number_confirming_read": dict(read),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "source_video": {"id": "synthetic-video", "sha256": "a" * 64},
        "episode_id": "synthetic-episode", "board_epoch": 1,
        "board_number": 12, "frames": frames,
    }


def test_synthetic_pair_is_structurally_valid_but_not_accuracy_evidence(tmp_path: Path):
    result = validate_holdout_manifest(_manifest(tmp_path), tmp_path)
    assert result["status"] == "STRUCTURAL_PASS"
    assert result["ocr_accuracy_evaluated"] is False
    assert result["source_pts_verified_by_decode"] is False


def test_non_object_manifest_fails_closed(tmp_path: Path):
    assert validate_holdout_manifest([], tmp_path)["issues"] == ["MANIFEST_INVALID"]


def test_existing_single_frame_receipt_cannot_be_promoted_to_two_frame_holdout(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "single_frame_holdout_incomplete.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    result = validate_holdout_manifest(data, tmp_path)
    assert result["status"] == "BLOCKED"
    assert {"SOURCE_VIDEO_IDENTITY_MISSING", "EPISODE_ID_MISSING", "BOARD_EPOCH_MISSING",
            "TWO_FRAMES_REQUIRED"}.issubset(result["issues"])
    assert result["ocr_accuracy_evaluated"] is False


def test_locally_available_single_frame_matches_receipt_but_is_not_holdout(tmp_path: Path):
    source = Path(os.environ.get("BRIDGIT_SINGLE_FRAME_SMOKE", ""))
    if not source.is_file():
        pytest.skip("owner-provided development frame is not available locally")
    fixture = Path(__file__).parent / "fixtures" / "single_frame_holdout_incomplete.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    (tmp_path / source.name).write_bytes(source.read_bytes())
    result = validate_holdout_manifest(data, tmp_path)
    assert "FRAME_0_SHA256_MISMATCH" not in result["issues"]
    assert "FRAME_0_NOT_PNG" not in result["issues"]
    assert "TWO_FRAMES_REQUIRED" in result["issues"]
    assert result["status"] == "BLOCKED"


@pytest.mark.parametrize("change,issue", [
    ("unreadable", "FRAME_1_BOARD_NUMBER_NOT_CONFIRMED"),
    ("wrong_board", "FRAME_1_BOARD_NUMBER_NOT_CONFIRMED"),
    ("same_bytes", "FRAMES_NOT_INDEPENDENT"),
    ("reverse_time", "PTS_NOT_INCREASING"),
    ("skipped_observation", "OBSERVATIONS_NOT_ADJACENT"),
    ("different_episode", "EPISODE_ID_MISSING"),
    ("frame_other_episode", "FRAME_1_EPISODE_MISMATCH"),
    ("frame_other_occurrence", "FRAME_1_BOARD_EPOCH_MISMATCH"),
    ("frame_other_video", "FRAME_1_SOURCE_MISMATCH"),
    ("missing_source", "SOURCE_VIDEO_IDENTITY_MISSING"),
    ("tampered_bytes", "FRAME_1_SHA256_MISMATCH"),
    ("outside_root", "FRAME_1_PATH_UNAVAILABLE"),
])
def test_identity_and_integrity_boundaries_fail_closed(tmp_path: Path, change: str, issue: str):
    data = _manifest(tmp_path)
    if change == "unreadable":
        data["frames"][1]["board_number_confirming_read"] = {"status": "UNREADABLE", "number": None}
    elif change == "wrong_board":
        data["frames"][1]["board_number_read"]["number"] = 13
    elif change == "same_bytes":
        data["frames"][1]["sha256"] = data["frames"][0]["sha256"]
        (tmp_path / data["frames"][1]["path"]).write_bytes((tmp_path / data["frames"][0]["path"]).read_bytes())
    elif change == "reverse_time":
        data["frames"][1]["pts_ms"] = 900
    elif change == "skipped_observation":
        data["frames"][1]["observation_index"] = 3
    elif change == "different_episode":
        data["episode_id"] = ""
    elif change == "frame_other_episode":
        data["frames"][1]["episode_id"] = "other-episode"
    elif change == "frame_other_occurrence":
        data["frames"][1]["board_epoch"] = 2
    elif change == "frame_other_video":
        data["frames"][1]["source_video_sha256"] = "b" * 64
    elif change == "missing_source":
        data["source_video"] = None
    elif change == "tampered_bytes":
        (tmp_path / data["frames"][1]["path"]).write_bytes(_png(30))
    elif change == "outside_root":
        data["frames"][1]["path"] = "../missing.png"
    result = validate_holdout_manifest(data, tmp_path)
    assert result["status"] == "BLOCKED"
    assert issue in result["issues"]
