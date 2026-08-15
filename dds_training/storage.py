from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from config import ALGORITHM_VERSION

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  stage TEXT NOT NULL,
  seed INTEGER NOT NULL,
  corpus_sha256 TEXT NOT NULL,
  solver_info_json TEXT,
  algorithm_version TEXT,
  status TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT
);
CREATE TABLE IF NOT EXISTS predictions (
  task_id TEXT PRIMARY KEY,
  deal_id TEXT NOT NULL,
  task_type TEXT NOT NULL,
  split TEXT NOT NULL,
  prediction_json TEXT NOT NULL,
  locked INTEGER NOT NULL DEFAULT 1,
  locked_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dds_results (
  task_id TEXT PRIMARY KEY,
  deal_id TEXT NOT NULL,
  task_type TEXT NOT NULL,
  split TEXT NOT NULL,
  result_json TEXT NOT NULL,
  dd_regret REAL,
  investigation_required INTEGER NOT NULL DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS error_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  error_code TEXT NOT NULL,
  magnitude REAL,
  details_json TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Legacy summary table retained for compatibility with old databases.
CREATE TABLE IF NOT EXISTS skills (
  skill_key TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  trigger_text TEXT,
  rule_text TEXT,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  validation_count INTEGER NOT NULL DEFAULT 0,
  regression_failures INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS skill_profiles (
  skill_key TEXT PRIMARY KEY,
  side TEXT NOT NULL,
  family TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'candidate',
  trigger_text TEXT,
  rule_text TEXT,
  algorithm_version TEXT NOT NULL,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  transfer_count INTEGER NOT NULL DEFAULT 0,
  regression_passes INTEGER NOT NULL DEFAULT 0,
  regression_failures INTEGER NOT NULL DEFAULT 0,
  counterexample_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS skill_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  skill_key TEXT NOT NULL,
  task_id TEXT NOT NULL,
  deal_id TEXT NOT NULL,
  split TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  outcome TEXT NOT NULL,
  regret REAL,
  confidence TEXT NOT NULL DEFAULT 'unknown',
  run_id TEXT,
  algorithm_version TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(skill_key, task_id, evidence_type, algorithm_version),
  FOREIGN KEY(skill_key) REFERENCES skill_profiles(skill_key)
);
CREATE TABLE IF NOT EXISTS skill_state_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  skill_key TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  reason_json TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS rule_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_key TEXT NOT NULL,
  skill_key TEXT,
  version INTEGER NOT NULL,
  rule_text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'candidate',
  evidence_json TEXT,
  algorithm_version TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  retired_at TEXT,
  UNIQUE(rule_key, version)
);
CREATE TABLE IF NOT EXISTS counterexamples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  skill_key TEXT NOT NULL,
  deal_id TEXT NOT NULL,
  task_id TEXT,
  description TEXT,
  result_json TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(skill_key, deal_id, task_id)
);
CREATE TABLE IF NOT EXISTS regression_cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  skill_key TEXT,
  deal_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  source_error_code TEXT,
  expected_json TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(task_id)
);
CREATE TABLE IF NOT EXISTS experience_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  task_id TEXT,
  deal_id TEXT,
  run_id TEXT,
  algorithm_version TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS learning_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  skill_key TEXT,
  purpose TEXT NOT NULL,
  priority REAL NOT NULL,
  requested_tasks INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'planned',
  source_run_id TEXT,
  details_json TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS correction_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target_table TEXT NOT NULL,
  target_key TEXT NOT NULL,
  correction_type TEXT NOT NULL,
  reason TEXT NOT NULL,
  replacement_json TEXT,
  supersedes_correction_id INTEGER,
  algorithm_version TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  status TEXT NOT NULL,
  details_json TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS checkpoints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  completed_tasks INTEGER NOT NULL,
  errors INTEGER NOT NULL,
  next_task_id TEXT,
  note TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS predictions_no_update
