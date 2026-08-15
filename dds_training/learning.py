from __future__ import annotations

import json
import math
import sqlite3

from config import ALGORITHM_VERSION, SKILL_LIFECYCLE
from experience_events import schedule_spaced_reviews


SKILL_CATALOG = {
    "declarer.trick_estimation": {
        "side": "declarer",
        "family": "calculation",
        "title": "Accurate trick estimation",
        "trigger": "Before committing to a contract-value estimate, count sure, potential and unavoidable losing tricks.",
    },
    "declarer.missed_resources": {
        "side": "declarer",
        "family": "calculation",
        "title": "Find hidden extra tricks",
        "trigger": "When the blind estimate is below DDS, search for the missing technical resource before seeing a line.",
    },
    "declarer.overclaim_detection": {
        "side": "declarer",
        "family": "calculation",
        "title": "Reject lines that rely on a defensive error",
        "trigger": "When a proposed result exceeds DDS, locate the first point where optimal defense refutes the line.",
    },
    "defense.opening_lead": {
        "side": "defense",
        "family": "opening_lead",
        "title": "Opening-lead selection",
        "trigger": "Compare all legal opening leads and preserve all equal-optimal choices rather than memorizing one card.",
    },
    "defense.overclaim_detection": {
        "side": "defense",
        "family": "calculation",
        "title": "Reject defensive claims above DDS",
        "trigger": "When predicted defensive tricks exceed DDS, locate the declarer mistake implicitly assumed by the line.",
    },
}


def learning_allowed_for_task(task: dict) -> bool:
    """Return True only when this task may alter skill/rule memory.

    Validation and sealed-test tasks are evaluation-only. A derived task may
    learn only if its root source is the train split, preventing benchmark
    leakage through follow-up generation.
    """
    split = str(task.get("split", ""))
    if split == "train":
        return True
    if split == "derived":
        return str(task.get("source_root_split", "")) == "train"
    return False


def _confidence_value(prediction: dict) -> str:
    value = str(prediction.get("confidence", "unknown")).strip().lower()
    return value if value in {"low", "medium", "high"} else "unknown"


def _ensure_skill(con: sqlite3.Connection, skill_key: str) -> None:
    meta = SKILL_CATALOG.get(skill_key, {})
    con.execute(
        """
        INSERT OR IGNORE INTO skill_profiles
          (skill_key, side, family, title, status, trigger_text, rule_text, algorithm_version)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            skill_key,
            meta.get("side", "general"),
            meta.get("family", "general"),
            meta.get("title", skill_key),
            "candidate",
            meta.get("trigger"),
            None,
            ALGORITHM_VERSION,
        ),
    )


def _add_evidence(
    con: sqlite3.Connection,
    *,
    skill_key: str,
    task: dict,
    evidence_type: str,
    outcome: str,
    regret: float | None,
    confidence: str,
    run_id: str | None,
    payload: dict,
) -> None:
    if evidence_type not in {"direct", "error_pattern", "transfer", "regression", "counterexample", "symmetry", "perturbation", "real_world"}:
        raise ValueError(f"Unsupported evidence_type: {evidence_type}")
    if outcome not in {"success", "error"}:
        raise ValueError(f"Unsupported outcome: {outcome}")
    _ensure_skill(con, skill_key)
    con.execute(
        """
        INSERT OR IGNORE INTO skill_evidence
          (skill_key, task_id, deal_id, split, evidence_type, outcome, regret,
           confidence, run_id, algorithm_version, evidence_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            skill_key,
            task["task_id"],
            task["deal_id"],
            task.get("split", "derived"),
            evidence_type,
            outcome,
            regret,
            confidence,
            run_id,
            ALGORITHM_VERSION,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ),
    )


