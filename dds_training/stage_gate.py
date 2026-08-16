from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from audit import audit_database
from investigations import open_investigations, sync_required_investigations
from methodology_audit import audit_methodology
from regression_links import sync_regression_skill_links
from run_provenance import ensure_run_task_table
from stage_scope import task_in_stage
from storage import connect


def _load_tasks(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def assess_stage(work: Path, stage: str) -> dict:
    """Assess technical completion and methodological readiness separately.

    Database integrity is necessary but not sufficient. A stage can be fully
    computed and auditable while still being methodologically unfit for expansion
    (for example, one-sided follow-ups or same-source probes counted as transfer).
    This gate therefore reports both states and gives one concrete next action.
    """
    task_path = work / "blind_tasks.jsonl"
    if not task_path.exists():
        raise FileNotFoundError(task_path)
    tasks = _load_tasks(task_path)
    stage_tasks = [t for t in tasks if t.get("split") != "derived" and task_in_stage(t, stage)]
    expected_by_split = Counter(t["split"] for t in stage_tasks)

    con = connect(work / "training.sqlite3")
    ensure_run_task_table(con)
    investigation_sync = sync_required_investigations(con, run_id=f"stage-gate-{stage}")
    regression_link_sync = sync_regression_skill_links(con)
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
    stage_ids = {t["task_id"] for t in stage_tasks}
    open_stage = [x for x in open_items if x["task_id"] in stage_ids]
    audit = audit_database(con)
    methodology = audit_methodology(work)
    report_path = work / f"report_{stage}.md"

    all_base_evaluated = all(v == 0 for v in missing_by_split.values())
    technical_stage_complete = all_base_evaluated and not open_stage and audit["status"] == "ok"
    ready_for_report = technical_stage_complete
    report_exists = report_path.exists()
    methodology_ready = bool(methodology.get("advance_allowed"))
    ready_for_next_stage = technical_stage_complete and report_exists and methodology_ready

    if not all_base_evaluated:
        next_action = "Complete the missing fresh blind evaluations shown in missing_by_split."
    elif open_stage:
        next_action = "Resolve every mandatory better-than-DDS investigation before closing the stage."
    elif audit["status"] != "ok":
        next_action = "Fix the database/provenance audit errors before closing the stage."
    elif not report_exists:
        next_action = f"Generate report_{stage}.md immediately; the stage is otherwise technically complete."
    elif not methodology_ready:
        blockers = [x["code"] for x in methodology.get("findings", []) if x.get("severity") == "error"]
        next_action = f"Apply the methodological corrections before expansion: {blockers}."
    elif stage == "pilot":
        next_action = "Review the corrected pilot report and obtain explicit user approval before expanding the same work directory to the main 30k corpus."
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
        "investigation_sync": investigation_sync,
        "regression_skill_link_sync": regression_link_sync,
        "audit_status": audit["status"],
        "methodology_status": methodology["status"],
        "methodology_findings": methodology["findings"],
        "technical_stage_complete": technical_stage_complete,
        "ready_for_report": ready_for_report,
        "report_exists": report_exists,
        "methodology_ready_for_expansion": methodology_ready,
        "ready_for_next_stage": ready_for_next_stage,
        "explicit_user_approval_required": True,
        "next_action": next_action,
        "dds_called": False,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="DDS stage-completion and methodology gate")
    p.add_argument("--work", required=True)
    p.add_argument("--stage", choices=("pilot", "main", "targeted"), required=True)
    args = p.parse_args()
    result = assess_stage(Path(args.work), args.stage)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
