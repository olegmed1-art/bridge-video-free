#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from bridge_school_api.tournament_coverage_release_v3 import build_coverage_manifest, build_release_gate
from bridge_school_api.tournament_mp_validation_v3 import assess_mp_recalculation_availability
from bridge_school_api.tournament_preanalysis_gate_v3 import build_preanalysis_gate


def build_checkpoint(
    facts_path: Path,
    *,
    received_at: str,
    receipt_commit: str,
    algorithm_revision_id: str,
) -> dict[str, Any]:
    raw = facts_path.read_bytes()
    source = json.loads(raw.decode("utf-8"))
    preanalysis = build_preanalysis_gate(
        source,
        normalized_facts_sha256=hashlib.sha256(raw).hexdigest(),
        normalized_facts_size_bytes=len(raw),
        normalized_facts_received_at=received_at,
        normalized_facts_commit=receipt_commit,
        algorithm_revision_id=algorithm_revision_id,
    )
    mp = assess_mp_recalculation_availability(source)

    # This checkpoint deliberately does not pretend the episode review is complete.
    # The current reviewed-evidence chain has PENDING teacher decisions; therefore
    # v1.4 episode inventory/slide planning remains open until explicit evidence
    # supplies the episode scores and provenance.
    coverage = build_coverage_manifest(source, episodes=(), episode_inventory_complete=False)
    release = build_release_gate(
        preanalysis_gate=preanalysis,
        coverage_manifest=coverage,
        mp_availability=mp,
        rendered_slide_keys=None,
        visual_qa_pass=None,
    )
    return {
        "schema": "tournament-v3-release-checkpoint-v1",
        "facts": {
            "path": str(facts_path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        },
        "preanalysis": preanalysis,
        "mp_recalculation": mp,
        "coverage": coverage,
        "release": release,
        "checkpoint": {
            "analysis_stage": "COVERAGE_PLANNING",
            "technical_analysis_ready": release["technical_analysis_ready"],
            "final_report_release_ready": release["final_report_release_ready"],
            "completed_components": [
                "INPUT_MANIFEST",
                "STRUCTURAL_VALIDATION",
                "DUPLICATE_SCORE_VALIDATION",
                "PREANALYSIS_GATE",
                "MP_TRAVELLER_AVAILABILITY_GATE",
                "MINIMUM_PLAYED_BOARD_COVERAGE_PLAN",
            ],
            "next_unfinished_step": "COMPLETE_EVIDENCE_GATED_EPISODE_INVENTORY",
            "hard_stop_conditions": release["hard_stop_conditions"],
            "limitations": release["limitations"],
        },
    }


def render_markdown(checkpoint: dict[str, Any]) -> str:
    cp = checkpoint["checkpoint"]
    coverage = checkpoint["coverage"]
    release = checkpoint["release"]
    lines = [
        "# Tournament Analyzer v3 — release checkpoint",
        "",
        f"- Stage: `{cp['analysis_stage']}`",
        f"- Technical analysis ready: `{str(cp['technical_analysis_ready']).lower()}`",
        f"- Final report release ready: `{str(cp['final_report_release_ready']).lower()}`",
        f"- Played boards: `{coverage['status_counts']['played']}`",
        f"- Average boards: `{coverage['status_counts']['average']}`",
        f"- Unplayed boards: `{coverage['status_counts']['unplayed']}`",
        f"- Minimum planned deck slides: `{coverage['planned_deck_slide_count']}`",
        f"- Next unfinished step: `{cp['next_unfinished_step']}`",
        "",
        "## Hard stops before final release",
    ]
    for value in release["hard_stop_conditions"]:
        lines.append(f"- `{value}`")
    lines.append("")
    lines.append("## Evidence limitations")
    for value in release["limitations"]:
        lines.append(f"- `{value}`")
    lines.extend(
        [
            "",
            "This checkpoint contains no automatic student-error attribution and no invented bridge methodology.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--received-at", required=True)
    parser.add_argument("--receipt-commit", required=True)
    parser.add_argument("--algorithm-revision-id", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    checkpoint = build_checkpoint(
        args.facts,
        received_at=args.received_at,
        receipt_commit=args.receipt_commit,
        algorithm_revision_id=args.algorithm_revision_id,
    )
    args.output_json.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render_markdown(checkpoint), encoding="utf-8")
    print(json.dumps(checkpoint["checkpoint"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
