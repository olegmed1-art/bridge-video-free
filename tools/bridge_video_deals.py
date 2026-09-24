#!/usr/bin/env python3
"""Fuse canonical Bridge Vision frame observations into evidence-based deals."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bridge_vision.multiframe import reconstruct_deals


def reconstruct_job(job_dir: Path) -> dict[str, object]:
    root = job_dir.resolve()
    positions = root / "bridge_positions.jsonl"
    records = [json.loads(line) for line in positions.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = reconstruct_deals(records).to_dict()
    output = root / "bridge_deals.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": result["status"],
        "deal_count": result["deal_count"],
        "verified_full_board_count": result["verified_full_board_count"],
        "review_deal_count": result["review_deal_count"],
        "review_frame_count": result["review_frame_count"],
        "canonical_promotion_allowed": False,
        "output": output.name,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(reconstruct_job(args.job_dir), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
