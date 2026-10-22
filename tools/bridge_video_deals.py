#!/usr/bin/env python3
"""Fuse canonical Bridge Vision frame observations into evidence-based deals."""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bridge_vision.multiframe import reconstruct_deals
from bridge_vision.pbn import export_frame_pbns


def reconstruct_job(job_dir: Path, *, video_title: str | None = None,
                    pbn_provenance_by_record_sha256: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, object]:
    root = job_dir.resolve()
    positions = root / "bridge_positions.jsonl"
    records = [json.loads(line) for line in positions.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = reconstruct_deals(records).to_dict()
    output = root / "bridge_deals.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path = root / "manifest.json"
    source_metadata = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    if video_title is not None:
        source_metadata = {**source_metadata, "video_title": video_title}
    pbn_outputs = export_frame_pbns(
        root, records, source_metadata=source_metadata,
        provenance_by_record_sha256=pbn_provenance_by_record_sha256,
    )
    return {
        "status": result["status"],
        "deal_count": result["deal_count"],
        "verified_full_board_count": result["verified_full_board_count"],
        "review_deal_count": result["review_deal_count"],
        "review_frame_count": result["review_frame_count"],
        "canonical_promotion_allowed": False,
        "output": output.name,
        "pbn_outputs": pbn_outputs,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--video-title", help="Actual video title; otherwise use manifest metadata or explicit UNKNOWN_TITLE")
    args = parser.parse_args()
    print(json.dumps(reconstruct_job(args.job_dir, video_title=args.video_title), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
