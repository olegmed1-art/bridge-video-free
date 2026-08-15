from __future__ import annotations

import json
import sqlite3

from config import ALGORITHM_VERSION


def _event(
    con: sqlite3.Connection,
    *,
    event_key: str,
    event_type: str,
    task_id: str | None,
    deal_id: str | None,
    run_id: str | None,
    payload: dict,
) -> None:
    con.execute(
        """
        INSERT OR IGNORE INTO experience_events
          (event_key,event_type,task_id,deal_id,run_id,algorithm_version,payload_json)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            event_key,
            event_type,
            task_id,
            deal_id,
            run_id,
            ALGORITHM_VERSION,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ),
    )


def record_reasoning_review(
    con: sqlite3.Connection,
    *,
    task_id: str,
    deal_id: str,
    verdict: str,
    details: dict,
    run_id: str | None = None,
    review_id: str = "primary",
) -> None:
    """Store whether the bridge explanation was sound independently of trick count."""
    allowed = {"correct", "correct_result_wrong_reasoning", "incorrect", "needs_review"}
    if verdict not in allowed:
        raise ValueError(f"reasoning verdict must be one of {sorted(allowed)}")
    _event(
        con,
        event_key=f"{ALGORITHM_VERSION}:{task_id}:reasoning:{review_id}",
        event_type="reasoning_review",
        task_id=task_id,
        deal_id=deal_id,
        run_id=run_id,
        payload={"verdict": verdict, "details": details},
    )


def summarize_value_trajectory(values: list[int], actors: list[str]) -> dict:
    """Summarize DD swings on a constant scale: projected final declarer tricks.

    `values` has one more entry than `actors`: the value before the first decision
    and after every recorded decision. Under this definition, a declarer choice
    can only preserve or reduce the optimum, while a defender choice can only
    preserve or increase declarer's optimum. Opposite-direction movements are
    recorded as invariant violations because they usually signal inconsistent
    position encoding or a change of value definition.

    Recovery is temporal. A defensive gift restores an earlier declarer loss only
    when that loss is already outstanding; an earlier gift cannot retroactively
    'repair' a later declarer error.
    """
    if len(values) != len(actors) + 1:
        raise ValueError("values must have len(actors)+1 entries")
    if any(a not in {"declarer", "defense"} for a in actors):
        raise ValueError("actors must be declarer or defense")
    if any(int(v) < 0 or int(v) > 13 for v in values):
        raise ValueError("DD projected trick values must be in 0..13")

    swings = []
    invariant_violations = []
    declarer_gross_loss = 0
    defense_gross_gift = 0
    recovered_declarer_loss = 0
    squandered_defense_gift = 0
    outstanding_declarer_loss = 0
    outstanding_defense_gift = 0
    first_error = None

    for i, actor in enumerate(actors):
        before, after = int(values[i]), int(values[i + 1])
        delta = after - before
        error = False
        magnitude = 0

        if actor == "declarer":
            if delta < 0:
                error, magnitude = True, -delta
                declarer_gross_loss += magnitude
                consume = min(outstanding_defense_gift, magnitude)
                if consume:
                    outstanding_defense_gift -= consume
                    squandered_defense_gift += consume
                outstanding_declarer_loss += magnitude - consume
            elif delta > 0:
                invariant_violations.append({
                    "decision_index": i,
                    "actor": actor,
                    "before": before,
                    "after": after,
                    "delta": delta,
                    "reason": "declarer move increased projected optimum",
                })
        else:
            if delta > 0:
                error, magnitude = True, delta
                defense_gross_gift += magnitude
                restore = min(outstanding_declarer_loss, magnitude)
                if restore:
                    outstanding_declarer_loss -= restore
                    recovered_declarer_loss += restore
                outstanding_defense_gift += magnitude - restore
            elif delta < 0:
                invariant_violations.append({
                    "decision_index": i,
                    "actor": actor,
                    "before": before,
                    "after": after,
                    "delta": delta,
                    "reason": "defender move reduced declarer's projected optimum",
                })

        if delta != 0:
            item = {
                "decision_index": i,
                "actor": actor,
                "before": before,
                "after": after,
                "delta": delta,
                "error": error,
                "magnitude": magnitude,
            }
            swings.append(item)
            if error and first_error is None:
                first_error = item

    start, end = int(values[0]), int(values[-1])
    return {
        "value_definition": "projected_final_declarer_tricks",
        "start_value": start,
        "end_value": end,
        "net_change": end - start,
        "first_error": first_error,
        "swings": swings,
        "invariant_violations": invariant_violations,
        "declarer_gross_loss": declarer_gross_loss,
        "defense_gross_gift": defense_gross_gift,
        "recovered_declarer_loss": recovered_declarer_loss,
        "squandered_defense_gift": squandered_defense_gift,
        "unrecovered_declarer_loss": outstanding_declarer_loss,
        "unrecovered_defense_gift": outstanding_defense_gift,
    }


def record_value_trajectory(
    con: sqlite3.Connection,
    *,
    task_id: str,
    deal_id: str,
    values: list[int],
    actors: list[str],
    run_id: str | None = None,
    trajectory_id: str = "main",
) -> dict:
    summary = summarize_value_trajectory(values, actors)
    _event(
        con,
        event_key=f"{ALGORITHM_VERSION}:{task_id}:trajectory:{trajectory_id}",
        event_type="value_trajectory",
        task_id=task_id,
        deal_id=deal_id,
        run_id=run_id,
        payload=summary,
    )
    return summary


def schedule_spaced_reviews(
    con: sqlite3.Connection,
    *,
    skill_key: str,
    source_task_id: str,
    current_evaluations: int,
    run_id: str | None = None,
    offsets: tuple[int, ...] = (100, 1000, 10000),
) -> None:
    """Schedule delayed checks so a corrected error must stay corrected."""
    for offset in offsets:
        details = {
            "source_task_id": source_task_id,
            "due_after_evaluations": current_evaluations + offset,
            "offset": offset,
        }
        details_json = json.dumps(details, ensure_ascii=False, sort_keys=True)
        exists = con.execute(
            "SELECT 1 FROM learning_queue WHERE skill_key=? AND purpose='spaced_review' AND details_json=? LIMIT 1",
            (skill_key, details_json),
        ).fetchone()
        if exists:
            continue
        con.execute(
            """
            INSERT INTO learning_queue
              (skill_key,purpose,priority,requested_tasks,status,source_run_id,details_json)
            VALUES(?,?,?,?,?,?,?)
            """,
            (skill_key, "spaced_review", 1.0 + 1000.0 / offset, 1, "planned", run_id, details_json),
        )
