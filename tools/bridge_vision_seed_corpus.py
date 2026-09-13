#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bridge_vision.seed_corpus import build_seed_corpus


def main() -> None:
    parser = argparse.ArgumentParser(description="Build human-labeling queue from real Universal Video keyframes")
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--target-frames", type=int, default=80)
    args = parser.parse_args()
    result = build_seed_corpus(args.job_dir, target_frames=args.target_frames)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
