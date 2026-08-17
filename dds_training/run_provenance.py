from __future__ import annotations

import json
import sqlite3


DDL = """
CREATE TABLE IF NOT EXISTS run_task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  deal_id TEXT NOT NULL,
  split TEXT NOT NULL,
  task_type TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('evaluated','reused')),
  details_json TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id, task_id, action),
  FOREIGN KEY(run_id) REFERENCES runs(run_id),
  FOREIGN KEY(task_id) REFERENCES dds_results(task_id)
);
CREATE TRIGGER IF NOT EXISTS run_task_events_no_update
BEFORE UPDATE ON run_task_events BEGIN SELECT RAISE(ABORT, 'run_task_events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS run_task_events_no_delete
BEFORE DELETE ON run_task_events BEGIN SELECT RAISE(ABORT, 'run_task_events are append-only'); END;
"""


def ensure_run_task_table(con: sqlite3.Connection) -> None:
    """Install the per-task provenance ledger on old and new databases."""
    con.executescript(DDL)


def record_run_task(
    con: sqlite3.Connection,
    *,
    run_id: str,
    task: dict,
    action: str,
    details: dict | None = None,
) -> None:
    """Append or verify one run→task provenance fact.

    `evaluated` means DDS was newly computed/written under this run. `reused`
    means an immutable result already existed and was merely read in this run.
    Reuse can never retroactively prove that a sealed result was originally
    created under an authorized sealed-only evaluation.
    """
    if action not in {"evaluated", "reused"}:
        raise ValueError(f"Unsupported run-task action: {action}")
    ensure_run_task_table(con)
    payload = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
    metadata = (task["deal_id"], task["split"], task["task_type"], payload)
    existing = con.execute(
        """
        SELECT deal_id,split,task_type,COALESCE(details_json,'{}')
        FROM run_task_events WHERE run_id=? AND task_id=? AND action=?
        """,
        (run_id, task["task_id"], action),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != metadata:
            raise ValueError(f"Run-task provenance mismatch for {run_id}/{task['task_id']}/{action}")
        return
    con.execute(
        """
        INSERT INTO run_task_events
          (run_id,task_id,deal_id,split,task_type,action,details_json)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            run_id,
            task["task_id"],
            task["deal_id"],
            task["split"],
            task["task_type"],
            action,
            payload,
        ),
    )
