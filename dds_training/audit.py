from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from config import ALGORITHM_VERSION, SKILL_LIFECYCLE
from investigations import ensure_investigation_table
from run_provenance import ensure_run_task_table


def _recent_regression_streak(con: sqlite3.Connection, skill_key: str) -> int:
    rows = con.execute(
        """
        SELECT outcome FROM skill_evidence
        WHERE skill_key=? AND evidence_type='regression' AND algorithm_version=?
        ORDER BY id
        """,
        (skill_key, ALGORITHM_VERSION),
    ).fetchall()
    streak = 0
    for (outcome,) in reversed(rows):
        if outcome == "success":
            streak += 1
        else:
            break
    return streak


def audit_database(con: sqlite3.Connection) -> dict:
    ensure_run_task_table(con)
    ensure_investigation_table(con)
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

    metadata_mismatch = con.execute(
        """
        SELECT COUNT(*) FROM predictions p JOIN dds_results r ON r.task_id=p.task_id
        WHERE p.deal_id<>r.deal_id OR p.task_type<>r.task_type OR p.split<>r.split
        """
    ).fetchone()[0]
    add("PREDICTION_RESULT_METADATA_MISMATCH", "error", metadata_mismatch, "Prediction and DDS result disagree on immutable task metadata")

    run_task_mismatch = con.execute(
        """
        SELECT COUNT(*) FROM run_task_events e JOIN dds_results r ON r.task_id=e.task_id
        WHERE e.deal_id<>r.deal_id OR e.task_type<>r.task_type OR e.split<>r.split
        """
    ).fetchone()[0]
    add("RUN_TASK_METADATA_MISMATCH", "error", run_task_mismatch, "Run provenance disagrees with immutable DDS task metadata")

    validation_learning = con.execute(
        """
        SELECT COUNT(*) FROM skill_evidence se JOIN dds_results r ON r.task_id=se.task_id
        WHERE r.split='validation'
        """
    ).fetchone()[0]
    add("VALIDATION_LEARNING_LEAK", "error", validation_learning, "Validation tasks must remain evaluation-only")

    sealed_learning = con.execute(
        """
        SELECT COUNT(*) FROM skill_evidence se JOIN dds_results r ON r.task_id=se.task_id
        WHERE r.split='sealed_test'
        """
    ).fetchone()[0]
    add("SEALED_LEARNING_LEAK", "error", sealed_learning, "Sealed-test tasks must never alter skill/rule memory")

    sealed_results = con.execute("SELECT COUNT(*) FROM dds_results WHERE split='sealed_test'").fetchone()[0]
    if sealed_results:
        missing_sealed_provenance = con.execute(
            """
            SELECT COUNT(*) FROM dds_results r
            WHERE r.split='sealed_test'
              AND NOT EXISTS (
                SELECT 1
                FROM run_task_events e
                JOIN runs ru ON ru.run_id=e.run_id
                WHERE e.task_id=r.task_id
                  AND e.action='evaluated'
                  AND ru.sealed_opened=1
                  AND ru.requested_splits_json='["sealed_test"]'
              )
            """
        ).fetchone()[0]
        add(
            "SEALED_TEST_WITHOUT_TASK_PROVENANCE",
            "error",
            missing_sealed_provenance,
            "Each sealed DDS result must have an original evaluated event from an explicitly authorized sealed-only run; later reuse is insufficient",
        )
        if not missing_sealed_provenance:
            issues.append({
                "code": "SEALED_TEST_OPENED",
                "severity": "info",
                "count": int(sealed_results),
                "detail": "All sealed-test results have per-task provenance from explicitly authorized sealed-only evaluation runs.",
            })

    required_investigations = con.execute(
        "SELECT COUNT(*) FROM dds_results WHERE investigation_required=1"
    ).fetchone()[0]
    untracked_investigations = con.execute(
        """
        SELECT COUNT(*) FROM dds_results r
        WHERE r.investigation_required=1
          AND NOT EXISTS (SELECT 1 FROM investigation_events i WHERE i.task_id=r.task_id)
        """
    ).fetchone()[0]
    add(
        "UNTRACKED_MANDATORY_INVESTIGATION",
        "error",
        untracked_investigations,
        "Every better-than-DDS claim must enter the append-only investigation ledger",
    )
    open_investigations = con.execute(
        """
        SELECT COUNT(*)
        FROM (
          SELECT i.task_id,i.event_type
          FROM investigation_events i
          JOIN (SELECT task_id,MAX(id) max_id FROM investigation_events GROUP BY task_id) x ON x.max_id=i.id
        ) latest
        WHERE latest.event_type IN ('opened','reopened')
        """
    ).fetchone()[0]
    add(
        "OPEN_MANDATORY_INVESTIGATION",
        "error",
        open_investigations,
        "Better-than-DDS claims must be resolved with cause, first refutation and bridge lesson before the stage can close",
    )

    stable_issues = 0
    for skill_key, transfer_count, counterexample_count in con.execute(
        """
        SELECT skill_key,transfer_count,counterexample_count FROM skill_profiles
        WHERE status='stable' AND algorithm_version=?
        """,
        (ALGORITHM_VERSION,),
    ):
        if (
            int(transfer_count) < SKILL_LIFECYCLE["stable_transfer"]
            or int(counterexample_count) < SKILL_LIFECYCLE["stable_counterexamples"]
            or _recent_regression_streak(con, skill_key) < SKILL_LIFECYCLE["stable_regression_passes"]
        ):
            stable_issues += 1
    add("UNSUPPORTED_STABLE_SKILL", "error", stable_issues, "Stable skill lacks current-revision transfer/regression/counterexample support")

    correction_without_reason = con.execute(
        "SELECT COUNT(*) FROM correction_events WHERE TRIM(reason)=''"
    ).fetchone()[0]
    add("CORRECTION_WITHOUT_REASON", "error", correction_without_reason, "Correction event must have an explicit reason")

    high_conf_errors = con.execute(
        """
        SELECT COUNT(*) FROM skill_evidence
        WHERE algorithm_version=? AND confidence='high' AND outcome!='success'
        """,
        (ALGORITHM_VERSION,),
    ).fetchone()[0]
    if high_conf_errors:
        issues.append({
            "code": "HIGH_CONFIDENCE_ERRORS",
            "severity": "priority",
            "count": int(high_conf_errors),
            "detail": "High-confidence errors should receive priority transfer and counterexample tasks.",
        })

    duplicate_plans = con.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT skill_key,purpose,source_run_id,COUNT(*) n FROM learning_queue
          WHERE purpose='targeted_transfer'
          GROUP BY skill_key,purpose,source_run_id HAVING n>1
        )
        """
    ).fetchone()[0]
    add("DUPLICATE_LEARNING_PLAN", "warning", duplicate_plans, "Same run contains duplicate targeted learning-plan rows")

    triggers = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    required = {
        "predictions_no_update", "predictions_no_delete",
        "dds_results_no_update", "dds_results_no_delete",
        "error_events_no_update", "error_events_no_delete",
        "skill_evidence_no_update", "skill_evidence_no_delete",
        "experience_events_no_update", "experience_events_no_delete",
        "correction_events_no_update", "correction_events_no_delete",
        "counterexamples_no_update", "counterexamples_no_delete",
        "skill_state_history_no_update", "skill_state_history_no_delete",
        "audit_events_no_update", "audit_events_no_delete",
        "checkpoints_no_update", "checkpoints_no_delete",
        "run_task_events_no_update", "run_task_events_no_delete",
        "investigation_events_no_update", "investigation_events_no_delete",
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
        "counterexamples", "correction_events", "learning_queue", "runs", "run_task_events", "investigation_events", "checkpoints",
    ):
        counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    counts["investigation_required_results"] = int(required_investigations)
    counts["open_investigations"] = int(open_investigations)

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
