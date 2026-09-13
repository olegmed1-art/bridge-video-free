#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bridge_school_api.tournament_teacher_confirmed_longitudinal_v3 import (
    build_teacher_confirmed_longitudinal_report,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _markdown(report: dict) -> str:
    lines = [
        "# Teacher-confirmed technical longitudinal report",
        "",
        f"Bundle ID: `{report['bundle_id']}`",
        "",
        "## Decision status",
        "",
    ]
    for status, count in report["status_counts"].items():
        lines.append(f"- {status}: {count}")
    lines.extend(
        [
            "",
            f"Confirmed technical items: **{len(report['confirmed_items'])}**",
            f"Persistent technical clusters: **{len(report['persistent_clusters'])}**",
            "",
            "## Safety boundary",
            "",
            "Only explicit CONFIRMED_TECHNICAL_RELEVANCE decisions enter this view. This does not establish a student error, causality, a teaching category, or methodology mapping.",
            "",
        ]
    )
    if report["clusters"]:
        lines.extend(["## Technical clusters", ""])
        for cluster in report["clusters"]:
            lines.append(
                f"- `{cluster['repeat_key']}`: findings={cluster['finding_count']}, events={cluster['event_count']}, "
                f"persistent={str(cluster['persistent_across_events']).lower()}, trick-loss-mass={cluster['technical_trick_loss_mass']:.2f}"
            )
        lines.append("")
    lines.extend([report["interpretation"], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build teacher-confirmed technical longitudinal view")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    bundle = _load(args.bundle)
    ledger = _load(args.ledger) if args.ledger else None
    report = build_teacher_confirmed_longitudinal_report(bundle, ledger)
    args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.out_md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({
        "schema": report["schema"],
        "confirmed": len(report["confirmed_items"]),
        "persistent_clusters": len(report["persistent_clusters"]),
        "status_counts": report["status_counts"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
