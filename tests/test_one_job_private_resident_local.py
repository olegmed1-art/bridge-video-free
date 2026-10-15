"""No-network one-shot resident path through the real claim and worker adapter."""
from __future__ import annotations

import os
import json
import sys
import tempfile
import types
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    sys.modules['requests'] = types.ModuleType('requests')
if sys.platform == 'win32':
    lock = types.ModuleType('universal_video.workload_lock')
    lock.shared_workload_lock = nullcontext
    sys.modules['universal_video.workload_lock'] = lock

from universal_video import neon_worker, spool_worker
from universal_video.private_one_job_entrypoint import (
    PrivateOneJobConfigurationError, _CLAIM_COLUMNS,
    _QUEUE_IDENTITY_COLUMNS,
)
from universal_video.subset_drive_observation import (
    DriveSubsetObservationProvider, SubsetDriveConfig,
    SubsetDriveObservationError,
)


class SyntheticQueue:
    def __init__(self):
        self.batch_id = str(uuid4())
        self.job_id = str(uuid4())
        self.lease_token = str(uuid4())
        self.admission_id = str(uuid4())
        self.lease_approval_id = str(uuid4())
        self.receipt_id = str(uuid4())
        self.claim = dict(zip(_CLAIM_COLUMNS, (
            self.job_id, self.batch_id, self.lease_token, 1,
            'source-folder-12345', 'output-folder-12345', 'work-folder-12345',
            neon_worker.APPROVED_PROFILE, neon_worker.APPROVED_REVISION,
            'source-file-123456', 'lesson.mp4', 'video/mp4', 1234567,
            None, 'a' * 32, False, 1,
        )))
        self.identity = dict(zip(_QUEUE_IDENTITY_COLUMNS, (
            'queue_login', 'video_rehearsal', True, False, False, False,
            False, True, True, True, True, False, False, False, False, True,
        )))
        self.approved = True
        self.lease_active = True
        self.expire_after_claim = False
        self.bad_finish = None
        self.lose_finish = False
        self.lose_claim_ack = False
        self.claim_calls = 0
        self.subset_connections = 0
        self.finished = False
        self.staged = False
        self.operations = []
        self.rollbacks = 0

    def connect_queue(self, _dsn):
        return Connection(self, 'queue')

    def connect_subset(self, _dsn):
        self.subset_connections += 1
        if self.lose_finish and self.subset_connections == 3:
            raise OSError('synthetic final connection lost')
        return Connection(self, 'subset', self.subset_connections)

    def stage(self, open_connection, **_kwargs):
        with open_connection():
            self.operations.append('stage_connection')
            if not self.approved or not self.lease_active:
                raise PrivateOneJobConfigurationError('ONE_JOB_APPROVAL_MISSING')
            self.staged = True
        with open_connection():
            self.operations.append('readback_connection')
        return dict(status='PRIVATE_RESULT_STAGED', receipt_id=self.receipt_id,
                    bundle_sha256='b' * 64)


class Connection:
    def __init__(self, state, kind, ordinal=0):
        self.state = state
        self.kind = kind
        self.ordinal = ordinal
        self.claim_selected = False

    def __enter__(self):
        return self

    def __exit__(self, error_type, *_):
        if error_type is not None:
            self.state.rollbacks += 1
        elif self.claim_selected:
            self.state.operations.append('claim_commit')
            if self.state.expire_after_claim:
                self.state.lease_active = False
            if self.state.lose_claim_ack:
                raise OSError('synthetic claim commit ACK lost')
        return False

    def cursor(self):
        return Cursor(self)


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        connection = self.connection
        state = connection.state
        if 'SELECT session_user::text AS session_login' in sql:
            self.row = dict(state.identity)
            if connection.kind == 'subset' and connection.ordinal == 3:
                if state.bad_finish == 'principal':
                    self.row['session_login'] = 'other_v2_login'
                elif state.bad_finish == 'role':
                    self.row['active_role_matches_login'] = False
            state.operations.append('identity')
        elif 'subset_claim_job' in sql:
            state.claim_calls += 1
            self.description = [SimpleNamespace(name=name) for name in _CLAIM_COLUMNS]
            self.row = None if state.finished else tuple(state.claim[name] for name in _CLAIM_COLUMNS)
            connection.claim_selected = self.row is not None
            state.operations.append('claim')
        elif 'heartbeat_subset_job' in sql:
            if not state.lease_active:
                raise RuntimeError('SUBSET_LEASE_EXPIRED')
            self.row = {'lease_expires_at': datetime.now(timezone.utc) + timedelta(minutes=15)}
            state.operations.append('heartbeat')
        elif 'authorize_subset_attempt' in sql:
            if not state.lease_active:
                raise RuntimeError('SUBSET_LEASE_EXPIRED')
            self.row = {'authorization': dict(
                status='SOURCE_AND_LEASE_VERIFIED', job_id=params[0],
                lease_token=params[1], attempt_count=1, observation_id=params[3],
                execution_allowed=False, release_allowed=False,
                database_persistence_allowed=False,
                canonical_promotion_allowed=False)}
            state.operations.append('before_work')
        elif 'finish_one_job_private_result_v1' in sql:
            if not state.lease_active or not state.approved or not state.staged:
                raise RuntimeError('ONE_JOB_APPROVAL_OR_LEASE_MISSING')
            state.finished = True
            self.row = dict(job_status='REVIEW_READY', batch_status='RUNNING',
                            released_jobs=0)
            state.operations.append('private_finish')
        elif 'retry_subset_job' in sql:
            raise RuntimeError('SUBSET_LEASE_EXPIRED')
        else:
            raise AssertionError(f'unexpected SQL: {sql[:90]}')

    def fetchone(self):
        return self.row


