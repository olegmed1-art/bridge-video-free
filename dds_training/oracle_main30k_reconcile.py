from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from audit import audit_database, persist_audit
from config import ALGORITHM_VERSION
from investigations import resolve_investigation, sync_required_investigations
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



def _resolve_deterministic_investigations(con, run_id: str) -> dict:
    rows = con.execute(
        """
        SELECT e.task_id,r.task_type,r.result_json
        FROM investigation_events e
        JOIN (SELECT task_id,MAX(id) max_id FROM investigation_events GROUP BY task_id) x
          ON x.max_id=e.id
        JOIN dds_results r ON r.task_id=e.task_id
        WHERE e.event_type IN ('opened','reopened')
        ORDER BY e.task_id
        """
    ).fetchall()
    resolved = 0
    unsupported = 0
    by_code: dict[str, int] = {}
    for task_id, task_type, raw in rows:
        result = json.loads(raw)
        code = str(result.get("error_code", ""))
        if code == "D_OVER_DDS_CLAIM":
            predicted = result.get("predicted_tricks")
            optimum = result.get("dds_tricks")
            if not isinstance(predicted, int) or not isinstance(optimum, int) or predicted <= optimum:
                unsupported += 1
                continue
            cause = f"Locked declarer prediction claimed {predicted} tricks, above the immutable DDS optimum of {optimum}."
            refutation = f"Optimal defence holds declarer to {optimum} tricks; the first contradiction is the {predicted}>{optimum} DDS bound."
            lesson = "Do not claim a declarer result above the double-dummy optimum; create transfer and counterexample tasks around the over-claim pattern."
        elif code == "F_DEFENSE_OVER_DDS_CLAIM":
            predicted = result.get("expected_defense_tricks")
            optimum = result.get("best_defense_tricks")
            if not isinstance(predicted, int) or not isinstance(optimum, int) or predicted <= optimum:
                unsupported += 1
                continue
            cause = f"Locked defence prediction claimed {predicted} tricks, above the immutable DDS optimum of {optimum}."
            refutation = f"Double-dummy play limits the defence to {optimum} tricks; the first contradiction is the {predicted}>{optimum} DDS bound."
            lesson = "Do not claim more defensive tricks than the DDS optimum; prioritize counterexamples for the responsible lead/defence skill."
        else:
            unsupported += 1
            continue
        resolve_investigation(
            con,
            task_id=task_id,
            cause=cause,
            first_refutation=refutation,
            lesson=lesson,
            run_id=run_id,
            evidence={
                "method": "immutable-dds-bound-reconciliation/v1",
                "task_type": task_type,
                "error_code": code,
                "dds_recomputed": False,
            },
        )
        resolved += 1
        by_code[code] = by_code.get(code, 0) + 1
    return {"resolved_now": resolved, "unsupported_open": unsupported, "resolved_by_code": by_code}


def reconcile(work: Path, run_id: str, plan_limit: int = 64) -> dict:
    con = connect(work / "training.sqlite3")
    sync = sync_required_investigations(con, run_id)
    resolution = _resolve_deterministic_investigations(con, run_id)
    sync["open_total"] = sync_required_investigations(con, run_id)["open_total"]
    recompute_all_skills(con)
    plan = build_learning_plan(con, plan_limit)
    existing_plan_rows = con.execute(
        "SELECT COUNT(*) FROM learning_queue WHERE source_run_id=?", (run_id,)
    ).fetchone()[0]
    if not existing_plan_rows:
        persist_learning_plan(con, plan, run_id)
        plan_rows_persisted = len(plan)
    else:
        plan_rows_persisted = int(existing_plan_rows)
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
        "deterministic_resolution": resolution,
        "high_confidence_errors": int(high_confidence),
        "priority_plan_rows_persisted": plan_rows_persisted,
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
        f"resolved_now={result['deterministic_resolution']['resolved_now']} "
        f"unsupported_open={result['deterministic_resolution']['unsupported_open']} "
        f"high_confidence_errors={result['high_confidence_errors']} "
        f"priority_plan_rows={result['priority_plan_rows_persisted']} "
        f"sealed_results={result['sealed_results_present']} "
        f"sealed_learning_leaks={result['sealed_learning_leaks']} "
        "dds_called=false validation_opened=false sealed_test_opened=false"
    )


if __name__ == "__main__":
    main()
