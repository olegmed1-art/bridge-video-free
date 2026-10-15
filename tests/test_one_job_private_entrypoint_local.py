"""No-network tests for explicit route selection and fail-closed preflight."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from universal_video.private_one_job_entrypoint import (
    PrivateOneJobConfigurationError, run_private_one_job,
)
from universal_video.private_result_handoff import PrivateResultStagingHandoff
from universal_video.subset_worker import SubsetWorkerAdapter


class Cursor:
    def __init__(self, row, calls):
        self.row = row
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, row, calls):
        self.row = row
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return Cursor(self.row, self.calls)


class EntryPointTest(unittest.TestCase):
    def setUp(self):
        self.claim = dict(batch_id=str(uuid4()), job_id=str(uuid4()),
            lease_token=str(uuid4()), attempt_count=1,
            stable_job_key='a' * 32, source_file_id='source-file-123456',
            algorithm_revision='3.1-free-r25.16',
            output_folder_id='output-folder-123456')
        self.adapter = SubsetWorkerAdapter(lambda claim, worker_key, boundary: str(uuid4()))
        self.arguments = dict(queue_dsn='synthetic-queue-dsn',
            expected_database='video_rehearsal', expected_login='queue_login',
            claim=self.claim, expected_batch_id=self.claim['batch_id'],
            expected_job_id=self.claim['job_id'],
            expected_lease_token=self.claim['lease_token'],
            expected_attempt_count=1,
            expected_source_file_id=self.claim['source_file_id'],
            worker_key='queue-worker', admission_id=str(uuid4()),
            source_fingerprint='b' * 64, subset_adapter=self.adapter)
        self.good_row = ('queue_login','video_rehearsal',True,False,False,False,
                         False,True,True,True,True,False,False,False,False)

    def test_selects_only_private_handoff_and_rechecks_every_connection(self):
        calls = []
        connections = []

        def connect(dsn):
            self.assertEqual(dsn, 'synthetic-queue-dsn')
            connection = Connection(self.good_row, calls)
            connections.append(connection)
            return connection

        def process(dsn, claim, worker_key, *, subset_adapter, staging_handoff):
            self.assertIs(subset_adapter, self.adapter)
            self.assertIsInstance(staging_handoff, PrivateResultStagingHandoff)
            self.assertEqual(dsn, self.arguments['queue_dsn'])
            self.assertEqual(claim, self.claim)
            self.assertEqual(worker_key, 'queue-worker')
            with staging_handoff.open_connection():
                pass
            with staging_handoff.open_connection():
                pass
            return {'job_status': 'REVIEW_READY', 'released_jobs': 0}

        with patch('universal_video.private_one_job_entrypoint._connect_queue',
                   side_effect=connect), \
             patch('universal_video.private_one_job_entrypoint._process_private_claim',
                   side_effect=process) as selected, \
             patch.dict(os.environ, {}, clear=True):
            result = run_private_one_job(**self.arguments)
        self.assertEqual(result['released_jobs'], 0)
        self.assertEqual(selected.call_count, 1)
        self.assertEqual(len(connections), 3)
        self.assertEqual(len(calls), 3)
        for sql, params in calls:
            self.assertIn('stage_one_job_private_result_v1', sql)
            self.assertIn('finish_one_job_staging_v1', sql)
            self.assertIn('has_any_column_privilege', sql)
            self.assertIn('public.analysis_candidate', params[0])
            self.assertIn('public.run_checkpoint_event', params[0])

    def test_inexact_claim_or_ambient_writer_stops_before_connect(self):
        bad_cases = (
            dict(expected_job_id=str(uuid4())),
            dict(expected_lease_token=str(uuid4())),
            dict(expected_attempt_count=2),
            dict(expected_source_file_id='different-file-123456'),
            dict(queue_dsn=''),
            dict(subset_adapter=object()),
        )
        with patch('universal_video.private_one_job_entrypoint._connect_queue') as connect, \
             patch('universal_video.private_one_job_entrypoint._process_private_claim') as process, \
             patch.dict(os.environ, {}, clear=True):
            for changes in bad_cases:
                with self.subTest(changes=changes):
                    with self.assertRaises(PrivateOneJobConfigurationError):
                        run_private_one_job(**(self.arguments | changes))
            self.assertEqual(connect.call_count, 0)
            self.assertEqual(process.call_count, 0)
        with patch('universal_video.private_one_job_entrypoint._connect_queue') as connect, \
             patch('universal_video.private_one_job_entrypoint._process_private_claim') as process, \
             patch.dict(os.environ, {'BRIDGE_WORKER_DATABASE_URL': 'synthetic-broad-dsn'}):
            with self.assertRaises(PrivateOneJobConfigurationError):
                run_private_one_job(**self.arguments)
            self.assertEqual(connect.call_count, 0)
            self.assertEqual(process.call_count, 0)

    def test_wrong_login_role_acl_or_old_finish_never_selects_route(self):
        invalid = {0: 'broad_writer', 1: 'other_db', 2: False, 3: True,
                   4: True, 5: True, 6: True, 7: False, 8: False,
                   9: False, 10: False, 11: True, 12: True, 13: True,
                   14: True}
        for index, value in invalid.items():
            with self.subTest(index=index):
                row = list(self.good_row)
                row[index] = value
                calls = []
                with patch('universal_video.private_one_job_entrypoint._connect_queue',
                           return_value=Connection(tuple(row), calls)), \
                     patch('universal_video.private_one_job_entrypoint._process_private_claim') as process, \
                     patch.dict(os.environ, {}, clear=True):
                    with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                                'ONE_JOB_QUEUE_IDENTITY_OR_ACL_INVALID'):
                        run_private_one_job(**self.arguments)
                    self.assertEqual(process.call_count, 0)

    def test_changed_identity_on_result_connection_fails_without_fallback(self):
        rows = [self.good_row, tuple(['broad_writer'] + list(self.good_row[1:]))]
        selected = []

        def connect(_dsn):
            return Connection(rows.pop(0), [])

        def process(_dsn, _claim, _worker_key, *, subset_adapter, staging_handoff):
            selected.append(staging_handoff)
            with staging_handoff.open_connection():
                pass
            raise AssertionError('must not reach result write')

        with patch('universal_video.private_one_job_entrypoint._connect_queue',
                   side_effect=connect), \
             patch('universal_video.private_one_job_entrypoint._process_private_claim',
                   side_effect=process), \
             patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                        'ONE_JOB_QUEUE_IDENTITY_OR_ACL_INVALID'):
                run_private_one_job(**self.arguments)
        self.assertEqual(len(selected), 1)
        self.assertEqual(rows, [])

    def test_missing_queue_connection_never_processes_or_falls_back(self):
        with patch('universal_video.private_one_job_entrypoint._connect_queue',
                   side_effect=RuntimeError('synthetic unavailable')), \
             patch('universal_video.private_one_job_entrypoint._process_private_claim') as process, \
             patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                        'ONE_JOB_QUEUE_CONNECTION_FAILED'):
                run_private_one_job(**self.arguments)
            self.assertEqual(process.call_count, 0)


if __name__ == '__main__':
    unittest.main()
