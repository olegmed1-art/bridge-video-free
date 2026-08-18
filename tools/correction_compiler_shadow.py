#!/usr/bin/env python3
"""Fail-closed Shadow compiler from confirmed corrections to regression candidates.

This module intentionally has no database write path. It accepts a JSON snapshot,
produces deterministic candidate records, and leaves all promotion decisions outside
this process. It is therefore suitable for META Shadow evaluation only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

COMPILER_VERSION = "correction-compiler-shadow-v1"
ELIGIBLE_CORRECTION_STATUSES = {"confirmed", "resolved"}
ELIGIBLE_APPROVAL_STATES = {"not_required", "approved"}
BLOCKED_EVIDENCE_QUALITY = {"quarantined", "rejected", "invalid"}
HIGH_IMPACT_SEVERITIES = {"high", "critical"}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _evidence_index(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = snapshot.get("evidence", [])
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    if isinstance(raw, list):
        result: dict[str, dict[str, Any]] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            evidence_id = _text(item.get("evidence_id"))
            if evidence_id:
                result[evidence_id] = item
        return result
    return {}


def _skip(correction_id: str, reason: str) -> dict[str, str]:
    return {"correction_record_id": correction_id or "<missing>", "reason": reason}


def _stable_key(record: dict[str, Any], details: dict[str, Any]) -> str:
    seed = {
        "school_id": _text(record.get("school_id")),
        "target_entity_id": _text(record.get("target_entity_id")),
        "target_entity_type": _text(record.get("target_entity_type")),
        "correction_class": _text(record.get("correction_class")),
        "summary": _text(record.get("summary")),
        "target_component": _text(details.get("target_component")),
        "test_reference": _text(details.get("test_reference")),
        "expected_contract": details.get("expected_contract"),
        "evidence_ids": sorted(str(x) for x in (record.get("evidence_ids") or [])),
    }
    digest = hashlib.sha256(_canonical_json(seed).encode("utf-8")).hexdigest()
    return f"correction-v1:{digest[:40]}"


def _validate_evidence(
    evidence_ids: list[str], evidence_by_id: dict[str, dict[str, Any]]
) -> str | None:
    for evidence_id in evidence_ids:
        item = evidence_by_id.get(evidence_id)
        if item is None:
            return f"missing_evidence_record:{evidence_id}"
        quality = _text(item.get("quality_status")).lower()
        if quality in BLOCKED_EVIDENCE_QUALITY:
            return f"blocked_evidence_quality:{evidence_id}:{quality}"
    return None


def compile_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Compile eligible correction rows into deterministic Shadow candidates.

    The function never guesses an expected contract, target component, test
    reference, approval, or evidence. Missing information causes a skip.
    """
    corrections = snapshot.get("corrections", [])
    if not isinstance(corrections, list):
        raise ValueError("snapshot.corrections must be a list")

    evidence_by_id = _evidence_index(snapshot)
    candidates_by_key: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, str]] = []

    for raw in corrections:
        if not isinstance(raw, dict):
            skipped.append(_skip("", "correction_not_object"))
            continue

        correction_id = _text(raw.get("correction_record_id"))
        if not correction_id:
            skipped.append(_skip(correction_id, "missing_correction_record_id"))
            continue
        if not _text(raw.get("school_id")):
            skipped.append(_skip(correction_id, "missing_school_id"))
            continue
        if not _text(raw.get("target_entity_id")) or not _text(raw.get("target_entity_type")):
            skipped.append(_skip(correction_id, "missing_target_entity"))
            continue

        status = _text(raw.get("status")).lower()
        if status not in ELIGIBLE_CORRECTION_STATUSES:
            skipped.append(_skip(correction_id, f"ineligible_status:{status or '<missing>'}"))
            continue
        if raw.get("regression_required") is not True:
            skipped.append(_skip(correction_id, "regression_not_required"))
            continue

        correction_class = _text(raw.get("correction_class")).lower()
        severity = _text(raw.get("severity")).lower()
        approval_state = _text(raw.get("teacher_approval_state")).lower()
        protected_methodology = raw.get("protected_methodology") is True
        requires_explicit_approval = (
            protected_methodology
            or correction_class == "methodology"
            or severity in HIGH_IMPACT_SEVERITIES
            or raw.get("material") is True
        )
        if requires_explicit_approval and approval_state != "approved":
            skipped.append(_skip(correction_id, "teacher_approval_required"))
            continue
        if not requires_explicit_approval and approval_state not in ELIGIBLE_APPROVAL_STATES:
            skipped.append(_skip(correction_id, "invalid_teacher_approval_state"))
            continue

        evidence_ids = [str(x) for x in (raw.get("evidence_ids") or []) if str(x).strip()]
        if not evidence_ids:
            skipped.append(_skip(correction_id, "missing_evidence_ids"))
            continue
        evidence_problem = _validate_evidence(evidence_ids, evidence_by_id)
        if evidence_problem:
            skipped.append(_skip(correction_id, evidence_problem))
            continue

        details = raw.get("details")
        if not isinstance(details, dict):
            skipped.append(_skip(correction_id, "details_not_object"))
            continue
        target_component = _text(details.get("target_component"))
        test_reference = _text(details.get("test_reference"))
        expected_contract = details.get("expected_contract")
        if not target_component:
            skipped.append(_skip(correction_id, "missing_target_component"))
            continue
        if not test_reference:
            skipped.append(_skip(correction_id, "missing_test_reference"))
            continue
        if not isinstance(expected_contract, dict) or not expected_contract:
            skipped.append(_skip(correction_id, "missing_expected_contract"))
            continue

        stable_key = _stable_key(raw, details)
        candidate = {
            "school_id": _text(raw.get("school_id")),
            "correction_record_id": correction_id,
            "stable_key": stable_key,
            "target_component": target_component,
            "test_reference": test_reference,
            "expected_contract": expected_contract,
            "provenance": {
                "compiler_version": COMPILER_VERSION,
                "shadow_only": True,
                "correction_record_id": correction_id,
                "correction_status": status,
                "correction_class": correction_class,
                "severity": severity,
                "teacher_approval_state": approval_state,
                "evidence_ids": sorted(evidence_ids),
            },
            "status": "candidate",
        }

        existing = candidates_by_key.get(stable_key)
        if existing is None:
            candidates_by_key[stable_key] = candidate
        elif existing != candidate:
            skipped.append(_skip(correction_id, f"stable_key_conflict:{stable_key}"))
        else:
            skipped.append(_skip(correction_id, f"deterministic_duplicate:{stable_key}"))

    candidates = [candidates_by_key[k] for k in sorted(candidates_by_key)]
    reason_counts: dict[str, int] = {}
    for item in skipped:
        root = item["reason"].split(":", 1)[0]
        reason_counts[root] = reason_counts.get(root, 0) + 1

    return {
        "compiler_version": COMPILER_VERSION,
        "mode": "SHADOW",
        "production_write": False,
        "input_corrections": len(corrections),
        "candidate_count": len(candidates),
        "skipped_count": len(skipped),
        "candidates": candidates,
        "skipped": skipped,
        "skip_reason_counts": dict(sorted(reason_counts.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON snapshot containing corrections and evidence")
    parser.add_argument("--output", type=Path, help="Optional candidate JSON file in a sandbox/evidence location")
    args = parser.parse_args()

    snapshot = json.loads(args.input.read_text(encoding="utf-8"))
    result = compile_snapshot(snapshot)
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
