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
    """Summarize DD value swings where value = max declarer tricks remaining/available.

    `values` has one more entry than `actors`: value before the first decision and
    after every recorded decision. A declarer error decreases value; a defense
    error increases it. This finds the first swing even when later opponent errors
    restore the lost trick.
    """
    if len(values) != len(actors) + 1:
        raise ValueError("values must have len(actors)+1 entries")
    if any(a not in {"declarer", "defense"} for a in actors):
        raise ValueError("actors must be declarer or defense")
    swings = []
    declarer_gross_loss = 0
    defense_gross_gift = 0
    first_error = None
    for i, actor in enumerate(actors):
        before, after = int(values[i]), int(values[i + 1])
        delta = after - before
        error = False
        magnitude = 0
        if actor == "declarer" and delta < 0:
            error, magnitude = True, -delta
            declarer_gross_loss += magnitude
        elif actor == "defense" and delta > 0:
            error, magnitude = True, delta
            defense_gross_gift += magnitude
        if delta != 0:
            item = {"decision_index": i, "actor": actor, "before": before, "after": after, "delta": delta, "error": error, "magnitude": magnitude}
            swings.append(item)
            if error and first_error is None:
                first_error = item
    start, end = int(values[0]), int(values[-1])
    net_change = end - start
    # A later defense error can restore declarer tricks previously lost and vice versa.
    recovered_declarer_loss = max(0, min(declarer_gross_loss, defense_gross_gift))
    recovered_defense_gain = recovered_declarer_loss
    return {
        "start_value": start,
        "end_value": end,
        "net_change": net_change,
        "first_error": first_error,
        "swings": swings,
        "declarer_gross_loss": declarer_gross_loss,
        "defense_gross_gift": defense_gross_gift,
        "recovered_declarer_loss": recovered_declarer_loss,
        "recovered_defense_gain": recovered_defense_gain,
        "unrecovered_declarer_loss": max(0, declarer_gross_loss - defense_gross_gift),
        "unrecovered_defense_gift": max(0, defense_gross_gift - declarer_gross_loss),
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
