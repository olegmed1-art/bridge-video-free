#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bridge_school_api.tournament_teacher_review_portfolio_v3 import (
    build_teacher_review_portfolio,
    verify_teacher_review_portfolio,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a hash-bound portfolio from pending teacher-review bundles")
    parser.add_argument("--bundle", action="append", required=True, help="Portable bundle JSON; repeat for every batch")
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md")
    args = parser.parse_args()

    bundles = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.bundle]
    portfolio = build_teacher_review_portfolio(bundles)
    verify_teacher_review_portfolio(portfolio, bundles)
    Path(args.out_json).write_text(json.dumps(portfolio, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.out_md:
        lines = [
            "# Tournament teacher-review portfolio",
            "",
            f"- Portfolio ID: `{portfolio['portfolio_id']}`",
            f"- Source bundles: {portfolio['source_bundle_count']}",
            f"- Pending review items: {portfolio['pending_decision_count']}",
            f"- Events: {portfolio['event_counts']}",
            f"- Categories: {portfolio['category_counts']}",
            f"- Multi-signal deals: {portfolio['multi_signal_deal_count']}",
            f"- State: `{portfolio['review_state']}`",
            "",
            "Multiple evidence families on the same deal remain separate teacher-review questions; no causal or pedagogical collapse is permitted.",
        ]
        Path(args.out_md).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        "TOURNAMENT_TEACHER_REVIEW_PORTFOLIO_COMPLETE",
        f"bundles={portfolio['source_bundle_count']}",
        f"items={portfolio['item_count']}",
        f"multi_signal={portfolio['multi_signal_deal_count']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
