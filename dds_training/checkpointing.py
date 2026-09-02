from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_database(
    con: sqlite3.Connection,
    *,
    db_path: Path,
    snapshot_dir: Path,
    run_id: str,
    completed_tasks: int,
    errors: int,
    next_task_id: str | None,
    keep_milestone_every: int = 5000,
) -> dict:
    """Create a crash-safe SQLite backup plus a small checkpoint manifest.

    `latest.sqlite3` is replaced at every requested snapshot. Milestone copies are
    retained at larger intervals, limiting storage while preserving rollback points.
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    latest = snapshot_dir / "latest.sqlite3"
    temp = snapshot_dir / "latest.sqlite3.tmp"
    if temp.exists():
        temp.unlink()
    dest = sqlite3.connect(temp)
    try:
        con.backup(dest)
    finally:
        dest.close()
    temp.replace(latest)

    digest = sha256_file(latest)
    milestone = None
    if completed_tasks and keep_milestone_every and completed_tasks % keep_milestone_every == 0:
        milestone = snapshot_dir / f"checkpoint_{completed_tasks:08d}.sqlite3"
        if not milestone.exists():
            src = sqlite3.connect(latest)
            dst = sqlite3.connect(milestone)
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()

    manifest = {
        "run_id": run_id,
        "completed_tasks": completed_tasks,
        "errors": errors,
        "next_task_id": next_task_id,
        "db_path": str(db_path),
        "latest_snapshot": str(latest),
        "latest_sha256": digest,
        "milestone_snapshot": None if milestone is None else str(milestone),
    }
    (snapshot_dir / "latest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
