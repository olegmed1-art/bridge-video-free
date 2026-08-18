#!/usr/bin/env python3
"""Shadow-only readiness audit for formal bridge decision assessment.

The audit does not decide whether a bridge action was correct. It only verifies
that the evidence and context needed for an independent assessment are present.
Missing context remains explicit as BLOCKED/UNKNOWN rather than being guessed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

AUDIT_VERSION = "decision-readiness-shadow-v1"
VERIFIED_EVIDENCE_QUALITY = {"verified", "derived_verified", "derived_checked"}
BLOCKED_EVIDENCE_QUALITY = {"quarantined", "rejected", "invalid"}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _evidence_index(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = snapshot.get("evidence", [])
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    result: dict[str, dict[str, Any]] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and _text(item.get("evidence_id")):
                result[_text(item.get("evidence_id"))] = item
    return result


def _position_bound(decision: dict[str, Any]) -> bool:
    if _text(decision.get("deal_id")):
        return True
    info = decision.get("available_information")
    return isinstance(info, dict) and _text(info.get("position_binding_status")).lower() == "verified"


def audit_decision(decision: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    decision_id = _text(decision.get("decision_id")) or "<missing>"
    blockers: list[str] = []

    action = decision.get("action_taken")
    if not isinstance(action, dict):
        blockers.append("action_missing")
        action = {}
    action_status = _text(action.get("status")).lower()
    if action_status != "observed_choice":
        blockers.append(f"not_observed_choice:{action_status or '<missing>'}")
    if not _text(action.get("text")):
        blockers.append("action_text_missing")

    evidence_ids = [str(x).strip() for x in (decision.get("evidence_ids") or []) if str(x).strip()]
    if not evidence_ids:
        blockers.append("evidence_missing")
    else:
        verified_high = False
        for evidence_id in evidence_ids:
            item = evidence_by_id.get(evidence_id)
            if item is None:
                blockers.append(f"evidence_record_missing:{evidence_id}")
                continue
            quality = _text(item.get("quality_status")).lower()
            confidence = _text(item.get("confidence_class")).upper()
            if quality in BLOCKED_EVIDENCE_QUALITY:
                blockers.append(f"evidence_blocked:{evidence_id}:{quality}")
            if quality in VERIFIED_EVIDENCE_QUALITY and confidence == "HIGH":
                verified_high = True
        if not verified_high:
            blockers.append("no_high_confidence_verified_evidence")

    actor_id = _text(decision.get("actor_person_id"))
    attribution_status = _text(action.get("actor_attribution_status")).lower()
    if not actor_id or attribution_status.startswith("unavailable"):
        blockers.append("actor_unresolved")

    if not _position_bound(decision):
        blockers.append("position_or_deal_unbound")

    formal_ready = not blockers
    student_id = _text(decision.get("student_id"))
    student_transfer_blockers: list[str] = []
    if not formal_ready:
        student_transfer_blockers.append("formal_assessment_not_ready")
    if not student_id:
        student_transfer_blockers.append("student_unresolved")

    return {
        "decision_id": decision_id,
        "formal_assessment_state": "READY" if formal_ready else "BLOCKED",
        "formal_assessment_blockers": blockers,
        "student_transfer_state": "READY" if not student_transfer_blockers else "BLOCKED",
        "student_transfer_blockers": student_transfer_blockers,
        "correctness_label": None,
    }


def audit_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    decisions = snapshot.get("decisions", [])
    if not isinstance(decisions, list):
        raise ValueError("snapshot.decisions must be a list")
    evidence_by_id = _evidence_index(snapshot)

    rows: list[dict[str, Any]] = []
    blocker_counts: dict[str, int] = {}
    formal_ready = 0
    transfer_ready = 0
    for item in decisions:
        if not isinstance(item, dict):
            row = {
                "decision_id": "<missing>",
                "formal_assessment_state": "BLOCKED",
                "formal_assessment_blockers": ["decision_not_object"],
                "student_transfer_state": "BLOCKED",
                "student_transfer_blockers": ["formal_assessment_not_ready", "student_unresolved"],
                "correctness_label": None,
            }
        else:
            row = audit_decision(item, evidence_by_id)
        rows.append(row)
        if row["formal_assessment_state"] == "READY":
            formal_ready += 1
        if row["student_transfer_state"] == "READY":
            transfer_ready += 1
        for blocker in row["formal_assessment_blockers"]:
            root = blocker.split(":", 1)[0]
            blocker_counts[root] = blocker_counts.get(root, 0) + 1

    return {
        "audit_version": AUDIT_VERSION,
        "mode": "SHADOW",
        "production_write": False,
        "decision_count": len(decisions),
        "formal_assessment_ready": formal_ready,
        "formal_assessment_blocked": len(decisions) - formal_ready,
        "student_transfer_ready": transfer_ready,
        "student_transfer_blocked": len(decisions) - transfer_ready,
        "formal_blocker_counts": dict(sorted(blocker_counts.items())),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON snapshot containing decisions and evidence")
    parser.add_argument("--output", type=Path, help="Optional Shadow audit output file")
    args = parser.parse_args()

    snapshot = json.loads(args.input.read_text(encoding="utf-8"))
    result = audit_snapshot(snapshot)
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
