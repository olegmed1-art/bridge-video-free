"""Offline JSON replay; optional disposable SQLite staging, never a DSN."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from bridge_contracts.video_canon_replay import canonical_json, replay_result_bundle


def stage_local(connection: sqlite3.Connection, result: dict) -> dict:
    """Separate disposable staging model, not production PostgreSQL validation."""
    inserted = 0
    with connection:
        connection.execute("CREATE TABLE IF NOT EXISTS replay_staging ("
                           "stable_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, "
                           "payload TEXT NOT NULL)")
        for row in result["candidates"]:
            existing = connection.execute(
                "SELECT payload_hash, payload FROM replay_staging WHERE stable_key=?",
                (row["stable_key"],),
            ).fetchone()
            values = (row["payload_hash"], canonical_json(row["payload"]))
            if existing is not None:
                if existing != values:
                    raise ValueError("staging identity collision")
                continue
            connection.execute("INSERT INTO replay_staging VALUES (?, ?, ?)",
                               (row["stable_key"], *values))
            inserted += 1
    return {"inserted": inserted, "existing": len(result["candidates"]) - inserted}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--local-staging-db", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; use a fresh receipt path")
    if args.output.resolve() == args.bundle.resolve():
        parser.error("output must not overwrite source bundle")
    if args.local_staging_db and args.local_staging_db.resolve() in {
        args.bundle.resolve(), args.output.resolve()
    }:
        parser.error("local staging database must be a separate file")
    result = replay_result_bundle(json.loads(args.bundle.read_text(encoding="utf-8-sig")))
    if args.local_staging_db:
        with closing(sqlite3.connect(str(args.local_staging_db))) as connection:
            stage_local(connection, result)
    # Exclusive create prevents overwriting a prior receipt.
    with args.output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(result) + "\n")


if __name__ == "__main__":
    main()
