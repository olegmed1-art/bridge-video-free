"""Opt-in queue path checks; resident remains disconnected from this handoff."""
import unittest
import sys
import types
import os
from contextlib import nullcontext
from unittest.mock import patch
from uuid import uuid4

# The bundled offline Python has no requests; Drive calls are patched below.
try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    sys.modules['requests'] = types.ModuleType('requests')
if sys.platform == 'win32':
    workload_lock = types.ModuleType('universal_video.workload_lock')
    workload_lock.shared_workload_lock = nullcontext
    sys.modules['universal_video.workload_lock'] = workload_lock

from universal_video import neon_worker
from universal_video.private_result_handoff import (
    PrivateOneJobConfigurationError, PrivateResultStagingHandoff,
)
from universal_video.subset_worker import SubsetWorkerAdapter


class NoHeartbeat:
    error = None

    def __init__(self, *_):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeSubset:
    def __init__(self):
        self.finished = None
        self.connection_validator = lambda connection: None

    def before_work(self, *_):
        return str(uuid4())

    def finish_private_job(self, *args, **kwargs):
        self.finished = (args, kwargs)
        return {'job_status': 'REVIEW_READY', 'batch_status': 'RUNNING',
                'released_jobs': 0}

    def finish_job(self, *_args, **_kwargs):
        raise AssertionError('public/shadow finish selected for private result')

    def retry_job(self, *_args, **_kwargs):
        raise AssertionError('unexpected retry')


