from __future__ import annotations

import json
import math
import sqlite3

from config import ALGORITHM_VERSION, SKILL_LIFECYCLE


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
    """Persist append-only learning evidence derived from one evaluated task.

    This never rewrites the locked prediction or DDS fact. Later re-interpretation
    is represented by correction_events and new evidence, preserving provenance.
    """
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

    event_payload = {
        "task_type": task["task_type"],
        "error_code": error_code,
        "regret": regret,
        "confidence": confidence,
        "skills": [x[0] for x in skills],
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
    """Append transfer/regression/counterexample/symmetry/perturbation evidence.

    This is the public path used after an initial error has generated new unseen
    checks. It never mutates prior evidence.
    """
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
                json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
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


def recompute_skill_state(con: sqlite3.Connection, skill_key: str) -> str:
    rows = con.execute(
        "SELECT evidence_type,outcome,split,confidence,COALESCE(regret,0) FROM skill_evidence WHERE skill_key=?",
        (skill_key,),
    ).fetchall()
    if not rows:
        return "candidate"

    total = len(rows)
    transfer = [r for r in rows if r[0] in {"transfer", "symmetry", "perturbation", "real_world"}]
    regression = [r for r in rows if r[0] == "regression"]
    counterexamples = [r for r in rows if r[0] == "counterexample"]
    transfer_pass = sum(r[1] == "success" for r in transfer)
    regression_pass = sum(r[1] == "success" for r in regression)
    regression_fail = sum(r[1] != "success" for r in regression)
    transfer_rate = transfer_pass / len(transfer) if transfer else 0.0

    old = con.execute("SELECT status FROM skill_profiles WHERE skill_key=?", (skill_key,)).fetchone()
    old_status = old[0] if old else None

    new_status = "candidate"
    if total >= SKILL_LIFECYCLE["testing_evidence"]:
        new_status = "testing"
    if len(transfer) >= SKILL_LIFECYCLE["confirmed_transfer"] and transfer_rate >= SKILL_LIFECYCLE["confirmed_rate"]:
        new_status = "confirmed"
    if (
        len(transfer) >= SKILL_LIFECYCLE["stable_transfer"]
        and transfer_rate >= SKILL_LIFECYCLE["stable_rate"]
        and regression_pass >= SKILL_LIFECYCLE["stable_regression_passes"]
        and len(counterexamples) >= SKILL_LIFECYCLE["stable_counterexamples"]
        and regression_fail == 0
    ):
        new_status = "stable"
    if old_status == "stable" and regression_fail > 0:
        new_status = "weakened"

    con.execute(
        """
        UPDATE skill_profiles
        SET status=?, evidence_count=?, transfer_count=?, regression_passes=?, regression_failures=?,
            counterexample_count=?, updated_at=CURRENT_TIMESTAMP
        WHERE skill_key=?
        """,
        (new_status, total, len(transfer), regression_pass, regression_fail, len(counterexamples), skill_key),
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
                        "total": total,
                        "transfer": len(transfer),
                        "transfer_rate": transfer_rate,
                        "regression_pass": regression_pass,
                        "regression_fail": regression_fail,
                        "counterexamples": len(counterexamples),
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
    """Rank weaknesses for the next targeted corpus without changing the benchmark."""
    rows = con.execute(
        """
        SELECT skill_key,
               COUNT(*) AS n,
               SUM(CASE WHEN outcome!='success' THEN 1 ELSE 0 END) AS errors,
               AVG(COALESCE(regret,0)) AS mean_regret,
               SUM(CASE WHEN outcome!='success' AND confidence='high' THEN 1 ELSE 0 END) AS high_conf_errors
        FROM skill_evidence
        WHERE evidence_type IN ('direct','error_pattern')
        GROUP BY skill_key
        """
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
                "purposes": ["transfer", "counterexample", "regression", "symmetry", "perturbation"],
            }
        )
    plan.sort(key=lambda x: (-x["priority"], x["skill_key"]))
    return plan[:limit]


def persist_learning_plan(con: sqlite3.Connection, plan: list[dict], run_id: str | None = None) -> None:
    for rank, item in enumerate(plan, 1):
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
