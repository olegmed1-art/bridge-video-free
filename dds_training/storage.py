from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  stage TEXT NOT NULL,
  seed INTEGER NOT NULL,
  corpus_sha256 TEXT NOT NULL,
  solver_info_json TEXT,
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
CREATE TABLE IF NOT EXISTS checkpoints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  completed_tasks INTEGER NOT NULL,
  errors INTEGER NOT NULL,
  next_task_id TEXT,
  note TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def upsert_prediction(con: sqlite3.Connection, task: dict, prediction: dict) -> None:
    if not prediction.get("locked", False):
        raise ValueError(f"Prediction {task['task_id']} must be locked before DDS evaluation")
    con.execute(
        "INSERT OR REPLACE INTO predictions(task_id,deal_id,task_type,split,prediction_json,locked) VALUES(?,?,?,?,?,1)",
        (task["task_id"], task["deal_id"], task["task_type"], task["split"], json.dumps(prediction, ensure_ascii=False)),
    )


def upsert_result(con: sqlite3.Connection, task: dict, result: dict) -> None:
    con.execute(
        "INSERT OR REPLACE INTO dds_results(task_id,deal_id,task_type,split,result_json,dd_regret,investigation_required) VALUES(?,?,?,?,?,?,?)",
        (
            task["task_id"], task["deal_id"], task["task_type"], task["split"],
            json.dumps(result, ensure_ascii=False), result.get("dd_regret"), int(bool(result.get("investigation_required"))),
        ),
    )
