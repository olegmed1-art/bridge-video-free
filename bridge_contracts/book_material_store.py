"""SQLite reference staging store, for local preflight only.

No network, production DSN, active knowledge table, or outbox publisher. A local
commit records a complete staging bundle; it is not a WORLD publication receipt.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Callable

from .book_material import canonical_json, digest, identity, validate_bundle, preview_citation


class BookStagingStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS book_object (
                object_id TEXT PRIMARY KEY, identity_hash TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS book_batch (
                run_id TEXT PRIMARY KEY, bundle_hash TEXT NOT NULL, bundle_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('STAGED','COMMITTED_LOCAL','RETIRED')));
            CREATE TABLE IF NOT EXISTS book_observation (
                run_id TEXT NOT NULL REFERENCES book_batch(run_id),
                object_id TEXT NOT NULL REFERENCES book_object(object_id),
                payload_hash TEXT NOT NULL, PRIMARY KEY(run_id, object_id));
            CREATE TABLE IF NOT EXISTS book_outbox (
                run_id TEXT PRIMARY KEY REFERENCES book_batch(run_id),
                bundle_hash TEXT NOT NULL, destination TEXT NOT NULL CHECK(destination='LOCAL_ONLY'));
        """)

    def stage(self, bundle: dict, *, before_commit: Callable[[], None] | None = None) -> dict:
        # Snapshot before validation so caller mutation cannot alter persisted data.
        snapshot = canonical_json(bundle)
        bundle = json.loads(snapshot)
        validate_bundle(bundle)
        batch_hash, run_id = digest(bundle), bundle["run"]["run_id"]
        db = self.connection
        if db.in_transaction:
            raise ValueError("STAGING_REQUIRES_OWN_TRANSACTION")
        db.execute("BEGIN IMMEDIATE")
        try:
            prior = db.execute("SELECT bundle_hash, status FROM book_batch WHERE run_id=?", (run_id,)).fetchone()
            if prior:
                if prior != (batch_hash, "COMMITTED_LOCAL"):
                    raise ValueError("RUN_REPLAY_CONFLICT_OR_RETIRED")
                db.rollback()
                return {"new_ids": 0, "new_observations": 0, "replay": True, "publication": "NOT_PUBLISHED"}
            db.execute("INSERT INTO book_batch VALUES (?, ?, ?, 'STAGED')", (run_id, batch_hash, snapshot))
            new_ids = 0
            for obj in bundle["objects"]:
                key = identity(bundle["source"], obj)
                prior = db.execute("SELECT object_id FROM book_object WHERE identity_hash=?", (key,)).fetchone()
                if prior and prior[0] != obj["object_id"]:
                    raise ValueError("LEGACY_MAPPING_REQUIRED")
                known = db.execute("SELECT identity_hash FROM book_object WHERE object_id=?", (obj["object_id"],)).fetchone()
                if known and known[0] != key:
                    raise ValueError("OBJECT_ID_COLLISION")
                if not known:
                    db.execute("INSERT INTO book_object VALUES (?, ?)", (obj["object_id"], key))
                    new_ids += 1
                db.execute("INSERT INTO book_observation VALUES (?, ?, ?)", (run_id, obj["object_id"], digest(obj)))
            db.execute("INSERT INTO book_outbox VALUES (?, ?, 'LOCAL_ONLY')", (run_id, batch_hash))
            if before_commit:
                before_commit()
            db.execute("UPDATE book_batch SET status='COMMITTED_LOCAL' WHERE run_id=?", (run_id,))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return {"new_ids": new_ids, "new_observations": len(bundle["objects"]),
                "replay": False, "publication": "NOT_PUBLISHED"}

    def retire(self, run_id: str) -> None:
        if self.connection.in_transaction:
            raise ValueError("STAGING_REQUIRES_OWN_TRANSACTION")
        with self.connection:
            self.connection.execute("UPDATE book_batch SET status='RETIRED' WHERE run_id=?", (run_id,))

    def preview(self, run_id: str, object_id: str) -> dict:
        row = self.connection.execute(
            "SELECT bundle_json, bundle_hash FROM book_batch WHERE run_id=? AND status='COMMITTED_LOCAL'", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError("BATCH_NOT_AVAILABLE")
        bundle = json.loads(row[0])
        if digest(bundle) != row[1]:
            raise ValueError("STORED_BUNDLE_HASH_MISMATCH")
        return preview_citation(bundle, object_id)
