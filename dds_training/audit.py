from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def audit_database(con: sqlite3.Connection) -> dict:
    issues: list[dict] = []

    def add(code: str, severity: str, count: int, detail: str) -> None:
        if count:
            issues.append({"code": code, "severity": severity, "count": int(count), "detail": detail})

    orphan_results = con.execute(
        "SELECT COUNT(*) FROM dds_results r LEFT JOIN predictions p ON p.task_id=r.task_id WHERE p.task_id IS NULL"
    ).fetchone()[0]
    add("ORPHAN_DDS_RESULT", "error", orphan_results, "DDS result exists without a locked prediction")

    orphan_errors = con.execute(
        "SELECT COUNT(*) FROM error_events e LEFT JOIN dds_results r ON r.task_id=e.task_id WHERE r.task_id IS NULL"
    ).fetchone()[0]
    add("ORPHAN_ERROR_EVENT", "error", orphan_errors, "Error event exists without a DDS result")

    bad_sealed = con.execute(
        "SELECT COUNT(*) FROM dds_results WHERE split='sealed_test'"
    ).fetchone()[0]
    # This is informational because sealed results are legitimate only at final stage.
    if bad_sealed:
        issues.append({
            "code": "SEALED_TEST_OPENED",
            "severity": "info",
            "count": int(bad_sealed),
            "detail": "Sealed-test results exist; verify they were opened only for final evaluation.",
        })

    stable_without_support = con.execute(
        """
        SELECT COUNT(*) FROM skill_profiles
        WHERE status='stable' AND (transfer_count<10 OR regression_passes<3 OR counterexample_count<2 OR regression_failures>0)
        """
    ).fetchone()[0]
    add("UNSUPPORTED_STABLE_SKILL", "error", stable_without_support, "Stable skill lacks transfer/regression/counterexample support")

    correction_without_reason = con.execute(
        "SELECT COUNT(*) FROM correction_events WHERE TRIM(reason)=''"
    ).fetchone()[0]
    add("CORRECTION_WITHOUT_REASON", "error", correction_without_reason, "Correction event must have an explicit reason")

    high_conf_errors = con.execute(
        "SELECT COUNT(*) FROM skill_evidence WHERE confidence='high' AND outcome!='success'"
    ).fetchone()[0]
    if high_conf_errors:
        issues.append({
            "code": "HIGH_CONFIDENCE_ERRORS",
            "severity": "priority",
            "count": int(high_conf_errors),
            "detail": "High-confidence errors should receive priority transfer and counterexample tasks.",
        })

    triggers = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    required = {
        "predictions_no_update", "predictions_no_delete",
        "dds_results_no_update", "dds_results_no_delete",
        "error_events_no_update", "error_events_no_delete",
        "skill_evidence_no_update", "skill_evidence_no_delete",
    }
    missing = required - triggers
    if missing:
        issues.append({
            "code": "IMMUTABILITY_TRIGGER_MISSING",
            "severity": "error",
            "count": len(missing),
            "detail": ", ".join(sorted(missing)),
        })

    counts = {}
    for table in (
        "predictions", "dds_results", "error_events", "experience_events",
        "skill_profiles", "skill_evidence", "rule_versions", "regression_cases",
        "counterexamples", "correction_events", "learning_queue",
    ):
        counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    status = "error" if any(x["severity"] == "error" for x in issues) else "ok"
    return {"status": status, "counts": counts, "issues": issues}


def persist_audit(con: sqlite3.Connection, audit: dict, run_id: str | None = None) -> None:
    con.execute(
        "INSERT INTO audit_events(run_id,status,details_json) VALUES(?,?,?)",
        (run_id, audit["status"], json.dumps(audit, ensure_ascii=False, sort_keys=True)),
    )


def audit_manifest(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    deal_ids = [r["deal_id"] for r in rows]
    duplicates = len(deal_ids) - len(set(deal_ids))
    split_by_id: dict[str, set[str]] = {}
    for r in rows:
        split_by_id.setdefault(r["deal_id"], set()).add(r["split"])
    cross_split = sum(len(v) > 1 for v in split_by_id.values())
    return {
        "status": "ok" if duplicates == 0 and cross_split == 0 else "error",
        "rows": len(rows),
        "duplicate_deal_ids": duplicates,
        "cross_split_deal_ids": cross_split,
    }
