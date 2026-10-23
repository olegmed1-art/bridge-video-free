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
    parser.add_argument("--scale-source-profile", type=Path)
    parser.add_argument("--expected-scale-profile-sha256")
    parser.add_argument("--scale-bundle-root", type=Path)
    parser.add_argument("--scale-caller-root", type=Path)
    parser.add_argument("--scale-asset-root", type=Path)
    args = parser.parse_args()
    options = (args.scale_source_profile, args.expected_scale_profile_sha256,
               args.scale_bundle_root, args.scale_caller_root, args.scale_asset_root)
    if any(value is not None for value in options) and not all(value is not None for value in options):
        parser.error("all five scale source profile arguments are required together")
    profile = None
    if args.scale_source_profile is not None:
        from tools.diana20_scale_pbn_profile import load_scale_pbn_profile
        profile = load_scale_pbn_profile(args.scale_source_profile,
            expected_sha256=args.expected_scale_profile_sha256, bundle_root=args.scale_bundle_root,
            caller_root=args.scale_caller_root, asset_root=args.scale_asset_root)
    result = export_diana20_jsonl(args.observations, args.output_dir,
        expected_sha256=args.expected_jsonl_sha256, video_title=args.video_title, scale_profile=profile)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
