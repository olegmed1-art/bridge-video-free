"""Offline structural gate for a two-frame Bridgit board holdout.

This verifies files and claimed identity boundaries, not OCR accuracy or the
truth of video timestamps. Source-video decoding is a separate evidence gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "bridgit-adjacent-holdout-v1"
PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def _sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number_read(value: Any, number: int) -> bool:
    return (isinstance(value, Mapping) and value.get("status") == "READ"
            and type(value.get("number")) is int and value["number"] == number)


def validate_holdout_manifest(data: Mapping[str, Any], root: Path) -> dict[str, Any]:
    """Return deterministic issues; STRUCTURAL_PASS never claims OCR accuracy."""
    if not isinstance(data, Mapping):
        return {
            "schema_version": SCHEMA_VERSION, "status": "BLOCKED",
            "issues": ["MANIFEST_INVALID"], "frame_count": 0,
            "ocr_accuracy_evaluated": False, "source_pts_verified_by_decode": False,
        }
    issues: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        issues.append("SCHEMA_VERSION")
    source = data.get("source_video")
    if not isinstance(source, Mapping) or not _nonempty(source.get("id")) or not _sha(source.get("sha256")):
        issues.append("SOURCE_VIDEO_IDENTITY_MISSING")
    source_sha = source.get("sha256") if isinstance(source, Mapping) else None
    if not _nonempty(data.get("episode_id")):
        issues.append("EPISODE_ID_MISSING")
    epoch = data.get("board_epoch")
    if type(epoch) is not int or epoch < 1:
        issues.append("BOARD_EPOCH_MISSING")
    number = data.get("board_number")
    if type(number) is not int or number < 1:
        issues.append("BOARD_NUMBER_MISSING")

    frames = data.get("frames")
    if not isinstance(frames, list) or len(frames) != 2:
        issues.append("TWO_FRAMES_REQUIRED")
        frames = []
    root = root.resolve()
    hashes: list[str] = []
    pts: list[int] = []
    indices: list[int] = []
    dimensions: list[tuple[int, int]] = []
    for index, frame in enumerate(frames):
        if not isinstance(frame, Mapping):
            issues.append(f"FRAME_{index}_INVALID")
            continue
        if not _sha(frame.get("source_video_sha256")) or frame.get("source_video_sha256") != source_sha:
            issues.append(f"FRAME_{index}_SOURCE_MISMATCH")
        if not _nonempty(frame.get("episode_id")) or frame.get("episode_id") != data.get("episode_id"):
            issues.append(f"FRAME_{index}_EPISODE_MISMATCH")
        if type(frame.get("board_epoch")) is not int or frame.get("board_epoch") != epoch:
            issues.append(f"FRAME_{index}_BOARD_EPOCH_MISMATCH")
        if type(frame.get("board_number")) is not int or frame.get("board_number") != number:
            issues.append(f"FRAME_{index}_BOARD_NUMBER_MISMATCH")
        name = frame.get("path")
        if not _nonempty(name) or "\x00" in name or Path(name).is_absolute():
            issues.append(f"FRAME_{index}_PATH_INVALID")
        else:
            path = (root / name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                issues.append(f"FRAME_{index}_PATH_UNAVAILABLE")
            else:
                raw = path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                hashes.append(digest)
                if not _sha(frame.get("sha256")) or digest != frame["sha256"]:
                    issues.append(f"FRAME_{index}_SHA256_MISMATCH")
                if raw[:8] != PNG_HEADER or raw[12:16] != b"IHDR" or len(raw) < 24:
                    issues.append(f"FRAME_{index}_NOT_PNG")
                else:
                    width, height = struct.unpack(">II", raw[16:24])
                    if not width or not height:
                        issues.append(f"FRAME_{index}_DIMENSIONS_INVALID")
                    else:
                        dimensions.append((width, height))
        timestamp = frame.get("pts_ms")
        if type(timestamp) is not int or timestamp < 0:
            issues.append(f"FRAME_{index}_PTS_MISSING")
        else:
            pts.append(timestamp)
        observation_index = frame.get("observation_index")
        if type(observation_index) is not int or observation_index < 0:
            issues.append(f"FRAME_{index}_OBSERVATION_INDEX_MISSING")
        else:
            indices.append(observation_index)
        if type(number) is int and number > 0 and (
                not _number_read(frame.get("board_number_read"), number)
                or not _number_read(frame.get("board_number_confirming_read"), number)):
            issues.append(f"FRAME_{index}_BOARD_NUMBER_NOT_CONFIRMED")
    if len(hashes) == 2 and hashes[0] == hashes[1]:
        issues.append("FRAMES_NOT_INDEPENDENT")
    if len(pts) == 2 and pts[1] <= pts[0]:
        issues.append("PTS_NOT_INCREASING")
    if len(indices) == 2 and indices[1] != indices[0] + 1:
        issues.append("OBSERVATIONS_NOT_ADJACENT")
    if len(dimensions) == 2 and dimensions[0] != dimensions[1]:
        issues.append("FRAME_DIMENSIONS_DIFFER")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "STRUCTURAL_PASS" if not issues else "BLOCKED",
        "issues": issues,
        "frame_count": len(frames),
        "ocr_accuracy_evaluated": False,
        "source_pts_verified_by_decode": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    path = args.manifest.resolve()
    report = validate_holdout_manifest(json.loads(path.read_text(encoding="utf-8")), path.parent)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "STRUCTURAL_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
