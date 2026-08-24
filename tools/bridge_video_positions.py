#!/usr/bin/env python3
"""Post-process Universal Video keyframes with the existing bridge image parser.

The resident Universal Video core remains dependency-light. This optional bridge
stage lazily imports `bridge_report_board_reconstruction.parse_image` and writes
only compact canonical frame observations. It does not alter the source video,
manifest, transcript, or production routing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from bridge_contracts.video_frame import canonicalize_frame_recognition

FrameParser = Callable[[Path], dict[str, Any]]


def _safe_frame_path(job_dir: Path, file_name: Any) -> Path:
    if not isinstance(file_name, str) or not file_name.strip():
        raise ValueError("frame file is missing")
    name = file_name.strip()
    if Path(name).name != name:
        raise ValueError("frame file must be a basename")
    frame = (job_dir / "frames" / name).resolve()
    frames_root = (job_dir / "frames").resolve()
    try:
        frame.relative_to(frames_root)
    except ValueError as exc:
        raise ValueError("frame path escapes frames directory") from exc
    if not frame.is_file():
        raise ValueError(f"frame does not exist: {name}")
    return frame


def _default_parser() -> FrameParser:
    # Heavy CV dependencies remain outside the resident Universal Video core.
    from bridge_report_board_reconstruction import parse_image

    return parse_image


def process_job_frames(
    job_dir: Path,
    *,
    parser: FrameParser | None = None,
    derive_fourth_hand: bool = False,
) -> dict[str, Any]:
    root = job_dir.resolve()
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frames = manifest.get("frames")
    if not isinstance(frames, list):
        raise ValueError("manifest frames must be an array")

    parse = parser or _default_parser()
    records: list[dict[str, Any]] = []
    recognized_frames = 0
    conflict_frames = 0

    for frame_meta in frames:
        if not isinstance(frame_meta, dict):
            raise ValueError("manifest frame entry must be an object")
        frame = _safe_frame_path(root, frame_meta.get("file"))
        recognition = parse(frame)
        canonical = canonicalize_frame_recognition(
            recognition,
            time=frame_meta.get("time"),
            frame_file=frame.name,
            frame_sha256=frame_meta.get("sha256"),
            derive_fourth_hand=derive_fourth_hand,
        ).to_dict()
        records.append(canonical)
        if canonical["recognized_card_count"]:
            recognized_frames += 1
        if canonical["parser_status"] == "CONFLICT":
            conflict_frames += 1

    output_path = root / "bridge_positions.jsonl"
    output_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )
    summary = {
        "status": "REVIEW" if conflict_frames else "COMPLETED",
        "job_id": manifest.get("job_id"),
        "source_fingerprint": manifest.get("source_fingerprint"),
        "input_frames": len(frames),
        "output_records": len(records),
        "recognized_frames": recognized_frames,
        "conflict_frames": conflict_frames,
        "derive_fourth_hand": bool(derive_fourth_hand),
        "output": output_path.name,
    }
    summary_path = root / "bridge_positions_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--derive-fourth-hand", action="store_true")
    args = parser.parse_args()
    summary = process_job_frames(
        args.job_dir,
        derive_fourth_hand=args.derive_fourth_hand,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
