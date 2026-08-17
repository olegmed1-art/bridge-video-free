from __future__ import annotations

import json
import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS skill_profile_versions (
  skill_key TEXT NOT NULL,
  algorithm_version TEXT NOT NULL,
  side TEXT NOT NULL,
  family TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'candidate',
  trigger_text TEXT,
  rule_text TEXT,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  transfer_count INTEGER NOT NULL DEFAULT 0,
  reinforcement_count INTEGER NOT NULL DEFAULT 0,
  regression_passes INTEGER NOT NULL DEFAULT 0,
  regression_failures INTEGER NOT NULL DEFAULT 0,
  counterexample_count INTEGER NOT NULL DEFAULT 0,
  metrics_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(skill_key, algorithm_version)
);
CREATE TABLE IF NOT EXISTS skill_profile_version_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  skill_key TEXT NOT NULL,
  algorithm_version TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  metrics_json TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS skill_profile_version_history_no_update
BEFORE UPDATE ON skill_profile_version_history BEGIN SELECT RAISE(ABORT, 'skill_profile_version_history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS skill_profile_version_history_no_delete
BEFORE DELETE ON skill_profile_version_history BEGIN SELECT RAISE(ABORT, 'skill_profile_version_history is append-only'); END;
"""


def ensure_version_tables(con: sqlite3.Connection) -> None:
    con.executescript(DDL)


def ensure_skill_version(
    con: sqlite3.Connection,
    *,
    skill_key: str,
    algorithm_version: str,
    side: str,
    family: str,
    title: str,
    trigger_text: str | None,
    rule_text: str | None = None,
) -> None:
    ensure_version_tables(con)
    con.execute(
        """
        INSERT OR IGNORE INTO skill_profile_versions
          (skill_key,algorithm_version,side,family,title,status,trigger_text,rule_text)
        VALUES(?,?,?,?,?,'candidate',?,?)
        """,
        (skill_key, algorithm_version, side, family, title, trigger_text, rule_text),
    )


def current_skill_version(con: sqlite3.Connection, skill_key: str, algorithm_version: str) -> dict | None:
    ensure_version_tables(con)
    row = con.execute(
        """
        SELECT side,family,title,status,trigger_text,rule_text,evidence_count,
               transfer_count,reinforcement_count,regression_passes,
               regression_failures,counterexample_count,metrics_json
        FROM skill_profile_versions WHERE skill_key=? AND algorithm_version=?
        """,
        (skill_key, algorithm_version),
    ).fetchone()
    if row is None:
        return None
    return {
        "skill_key": skill_key,
        "algorithm_version": algorithm_version,
        "side": row[0],
        "family": row[1],
        "title": row[2],
        "status": row[3],
        "trigger_text": row[4],
        "rule_text": row[5],
        "evidence_count": int(row[6]),
        "transfer_count": int(row[7]),
        "reinforcement_count": int(row[8]),
        "regression_passes": int(row[9]),
        "regression_failures": int(row[10]),
        "counterexample_count": int(row[11]),
        "metrics": json.loads(row[12] or "{}"),
    }


def update_skill_version(
    con: sqlite3.Connection,
    *,
    skill_key: str,
    algorithm_version: str,
    status: str,
    evidence_count: int,
    transfer_count: int,
    reinforcement_count: int,
    regression_passes: int,
    regression_failures: int,
    counterexample_count: int,
    metrics: dict,
) -> None:
    ensure_version_tables(con)
    old = current_skill_version(con, skill_key, algorithm_version)
    if old is None:
        raise ValueError(f"Skill version does not exist: {skill_key}/{algorithm_version}")
    metrics_json = json.dumps(metrics, ensure_ascii=False, sort_keys=True)
    con.execute(
        """
        UPDATE skill_profile_versions
        SET status=?,evidence_count=?,transfer_count=?,reinforcement_count=?,
            regression_passes=?,regression_failures=?,counterexample_count=?,
            metrics_json=?,updated_at=CURRENT_TIMESTAMP
        WHERE skill_key=? AND algorithm_version=?
        """,
        (
            status,
            int(evidence_count),
            int(transfer_count),
            int(reinforcement_count),
            int(regression_passes),
            int(regression_failures),
            int(counterexample_count),
            metrics_json,
            skill_key,
            algorithm_version,
        ),
    )
    if old["status"] != status:
        con.execute(
            """
            INSERT INTO skill_profile_version_history
              (skill_key,algorithm_version,from_status,to_status,metrics_json)
            VALUES(?,?,?,?,?)
            """,
            (skill_key, algorithm_version, old["status"], status, metrics_json),
        )


def snapshot_current_legacy_profile(
    con: sqlite3.Connection,
    *,
    skill_key: str,
    algorithm_version: str,
) -> None:
    """Copy a legacy current-profile row into the version table if absent.

    This migration never changes evidence. It preserves the last visible state of
    an older analyzer revision before the compatibility `skill_profiles` row is
    reused as the current profile for a new revision.
    """
    ensure_version_tables(con)
    existing = con.execute(
        "SELECT 1 FROM skill_profile_versions WHERE skill_key=? AND algorithm_version=?",
        (skill_key, algorithm_version),
    ).fetchone()
    if existing:
        return
    row = con.execute(
        """
        SELECT side,family,title,status,trigger_text,rule_text,evidence_count,
               transfer_count,regression_passes,regression_failures,counterexample_count
        FROM skill_profiles WHERE skill_key=?
        """,
        (skill_key,),
    ).fetchone()
    if row is None:
        return
    con.execute(
        """
        INSERT INTO skill_profile_versions
          (skill_key,algorithm_version,side,family,title,status,trigger_text,rule_text,
           evidence_count,transfer_count,reinforcement_count,regression_passes,
           regression_failures,counterexample_count,metrics_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)
        """,
        (
            skill_key,
            algorithm_version,
            row[0], row[1], row[2], row[3], row[4], row[5],
            int(row[6]), int(row[7]), int(row[8]), int(row[9]), int(row[10]),
            json.dumps({"migration": "legacy_profile_snapshot"}, sort_keys=True),
        ),
    )