def record_task_experience(
    con: sqlite3.Connection,
    task: dict,
    prediction: dict,
    result: dict,
    run_id: str | None = None,
) -> list[str]:
    """Persist append-only learning evidence derived from one evaluated task."""
    if not learning_allowed_for_task(task):
        raise ValueError(f"Learning is forbidden for split/root: {task.get('split')}/{task.get('source_root_split')}")

    confidence = _confidence_value(prediction)
    error_code = result.get("error_code", "UNKNOWN")
    regret = result.get("dd_regret")
    if regret is None:
        regret = result.get("prediction_error")
    regret = None if regret is None else float(regret)

    skills: list[tuple[str, str, str]] = []
    if task["task_type"] == "contract_tricks":
        delta = int(result.get("delta_pred_minus_dds", 0))
        skills.append(("declarer.trick_estimation", "success" if delta == 0 else "error", "direct"))
        if delta < 0:
            skills.append(("declarer.missed_resources", "error", "error_pattern"))
        elif delta > 0:
            skills.append(("declarer.overclaim_detection", "error", "error_pattern"))
    elif task["task_type"] == "opening_lead":
        good = bool(result.get("legal_or_equivalent")) and float(result.get("dd_regret") or 0) == 0
        skills.append(("defense.opening_lead", "success" if good else "error", "direct"))
        if error_code == "F_DEFENSE_OVER_DDS_CLAIM":
            skills.append(("defense.overclaim_detection", "error", "error_pattern"))

    current_evaluations = con.execute("SELECT COUNT(*) FROM dds_results").fetchone()[0]
    for key, outcome, evidence_type in skills:
        _add_evidence(
            con,
            skill_key=key,
            task=task,
            evidence_type=evidence_type,
            outcome=outcome,
            regret=regret,
            confidence=confidence,
            run_id=run_id,
            payload={"prediction": prediction, "result": result},
        )
        if outcome == "error":
            schedule_spaced_reviews(
                con,
                skill_key=key,
                source_task_id=task["task_id"],
                current_evaluations=current_evaluations,
                run_id=run_id,
            )

    event_payload = {
        "task_type": task["task_type"],
        "error_code": error_code,
        "regret": regret,
        "confidence": confidence,
        "skills": [x[0] for x in skills],
        "source_root_split": task.get("source_root_split", task.get("split")),
    }
    con.execute(
        """
        INSERT OR IGNORE INTO experience_events
          (event_key, event_type, task_id, deal_id, run_id, algorithm_version, payload_json)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            f"{ALGORITHM_VERSION}:{task['task_id']}:evaluation",
            "evaluation",
            task["task_id"],
            task["deal_id"],
            run_id,
            ALGORITHM_VERSION,
            json.dumps(event_payload, ensure_ascii=False, sort_keys=True),
        ),
    )
    for key, _, _ in skills:
        recompute_skill_state(con, key)
    return [x[0] for x in skills]


def record_skill_check(
    con: sqlite3.Connection,
    *,
    skill_key: str,
    task_id: str,
    deal_id: str,
    evidence_type: str,
    success: bool,
    regret: float | None = None,
    confidence: str = "unknown",
    run_id: str | None = None,
    split: str = "derived",
    details: dict | None = None,
) -> str:
    """Append transfer/regression/counterexample/symmetry/perturbation evidence."""
    task = {"task_id": task_id, "deal_id": deal_id, "split": split}
    _add_evidence(
        con,
        skill_key=skill_key,
        task=task,
        evidence_type=evidence_type,
        outcome="success" if success else "error",
        regret=regret,
        confidence=confidence,
        run_id=run_id,
        payload=details or {},
    )
    if evidence_type == "counterexample":
        con.execute(
            """
            INSERT OR IGNORE INTO counterexamples(skill_key,deal_id,task_id,description,result_json)
            VALUES(?,?,?,?,?)
            """,
            (
                skill_key,
                deal_id,
                task_id,
                (details or {}).get("description"),
                json.dumps({"success": success, **(details or {})}, ensure_ascii=False, sort_keys=True),
            ),
        )
    status = recompute_skill_state(con, skill_key)
    con.execute(
        """
        INSERT OR IGNORE INTO experience_events
          (event_key,event_type,task_id,deal_id,run_id,algorithm_version,payload_json)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            f"{ALGORITHM_VERSION}:{task_id}:{evidence_type}:{skill_key}",
            evidence_type,
            task_id,
            deal_id,
            run_id,
            ALGORITHM_VERSION,
            json.dumps({"skill_key": skill_key, "success": success, "status_after": status, "details": details or {}}, ensure_ascii=False, sort_keys=True),
        ),
    )
    return status


def _consecutive_successes(rows: list[tuple]) -> int:
    streak = 0
    for row in reversed(rows):
        if row[2] == "success":
            streak += 1
        else:
            break
    return streak


