#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bridge_school_api.tournament_duplicate_scoring_v3 import validate_tournament_fact_scores


def main() -> int:
    parser = argparse.ArgumentParser(description="Independently validate duplicate contract scores in tournament facts")
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    source = json.loads(args.facts.read_text(encoding="utf-8"))
    report = validate_tournament_fact_scores(source)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema": report["schema"],
        "checked": report["played_scores_checked"],
        "skipped_nonplayed": report["skipped_nonplayed"],
        "all_match": report["all_published_scores_match"],
        "mismatches": len(report["mismatches"]),
    }, sort_keys=True))
    return 0 if report["all_published_scores_match"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
