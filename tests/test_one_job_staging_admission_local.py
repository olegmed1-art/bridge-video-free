"""Local fail-closed tests; SQL still requires isolated PostgreSQL review."""
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from database.video_one_job_staging_admission import stage_admitted_candidate_on_connection


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        self.connection.calls.append((sql, params, self.connection.in_transaction))
        if not self.connection.in_transaction:
            raise AssertionError('SQL outside staging transaction')
        if 'assert_one_job_staging_v1' in sql:
            if self.connection.reject == 'lease':
                raise ValueError('SUBSET_LEASE_LOST')
            self.row = (params[0],)
        elif 'SELECT payload_hash' in sql:
            self.row = ('a' * 64,)
        elif 'attest_one_job_staging_receipt_v1' in sql:
            if self.connection.reject == 'receipt':
                raise ValueError('ONE_JOB_RECEIPT_IDENTITY_CHANGED')
            binding = (params[0], params[1], params[5], params[7], params[8], params[9])
            prior = self.connection.store.setdefault(params[6], binding)
            if prior != binding:
                raise ValueError('ONE_JOB_RECEIPT_IDENTITY_CHANGED')
            attempt = (params[1], params[4])
            prior_attempt = self.connection.attempts.setdefault(attempt, (params[6], params[2]))
            if prior_attempt != (params[6], params[2]):
                raise ValueError('ONE_JOB_ATTEMPT_RECEIPT_CHANGED')
            self.row = (params[6],)
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, reject=None, store=None, attempts=None):
        self.reject = reject
        self.store = {} if store is None else store
        self.attempts = {} if attempts is None else attempts
        self.in_transaction = False
        self.calls = []
        self.committed = False
        self.rolled_back = False

    @contextmanager
    def transaction(self):
        self.in_transaction = True
        try:
            yield
        except BaseException:
            self.rolled_back = True
            raise
        else:
            self.committed = True
        finally:
            self.in_transaction = False

    def cursor(self):
        return Cursor(self)


class OneJobAdapterTest(unittest.TestCase):
    def setUp(self):
        self.scope = dict(admission_id=str(uuid4()), job_id=str(uuid4()),
                          lease_token=str(uuid4()), attempt_count=1,
                          worker_key='local-worker', source_fingerprint='b' * 64)
        self.receipt_id = str(uuid4())
        self.run_id = str(uuid4())
        self.source_id = str(uuid4())

    def writer(self, connection, payload, *, persisted_result):
        self.assertTrue(connection.in_transaction)
        self.assertEqual(payload, {'job_id': 'synthetic'})
        self.assertEqual(persisted_result, {'persisted': True})
        connection.calls.append(('WRITE_STAGING', None, True))
        return dict(status='PERSISTED', replay_receipt_id=self.receipt_id,
                    analysis_run_id=self.run_id, source_id=self.source_id)

    def stage(self, connection, scope=None):
        return stage_admitted_candidate_on_connection(
            connection, {'job_id': 'synthetic'}, {'persisted': True},
            admission=self.scope if scope is None else scope, writer=self.writer)

    def test_assert_write_attest_share_one_transaction(self):
        conn = Connection()
        result = self.stage(conn)
        self.assertEqual(result['replay_receipt_id'], self.receipt_id)
        self.assertEqual([next((name for name in
            ('assert_one_job_staging_v1', 'WRITE_STAGING', 'SELECT payload_hash',
             'attest_one_job_staging_receipt_v1') if name in call[0]), None)
            for call in conn.calls],
            ['assert_one_job_staging_v1', 'WRITE_STAGING', 'SELECT payload_hash',
             'attest_one_job_staging_receipt_v1'])
        self.assertTrue(conn.committed)

    def test_lost_lease_stops_write(self):
        conn = Connection('lease')
        with self.assertRaisesRegex(ValueError, 'SUBSET_LEASE_LOST'):
            self.stage(conn)
        self.assertEqual(len(conn.calls), 1)
        self.assertTrue(conn.rolled_back)

    def test_receipt_identity_change_rolls_back_staging(self):
        conn = Connection('receipt')
        with self.assertRaisesRegex(ValueError, 'ONE_JOB_RECEIPT_IDENTITY_CHANGED'):
            self.stage(conn)
        self.assertFalse(conn.committed)
        self.assertTrue(conn.rolled_back)

    def test_invalid_scope_never_starts_transaction(self):
        for bad in (dict(self.scope, job_id='other-job'),
                    dict(self.scope, source_fingerprint='unknown'),
                    dict(self.scope, attempt_count=0)):
            with self.subTest(bad=bad):
                conn = Connection()
                with self.assertRaisesRegex(ValueError, 'ONE_JOB_ADMISSION_ARGUMENT_INVALID'):
                    self.stage(conn, bad)
                self.assertEqual(conn.calls, [])

    def test_same_receipt_rebinds_after_crash_only_for_new_exact_lease(self):
        store, attempts = {}, {}
        first = Connection(store=store, attempts=attempts)
        self.stage(first)
        retry = dict(self.scope, attempt_count=2, lease_token=str(uuid4()))
        second = Connection(store=store, attempts=attempts)
        self.stage(second, retry)
        self.assertTrue(first.committed and second.committed)
        self.assertEqual(len(store), 1)
        self.assertEqual(len(attempts), 2)
        changed_revision = dict(retry, attempt_count=3,
                                lease_token=str(uuid4()), source_fingerprint='c' * 64)
        with self.assertRaisesRegex(ValueError, 'ONE_JOB_RECEIPT_IDENTITY_CHANGED'):
            self.stage(Connection(store=store, attempts=attempts), changed_revision)
        changed_job = dict(retry, job_id=str(uuid4()))
        with self.assertRaisesRegex(ValueError, 'ONE_JOB_RECEIPT_IDENTITY_CHANGED'):
            self.stage(Connection(store=store, attempts=attempts), changed_job)
        changed_lease_same_attempt = dict(retry, lease_token=str(uuid4()))
        with self.assertRaisesRegex(ValueError, 'ONE_JOB_ATTEMPT_RECEIPT_CHANGED'):
            self.stage(Connection(store=store, attempts=attempts), changed_lease_same_attempt)

    def test_sql_proposal_has_separate_writer_and_native_finish(self):
        sql = (Path(__file__).resolve().parents[1] / 'database' / 'proposals' /
               '0061_one_job_staging_admission_v1.sql').read_text(encoding='utf-8')
        scope = sql.split('CREATE FUNCTION video_queue.check_one_job_staging_scope_v1(', 1)[1]
        scope = scope.split('CREATE FUNCTION video_queue.assert_one_job_staging_v1(', 1)[0]
        self.assertIn("session_user <> 'bridge_school_worker_principal'", scope)
        self.assertIn("pg_has_role(session_user,'bridge_school_worker','MEMBER')", scope)
        self.assertNotIn('subset_lock_lease(', scope)
        finish = sql.split('CREATE FUNCTION video_queue.finish_one_job_staging_v1(', 1)[1]
        self.assertIn("SET status='REVIEW_READY'", finish)
        self.assertIn('video_job_output_mode_v1_check', sql)
        self.assertNotIn('finish_subset_job(', finish)
        self.assertNotIn('v_shadow', finish)
        self.assertNotIn("status='QUEUED'", finish)
        self.assertNotIn('pg_current_xact_id()', sql)
        self.assertIn('one_job_staging_receipt_attempt_v1', sql)


if __name__ == '__main__':
    unittest.main()
