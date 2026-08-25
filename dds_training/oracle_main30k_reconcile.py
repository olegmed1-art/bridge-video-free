from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from audit import audit_database, persist_audit
from config import ALGORITHM_VERSION
from investigations import sync_required_investigations
from learning import build_learning_plan, persist_learning_plan, recompute_all_skills
from storage import connect


def _sealed_provenance(con) -> list[dict]:
    rows = con.execute(
        """
        SELECT ru.run_id,ru.stage,ru.requested_splits_json,ru.sealed_opened,
               ru.status,COUNT(DISTINCT e.task_id)
        FROM runs ru
        JOIN run_task_events e ON e.run_id=ru.run_id AND e.action='evaluated'
        JOIN dds_results r ON r.task_id=e.task_id AND r.split='sealed_test'
        GROUP BY ru.run_id,ru.stage,ru.requested_splits_json,ru.sealed_opened,ru.status
        ORDER BY ru.run_id
        """
    ).fetchall()
    return [
        {
            "run_id": run_id,
            "stage": stage,
            "requested_splits": json.loads(splits),
            "sealed_opened": bool(sealed_opened),
            "status": status,
            "evaluated_sealed_tasks": int(count),
        }
        for run_id, stage, splits, sealed_opened, status, count in rows
    ]


def reconcile(work: Path, run_id: str, plan_limit: int = 64) -> dict:
    con = connect(work / "training.sqlite3")
    sync = sync_required_investigations(con, run_id)
    recompute_all_skills(con)
    plan = build_learning_plan(con, plan_limit)
    persist_learning_plan(con, plan, run_id)
    audit = audit_database(con)
    persist_audit(con, audit, run_id)
    high_confidence = con.execute(
        """
        SELECT COUNT(*) FROM skill_evidence
        WHERE algorithm_version=? AND confidence='high' AND outcome!='success'
        """,
        (ALGORITHM_VERSION,),
    ).fetchone()[0]
    sealed_results = con.execute(
        "SELECT COUNT(*) FROM dds_results WHERE split='sealed_test'"
    ).fetchone()[0]
    sealed_learning = con.execute(
        """
        SELECT COUNT(*) FROM skill_evidence se
        JOIN dds_results r ON r.task_id=se.task_id
        WHERE r.split='sealed_test'
        """
    ).fetchone()[0]
    result = {
        "schema": "bridge-school-dds3-main30k-reconciliation/v1",
        "status": "reconciled",
        "dds_called": False,
        "validation_opened": False,
        "sealed_test_opened": False,
        "run_id": run_id,
        "investigations": sync,
        "high_confidence_errors": int(high_confidence),
        "priority_plan_rows_persisted": len(plan),
        "sealed_results_present": int(sealed_results),
        "sealed_learning_leaks": int(sealed_learning),
        "sealed_provenance": _sealed_provenance(con),
        "audit_status": audit["status"],
        "audit_issues": audit["issues"],
    }
    con.commit()
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--plan-limit", type=int, default=64)
    args = p.parse_args()
    if not 1 <= args.plan_limit <= 500:
        raise SystemExit("plan-limit must be between 1 and 500")
    result = reconcile(args.work.resolve(), args.run_id, args.plan_limit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(args.out.suffix + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    os.replace(tmp, args.out)
    print(
        "MAIN30K_RECONCILE_PASS "
        f"opened_now={result['investigations']['opened_now']} "
        f"open_total={result['investigations']['open_total']} "
        f"high_confidence_errors={result['high_confidence_errors']} "
        f"priority_plan_rows={result['priority_plan_rows_persisted']} "
        f"sealed_results={result['sealed_results_present']} "
        f"sealed_learning_leaks={result['sealed_learning_leaks']} "
        "dds_called=false validation_opened=false sealed_test_opened=false"
    )


if __name__ == "__main__":
    main()
