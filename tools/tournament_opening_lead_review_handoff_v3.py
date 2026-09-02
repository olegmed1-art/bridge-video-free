#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bridge_school_api.tournament_opening_lead_review_handoff_v3 import build_opening_lead_review_handoff


def main() -> int:
    parser = argparse.ArgumentParser(description="Build evidence-only teacher-review handoff for opening-lead DDS3 candidates")
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--opening-lead-report", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    source = json.loads(args.facts.read_text(encoding="utf-8"))
    report = json.loads(args.opening_lead_report.read_text(encoding="utf-8"))
    handoff = build_opening_lead_review_handoff(source, report)
    args.out.write_text(json.dumps(handoff, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "OPENING_LEAD_REVIEW_HANDOFF",
        f"candidates={handoff['candidate_count']}",
        f"decisions={len(handoff['decision_ledger']['decisions'])}",
        f"dossiers={len(handoff['dossier']['items'])}",
        f"inventory={handoff['episode_candidate_inventory']['technical_candidate_count']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
