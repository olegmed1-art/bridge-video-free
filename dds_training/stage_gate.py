from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from audit import audit_database
from investigations import open_investigations, sync_required_investigations
from run_provenance import ensure_run_task_table
from stage_scope import task_in_stage
from storage import connect


def _load_tasks(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def assess_stage(work: Path, stage: str) -> dict:
    """Assess whether a stage is truly complete before report/next-stage work.

    This gate is intentionally separate from DDS evaluation. It never calls DDS
    and never mutates skill memory. It synchronizes the mandatory-investigation
    ledger, checks fresh stage task coverage and database provenance, then gives
    one concrete next action for the operator/user.
    """
    task_path = work / "blind_tasks.jsonl"
    if not task_path.exists():
        raise FileNotFoundError(task_path)
    tasks = _load_tasks(task_path)
    stage_tasks = [t for t in tasks if t.get("split") != "derived" and task_in_stage(t, stage)]
    expected_by_split = Counter(t["split"] for t in stage_tasks)

    con = connect(work / "training.sqlite3")
    ensure_run_task_table(con)
    sync_required_investigations(con, run_id=f"stage-gate-{stage}")
    con.commit()

    evaluated_ids = {
        row[0]
        for row in con.execute(
            """
            SELECT DISTINCT e.task_id
            FROM run_task_events e
            JOIN runs ru ON ru.run_id=e.run_id
            WHERE e.action='evaluated' AND ru.stage=?
            """,
            (stage,),
        )
    }
    actual_by_split = Counter(t["split"] for t in stage_tasks if t["task_id"] in evaluated_ids)
    missing_by_split = {
        split: int(expected_by_split[split] - actual_by_split[split])
        for split in ("train", "validation", "sealed_test")
    }

    open_items = open_investigations(con)
    open_stage = [x for x in open_items if x["task_id"] in {t["task_id"] for t in stage_tasks}]
    audit = audit_database(con)
    report_path = work / f"report_{stage}.md"

    all_base_evaluated = all(v == 0 for v in missing_by_split.values())
    ready_for_report = all_base_evaluated and not open_stage and audit["status"] == "ok"
    report_exists = report_path.exists()
    ready_for_next_stage = ready_for_report and report_exists

    if not all_base_evaluated:
        next_action = "Complete the missing fresh blind evaluations shown in missing_by_split."
    elif open_stage:
        next_action = "Resolve every mandatory better-than-DDS investigation before closing the stage."
    elif audit["status"] != "ok":
        next_action = "Fix the database/provenance audit errors before closing the stage."
    elif not report_exists:
        next_action = f"Generate report_{stage}.md immediately; the stage is otherwise complete."
    elif stage == "pilot":
        next_action = "Review the pilot report and obtain explicit user approval before expanding the same work directory to the main 30k corpus."
    elif stage == "main":
        next_action = "Review the main report and choose targeted weaknesses before generating the next ~10k focused tasks."
    else:
        next_action = "Review the targeted-stage report and expand further only if metrics justify it."

    return {
        "stage": stage,
        "expected_by_split": dict(expected_by_split),
        "evaluated_by_split": {k: int(actual_by_split[k]) for k in expected_by_split},
        "missing_by_split": missing_by_split,
        "open_mandatory_investigations": len(open_stage),
        "audit_status": audit["status"],
        "ready_for_report": ready_for_report,
        "report_exists": report_exists,
        "ready_for_next_stage": ready_for_next_stage,
        "next_action": next_action,
        "dds_called": False,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="DDS stage-completion gate")
    p.add_argument("--work", required=True)
    p.add_argument("--stage", choices=("pilot", "main", "targeted"), required=True)
    args = p.parse_args()
    result = assess_stage(Path(args.work), args.stage)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
