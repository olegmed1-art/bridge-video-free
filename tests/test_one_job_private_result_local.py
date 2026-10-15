"""Local adapter checks; SQL awaits independent disposable PostgreSQL review."""
import hashlib
import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from database.video_one_job_private_result import (
    stage_and_read_private_result, stage_private_result_on_connection,
    stage_completed_drive_result_private,
)


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        self.connection.calls.append((sql, params))
        if 'stage_one_job_private_result_v1' in sql:
            if not self.connection.in_transaction:
                raise AssertionError('stage outside transaction')
            self.row = ({'status': 'PRIVATE_RESULT_STAGED',
                         'receipt_id': params[6], 'bundle_sha256': 'a' * 64,
                         'master_json_sha256': hashlib.sha256(params[7].encode()).hexdigest(),
                         'ai_done_sha256': hashlib.sha256(params[9].encode()).hexdigest()},)
        elif 'read_one_job_private_result_v1' in sql:
            self.row = ({'status': 'COMMITTED_PRIVATE_RESULT',
                         'receipt_id': params[6], 'bundle_sha256': 'a' * 64},)
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self):
        self.in_transaction = False
        self.committed = False
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    @contextmanager
    def transaction(self):
        self.in_transaction = True
        try:
            yield
        except BaseException:
            raise
        else:
            self.committed = True
        finally:
            self.in_transaction = False

    def cursor(self):
        return Cursor(self)