BEFORE UPDATE ON predictions BEGIN SELECT RAISE(ABORT, 'predictions are immutable; use correction_events'); END;
CREATE TRIGGER IF NOT EXISTS predictions_no_delete
BEFORE DELETE ON predictions BEGIN SELECT RAISE(ABORT, 'predictions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS dds_results_no_update
BEFORE UPDATE ON dds_results BEGIN SELECT RAISE(ABORT, 'dds_results are immutable; use correction_events'); END;
CREATE TRIGGER IF NOT EXISTS dds_results_no_delete
BEFORE DELETE ON dds_results BEGIN SELECT RAISE(ABORT, 'dds_results are immutable'); END;
CREATE TRIGGER IF NOT EXISTS error_events_no_update
BEFORE UPDATE ON error_events BEGIN SELECT RAISE(ABORT, 'error_events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS error_events_no_delete
BEFORE DELETE ON error_events BEGIN SELECT RAISE(ABORT, 'error_events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS skill_evidence_no_update
BEFORE UPDATE ON skill_evidence BEGIN SELECT RAISE(ABORT, 'skill_evidence is append-only; add new evidence or correction'); END;
CREATE TRIGGER IF NOT EXISTS skill_evidence_no_delete
BEFORE DELETE ON skill_evidence BEGIN SELECT RAISE(ABORT, 'skill_evidence is append-only'); END;
"""


def _canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    # Safe migration for databases created by v1 before algorithm_version existed.
    run_cols = {r[1] for r in con.execute("PRAGMA table_info(runs)")}
    if "algorithm_version" not in run_cols:
        con.execute("ALTER TABLE runs ADD COLUMN algorithm_version TEXT")
    con.commit()
    return con


def _insert_immutable_json(
    con: sqlite3.Connection,
    table: str,
    key: str,
    row_values: tuple,
    insert_sql: str,
    json_column: str,
    payload: dict,
) -> None:
    existing = con.execute(f"SELECT {json_column} FROM {table} WHERE task_id=?", (key,)).fetchone()
    if existing is not None:
        if _canonical(json.loads(existing[0])) != _canonical(payload):
            raise ValueError(f"Immutable {table} fact for {key} already exists with different content")
        return
    con.execute(insert_sql, row_values)


def upsert_prediction(con: sqlite3.Connection, task: dict, prediction: dict) -> None:
    """Compatibility name; semantics are immutable insert-or-verify, never replace."""
    if not prediction.get("locked", False):
        raise ValueError(f"Prediction {task['task_id']} must be locked before DDS evaluation")
    payload = dict(prediction)
    _insert_immutable_json(
        con,
        "predictions",
        task["task_id"],
        (
            task["task_id"], task["deal_id"], task["task_type"], task["split"],
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ),
        "INSERT INTO predictions(task_id,deal_id,task_type,split,prediction_json,locked) VALUES(?,?,?,?,?,1)",
        "prediction_json",
        payload,
    )


def upsert_result(con: sqlite3.Connection, task: dict, result: dict) -> None:
    """Compatibility name; semantics are immutable insert-or-verify, never replace."""
    payload = dict(result)
    _insert_immutable_json(
        con,
        "dds_results",
        task["task_id"],
        (
            task["task_id"], task["deal_id"], task["task_type"], task["split"],
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            result.get("dd_regret"), int(bool(result.get("investigation_required"))),
        ),
        "INSERT INTO dds_results(task_id,deal_id,task_type,split,result_json,dd_regret,investigation_required) VALUES(?,?,?,?,?,?,?)",
        "result_json",
        payload,
    )


def record_correction(
    con: sqlite3.Connection,
    *,
    target_table: str,
    target_key: str,
    correction_type: str,
    reason: str,
    replacement: dict | None = None,
    supersedes_correction_id: int | None = None,
) -> int:
    reason = reason.strip()
    if not reason:
        raise ValueError("Correction reason is required")
    cur = con.execute(
        """
        INSERT INTO correction_events
          (target_table,target_key,correction_type,reason,replacement_json,supersedes_correction_id,algorithm_version)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            target_table,
            target_key,
            correction_type,
            reason,
            None if replacement is None else json.dumps(replacement, ensure_ascii=False, sort_keys=True),
            supersedes_correction_id,
            ALGORITHM_VERSION,
        ),
    )
    return int(cur.lastrowid)


def add_rule_version(
    con: sqlite3.Connection,
    *,
    rule_key: str,
    skill_key: str | None,
    rule_text: str,
    evidence: dict,
    status: str = "candidate",
) -> int:
    prev = con.execute("SELECT COALESCE(MAX(version),0) FROM rule_versions WHERE rule_key=?", (rule_key,)).fetchone()[0]
    version = int(prev) + 1
    con.execute(
        """
        INSERT INTO rule_versions(rule_key,skill_key,version,rule_text,status,evidence_json,algorithm_version)
        VALUES(?,?,?,?,?,?,?)
        """,
        (rule_key, skill_key, version, rule_text, status, json.dumps(evidence, ensure_ascii=False, sort_keys=True), ALGORITHM_VERSION),
    )
    return version


def add_regression_case(con: sqlite3.Connection, task: dict, result: dict, skill_key: str | None = None) -> None:
    con.execute(
        """
        INSERT OR IGNORE INTO regression_cases(skill_key,deal_id,task_id,source_error_code,expected_json)
        VALUES(?,?,?,?,?)
        """,
        (
            skill_key,
            task["deal_id"],
            task["task_id"],
            result.get("error_code"),
            json.dumps(result, ensure_ascii=False, sort_keys=True),
        ),
    )
