"""Local fail-closed tests; SQL still requires isolated PostgreSQL review."""
import unittest
from contextlib import contextmanager
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
                raise ValueError('ONE_JOB_RECEIPT_REUSE_HOLD')
            self.row = (params[6],)
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, reject=None):
        self.reject = reject
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

    def writer(self, connection, payload, *, persisted_result):
        self.assertTrue(connection.in_transaction)
        self.assertEqual(payload, {'job_id': 'synthetic'})
        self.assertEqual(persisted_result, {'persisted': True})
        connection.calls.append(('WRITE_STAGING', None, True))
        return dict(status='PERSISTED', replay_receipt_id=self.receipt_id,
                    analysis_run_id=str(uuid4()), source_id=str(uuid4()))

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

    def test_old_receipt_rolls_back_staging(self):
        conn = Connection('receipt')
        with self.assertRaisesRegex(ValueError, 'ONE_JOB_RECEIPT_REUSE_HOLD'):
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


if __name__ == '__main__':
    unittest.main()
