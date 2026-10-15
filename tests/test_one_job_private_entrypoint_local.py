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
                         False,True,True,True,True,False,False,False,False,True)

    def test_selects_only_private_handoff_and_rechecks_every_connection(self):
        calls = []
        connections = []

        def connect(dsn):
            self.assertEqual(dsn, 'synthetic-queue-dsn')
            connection = Connection(self.good_row, calls)
            connections.append(connection)
            return connection

        def process(dsn, claim, worker_key, *, subset_adapter, staging_handoff):
            self.assertIsInstance(subset_adapter, SubsetWorkerAdapter)
            self.assertIs(subset_adapter.observation_provider,
                          self.adapter.observation_provider)
            self.assertTrue(callable(subset_adapter.connection_validator))
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
            self.assertIn('current_user::text = session_user::text', sql)
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
                   14: True, 15: False}
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

    def test_swapped_finish_connection_is_rejected_before_finish_sql(self):
        preflight_calls = []
        finish_calls = []
        swapped_row = list(self.good_row)
        swapped_row[0] = 'other_v2_login'
        swapped_row[14] = True  # inherited analysis_candidate INSERT

        def process(dsn, claim, worker_key, *, subset_adapter, staging_handoff):
            before = str(uuid4())
            with patch('universal_video.subset_worker._connect_subset',
                       return_value=Connection(tuple(swapped_row), finish_calls)):
                return subset_adapter.finish_private_job(
                    dsn, claim, worker_key,
                    before_work_observation_id=before,
                    admission_id=staging_handoff.admission_id,
                    source_fingerprint=staging_handoff.source_fingerprint,
                    receipt_id=str(uuid4()), outcome='REVIEW_READY',
                    output={'result_mode': 'PRIVATE_RESULT_STAGING_ONLY'})

        with patch('universal_video.private_one_job_entrypoint._connect_queue',
                   return_value=Connection(self.good_row, preflight_calls)), \
             patch('universal_video.private_one_job_entrypoint._process_private_claim',
                   side_effect=process), \
             patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                        'ONE_JOB_QUEUE_IDENTITY_OR_ACL_INVALID'):
                run_private_one_job(**self.arguments)
        self.assertEqual(len(preflight_calls), 1)
        self.assertEqual(len(finish_calls), 1)
        self.assertIn('has_any_column_privilege', finish_calls[0][0])
        self.assertTrue(finish_calls[0][0].lstrip().startswith('SELECT session_user'))

    def test_all_adapter_queue_calls_use_connection_validator(self):
        observed = []
        def process(_dsn, _claim, _worker_key, *, subset_adapter, staging_handoff):
            with patch('universal_video.subset_worker._execute',
                       side_effect=lambda dsn, sql, params, **kwargs:
                       observed.append((sql, kwargs)) or {}):
                for method, args, kwargs in (
                    (subset_adapter.before_work, (self.arguments['queue_dsn'],
                        self.claim, 'queue-worker'), {}),
                    (subset_adapter.heartbeat_job, (self.arguments['queue_dsn'],),
                        dict(job_id=self.claim['job_id'],
                             lease_token=self.claim['lease_token'],
                             worker_key='queue-worker')),
                    (subset_adapter.retry_job, (self.arguments['queue_dsn'],),
                        dict(job_id=self.claim['job_id'],
                             lease_token=self.claim['lease_token'],
                             worker_key='queue-worker', error_code='TEST')),
                    (subset_adapter.finish_job, (self.arguments['queue_dsn'],
                        self.claim, 'queue-worker'),
                        dict(before_work_observation_id=str(uuid4()),
                             outcome='AMBIGUOUS', output={})),
                    (subset_adapter.finish_private_job,
                        (self.arguments['queue_dsn'], self.claim, 'queue-worker'),
                        dict(before_work_observation_id=str(uuid4()),
                             admission_id=staging_handoff.admission_id,
                             source_fingerprint=staging_handoff.source_fingerprint,
                             receipt_id=str(uuid4()), outcome='REVIEW_READY',
                             output={})),
                ):
                    try:
                        method(*args, **kwargs)
                    except Exception:
                        pass  # Synthetic empty response; only SQL dispatch matters.
            return {'job_status': 'REVIEW_READY', 'released_jobs': 0}

        with patch('universal_video.private_one_job_entrypoint._connect_queue',
                   return_value=Connection(self.good_row, [])), \
             patch('universal_video.private_one_job_entrypoint._process_private_claim',
                   side_effect=process), \
             patch.dict(os.environ, {}, clear=True):
            run_private_one_job(**self.arguments)
        self.assertEqual(len(observed), 5)
        self.assertTrue(all(call[1].get('connection_validator') for call in observed))

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
