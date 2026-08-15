from __future__ import annotations

import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS regression_skill_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  skill_key TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(task_id, skill_key),
  FOREIGN KEY(task_id) REFERENCES regression_cases(task_id),
  FOREIGN KEY(skill_key) REFERENCES skill_profiles(skill_key)
);
CREATE TRIGGER IF NOT EXISTS regression_skill_links_no_update
BEFORE UPDATE ON regression_skill_links BEGIN SELECT RAISE(ABORT, 'regression_skill_links are append-only'); END;
CREATE TRIGGER IF NOT EXISTS regression_skill_links_no_delete
BEFORE DELETE ON regression_skill_links BEGIN SELECT RAISE(ABORT, 'regression_skill_links are append-only'); END;
"""


def ensure_regression_skill_links(con: sqlite3.Connection) -> None:
    con.executescript(DDL)


def link_regression_skills(con: sqlite3.Connection, task_id: str, skill_keys: list[str] | tuple[str, ...]) -> int:
    """Attach one regression position to every skill implicated by the error.

    `regression_cases` stays one row per mathematical task for compatibility;
    this append-only link table prevents a multi-skill error (for example both
    trick estimation and overclaim detection) from being remembered only under
    the first skill.
    """
    ensure_regression_skill_links(con)
    if con.execute("SELECT 1 FROM regression_cases WHERE task_id=?", (task_id,)).fetchone() is None:
        raise ValueError(f"Regression case {task_id} does not exist")
    added = 0
    for skill_key in sorted(set(skill_keys)):
        if not skill_key:
            continue
        before = con.total_changes
        con.execute(
            "INSERT OR IGNORE INTO regression_skill_links(task_id,skill_key) VALUES(?,?)",
            (task_id, skill_key),
        )
        added += int(con.total_changes > before)
    return added