def recompute_skill_state(con: sqlite3.Connection, skill_key: str) -> str:
    rows = con.execute(
        """
        SELECT id,evidence_type,outcome,split,confidence,COALESCE(regret,0)
        FROM skill_evidence
        WHERE skill_key=? AND algorithm_version=? ORDER BY id
        """,
        (skill_key, ALGORITHM_VERSION),
    ).fetchall()
    if not rows:
        return "candidate"

    total = len(rows)
    transfer = [r for r in rows if r[1] in {"transfer", "symmetry", "perturbation", "real_world"}]
    regression = [r for r in rows if r[1] == "regression"]
    counterexamples = [r for r in rows if r[1] == "counterexample"]
    transfer_pass = sum(r[2] == "success" for r in transfer)
    regression_pass = sum(r[2] == "success" for r in regression)
    regression_fail = sum(r[2] != "success" for r in regression)
    counterexample_pass = sum(r[2] == "success" for r in counterexamples)
    counterexample_fail = sum(r[2] != "success" for r in counterexamples)
    transfer_rate = transfer_pass / len(transfer) if transfer else 0.0
    recent_regression_streak = _consecutive_successes(regression)
    recent_counterexample_streak = _consecutive_successes(counterexamples)
    latest_regression_failed = bool(regression and regression[-1][2] != "success")
    latest_counterexample_failed = bool(counterexamples and counterexamples[-1][2] != "success")

    old = con.execute("SELECT status FROM skill_profiles WHERE skill_key=?", (skill_key,)).fetchone()
    old_status = old[0] if old else None

    new_status = "candidate"
    if total >= SKILL_LIFECYCLE["testing_evidence"]:
        new_status = "testing"
    if len(transfer) >= SKILL_LIFECYCLE["confirmed_transfer"] and transfer_rate >= SKILL_LIFECYCLE["confirmed_rate"]:
        new_status = "confirmed"

    stable_ready = (
        len(transfer) >= SKILL_LIFECYCLE["stable_transfer"]
        and transfer_rate >= SKILL_LIFECYCLE["stable_rate"]
        and recent_regression_streak >= SKILL_LIFECYCLE["stable_regression_passes"]
        and recent_counterexample_streak >= SKILL_LIFECYCLE["stable_counterexamples"]
    )
    if stable_ready:
        new_status = "stable"

    fresh_failure = latest_regression_failed or latest_counterexample_failed
    if fresh_failure and old_status in {"stable", "confirmed", "weakened"}:
        new_status = "weakened"
    elif (
        old_status == "weakened"
        and stable_ready
        and recent_regression_streak >= SKILL_LIFECYCLE["recovery_regression_passes"]
    ):
        new_status = "stable"

    con.execute(
        """
        UPDATE skill_profiles
        SET status=?, evidence_count=?, transfer_count=?, regression_passes=?, regression_failures=?,
            counterexample_count=?, algorithm_version=?, updated_at=CURRENT_TIMESTAMP
        WHERE skill_key=?
        """,
        (new_status, total, len(transfer), regression_pass, regression_fail, counterexample_pass, ALGORITHM_VERSION, skill_key),
    )
    if old_status and old_status != new_status:
        con.execute(
            "INSERT INTO skill_state_history(skill_key,from_status,to_status,reason_json) VALUES(?,?,?,?)",
            (
                skill_key,
                old_status,
                new_status,
                json.dumps(
                    {
                        "algorithm_version": ALGORITHM_VERSION,
                        "total": total,
                        "transfer": len(transfer),
                        "transfer_rate": transfer_rate,
                        "regression_pass": regression_pass,
                        "regression_fail": regression_fail,
                        "recent_regression_streak": recent_regression_streak,
                        "counterexample_pass": counterexample_pass,
                        "counterexample_fail": counterexample_fail,
                        "recent_counterexample_streak": recent_counterexample_streak,
                    },
                    sort_keys=True,
                ),
            ),
        )
    return new_status


def recompute_all_skills(con: sqlite3.Connection) -> None:
    keys = [r[0] for r in con.execute("SELECT skill_key FROM skill_profiles")]
    for key in keys:
        recompute_skill_state(con, key)


def build_learning_plan(con: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Rank current-version weaknesses without using holdout evidence."""
    rows = con.execute(
        """
        SELECT skill_key,
               COUNT(*) AS n,
               SUM(CASE WHEN outcome!='success' THEN 1 ELSE 0 END) AS errors,
               AVG(COALESCE(regret,0)) AS mean_regret,
               SUM(CASE WHEN outcome!='success' AND confidence='high' THEN 1 ELSE 0 END) AS high_conf_errors
        FROM skill_evidence
        WHERE evidence_type IN ('direct','error_pattern')
          AND split IN ('train','derived')
          AND algorithm_version=?
        GROUP BY skill_key
        """,
        (ALGORITHM_VERSION,),
    ).fetchall()
    plan = []
    for key, n, errors, mean_regret, high_conf_errors in rows:
        error_rate = (errors or 0) / n if n else 0.0
        scarcity = 1.0 / math.sqrt(max(1, n))
        priority = 4.0 * error_rate + 1.5 * float(mean_regret or 0) + 2.0 * ((high_conf_errors or 0) / max(1, n)) + scarcity
        recommended = max(50, min(2000, int(100 + 500 * error_rate + 100 * float(mean_regret or 0))))
        plan.append(
            {
                "skill_key": key,
                "observations": n,
                "errors": errors or 0,
                "error_rate": round(error_rate, 4),
                "mean_regret": round(float(mean_regret or 0), 4),
                "high_confidence_errors": high_conf_errors or 0,
                "priority": round(priority, 4),
                "recommended_targeted_tasks": recommended,
                "purposes": ["transfer", "counterexample", "regression", "symmetry", "perturbation", "spaced_review"],
            }
        )
    plan.sort(key=lambda x: (-x["priority"], x["skill_key"]))
    return plan[:limit]


def persist_learning_plan(con: sqlite3.Connection, plan: list[dict], run_id: str | None = None) -> None:
    for rank, item in enumerate(plan, 1):
        existing = con.execute(
            "SELECT 1 FROM learning_queue WHERE skill_key=? AND purpose='targeted_transfer' AND source_run_id IS ? LIMIT 1",
            (item["skill_key"], run_id),
        ).fetchone()
        if existing:
            continue
        con.execute(
            """
            INSERT INTO learning_queue
              (skill_key, purpose, priority, requested_tasks, status, source_run_id, details_json)
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                item["skill_key"],
                "targeted_transfer",
                float(item["priority"]),
                int(item["recommended_targeted_tasks"]),
                "planned",
                run_id,
                json.dumps({"rank": rank, **item}, ensure_ascii=False, sort_keys=True),
            ),
        )
