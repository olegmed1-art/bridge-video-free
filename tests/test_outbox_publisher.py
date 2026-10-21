"""Offline publisher boundary tests; no driver, credentials or service required.

SQLite executes selection/JSON logic after removing PostgreSQL's FOR UPDATE.
Psycopg plumbing, DSN validation and the DB publication function are doubles.
PostgreSQL row locking, snapshots and concurrent rebinds are not tested here.
This verifies the Python publisher, not deployed PostgreSQL or other consumers.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import sys
from types import ModuleType
import unittest
from unittest.mock import patch
import uuid
from contextlib import contextmanager
from types import SimpleNamespace


CHANGESET = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_CHANGESET = uuid.UUID("00000000-0000-0000-0000-000000000002")
SOURCE = Path(__file__).resolve().parents[1] / "database" / "outbox_publisher.py"
BLOCKED = {"publication_authorization_status": "blocked"}


class OfflineConnection:
    """Local ledger with an observable publication boundary and transaction exit."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("ATTACH DATABASE ':memory:' AS public")
        self.db.executescript("""
            CREATE TABLE public.domain_event (
                event_id TEXT PRIMARY KEY, payload TEXT, event_position INTEGER,
                event_type TEXT, school_id TEXT, changeset_id TEXT
            );
            CREATE TABLE public.outbox_message (
                outbox_id TEXT PRIMARY KEY, event_id TEXT, changeset_id TEXT,
                created_at INTEGER, status TEXT, attempt_count INTEGER
            );
        """)
        self.db.create_function("publish_outbox_event", 1, self.publish)
        self.publication_calls = []
        self.commit_calls = 0
        self.rolled_back = False
        self.selection_error = False
        self.missing_position = False
        self.autocommit = False
        self.info = SimpleNamespace(transaction_status=0)

    def seed(self, index, payload, *, changeset=CHANGESET, position=None):
        key = str(uuid.UUID(int=100 + index))
        self.db.execute(
            "INSERT INTO public.domain_event VALUES (?, ?, ?, ?, ?, ?)",
            (key, json.dumps(payload), position, "VideoTechnicalRecorded",
             str(uuid.UUID(int=9)), str(changeset)),
        )
        self.db.execute(
            "INSERT INTO public.outbox_message VALUES (?, ?, ?, ?, ?, ?)",
            (key, key, str(changeset), index,
             "published" if position is not None else "pending", 0),
        )
        self.db.commit()
        return key

    def publish(self, outbox_id):
        # This callback is the test's publication sink, not a PostgreSQL emulator.
        self.publication_calls.append(outbox_id)
        if self.missing_position:
            return None
        event_id, position = self.db.execute(
            "SELECT e.event_id, e.event_position FROM public.domain_event e "
            "JOIN public.outbox_message o ON o.event_id=e.event_id "
            "WHERE o.outbox_id=?", (outbox_id,),
        ).fetchone()
        if position is None:
            position = self.db.execute(
                "SELECT COALESCE(MAX(event_position), 0)+1 FROM public.domain_event"
            ).fetchone()[0]
            self.db.execute(
                "UPDATE public.domain_event SET event_position=? WHERE event_id=?",
                (position, event_id),
            )
        self.db.execute(
            "UPDATE public.outbox_message SET status='published' WHERE outbox_id=?",
            (outbox_id,),
        )
        return position

    def snapshot(self):
        return (
            self.db.execute("SELECT * FROM public.outbox_message ORDER BY outbox_id").fetchall(),
            self.db.execute("SELECT * FROM public.domain_event ORDER BY event_id").fetchall(),
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.db.rollback()
            self.rolled_back = True
        else:
            self.db.commit()

    def cursor(self):
        return OfflineCursor(self)

    @contextmanager
    def transaction(self):
        self.info.transaction_status = 2
        try:
            yield
        finally:
            self.info.transaction_status = 0

    def commit(self):
        self.commit_calls += 1
        self.db.commit()


class OfflineCursor:
    def __init__(self, connection):
        self.connection = connection
        self.cursor = connection.db.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cursor.close()

    def execute(self, sql, params):
        if self.connection.selection_error and "FROM public.outbox_message" in sql:
            raise RuntimeError("OFFLINE_READ_FAILED")
        # SQLite has no FOR UPDATE. This adapter does not simulate PostgreSQL locks.
        sql = sql.replace("FOR UPDATE", "").replace("%s", "?").replace(
            "public.publish_outbox_event(", "publish_outbox_event("
        )
        self.cursor.execute(sql, tuple(str(p) if isinstance(p, uuid.UUID) else p for p in params))

    def fetchall(self):
        rows = self.cursor.fetchall()
        return [(*row[:-1], json.loads(row[-1])) if len(row) == 7 and row[-1] is not None
                else row for row in rows]

    def fetchone(self):
        return self.cursor.fetchone()


class OutboxPublisherTests(unittest.TestCase):
    def setUp(self):
        self.connection = OfflineConnection()
        self.addCleanup(self.connection.db.close)
        driver = ModuleType("psycopg")
        driver.connect = lambda *args, **kwargs: self.connection
        preflight = ModuleType("database.runtime_worker_preflight")
        preflight.normalize_dsn = lambda raw: raw
        spec = importlib.util.spec_from_file_location("_outbox_publisher_test", SOURCE)
        self.publisher = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            "psycopg": driver, "database.runtime_worker_preflight": preflight,
        }):
            spec.loader.exec_module(self.publisher)
        # A real outbound network attempt fails the test; the DB boundary is local.
        self.addCleanup(patch.stopall)
        patch.object(socket.socket, "connect", side_effect=AssertionError("NETWORK_FORBIDDEN")).start()
        patch.object(socket.socket, "connect_ex", side_effect=AssertionError("NETWORK_FORBIDDEN")).start()
        patch.object(socket, "create_connection", side_effect=AssertionError("NETWORK_FORBIDDEN")).start()

    def publish(self):
        return self.publisher.publish_changeset_outbox("offline-test-only", CHANGESET)

    def assert_blocked_without_publication(self):
        before = self.connection.snapshot()
        with self.assertRaisesRegex(RuntimeError, "^OUTBOX_PUBLICATION_AUTHORIZATION_BLOCKED$"):
            self.publish()
        self.assertEqual(self.connection.publication_calls, [])
        self.assertEqual(self.connection.commit_calls, 0)
        self.assertTrue(self.connection.rolled_back)
        self.assertEqual(self.connection.snapshot(), before)

    def assert_unknown_without_publication(self):
        before = self.connection.snapshot()
        with self.assertRaisesRegex(RuntimeError, "^OUTBOX_PUBLICATION_AUTHORIZATION_UNKNOWN$"):
            self.publish()
        self.assertEqual(self.connection.publication_calls, [])
        self.assertEqual(self.connection.commit_calls, 0)
        self.assertTrue(self.connection.rolled_back)
        self.assertEqual(self.connection.snapshot(), before)

    def test_pending_blocked_technical_event_never_reaches_publication(self):
        self.connection.seed(1, {
            **BLOCKED, "quality_confirmation_status": "pending",
            "technical_record_status": "recorded", "private_text": "DO_NOT_ECHO",
        })
        self.assert_blocked_without_publication()

    def test_late_blocked_event_rejects_whole_batch_before_first_publication(self):
        self.connection.seed(1, {"legacy": True})
        self.connection.seed(2, {"publication_authorization_status": "allowed"})
        self.connection.seed(3, BLOCKED)
        self.assert_blocked_without_publication()

    def test_blocked_retry_does_not_return_preexisting_position(self):
        self.connection.seed(1, BLOCKED, position=17)
        self.assert_blocked_without_publication()

    def test_repeated_blocked_attempts_do_not_publish(self):
        self.connection.seed(1, BLOCKED)
        self.assert_blocked_without_publication()
        self.assert_blocked_without_publication()

    def test_unavailable_event_cannot_be_treated_as_legacy_missing_status(self):
        key = self.connection.seed(1, BLOCKED)
        self.connection.db.execute("DELETE FROM public.domain_event WHERE event_id=?", (key,))
        self.connection.db.commit()
        before = self.connection.snapshot()
        with self.assertRaisesRegex(RuntimeError, "^OUTBOX_EVENT_UNAVAILABLE$"):
            self.publish()
        self.assertEqual(self.connection.publication_calls, [])
        self.assertEqual(self.connection.commit_calls, 0)
        self.assertTrue(self.connection.rolled_back)
        self.assertEqual(self.connection.snapshot(), before)

    def test_late_unavailable_event_prevents_earlier_publication(self):
        self.connection.seed(1, {})
        key = self.connection.seed(2, BLOCKED)
        self.connection.db.execute("DELETE FROM public.domain_event WHERE event_id=?", (key,))
        self.connection.db.commit()
        with self.assertRaisesRegex(RuntimeError, "^OUTBOX_EVENT_UNAVAILABLE$"):
            self.publish()
        self.assertEqual(self.connection.publication_calls, [])
        self.assertEqual(self.connection.commit_calls, 0)

    def test_explicit_allowed_event_keeps_success_contract(self):
        key = self.connection.seed(1, {"publication_authorization_status": "allowed"})
        self.assertEqual(self.publish(), {
            "changeset_id": str(CHANGESET), "published_count": 1, "event_positions": [1],
        })
        self.assertEqual(self.connection.publication_calls, [key])
        self.assertEqual(self.connection.commit_calls, 1)

    def test_allowed_status_remains_publishable_and_idempotent(self):
        key = self.connection.seed(1, {"publication_authorization_status": "allowed"})
        first = self.publish()
        second = self.publish()
        self.assertEqual(first, second)
        self.assertEqual(first["published_count"], 1)
        self.assertEqual(self.connection.publication_calls, [key, key])

    def test_empty_changeset_preserves_empty_success(self):
        self.assertEqual(self.publish(), {
            "changeset_id": str(CHANGESET), "published_count": 0, "event_positions": [],
        })

    def test_blocked_event_from_other_changeset_does_not_leak_into_selection(self):
        self.connection.seed(1, BLOCKED, changeset=OTHER_CHANGESET)
        key = self.connection.seed(2, {"publication_authorization_status": "allowed"})
        self.assertEqual(self.publish()["published_count"], 1)
        self.assertEqual(self.connection.publication_calls, [key])

    def test_mixed_allowed_and_unknown_batch_rejects_before_first_publication(self):
        self.connection.seed(1, {"publication_authorization_status": "allowed"})
        self.connection.seed(2, {"publication_authorization_status": "unknown"})
        self.assert_unknown_without_publication()
        self.assert_unknown_without_publication()  # retry cannot publish either row

    def test_allowed_summary_cannot_override_unknown_event_in_changeset(self):
        self.connection.seed(1, {"publication_authorization_status": "allowed"})
        self.connection.seed(2, {"publication_authorization_status": "unknown"})
        decision_source = SOURCE.with_name("outbox_publication_decision.py")
        spec = importlib.util.spec_from_file_location("_outbox_decision_test", decision_source)
        decision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(decision)
        before = self.connection.snapshot()
        with self.assertRaisesRegex(RuntimeError, "^OUTBOX_PUBLICATION_AUTHORIZATION_UNKNOWN$"):
            decision.publish_if_authorized(
                "offline-test-only",
                {"changeset_id": str(CHANGESET), "publication_authorization_status": "allowed"},
                self.publisher.publish_changeset_outbox,
            )
        self.assertEqual(self.connection.publication_calls, [])
        self.assertEqual(self.connection.snapshot(), before)

    def test_missing_null_and_other_values_are_not_authorization(self):
        payloads = [{"assessment_status": "quarantined"}] + [
            {"publication_authorization_status": status}
            for status in (None, "revoked", "unknown", "", "BLOCKED")
        ]
        for index, payload in enumerate(payloads, 1):
            with self.subTest(payload=payload):
                changeset = uuid.UUID(int=CHANGESET.int + 10 + index)
                self.connection.seed(index, payload, changeset=changeset)
                before = self.connection.snapshot()
                with self.assertRaisesRegex(RuntimeError, "^OUTBOX_PUBLICATION_AUTHORIZATION_UNKNOWN$"):
                    self.publisher.publish_changeset_outbox("offline-test-only", changeset)
                self.assertEqual(self.connection.publication_calls, [])
                self.assertEqual(self.connection.snapshot(), before)

    def test_selection_error_cannot_become_success_or_publication(self):
        self.connection.seed(1, {})
        self.connection.selection_error = True
        with self.assertRaisesRegex(RuntimeError, "^OFFLINE_READ_FAILED$"):
            self.publish()
        self.assertEqual(self.connection.publication_calls, [])
        self.assertEqual(self.connection.commit_calls, 0)
        self.assertTrue(self.connection.rolled_back)

    def test_missing_position_still_raises_existing_error(self):
        self.connection.seed(1, {"publication_authorization_status": "allowed"})
        self.connection.missing_position = True
        with self.assertRaisesRegex(RuntimeError, "^OUTBOX_PUBLISH_POSITION_MISSING$"):
            self.publish()
        self.assertEqual(self.connection.commit_calls, 0)
        self.assertTrue(self.connection.rolled_back)


if __name__ == "__main__":
    unittest.main()
