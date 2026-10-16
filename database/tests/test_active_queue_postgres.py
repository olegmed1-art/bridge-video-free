"""Real PostgreSQL queue admission tests; only the disposable local CI clone."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import threading
import time
import unittest
import uuid

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]


class ActiveQueuePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ['ACTIVE_QUEUE_CI_DATABASE_URL']
        route = conninfo_to_dict(cls.dsn)
        route.pop('password', None)
        if os.environ.get('GITHUB_ACTIONS') != 'true' or route != {
            'host': 'localhost', 'port': '5432', 'dbname': 'bridge_school_queue_ci', 'user': 'bridge_ci_owner',
        }:
            raise RuntimeError('EXACT_DISPOSABLE_ACTIVE_QUEUE_CI_REQUIRED')
        cls.admin_dsn = os.environ['ADMIN_DATABASE_URL']
        admin_route = conninfo_to_dict(cls.admin_dsn)
        admin_route.pop('password', None)
        if admin_route != {'host': 'localhost', 'port': '5432', 'dbname': 'bridge_school_ci', 'user': 'postgres'}:
            raise RuntimeError('EXACT_DISPOSABLE_ACTIVE_QUEUE_ADMIN_REQUIRED')

    @classmethod
    def connect(cls):
        return psycopg.connect(cls.dsn, options='-c statement_timeout=10000 -c lock_timeout=8000')

    def setUp(self):
        self.prefix = uuid.uuid4().hex
        # One fresh local clone per test preserves immutable event/receipt
        # guards. Cleanup drops only this explicitly named disposable DB.
        with psycopg.connect(self.admin_dsn, autocommit=True) as admin:
            admin.execute('CREATE DATABASE bridge_school_queue_ci WITH TEMPLATE bridge_school_ci OWNER bridge_ci_owner')
        with self.connect() as conn:
            assert conn.execute('SELECT current_database(),session_user').fetchone() == (
                'bridge_school_queue_ci', 'bridge_ci_owner')
            assert not conn.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()[0]
            assert not conn.execute('SELECT 1 FROM autopilot.role_dispatch_active LIMIT 1').fetchone()

    def fixture(self, conn, suffix='', status='PENDING'):
        conn.execute((ROOT / 'database/tests/fixtures/active_queue_fixture.sql').read_text())
        return conn.execute('SELECT pg_temp.active_queue_fixture(%s,%s)',
                            (self.prefix + suffix, status)).fetchone()[0]

    def readiness(self, conn):
        return conn.execute('SELECT used_dispatches,max_dispatches,remaining,readiness '
                            'FROM autopilot.mailbox_rotation_readiness()').fetchone()

    def tearDown(self):
        with psycopg.connect(self.admin_dsn, autocommit=True) as admin:
            admin.execute('DROP DATABASE bridge_school_queue_ci WITH (FORCE)')

    def test_archive_frees_slot_and_receipt_replay_survives(self):
        with self.connect() as conn:
            # More than forty historical rows never consume working capacity.
            for i in range(41):
                did = self.fixture(conn, str(i), 'FAILED_CLOSED')
            self.assertEqual(self.readiness(conn), (0, 40, 40, 'NORMAL'))
            self.assertEqual(conn.execute('SELECT count(*) FROM autopilot.role_dispatch_archive').fetchone()[0], 41)
            body = {'dispatch_id': str(did), 'dispatch_epoch': 1, 'role': 'AUTOPILOT',
                    'task_fingerprint': 'c' * 64, 'target_pr': 999973, 'status': 'BLOCKED',
                    'result_code': 'SQL_FIXTURE_FAILED', 'target_head_sha': 'a' * 40,
                    'summary': 'Synthetic retained callback fixture.'}
            delivery = 'sql-0403-' + self.prefix
            conn.execute('INSERT INTO autopilot.role_dispatch_callback_receipt(delivery_id,payload_fingerprint,dispatch_id,'
                         'actor_login,actor_id,author_association,app_slug,app_id,callback_body) '
                         "VALUES(%s,%s,%s,'olegmed1-art',315099490,'OWNER','chatgpt-codex-connector',1144995,%s)",
                         (delivery, 'd' * 64, did, Jsonb(body)))
            mailbox = conn.execute('SELECT mailbox_pr FROM autopilot.role_dispatch_outbox WHERE dispatch_id=%s', (did,)).fetchone()[0]
            replay = conn.execute('SELECT accepted,duplicate FROM autopilot.accept_role_dispatch_callback('
                                  "%s,%s,true,'olegmed1-art/bridge-video-free',%s,'olegmed1-art',315099490,"
                                  "'OWNER','chatgpt-codex-connector',1144995,%s)",
                                  (delivery, 'd' * 64, mailbox, Jsonb(body))).fetchone()
            self.assertEqual(replay, (False, True))
            self.assertEqual(self.readiness(conn), (0, 40, 40, 'NORMAL'))

    def test_terminal_transition_is_atomic_and_cannot_reopen_or_move(self):
        with self.connect() as conn:
            did = self.fixture(conn)
            self.assertEqual(self.readiness(conn)[0], 1)
            with self.assertRaisesRegex(RuntimeError, 'rollback'):
                with conn.transaction():
                    conn.execute("UPDATE autopilot.role_dispatch_outbox SET status='FAILED_CLOSED',completed_at=now() WHERE dispatch_id=%s", (did,))
                    self.assertEqual(self.readiness(conn)[0], 0)
                    raise RuntimeError('rollback')
            self.assertEqual(self.readiness(conn)[0], 1)
            conn.execute("UPDATE autopilot.role_dispatch_outbox SET status='FAILED_CLOSED',completed_at=now() WHERE dispatch_id=%s", (did,))
            conn.execute("UPDATE autopilot.role_dispatch_outbox SET status='FAILED_CLOSED' WHERE dispatch_id=%s", (did,))
            self.assertEqual(self.readiness(conn)[0], 0)
            for sql, code in [("SET status='PENDING',completed_at=NULL", 'AUTOPILOT_TERMINAL_DISPATCH_REOPEN_FORBIDDEN'),
                              ('SET mailbox_pr=1150', 'AUTOPILOT_DISPATCH_IDENTITY_IMMUTABLE')]:
                with self.assertRaisesRegex(psycopg.Error, code), conn.transaction():
                    conn.execute('UPDATE autopilot.role_dispatch_outbox ' + sql + ' WHERE dispatch_id=%s', (did,))

    def test_threshold_full_duplicate_and_backpressure(self):
        with self.connect() as conn:
            first = None
            for i in range(40):
                did = self.fixture(conn, str(i))
                first = first or did
                if i == 30:
                    self.assertEqual(self.readiness(conn), (31, 40, 9, 'NORMAL'))
                if i == 31:
                    self.assertEqual(self.readiness(conn), (32, 40, 8, 'NEAR_CAPACITY'))
            self.assertEqual(self.readiness(conn), (40, 40, 0, 'CAPACITY_REACHED'))
            # Same durable row at full capacity is a no-op, not a new charge.
            conn.execute('INSERT INTO autopilot.role_dispatch_outbox SELECT * FROM '
                         'autopilot.role_dispatch_outbox WHERE dispatch_id=%s ON CONFLICT DO NOTHING', (first,))
            with self.assertRaisesRegex(psycopg.Error, 'AUTOPILOT_MAILBOX_CAPACITY_REACHED'), conn.transaction():
                conn.execute("SELECT pg_temp.active_queue_fixture(%s)", (self.prefix + 'overflow',))
            mailbox = conn.execute('SELECT mailbox_pr FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle=\'ACTIVE\'').fetchone()[0]
            tid = conn.execute("INSERT INTO autopilot.task(task_key,goal_type,goal_json,status,current_step_key,created_by,source) "
                               "VALUES(%s,'CHATGPT_ROLE_DISPATCH_V1',%s,'READY','github.chatgpt.role.dispatch','sql-test','SQL_TEST') RETURNING task_id",
                               ('sql-0403-' + self.prefix + 'waiting', Jsonb({'mailbox_pr': mailbox, 'role': 'AUTOPILOT'}))).fetchone()[0]
            self.assertEqual(conn.execute("SELECT count(*) FROM autopilot.claim_next_task('sql-0403-worker',60)").fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT status,attempts FROM autopilot.task WHERE task_id=%s', (tid,)).fetchone(), ('READY', 0))
            conn.execute("UPDATE autopilot.role_dispatch_outbox SET status='FAILED_CLOSED',completed_at=now() WHERE dispatch_id=%s", (first,))
            self.assertEqual(self.readiness(conn)[0], 39)
            self.assertEqual(conn.execute("SELECT task_id FROM autopilot.claim_next_task('sql-0403-worker',60)").fetchone()[0], tid)

    def compete_last_slot(self, rollback_first):
        with self.connect() as conn:
            conn.execute("UPDATE autopilot.role_dispatch_mailbox_registry SET max_dispatches=1 WHERE lifecycle='ACTIVE'")
        first = self.connect()
        second_started = threading.Event()
        try:
            self.fixture(first, 'first')
            def compete():
                with self.connect() as second:
                    second_started.set()
                    return self.fixture(second, 'second')
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(compete)
                self.assertTrue(second_started.wait(3))
                # Observe actual lock waiting with fresh activity snapshots.
                with self.connect() as observer:
                    deadline = time.monotonic() + 5
                    waiting = 0
                    while time.monotonic() < deadline:
                        observer.execute('SELECT pg_stat_clear_snapshot()')
                        waiting = observer.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock'").fetchone()[0]
                        if waiting:
                            break
                        time.sleep(0.01)
                    self.assertGreater(waiting, 0)
                first.rollback() if rollback_first else first.commit()
                if rollback_first:
                    self.assertIsNotNone(future.result(timeout=10))
                else:
                    with self.assertRaisesRegex(psycopg.Error, 'AUTOPILOT_MAILBOX_CAPACITY_REACHED'):
                        future.result(timeout=10)
            with self.connect() as conn:
                self.assertEqual(self.readiness(conn)[0], 1)
        finally:
            first.close()

    def test_competing_last_slot_commit(self):
        self.compete_last_slot(False)

    def test_competing_last_slot_rollback(self):
        self.compete_last_slot(True)

    def test_concurrent_claims_reserve_last_slot_without_consuming_loser_attempt(self):
        ids = []
        with self.connect() as conn:
            conn.execute("UPDATE autopilot.role_dispatch_mailbox_registry SET max_dispatches=1 WHERE lifecycle='ACTIVE'")
            mailbox = conn.execute("SELECT mailbox_pr FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE'").fetchone()[0]
            for i in range(2):
                ids.append(conn.execute("INSERT INTO autopilot.task(task_key,goal_type,goal_json,status,current_step_key,created_by,source) "
                                        "VALUES(%s,'CHATGPT_ROLE_DISPATCH_V1',%s,'READY','github.chatgpt.role.dispatch','sql-test','SQL_TEST') RETURNING task_id",
                                        ('sql-0403-' + self.prefix + str(i), Jsonb({'mailbox_pr': mailbox, 'role': 'AUTOPILOT'}))).fetchone()[0])
        barrier = threading.Barrier(2)
        def claim(i):
            with self.connect() as conn:
                barrier.wait(timeout=5)
                return conn.execute('SELECT task_id FROM autopilot.claim_next_task(%s,60)',
                                    ('sql-0403-worker-' + str(i),)).fetchone()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, range(2)))
        self.assertEqual(sum(row is not None for row in results), 1)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT status,attempts FROM autopilot.task WHERE task_id=ANY(%s) ORDER BY status', (ids,)).fetchall(),
                             [('READY', 0), ('RUNNING', 1)])
            self.assertFalse(conn.execute('SELECT autopilot.role_dispatch_queue_available(%s)', (mailbox,)).fetchone()[0])
            self.assertEqual(self.readiness(conn)[0], 0)

    def test_multirow_overflow_rolls_back_all_new_rows(self):
        with self.connect() as conn:
            conn.execute('UPDATE autopilot.role_dispatch_mailbox_registry SET max_dispatches=1 WHERE lifecycle=\'ACTIVE\'')
            first = self.fixture(conn, 'first', 'FAILED_CLOSED')
            conn.execute('SELECT pg_temp.active_queue_fixture(%s,%s)', (self.prefix + 'second', 'FAILED_CLOSED'))
            conn.execute('CREATE TEMP TABLE insert_rows AS SELECT * FROM autopilot.role_dispatch_outbox')
            conn.execute("UPDATE insert_rows SET status='PENDING',completed_at=NULL")
            # One real multi-row INSERT must leave no partial admission. The
            # savepoint also restores the two original synthetic archive rows.
            with self.assertRaisesRegex(psycopg.Error, 'AUTOPILOT_MAILBOX_CAPACITY_REACHED'), conn.transaction():
                conn.execute('DELETE FROM autopilot.role_dispatch_outbox')
                conn.execute('INSERT INTO autopilot.role_dispatch_outbox SELECT * FROM insert_rows')
            self.assertEqual(self.readiness(conn)[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM autopilot.role_dispatch_archive').fetchone()[0], 2)
            conn.execute('UPDATE autopilot.role_dispatch_mailbox_registry SET max_dispatches=40 WHERE lifecycle=\'ACTIVE\'')

    def test_stalled_missing_deadline_is_visible_and_does_not_release_capacity(self):
        with self.connect() as conn:
            did = self.fixture(conn)
            conn.execute("UPDATE autopilot.role_dispatch_outbox SET status='PUBLISHED' WHERE dispatch_id=%s", (did,))
            health = conn.execute("SELECT severity,details->>'stalled_dispatches' FROM public.autopilot_operational_health_signal WHERE signal_key='autopilot_mailbox_e2e'").fetchone()
            self.assertEqual(health, ('critical', '1'))
            self.assertEqual(self.readiness(conn)[0], 1)

    def test_repeatable_read_admission_fails_closed(self):
        with self.connect() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            with self.assertRaisesRegex(psycopg.Error, 'AUTOPILOT_MAILBOX_ISOLATION_UNSUPPORTED'), conn.transaction():
                self.fixture(conn)

    def sent_contract(self):
        """Real registration/claim/prepare/send RPCs, on retained legacy v1."""
        with self.connect() as conn:
            mailbox = conn.execute("SELECT mailbox_pr FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE'").fetchone()[0]
            goal = {'repository': 'olegmed1-art/bridge-video-free', 'mailbox_pr': mailbox,
                    'role': 'AUTOPILOT', 'target_pr': 999973, 'expected_head_sha': 'a' * 40,
                    'dispatch_epoch': 1, 'successor_task_key': None, 'successor_role': None,
                    'successor_target_pr': None, 'successor_expected_head_sha': None}
            tid = conn.execute("SELECT task_id FROM autopilot.create_chatgpt_role_dispatch_task(%s,%s,0,'sql-test','SQL_TEST')",
                               ('sql-0403-' + self.prefix, Jsonb(goal))).fetchone()[0]
            epoch = conn.execute("SELECT lease_epoch FROM autopilot.claim_next_task('sql-0403-worker',60)").fetchone()[0]
            did, fingerprint = conn.execute("SELECT dispatch_id,task_fingerprint FROM autopilot.prepare_role_dispatch(%s,'sql-0403-worker',%s)",
                                            (tid, epoch)).fetchone()
            conn.execute('UPDATE autopilot.role_dispatch_outbox SET delivery_contract_version=1 WHERE dispatch_id=%s', (did,))
            claim = conn.execute("SELECT dispatch_id,claim_epoch FROM autopilot.claim_role_dispatch_outbox('sql-0403-publisher',60)").fetchone()
            self.assertEqual(claim[0], did)
            self.assertTrue(conn.execute("SELECT autopilot.mark_role_dispatch_sent(%s,'sql-0403-publisher',%s,999974,%s)",
                                         (did, claim[1], 'e' * 64)).fetchone()[0])
            conn.execute("UPDATE autopilot.role_dispatch_outbox SET callback_deadline_at=now()-interval '1 second' WHERE dispatch_id=%s", (did,))
        body = {'dispatch_id': str(did), 'dispatch_epoch': 1, 'role': 'AUTOPILOT',
                'task_fingerprint': fingerprint, 'target_pr': 999973, 'status': 'SUCCEEDED',
                'result_code': 'SQL_FIXTURE_AUDIT', 'target_head_sha': 'a' * 40,
                'summary': 'Synthetic callback concurrency evidence.'}
        return did, mailbox, body

    def callback(self, conn, mailbox, body):
        return conn.execute('SELECT accepted,duplicate FROM autopilot.accept_role_dispatch_callback('
                            "%s,%s,true,'olegmed1-art/bridge-video-free',%s,'olegmed1-art',315099490,"
                            "'OWNER','chatgpt-codex-connector',1144995,%s)",
                            ('sql-0403-' + self.prefix, 'd' * 64, mailbox, Jsonb(body))).fetchone()

    def test_callback_commit_or_rollback_and_reconcile_preserve_slot_and_dedup(self):
        did, mailbox, body = self.sent_contract()
        with self.connect() as callback_conn, self.connect() as observer:
            self.assertEqual(self.callback(callback_conn, mailbox, body), (True, False))
            self.assertEqual(self.readiness(observer)[0], 1)
            self.assertEqual(observer.execute('SELECT autopilot.reconcile_role_dispatch_callbacks()').fetchone()[0], 0)
            callback_conn.rollback()
            self.assertEqual(self.readiness(observer)[0], 1)
            self.assertEqual(self.callback(callback_conn, mailbox, body), (True, False))
            callback_conn.commit()
            self.assertEqual(self.readiness(observer)[0], 0)
            self.assertEqual(self.callback(observer, mailbox, body), (False, True))
            self.assertEqual(observer.execute('SELECT count(*) FROM autopilot.role_dispatch_archive WHERE dispatch_id=%s', (did,)).fetchone()[0], 1)

    def test_reconcile_winner_prevents_late_callback_reopening(self):
        did, mailbox, body = self.sent_contract()
        with self.connect() as reconciler:
            self.assertEqual(reconciler.execute('SELECT autopilot.reconcile_role_dispatch_callbacks()').fetchone()[0], 1)
            started = threading.Event()
            def late_callback():
                with self.connect() as conn:
                    started.set()
                    return self.callback(conn, mailbox, body)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(late_callback)
                self.assertTrue(started.wait(3))
                with self.connect() as observer:
                    deadline = time.monotonic() + 5
                    waiting = 0
                    while time.monotonic() < deadline:
                        observer.execute('SELECT pg_stat_clear_snapshot()')
                        waiting = observer.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock'").fetchone()[0]
                        if waiting:
                            break
                        time.sleep(0.01)
                    self.assertGreater(waiting, 0)
                    self.assertEqual(self.readiness(observer)[0], 1)
                reconciler.commit()
                with self.assertRaisesRegex(psycopg.Error, 'AUTOPILOT_CALLBACK_DISPATCH_NOT_SENT'):
                    future.result(timeout=10)
        with self.connect() as conn:
            self.assertEqual(self.readiness(conn)[0], 0)
            self.assertEqual(conn.execute('SELECT status FROM autopilot.role_dispatch_archive WHERE dispatch_id=%s', (did,)).fetchone()[0], 'FAILED_CLOSED')
            self.assertEqual(conn.execute('SELECT count(*) FROM autopilot.role_dispatch_callback_receipt WHERE dispatch_id=%s', (did,)).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