class PrivateResultTest(unittest.TestCase):
    def setUp(self):
        self.scope = dict(admission_id=str(uuid4()), job_id=str(uuid4()),
                          lease_token=str(uuid4()), worker_key='queue-worker',
                          attempt_count=1, source_fingerprint='b' * 64)
        self.master = json.dumps({'job_id': 'one-job'}, separators=(',', ':'))
        self.done = json.dumps({'job_id': 'one-job', 'status': 'AI_DONE'})
        self.done_drive_id = 'synthetic_ai_done_001'

    def test_stage_then_separate_committed_readback_only_calls_gate_functions(self):
        connections = []

        def open_connection():
            conn = Connection()
            connections.append(conn)
            return conn

        result = stage_and_read_private_result(
            open_connection, admission=self.scope,
            master_json_text=self.master, ai_done_json_text=self.done,
            ai_done_drive_id=self.done_drive_id)
        self.assertEqual(result['status'], 'PRIVATE_RESULT_STAGED')
        self.assertEqual(len(connections), 2)
        self.assertTrue(connections[0].committed)
        self.assertEqual(len(connections[0].calls), 1)
        self.assertEqual(len(connections[1].calls), 1)
        self.assertIn('stage_one_job_private_result_v1', connections[0].calls[0][0])
        self.assertIn('read_one_job_private_result_v1', connections[1].calls[0][0])
        self.assertEqual(connections[0].calls[0][1][:6], tuple(self.scope[k] for k in
            ('admission_id','job_id','lease_token','worker_key','attempt_count','source_fingerprint')))
        for conn in connections:
            self.assertFalse(any('public.' in sql or 'INSERT INTO' in sql for sql, _ in conn.calls))

    def test_invalid_scope_and_payload_never_touch_database(self):
        for bad_scope, bad_master, bad_done in (
            (dict(self.scope, lease_token='wrong'), self.master, self.done),
            (dict(self.scope, attempt_count=0), self.master, self.done),
            (self.scope, '{', self.done),
            (self.scope, self.master, '{"x":NaN}'),
        ):
            with self.subTest(bad_scope=bad_scope, bad_master=bad_master):
                conn = Connection()
                with self.assertRaises(ValueError):
                    stage_private_result_on_connection(conn, admission=bad_scope,
                        master_json_text=bad_master, ai_done_json_text=bad_done,
                        ai_done_drive_id=self.done_drive_id)
                self.assertEqual(conn.calls, [])

    def test_retry_keeps_receipt_identity_but_changes_lease_scope(self):
        first = Connection()
        receipt = stage_private_result_on_connection(first, admission=self.scope,
            master_json_text=self.master, ai_done_json_text=self.done,
            ai_done_drive_id=self.done_drive_id)['receipt_id']
        retry = dict(self.scope, lease_token=str(uuid4()), attempt_count=2)
        second = Connection()
        self.assertEqual(stage_private_result_on_connection(second, admission=retry,
            master_json_text=self.master, ai_done_json_text=self.done,
            ai_done_drive_id=self.done_drive_id)['receipt_id'], receipt)
        self.assertNotEqual(first.calls[0][1][2], second.calls[0][1][2])
        with self.assertRaisesRegex(ValueError, 'ONE_JOB_PRIVATE_RECEIPT_ID_CHANGED'):
            stage_private_result_on_connection(Connection(), admission=retry,
                master_json_text=self.master, ai_done_json_text=self.done,
                ai_done_drive_id=self.done_drive_id, receipt_id=uuid4())

    def test_sql_gate_is_private_and_requires_exact_attempt(self):
        sql = (Path(__file__).resolve().parents[1] / 'database/proposals'
               / '0062_one_job_private_result_gate_v1.sql').read_text(encoding='utf-8')
        stage = sql.split('CREATE FUNCTION video_queue.stage_one_job_private_result_v1(', 1)[1]
        stage = stage.split('CREATE FUNCTION video_queue.read_one_job_private_result_v1(', 1)[0]
        self.assertEqual(stage.count("p_attempt_count,p_source_fingerprint,'queue'"), 2)
        self.assertIn("pg_has_role(session_user,'bridge_school_worker','MEMBER')", stage)
        self.assertIn('ONE_JOB_PRIVATE_ROLE_CONFLICT', stage)
        self.assertIn('ONE_JOB_PRIVATE_RESULT_REPLAY_CHANGED', stage)
        self.assertIn('ONE_JOB_PRIVATE_ATTEMPT_REPLAY_CHANGED', stage)
        self.assertIn('v_master#>>\'{source,driveId}\' IS DISTINCT FROM v_job.source_file_id', stage)
        self.assertIn('v_source_file->>\'source_fingerprint\' IS DISTINCT FROM p_source_fingerprint', stage)
        self.assertIn("v_done#>>'{masterPdf,masterJsonSha256}' IS DISTINCT FROM v_master_sha", stage)
        self.assertNotIn('public.analysis_', stage)
        self.assertNotIn('public.source_identity', stage)
        self.assertNotIn('public.run_checkpoint_event', stage)
        finish = sql.split('CREATE FUNCTION video_queue.finish_one_job_private_result_v1(', 1)[1]
        self.assertIn('ONE_JOB_PRIVATE_ROLE_CONFLICT', finish)
        self.assertIn('assert_one_job_terminal_evidence_v1(p_job_id,p_output)', finish)
        self.assertIn('authorize_subset_attempt(', finish)
        self.assertIn("SET status='REVIEW_READY'", finish)
        self.assertIn("p_output->>'ai_done_drive_id' IS DISTINCT FROM v_result.ai_done_drive_id", finish)
        self.assertIn("p_output->>'master_pdf_sha256' IS DISTINCT FROM", finish)
        self.assertIn("'released_jobs',0", finish)
        self.assertNotIn('finish_one_job_staging_v1(', finish)

    def test_drive_adapter_uses_verified_raw_master_and_private_gate_only(self):
        stable_key = 'stable-job'
        master = {'job_id': stable_key, 'algorithmRevision': 'test-revision'}
        done = {'job_id': stable_key, 'status': 'AI_DONE',
                'algorithmRevision': 'test-revision'}
        connections = []

        def open_connection():
            conn = Connection()
            connections.append(conn)
            return conn

        result = stage_completed_drive_result_private(
            open_connection, token='synthetic', admission=self.scope,
            stable_job_key=stable_key, algorithm_revision='test-revision',
            output_folder_id='output-folder-12345',
            load_done=lambda token, key, return_raw, output_folder_id: (
                done, self.done_drive_id, json.dumps(done)),
            load_embedded_master=lambda token, value, return_raw: (
                master, json.dumps(master, separators=(',', ':'))))
        self.assertEqual(result['status'], 'PRIVATE_RESULT_STAGED')
        self.assertEqual(len(connections), 2)
        with self.assertRaisesRegex(ValueError, 'ONE_JOB_PRIVATE_DRIVE_RESULT_MISMATCH'):
            stage_completed_drive_result_private(
                open_connection, token='synthetic', admission=self.scope,
                stable_job_key='other-job', algorithm_revision='test-revision',
                output_folder_id='output-folder-12345',
                load_done=lambda token, key, return_raw, output_folder_id: (
                    done, self.done_drive_id, json.dumps(done)),
                load_embedded_master=lambda token, value, return_raw: (
                    master, json.dumps(master)))
        self.assertEqual(len(connections), 2)


if __name__ == '__main__':
    unittest.main()
