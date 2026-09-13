#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bridge_school_api.tournament_episode_inventory_v3 import build_evidence_episode_candidate_inventory


def main() -> int:
    parser = argparse.ArgumentParser(description="Build evidence-only tournament episode candidate inventory")
    parser.add_argument("--facts", required=True)
    parser.add_argument("--dossier", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", required=True)
    args = parser.parse_args()

    source = json.loads(Path(args.facts).read_text(encoding="utf-8"))
    dossier = json.loads(Path(args.dossier).read_text(encoding="utf-8"))
    inventory = build_evidence_episode_candidate_inventory(source, dossier, event_id=args.event_id)

    Path(args.out_json).write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Tournament evidence episode candidate inventory",
        "",
        f"- event: `{inventory['event_id']}`",
        f"- provider: `{inventory['provider_native_key']}`",
        f"- played boards: **{inventory['played_board_count']}**",
        f"- evidence-bound technical candidates: **{inventory['technical_candidate_count']}**",
        f"- boards with technical candidates: **{inventory['technical_candidate_board_count']}**",
        f"- candidate enumeration complete: **{str(inventory['evidence_candidate_inventory_complete']).lower()}**",
        f"- v1.4 episode inventory complete: **{str(inventory['v1_4_episode_inventory_complete']).lower()}**",
        f"- coverage-ready scored episodes: **{len(inventory['coverage_episode_inputs'])}**",
        "",
        "## Release blockers",
    ]
    lines.extend(f"- `{value}`" for value in inventory["release_blockers"])
    lines.extend(["", "## Technical candidates"])
    for item in inventory["technical_candidates"]:
        lines.append(
            f"- board {item['board_number']}: `{item['category']}` / `{item['review_id']}` — PENDING teacher review"
        )
    lines.extend(
        [
            "",
            "> Technical candidates are evidence items, not confirmed student errors or teaching episodes. "
            "No impact/transferability/reliability score, methodology mapping, or deep-slide requirement is inferred.",
            "",
        ]
    )
    Path(args.out_md).write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