class PrivateWorkerTest(unittest.TestCase):
    def setUp(self):
        self.claim = dict(batch_id=str(uuid4()), job_id=str(uuid4()),
            lease_token=str(uuid4()), stable_job_key='stable-job',
            source_file_id='source-file-123456', algorithm_revision='test-revision',
            attempt_count=1, source_folder_id='source-folder-12345',
            output_folder_id='output-folder-12345', work_folder_id='work-folder-12345',
            sequence=1)
        self.admission_id = str(uuid4())
        self.receipt_id = str(uuid4())
        self.handoff = PrivateResultStagingHandoff(
            claim=self.claim, worker_key='queue-worker',
            admission_id=self.admission_id, source_fingerprint='b' * 64,
            open_connection=lambda: None)

    def test_private_result_stages_and_finishes_without_public_writer(self):
        subset = FakeSubset()
        staged = dict(status='PRIVATE_RESULT_STAGED', receipt_id=self.receipt_id,
                      bundle_sha256='a' * 64)
        with patch.object(neon_worker, '_Heartbeat', NoHeartbeat), \
             patch.object(neon_worker, '_processing_timeout', nullcontext), \
             patch.object(neon_worker, 'access_token', return_value='synthetic'), \
             patch.object(neon_worker, 'verify_claimed_source', return_value={'id': 'same'}), \
             patch.object(neon_worker, 'verify_terminal_output_live'), \
             patch('universal_video.private_result_handoff.stage_completed_drive_result_private',
                   return_value=staged) as stage:
            result = neon_worker.process_claim(
                'synthetic-queue-dsn', self.claim, 'queue-worker',
                processor=lambda claim: {'terminal_evidence_sha256': 'c' * 64},
                subset_adapter=subset, staging_handoff=self.handoff)
        self.assertEqual(result['job_status'], 'REVIEW_READY')
        self.assertEqual(result['released_jobs'], 0)
        self.assertEqual(stage.call_count, 1)
        self.assertEqual(subset.finished[1]['receipt_id'], self.receipt_id)
        output = subset.finished[1]['output']
        self.assertEqual(output['result_mode'], 'PRIVATE_RESULT_STAGING_ONLY')
        self.assertEqual(output['private_staging'], staged)
        self.assertTrue(output['database_persistence_allowed'])
        self.assertFalse(output['canonical_promotion_allowed'])

    def test_private_handoff_requires_subset_adapter_before_processing(self):
        with self.assertRaisesRegex(neon_worker.NeonVideoWorkerError,
                                    'VIDEO_PRIVATE_RESULT_SUBSET_ADAPTER_REQUIRED'):
            neon_worker.process_claim('synthetic-queue-dsn', self.claim,
                'queue-worker', processor=lambda claim: {},
                staging_handoff=self.handoff)

    def test_private_handoff_requires_checked_queue_adapter(self):
        adapter = SubsetWorkerAdapter(
            lambda claim, worker_key, boundary: str(uuid4()))
        with self.assertRaisesRegex(neon_worker.NeonVideoWorkerError,
                                    'VIDEO_PRIVATE_RESULT_CONNECTION_VALIDATOR_REQUIRED'):
            neon_worker.process_claim('synthetic-queue-dsn', self.claim,
                'queue-worker', processor=lambda claim: {},
                subset_adapter=adapter, staging_handoff=self.handoff)

    def test_prework_acl_change_propagates_without_retry(self):
        subset = FakeSubset()
        subset.before_work = lambda *_: (_ for _ in ()).throw(
            PrivateOneJobConfigurationError('ONE_JOB_QUEUE_IDENTITY_OR_ACL_INVALID'))
        with patch.object(neon_worker, '_Heartbeat', NoHeartbeat), \
             patch.object(neon_worker, '_processing_timeout', nullcontext), \
             patch.object(neon_worker, 'access_token', return_value='synthetic'), \
             patch.object(neon_worker, 'verify_claimed_source', return_value={'id': 'same'}):
            with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                        'ONE_JOB_QUEUE_IDENTITY_OR_ACL_INVALID'):
                neon_worker.process_claim('synthetic-queue-dsn', self.claim,
                    'queue-worker', processor=lambda claim: {},
                    subset_adapter=subset, staging_handoff=self.handoff)

    def test_private_handoff_rejects_ambient_broad_writer_dsn(self):
        with patch.dict(os.environ, {'BRIDGE_WORKER_DATABASE_URL': 'synthetic-broad-dsn'}):
            with self.assertRaisesRegex(neon_worker.NeonVideoWorkerError,
                                        'VIDEO_PRIVATE_RESULT_BROAD_WRITER_DSN_FORBIDDEN'):
                neon_worker.process_claim('synthetic-queue-dsn', self.claim,
                    'queue-worker', processor=lambda claim: {},
                    subset_adapter=FakeSubset(), staging_handoff=self.handoff)

    def test_private_finish_calls_only_versioned_sql_and_distinct_observation(self):
        seen = []
        before = str(uuid4())
        after = str(uuid4())
        adapter = SubsetWorkerAdapter(
            lambda claim, worker_key, boundary: after,
            connection_validator=lambda connection: None)
        with patch('universal_video.subset_worker._execute',
                   side_effect=lambda dsn, sql, params, **kwargs: (
                       seen.append((sql, params)) or
                       {'job_status': 'REVIEW_READY', 'batch_status': 'RUNNING',
                        'released_jobs': 0})):
            row = adapter.finish_private_job(
                'synthetic-queue-dsn', self.claim, 'queue-worker',
                before_work_observation_id=before,
                admission_id=self.admission_id, source_fingerprint='b' * 64,
                receipt_id=self.receipt_id, outcome='REVIEW_READY',
                output={'result_mode': 'PRIVATE_RESULT_STAGING_ONLY'})
        self.assertEqual(row['released_jobs'], 0)
        self.assertEqual(len(seen), 1)
        self.assertIn('finish_one_job_private_result_v1', seen[0][0])
        self.assertNotIn('finish_subset_job', seen[0][0])
        self.assertEqual(seen[0][1][0], self.admission_id)
        self.assertEqual(seen[0][1][7], after)

    def test_private_finish_requires_connection_validator(self):
        adapter = SubsetWorkerAdapter(
            lambda claim, worker_key, boundary: str(uuid4()))
        with patch('universal_video.subset_worker._execute') as execute:
            with self.assertRaisesRegex(Exception,
                                        'VIDEO_PRIVATE_CONNECTION_VALIDATOR_REQUIRED'):
                adapter.finish_private_job(
                    'synthetic-queue-dsn', self.claim, 'queue-worker',
                    before_work_observation_id=str(uuid4()),
                    admission_id=self.admission_id,
                    source_fingerprint='b' * 64,
                    receipt_id=self.receipt_id, outcome='REVIEW_READY', output={})
            execute.assert_not_called()

    def test_late_acl_change_stops_without_retry_or_old_finish(self):
        with patch.object(neon_worker, '_Heartbeat', NoHeartbeat), \
             patch.object(neon_worker, '_processing_timeout', nullcontext), \
             patch.object(neon_worker, 'access_token', return_value='synthetic'), \
             patch.object(neon_worker, 'verify_claimed_source', return_value={'id': 'same'}), \
             patch.object(neon_worker, 'verify_terminal_output_live'), \
             patch('universal_video.private_result_handoff.stage_completed_drive_result_private',
                   side_effect=PrivateOneJobConfigurationError('ONE_JOB_QUEUE_IDENTITY_OR_ACL_INVALID')):
            with self.assertRaises(PrivateOneJobConfigurationError):
                neon_worker.process_claim(
                    'synthetic-queue-dsn', self.claim, 'queue-worker',
                    processor=lambda claim: {'terminal_evidence_sha256': 'c' * 64},
                    subset_adapter=FakeSubset(), staging_handoff=self.handoff)


if __name__ == '__main__':
    unittest.main()
