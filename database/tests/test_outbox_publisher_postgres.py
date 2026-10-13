"""Real PostgreSQL publisher tests restricted to the existing disposable CI DB.

Only DSN routing is replaced for the production wrapper. Connections, cursors,
SQL, roles, transactions, publication function and lock waits are real. A cursor
hook pauses at a publication call to coordinate an observed concurrent writer;
it never changes SQL or fabricates a database result.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import re
import threading
import time
import unittest
from unittest.mock import patch
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from database import outbox_publisher as publisher


CI_DSN = os.environ.get("DATABASE_URL", "")
REAL_CONNECT = psycopg.connect
BLOCKED = {"publication_authorization_status": "blocked"}


def evidence(scenario, **facts):
    print("OUTBOX_GUARD_POSTGRES " + json.dumps({
        "scenario": scenario, "source_sha": os.environ["OUTBOX_GUARD_SOURCE_SHA"],
        **facts,
    }, sort_keys=True), flush=True)


class OutboxPublisherPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # No environment discovery, production URL fallback, host overrides or skip.
        if (os.environ.get("GITHUB_ACTIONS") != "true"
                or not CI_DSN
                or not re.fullmatch(r"[0-9a-f]{40}", os.environ.get("OUTBOX_GUARD_SOURCE_SHA", ""))):
            raise RuntimeError("EXACT_EPHEMERAL_CI_DATABASE_AND_SOURCE_REQUIRED")
        try:
            route = conninfo_to_dict(CI_DSN)
        except psycopg.ProgrammingError:
            raise RuntimeError("EXACT_EPHEMERAL_CI_ROUTE_REQUIRED") from None
        # Use the harness credential without copying or logging it in this test.
        # Reject extra libpq route/options fields as well as any other endpoint.
        route.pop("password", None)
        if route != {"host": "localhost", "port": "5432",
                     "dbname": "bridge_school_ci", "user": "bridge_ci_owner"}:
            raise RuntimeError("EXACT_EPHEMERAL_CI_ROUTE_REQUIRED")
        with REAL_CONNECT(CI_DSN, connect_timeout=3) as conn:
            identity = conn.execute("SELECT current_database(),session_user,inet_server_port(),"
                                    "current_setting('server_version_num')::int").fetchone()
            assert identity[:3] == ("bridge_school_ci", "bridge_ci_owner", 5432), identity[:3]
            assert 180000 <= identity[3] < 190000, identity[3]
            assert not conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[0]
            assert conn.execute("SELECT has_table_privilege('bridge_school_worker',"
                                "'public.outbox_message','UPDATE'),"
                                "has_table_privilege('bridge_school_worker',"
                                "'public.domain_event','UPDATE')").fetchone() == (True, False)
            evidence("ephemeral_identity", postgres_version_num=identity[3],
                     owner_is_superuser=False, worker_outbox_update=True,
                     worker_event_update=False)

    def connect(self, *, worker=False, autocommit=False, cursor_factory=psycopg.Cursor):
        conn = REAL_CONNECT(
            CI_DSN, connect_timeout=3, autocommit=autocommit,
            options="-c statement_timeout=10000 -c lock_timeout=8000",
            cursor_factory=cursor_factory,
        )
        if worker:
            conn.execute("SET ROLE bridge_school_worker")
            conn.commit()
            assert conn.execute("SELECT current_user").fetchone()[0] == "bridge_school_worker"
            conn.commit()
        return conn

    def setUp(self):
        self.school = uuid.uuid4()
        self.partition = "ci-outbox-guard-" + self.school.hex
        self.publication_calls = []
        self.pause_publication = False
        self.at_publication = threading.Event()
        self.release_publication = threading.Event()
        self.publisher_open = threading.Event()
        self.publisher_pid = None
        self.writer_open = threading.Event()
        self.writer_pid = None
        with self.connect() as conn:
            conn.execute("INSERT INTO public.school(school_id,stable_name) VALUES (%s,%s)",
                         (self.school, self.partition))
        self.addCleanup(self.cleanup_fixture)

    def cleanup_fixture(self):
        self.release_publication.set()
        with self.connect() as conn:
            conn.execute("DELETE FROM public.outbox_message WHERE changeset_id IN "
                         "(SELECT changeset_id FROM public.changeset WHERE school_id=%s)", (self.school,))
            conn.execute("DELETE FROM public.domain_event WHERE school_id=%s", (self.school,))
            conn.execute("DELETE FROM public.event_position_allocator WHERE partition_key=%s", (self.partition,))
            conn.execute("DELETE FROM public.changeset WHERE school_id=%s", (self.school,))
            conn.execute("DELETE FROM public.school WHERE school_id=%s", (self.school,))

    def event(self, conn, changeset, payload):
        event_id = uuid.uuid4()
        conn.execute("""
            INSERT INTO public.domain_event(
                event_id,school_id,partition_key,event_type,aggregate_id,aggregate_type,
                aggregate_version,changeset_id,correlation_id,idempotency_namespace,
                idempotency_key,payload_hash,payload)
            VALUES (%s,%s,%s,'BridgeVideoResultRecorded',%s,'ci_synthetic',1,%s,%s,
                    'ci-outbox-guard',%s,%s,%s)
        """, (event_id, self.school, self.partition, uuid.uuid4(), changeset, changeset,
              str(event_id), hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
              Jsonb(payload)))
        return event_id

    def seed(self, payloads):
        with self.connect() as conn:
            changeset = conn.execute("INSERT INTO public.changeset(school_id,status,committed_at) "
                                     "VALUES (%s,'committed',now()) RETURNING changeset_id",
                                     (self.school,)).fetchone()[0]
            rows = []
            for index, payload in enumerate(payloads):
                event_id = self.event(conn, changeset, payload)
                outbox_id = conn.execute("INSERT INTO public.outbox_message(changeset_id,event_id,created_at) "
                                         "VALUES (%s,%s,'2000-01-01'::timestamptz + %s * interval '1 second') "
                                         "RETURNING outbox_id", (changeset, event_id, index)).fetchone()[0]
                rows.append((outbox_id, event_id))
        return changeset, rows

    def snapshot(self):
        with self.connect() as conn:
            events = conn.execute("SELECT event_id,event_position,payload FROM public.domain_event "
                                  "WHERE school_id=%s ORDER BY event_id", (self.school,)).fetchall()
            outbox = conn.execute("SELECT outbox_id,event_id,status,published_at,attempt_count FROM public.outbox_message "
                                  "WHERE changeset_id IN (SELECT changeset_id FROM public.changeset "
                                  "WHERE school_id=%s) ORDER BY outbox_id", (self.school,)).fetchall()
        return events, outbox

    def assert_unpublished(self):
        events, rows = self.snapshot()
        self.assertTrue(all(row[1] is None for row in events))
        self.assertTrue(all(row[2:] == ("pending", None, 0) for row in rows))

    def publish(self, changeset):
        case = self

        class ObservedCursor(psycopg.Cursor):
            def execute(self, query, params=None, **kwargs):
                if isinstance(query, str) and query.strip().startswith("SELECT public.publish_outbox_event("):
                    case.publication_calls.append(params[0])
                    case.at_publication.set()
                    if case.pause_publication and not case.release_publication.wait(timeout=5):
                        raise AssertionError("PUBLICATION_COORDINATION_TIMEOUT")
                return super().execute(query, params, **kwargs)

        def exact_ci_route(raw):
            if raw != CI_DSN:
                raise RuntimeError("EXACT_EPHEMERAL_CI_ROUTE_REQUIRED")
            return raw

        def open_worker(dsn, **kwargs):
            assert dsn == CI_DSN
            assert kwargs == {"connect_timeout": 10, "application_name": "bridge-video-outbox-publisher"}
            conn = self.connect(worker=True, cursor_factory=ObservedCursor)
            self.publisher_pid = conn.info.backend_pid
            self.publisher_open.set()
            return conn

        # The production normalizer stays unchanged and its separate contracts run
        # in this harness. This scoped test routes the real wrapper only to CI.
        with patch.object(publisher, "normalize_dsn", side_effect=exact_ci_route), \
                patch.object(publisher.psycopg, "connect", side_effect=open_worker):
            return publisher.publish_changeset_outbox(CI_DSN, changeset)

    def wait_for_blocker(self, blocked_pid, blocker_pid):
        until = time.monotonic() + 4
        with self.connect(autocommit=True) as observer:
            while time.monotonic() < until:
                if observer.execute("SELECT %s=ANY(pg_blocking_pids(%s))",
                                    (blocker_pid, blocked_pid)).fetchone()[0]:
                    return
                time.sleep(.02)
        self.fail("EXPECTED_POSTGRESQL_LOCK_WAIT_NOT_OBSERVED")

    def test_blocked_changeset_has_zero_publication_calls_and_no_changed_rows(self):
        changeset, _ = self.seed([{}, {"publication_authorization_status": "allowed"}, BLOCKED])
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "^OUTBOX_PUBLICATION_AUTHORIZATION_BLOCKED$"):
            self.publish(changeset)
        self.assertEqual(self.publication_calls, [])
        self.assertEqual(self.snapshot(), before)
        self.assert_unpublished()
        evidence("blocked_batch", selected_rows=3, publication_calls=0, changed_rows=0)

    def test_legacy_and_allowed_are_published_idempotently(self):
        changeset, rows = self.seed([{}, {"publication_authorization_status": "allowed"}])
        first = self.publish(changeset)
        second = self.publish(changeset)
        self.assertEqual(first, second)
        self.assertEqual(first["published_count"], 2)
        self.assertEqual(len(set(first["event_positions"])), 2)
        self.assertTrue(all(position > 0 for position in first["event_positions"]))
        self.assertEqual(self.publication_calls, [row[0] for row in rows] * 2)
        events, stored = self.snapshot()
        self.assertTrue(all(row[1] is not None for row in events))
        self.assertTrue(all(row[2] == "published" and row[3] is not None and row[4] == 1 for row in stored))
        evidence("legacy_idempotence", published_count=2, same_positions_on_retry=True)

    def test_invisible_event_is_fail_closed_before_any_publication(self):
        changeset, rows = self.seed([{}, BLOCKED])
        hidden = rows[1][1]
        with self.connect() as conn:
            self.assertFalse(conn.execute("SELECT relrowsecurity FROM pg_class "
                                          "WHERE oid='public.domain_event'::regclass").fetchone()[0])
            conn.execute("ALTER TABLE public.domain_event ENABLE ROW LEVEL SECURITY")
            conn.execute(sql.SQL("CREATE POLICY ci_outbox_guard_visibility ON public.domain_event "
                                        "FOR SELECT TO bridge_school_worker USING (event_id <> {})")
                         .format(sql.Literal(hidden)))
        try:
            before = self.snapshot()
            with self.assertRaisesRegex(RuntimeError, "^OUTBOX_EVENT_UNAVAILABLE$"):
                self.publish(changeset)
            self.assertEqual(self.publication_calls, [])
            self.assertEqual(self.snapshot(), before)
            self.assert_unpublished()
            evidence("invisible_event", publication_calls=0, changed_rows=0)
        finally:
            with self.connect() as conn:
                conn.execute("DROP POLICY ci_outbox_guard_visibility ON public.domain_event")
                conn.execute("ALTER TABLE public.domain_event DISABLE ROW LEVEL SECURITY")

    def test_publisher_first_blocks_rebind_until_commit_without_deadlock(self):
        changeset, rows = self.seed([{}])
        outbox_id, original = rows[0]
        with self.connect() as conn:
            blocked = self.event(conn, changeset, BLOCKED)
        self.pause_publication = True

        def rebind():
            with self.connect(worker=True) as conn:
                self.writer_pid = conn.info.backend_pid
                self.writer_open.set()
                conn.execute("UPDATE public.outbox_message SET event_id=%s WHERE outbox_id=%s", (blocked, outbox_id))
            return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            publish_future = pool.submit(self.publish, changeset)
            try:
                self.assertTrue(self.at_publication.wait(4))
                rebind_future = pool.submit(rebind)
                self.assertTrue(self.writer_open.wait(3))
                self.wait_for_blocker(self.writer_pid, self.publisher_pid)
                self.assertFalse(rebind_future.done())
            finally:
                self.release_publication.set()
            result = publish_future.result(timeout=12)
            self.assertTrue(rebind_future.result(timeout=12))
        self.assertEqual(result["published_count"], 1)
        with self.connect() as conn:
            positions = dict(conn.execute("SELECT event_id,event_position FROM public.domain_event WHERE school_id=%s", (self.school,)))
        self.assertIsNotNone(positions[original])
        self.assertIsNone(positions[blocked])
        self.assertEqual(len(self.publication_calls), 1)
        # A post-commit writer can still rebind the row. This Python guard cannot
        # make arbitrary outbox mutation or other consumers globally safe.
        evidence("publisher_first_rebind", postgres_lock_wait_observed=True,
                 original_event_published=True, blocked_event_position_is_null=True,
                 both_transactions_completed=True, deadlock=False)

    def writer_first(self, *, new_event):
        changeset, rows = self.seed([{}])
        outbox_id, _ = rows[0]
        if not new_event:
            with self.connect() as conn:
                blocked = self.event(conn, changeset, BLOCKED)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.connect(worker=True) as writer:
                if new_event:
                    blocked = self.event(writer, changeset, BLOCKED)
                writer.execute("UPDATE public.outbox_message SET event_id=%s WHERE outbox_id=%s", (blocked, outbox_id))
                future = pool.submit(self.publish, changeset)
                self.assertTrue(self.publisher_open.wait(3))
                self.wait_for_blocker(self.publisher_pid, writer.info.backend_pid)
                self.assertFalse(future.done())
            error = "OUTBOX_EVENT_UNAVAILABLE" if new_event else "OUTBOX_PUBLICATION_AUTHORIZATION_BLOCKED"
            with self.assertRaisesRegex(RuntimeError, "^" + error + "$"):
                future.result(timeout=12)
        self.assertEqual(self.publication_calls, [])
        self.assert_unpublished()
        evidence("writer_first_new_event" if new_event else "writer_first_existing_event",
                 postgres_lock_wait_observed=True, publication_calls=0,
                 event_positions_are_null=True, expected_error=error,
                 both_transactions_completed=True, deadlock=False)

    def test_writer_first_existing_blocked_event_is_rechecked_after_wait(self):
        self.writer_first(new_event=False)

    def test_writer_first_new_invisible_event_fails_closed_after_wait(self):
        self.writer_first(new_event=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
