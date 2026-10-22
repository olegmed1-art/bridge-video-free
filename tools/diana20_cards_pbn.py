"""Add PBN sidecars to saved installed Diana20 JSONL; never run recognition."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.diana20_cards_pbn_adapter import export_diana20_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--expected-jsonl-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="Existing trusted output directory")
    parser.add_argument("--video-title", help="Actual video title, otherwise explicit UNKNOWN_TITLE/source fallback")
    args = parser.parse_args()
    result = export_diana20_jsonl(args.observations, args.output_dir,
        expected_sha256=args.expected_jsonl_sha256, video_title=args.video_title)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
