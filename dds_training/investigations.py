from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from config import ALGORITHM_VERSION
from storage import connect

DDL = """
CREATE TABLE IF NOT EXISTS investigation_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  event_type TEXT NOT NULL CHECK(event_type IN ('opened','resolved','reopened')),
  run_id TEXT,
  algorithm_version TEXT NOT NULL,
  details_json TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(task_id) REFERENCES dds_results(task_id)
);
CREATE INDEX IF NOT EXISTS investigation_events_task_id_id ON investigation_events(task_id,id);
CREATE TRIGGER IF NOT EXISTS investigation_events_no_update
BEFORE UPDATE ON investigation_events BEGIN SELECT RAISE(ABORT, 'investigation_events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS investigation_events_no_delete
BEFORE DELETE ON investigation_events BEGIN SELECT RAISE(ABORT, 'investigation_events are append-only'); END;
"""


def ensure_investigation_table(con: sqlite3.Connection) -> None:
    con.executescript(DDL)


def _latest_event(con: sqlite3.Connection, task_id: str) -> tuple | None:
    return con.execute(
        "SELECT id,event_type,details_json FROM investigation_events WHERE task_id=? ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()


def sync_required_investigations(con: sqlite3.Connection, run_id: str | None = None) -> dict:
    """Open ledger entries for every immutable DDS fact that requires replay.

    The operation is idempotent. A previously resolved case is not reopened just
    because the immutable DDS result still has investigation_required=1; reopen
    must be explicit when new evidence invalidates the resolution.
    """
    ensure_investigation_table(con)
    rows = con.execute(
        """
        SELECT task_id,deal_id,task_type,split,result_json
        FROM dds_results WHERE investigation_required=1 ORDER BY task_id
        """
    ).fetchall()
    opened = 0
    for task_id, deal_id, task_type, split, result_json in rows:
        if _latest_event(con, task_id) is not None:
            continue
        result = json.loads(result_json)
        con.execute(
            """
            INSERT INTO investigation_events(task_id,event_type,run_id,algorithm_version,details_json)
            VALUES(?,?,?,?,?)
            """,
            (
                task_id,
                "opened",
                run_id,
                ALGORITHM_VERSION,
                json.dumps(
                    {
                        "deal_id": deal_id,
                        "task_type": task_type,
                        "split": split,
                        "error_code": result.get("error_code"),
                        "reason": "Prediction claims a result better than the DDS optimum and must be replayed against optimal opposition.",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        )
        opened += 1
    return {"required_results": len(rows), "opened_now": opened, "open_total": len(open_investigations(con))}


def open_investigations(con: sqlite3.Connection) -> list[dict]:
    ensure_investigation_table(con)
    rows = con.execute(
        """
        SELECT e.task_id,e.event_type,e.details_json,r.deal_id,r.task_type,r.split,r.result_json
        FROM investigation_events e
        JOIN (
          SELECT task_id,MAX(id) max_id FROM investigation_events GROUP BY task_id
        ) latest ON latest.max_id=e.id
        JOIN dds_results r ON r.task_id=e.task_id
        WHERE e.event_type IN ('opened','reopened')
        ORDER BY e.task_id
        """
    ).fetchall()
    return [
        {
            "task_id": task_id,
            "status": event_type,
            "deal_id": deal_id,
            "task_type": task_type,
            "split": split,
            "opening_details": json.loads(details_json),
            "dds_result": json.loads(result_json),
        }
        for task_id, event_type, details_json, deal_id, task_type, split, result_json in rows
    ]


def resolve_investigation(
    con: sqlite3.Connection,
    *,
    task_id: str,
    cause: str,
    first_refutation: str,
    lesson: str,
    run_id: str | None = None,
    evidence: dict | None = None,
) -> int:
    ensure_investigation_table(con)
    latest = _latest_event(con, task_id)
    if latest is None:
        raise ValueError(f"Investigation {task_id} is not open")
    if latest[1] not in {"opened", "reopened"}:
        raise ValueError(f"Investigation {task_id} is already resolved")
    cause, first_refutation, lesson = cause.strip(), first_refutation.strip(), lesson.strip()
    if not cause or not first_refutation or not lesson:
        raise ValueError("cause, first_refutation, and lesson are all required")
    cur = con.execute(
        """
        INSERT INTO investigation_events(task_id,event_type,run_id,algorithm_version,details_json)
        VALUES(?,?,?,?,?)
        """,
        (
            task_id,
            "resolved",
            run_id,
            ALGORITHM_VERSION,
            json.dumps(
                {
                    "cause": cause,
                    "first_refutation": first_refutation,
                    "lesson": lesson,
                    "evidence": evidence or {},
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        ),
    )
    return int(cur.lastrowid)


def reopen_investigation(
    con: sqlite3.Connection,
    *,
    task_id: str,
    reason: str,
    run_id: str | None = None,
) -> int:
    ensure_investigation_table(con)
    latest = _latest_event(con, task_id)
    if latest is None or latest[1] != "resolved":
        raise ValueError(f"Investigation {task_id} must be resolved before it can be reopened")
    reason = reason.strip()
    if not reason:
        raise ValueError("Reopen reason is required")
    cur = con.execute(
        """
        INSERT INTO investigation_events(task_id,event_type,run_id,algorithm_version,details_json)
        VALUES(?,?,?,?,?)
        """,
        (task_id, "reopened", run_id, ALGORITHM_VERSION, json.dumps({"reason": reason}, ensure_ascii=False, sort_keys=True)),
    )
    return int(cur.lastrowid)


def main() -> None:
    p = argparse.ArgumentParser(description="Manage mandatory better-than-DDS investigations")
    p.add_argument("--work", required=True)
    sp = p.add_subparsers(dest="command", required=True)

    q = sp.add_parser("sync")
    q.add_argument("--run-id")

    sp.add_parser("list-open")

    q = sp.add_parser("resolve")
    q.add_argument("--task-id", required=True)
    q.add_argument("--cause", required=True)
    q.add_argument("--first-refutation", required=True)
    q.add_argument("--lesson", required=True)
    q.add_argument("--run-id")

    q = sp.add_parser("reopen")
    q.add_argument("--task-id", required=True)
    q.add_argument("--reason", required=True)
    q.add_argument("--run-id")

    args = p.parse_args()
    con = connect(Path(args.work) / "training.sqlite3")
    if args.command == "sync":
        result = sync_required_investigations(con, args.run_id)
    elif args.command == "list-open":
        result = {"open": open_investigations(con)}
    elif args.command == "resolve":
        event_id = resolve_investigation(
            con,
            task_id=args.task_id,
            cause=args.cause,
            first_refutation=args.first_refutation,
            lesson=args.lesson,
            run_id=args.run_id,
        )
        result = {"resolved_event_id": event_id, "open_total": len(open_investigations(con))}
    else:
        event_id = reopen_investigation(con, task_id=args.task_id, reason=args.reason, run_id=args.run_id)
        result = {"reopened_event_id": event_id, "open_total": len(open_investigations(con))}
    con.commit()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