class ImmediateHeartbeat:
    def __init__(self, dsn, claim, worker_key, subset_adapter):
        self.dsn, self.claim, self.worker_key = dsn, claim, worker_key
        self.subset_adapter = subset_adapter
        self.error = None

    def __enter__(self):
        self.subset_adapter.heartbeat_job(
            self.dsn, job_id=self.claim['job_id'],
            lease_token=self.claim['lease_token'], worker_key=self.worker_key)
        return self

    def __exit__(self, *_):
        return False


class PrivateResidentTest(unittest.TestCase):
    def setUp(self):
        self.state = SyntheticQueue()
        self.config = SubsetDriveConfig(
            str(uuid4()), self.state.batch_id,
            'postgresql://collector@127.0.0.1:5432/video_rehearsal',
            '127.0.0.1', 'video_rehearsal')
        self.dsn = 'postgresql://queue@127.0.0.1:5432/video_rehearsal'
        self.binding = dict(
            queue_dsn=self.dsn, expected_database='video_rehearsal',
            expected_login='queue_login', expected_batch_id=self.state.batch_id,
            expected_job_id=self.state.job_id,
            expected_source_file_id='source-file-123456',
            expected_attempt_count=1, admission_id=self.state.admission_id,
            source_fingerprint='c' * 64, worker_key='queue-worker',
            claim_reserver=self.reserve_claim,
            claim_publisher=self.publish_claim,
            claim_committer=self.commit_claim,
            approval_waiter=self.wait_for_external_approval,
            processor=lambda claim: {'terminal_evidence_sha256': 'd' * 64})

    def reserve_claim(self):
        self.state.operations.append('reserve')

    def publish_claim(self, claim, worker_key, admission_id,
                      source_fingerprint):
        self.assertEqual((claim['job_id'], claim['lease_token'], worker_key,
                          admission_id, source_fingerprint),
                         (self.state.job_id, self.state.lease_token,
                          'queue-worker', self.state.admission_id, 'c' * 64))
        self.state.operations.append('claim_receipt')

    def commit_claim(self, claim):
        self.assertEqual(claim['lease_token'], self.state.lease_token)
        self.state.operations.append('claim_acknowledged')

    def wait_for_external_approval(self, claim, worker_key, admission_id,
                                   source_fingerprint):
        self.assertEqual((claim['job_id'], claim['lease_token'], worker_key,
                          admission_id, source_fingerprint),
                         (self.state.job_id, self.state.lease_token,
                          'queue-worker', self.state.admission_id, 'c' * 64))
        self.state.operations.append('approval_wait')
        if not self.state.approved:
            raise PrivateOneJobConfigurationError('ONE_JOB_APPROVAL_MISSING')
        return dict(admission_id=admission_id, job_id=claim['job_id'],
                    lease_token=claim['lease_token'], worker_key=worker_key,
                    attempt_count=claim['attempt_count'],
                    source_fingerprint=source_fingerprint,
                    lease_approval_id=self.state.lease_approval_id)

    def invoke(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch('universal_video.private_one_job_entrypoint._connect_queue',
                   side_effect=self.state.connect_queue), \
             patch('universal_video.subset_worker._connect_subset',
                   side_effect=self.state.connect_subset), \
             patch.object(neon_worker, 'shared_workload_lock',
                          side_effect=lambda **kw: (
                              self.assertEqual(kw, {'exclusive': True}) or nullcontext())), \
             patch.object(neon_worker, 'validate_video_runtime'), \
             patch.object(neon_worker, '_Heartbeat', ImmediateHeartbeat), \
             patch.object(neon_worker, '_processing_timeout', nullcontext), \
             patch.object(neon_worker, 'access_token', return_value='synthetic'), \
             patch.object(neon_worker, 'verify_claimed_source', return_value={'id': 'same'}), \
             patch.object(neon_worker, 'verify_terminal_output_live'), \
             patch.object(SubsetDriveConfig, 'from_env', return_value=self.config), \
             patch.object(SubsetDriveConfig, 'validate_target'), \
             patch('universal_video.subset_drive_observation.restricted_identity',
                   side_effect=lambda _dsn, capability: (
                       'queue_login' if capability == 'video_subset_worker_v2'
                       else 'collector_login')), \
             patch('universal_video.subset_drive_observation.DriveSubsetObservationProvider',
                   return_value=lambda *_args, **_kwargs: str(uuid4())), \
             patch('universal_video.private_result_handoff.stage_completed_drive_result_private',
                   side_effect=self.state.stage):
            return spool_worker.process_private_one_job_once(**self.binding)

    def test_success_checks_claim_heartbeat_approval_stage_and_private_finish(self):
        result = self.invoke()
        self.assertEqual(result, dict(job_status='REVIEW_READY',
                                      batch_status='RUNNING', released_jobs=0))
        self.assertEqual(self.state.claim_calls, 1)
        self.assertEqual([op for op in self.state.operations if op in
                          ('reserve','claim','claim_receipt','heartbeat','before_work','approval_wait',
                           'stage_connection','readback_connection','private_finish')],
                         ['reserve','claim','claim_receipt','heartbeat','before_work','approval_wait',
                          'stage_connection','readback_connection','private_finish'])
        self.assertLess(self.state.operations.index('identity'),
                        self.state.operations.index('reserve'))
        self.assertLess(self.state.operations.index('reserve'),
                        self.state.operations.index('claim'))
        self.assertLess(self.state.operations.index('claim_receipt'),
                        self.state.operations.index('claim_commit'))
        self.assertLess(self.state.operations.index('claim_commit'),
                        self.state.operations.index('claim_acknowledged'))

    def test_lost_claim_commit_ack_retains_precommit_receipt_and_never_reclaims(self):
        self.state.lose_claim_ack = True
        with self.assertRaisesRegex(OSError, 'claim commit ACK lost'):
            self.invoke()
        self.assertIn('claim_receipt', self.state.operations)
        self.assertIn('claim_commit', self.state.operations)
        self.assertNotIn('claim_acknowledged', self.state.operations)
        self.assertNotIn('heartbeat', self.state.operations)
        self.assertEqual(self.state.claim_calls, 1)

    def test_receipt_publication_failure_rolls_back_claim(self):
        self.binding['claim_publisher'] = lambda *_: (_ for _ in ()).throw(
            PrivateOneJobConfigurationError('ONE_JOB_CLAIM_RECEIPT_WRITE_FAILED'))
        with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                    'ONE_JOB_CLAIM_RECEIPT_WRITE_FAILED'):
            self.invoke()
        self.assertEqual(self.state.rollbacks, 1)
        self.assertNotIn('claim_commit', self.state.operations)
        self.assertNotIn('heartbeat', self.state.operations)

    def test_queue_acl_preflight_failure_leaves_no_reservation_or_claim(self):
        self.state.identity['is_v2_member'] = False
        with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                    'ONE_JOB_QUEUE_IDENTITY_OR_ACL_INVALID'):
            self.invoke()
        self.assertNotIn('reserve', self.state.operations)
        self.assertEqual(self.state.claim_calls, 0)

    def test_missing_receipt_parent_stops_before_claim(self):
        from universal_video.private_one_job_operator import run_from_binding_file
        # The Windows sandbox reports POSIX mode bits as world-writable even
        # after chmod. Keep the production file check and substitute only the
        # file decoder for this path-preflight test.
        file_fixture = (patch(
            'universal_video.private_one_job_operator._read_local_json',
            side_effect=lambda path: json.loads(path.read_text())
            if path.exists() else None) if sys.platform == 'win32'
            else nullcontext())
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as root, patch.dict(os.environ, {
                'BRIDGE_VIDEO_QUEUE_DATABASE_URL': self.dsn}, clear=True), file_fixture:
            binding_path = Path(root) / 'binding.json'
            binding = {key: value for key, value in self.binding.items()
                       if key not in ('queue_dsn', 'claim_publisher',
                                      'claim_committer', 'claim_reserver',
                                      'approval_waiter', 'processor')}
            binding['claim_receipt_path'] = str(Path(root) / 'absent' / 'claim.json')
            binding['approval_signal_path'] = str(Path(root) / 'approval.json')
            binding_path.write_text(json.dumps(binding), encoding='utf-8')
            os.chmod(binding_path, 0o600)
            with patch('universal_video.spool_worker.process_private_one_job_once') as run:
                with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                            'ONE_JOB_HANDOFF_DIRECTORY_UNAVAILABLE'):
                    run_from_binding_file(str(binding_path))
                run.assert_not_called()

    def test_failed_runtime_preflight_does_not_wedge_corrected_rerun(self):
        from universal_video.private_one_job_operator import run_from_binding_file
        file_fixture = (patch(
            'universal_video.private_one_job_operator._read_local_json',
            side_effect=lambda path: json.loads(path.read_text())
            if path.exists() else None) if sys.platform == 'win32'
            else nullcontext())
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as root, patch.dict(os.environ, {
                'BRIDGE_VIDEO_QUEUE_DATABASE_URL': self.dsn}, clear=True), file_fixture:
            claim_path = Path(root) / 'claim.json'
            binding_path = Path(root) / 'binding.json'
            binding = {key: value for key, value in self.binding.items()
                       if key not in ('queue_dsn', 'claim_publisher',
                                      'claim_committer', 'claim_reserver',
                                      'approval_waiter', 'processor')}
            binding['claim_receipt_path'] = str(claim_path)
            binding['approval_signal_path'] = str(Path(root) / 'approval.json')
            binding_path.write_text(json.dumps(binding), encoding='utf-8')
            os.chmod(binding_path, 0o600)
            with patch('universal_video.spool_worker.process_private_one_job_once',
                       side_effect=PrivateOneJobConfigurationError(
                           'ONE_JOB_QUEUE_IDENTITY_OR_ACL_INVALID')):
                with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                            'ONE_JOB_QUEUE_IDENTITY_OR_ACL_INVALID'):
                    run_from_binding_file(str(binding_path))
            self.assertFalse(claim_path.exists())

            def after_preflight(**kwargs):
                kwargs['claim_reserver']()
                return 'reserved-before-claim'

            with patch('universal_video.spool_worker.process_private_one_job_once',
                       side_effect=after_preflight):
                self.assertEqual(run_from_binding_file(str(binding_path)),
                                 'reserved-before-claim')
            self.assertEqual(json.loads(claim_path.read_text())['claim_state'],
                             'RESERVED')

    def test_receipt_journal_preserves_token_until_ambiguous_commit_reconciled(self):
        from universal_video.private_one_job_operator import ClaimReceiptJournal
        file_fixture = (patch(
            'universal_video.private_one_job_operator._read_local_json',
            side_effect=lambda path: json.loads(path.read_text())
            if path.exists() else None) if sys.platform == 'win32'
            else nullcontext())
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as root, file_fixture:
            path = Path(root) / 'claim.json'
            journal = ClaimReceiptJournal(path, dict(
                admission_id=self.state.admission_id,
                expected_job_id=self.state.job_id,
                expected_batch_id=self.state.batch_id))
            journal.reserve()
            self.assertEqual(json.loads(path.read_text())['claim_state'],
                             'RESERVED')
            journal.publish_precommit(self.state.claim, 'queue-worker',
                                      self.state.admission_id, 'c' * 64)
            uncertain = json.loads(path.read_text())
            self.assertEqual(uncertain['claim_state'], 'PRECOMMIT_UNCONFIRMED')
            self.assertEqual(uncertain['lease_token'], self.state.lease_token)
            with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                        'ONE_JOB_CLAIM_RECEIPT_WRITE_FAILED'):
                journal.reserve()
            journal.acknowledge_commit(self.state.claim)
            self.assertEqual(json.loads(path.read_text())['claim_state'],
                             'COMMIT_ACKNOWLEDGED')

    def test_finish_principal_or_active_role_change_stops_before_finish_sql(self):
        for change in ('principal', 'role'):
            with self.subTest(change=change):
                self.setUp()
                self.state.bad_finish = change
                with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                            'ONE_JOB_QUEUE_IDENTITY_OR_ACL_INVALID'):
                    self.invoke()
                self.assertTrue(self.state.staged)
                self.assertNotIn('private_finish', self.state.operations)

    def test_expired_lease_stops_before_processing(self):
        self.state.expire_after_claim = True
        with self.assertRaisesRegex(RuntimeError, 'SUBSET_LEASE_EXPIRED'):
            self.invoke()
        self.assertNotIn('before_work', self.state.operations)
        self.assertNotIn('private_finish', self.state.operations)

    def test_different_claimed_job_rolls_back_same_transaction(self):
        self.state.claim['job_id'] = str(uuid4())
        with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                    'ONE_JOB_EXACT_CLAIM_MISMATCH'):
            self.invoke()
        self.assertEqual(self.state.claim_calls, 1)
        self.assertEqual(self.state.rollbacks, 1)
        self.assertNotIn('heartbeat', self.state.operations)

    def test_missing_external_approval_stops_before_stage_without_retry(self):
        self.state.approved = False
        with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                    'ONE_JOB_APPROVAL_MISSING'):
            self.invoke()
        self.assertIn('approval_wait', self.state.operations)
        self.assertNotIn('stage_connection', self.state.operations)
        self.assertNotIn('private_finish', self.state.operations)

    def test_replayed_approval_signal_for_other_lease_is_rejected(self):
        original = self.binding['approval_waiter']
        self.binding['approval_waiter'] = lambda *args: (
            original(*args) | {'lease_token': str(uuid4())})
        with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                    'ONE_JOB_APPROVAL_SIGNAL_NOT_EXACT'):
            self.invoke()
        self.assertNotIn('stage_connection', self.state.operations)

    def test_repeat_cannot_claim_a_second_job(self):
        self.invoke()
        with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                    'ONE_JOB_EXACT_CLAIM_MISMATCH'):
            self.invoke()
        self.assertEqual(self.state.claim_calls, 2)
        self.assertEqual(self.state.operations.count('private_finish'), 1)

    def test_lost_finish_connection_does_not_resubmit(self):
        self.state.lose_finish = True
        with self.assertRaisesRegex(OSError, 'synthetic final connection lost'):
            self.invoke()
        self.assertTrue(self.state.staged)
        self.assertEqual(self.state.operations.count('private_finish'), 0)
        self.assertEqual(self.state.claim_calls, 1)

    def test_collector_checks_principal_and_role_on_observation_connection(self):
        provider = DriveSubsetObservationProvider(
            self.config, 'queue-worker', expected_login='collector_login',
            token_source=lambda: 'synthetic',
            metadata_fetcher=lambda *_: {'id': 'source-file-123456'})
        claim = dict(self.state.claim)
        seen = []

        class CollectorConnection:
            def __init__(self, change):
                self.change = change

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, sql, params):
                if 'SELECT session_user::text AS login' in sql:
                    row = dict(login='collector_login',
                               database_name='video_rehearsal',
                               active_role_matches=True, collector_member=True,
                               worker_member=False, approver_member=False,
                               broad_worker_member=False, app_member=False,
                               elevated=False, pipeline_dml=False)
                    row.update(self.change)
                    return SimpleNamespace(fetchone=lambda: row)
                seen.append('observe')
                return SimpleNamespace(fetchone=lambda: {'observation_id': params[0]})

        for change in ({}, {'login': 'swapped_login'},
                       {'active_role_matches': False},
                       {'pipeline_dml': True}):
            with self.subTest(change=change), \
                 patch('universal_video.subset_drive_observation.psycopg.connect',
                       return_value=CollectorConnection(change)):
                if change:
                    with self.assertRaisesRegex(SubsetDriveObservationError,
                                                'VIDEO_SUBSET_COLLECTOR_IDENTITY_INVALID'):
                        provider(claim, 'queue-worker', boundary='before_work')
                else:
                    self.assertTrue(provider(claim, 'queue-worker',
                                             boundary='before_work'))
        self.assertEqual(seen, ['observe'])


if __name__ == '__main__':
    unittest.main()
