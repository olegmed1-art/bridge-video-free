"""No-network checks of the separate one-shot approval dispatcher."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from universal_video.private_one_job_approver import approve_once
from universal_video.private_result_handoff import PrivateOneJobConfigurationError


class FakeCursor:
    def __init__(self, state):
        self.state = state
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        if 'AS login' in sql:
            self.row = dict(self.state.identity)
            self.state.events.append('identity')
        elif 'approve_one_job_staging_attempt_v1' in sql:
            self.state.events.append('approve')
            self.state.sql_params = params
            self.row = {'admission_id': self.state.binding['admission_id']}
        else:
            raise AssertionError(sql[:80])

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, kind, *_):
        self.state.events.append('rollback' if kind else 'commit')
        return False

    def cursor(self, **_kwargs):
        return FakeCursor(self.state)


class ApproverTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.claim_path = root / 'claim.json'
        self.signal_path = root / 'approval.json'
        self.ready_path = root / 'approval.json.ready'
        self.binding_path = root / 'binding.json'
        self.binding = dict(
            expected_database='rehearsal', expected_host='127.0.0.1',
            expected_port=5432,
            expected_approver_login='approver_login',
            expected_worker_login='worker_login',
            expected_batch_id=str(uuid4()), expected_job_id=str(uuid4()),
            expected_source_file_id='source-file-123456',
            expected_attempt_count=1, admission_id=str(uuid4()),
            source_fingerprint='c' * 64, worker_key='one-job-worker',
            claim_receipt_path=str(self.claim_path),
            approval_signal_path=str(self.signal_path),
            approver_dsn_file=str(root / 'approver-dsn'),
            approval_reference='local-synthetic-review')
        self.receipt = dict(
            admission_id=self.binding['admission_id'],
            job_id=self.binding['expected_job_id'],
            batch_id=self.binding['expected_batch_id'],
            lease_token=str(uuid4()), worker_key='one-job-worker',
            attempt_count=1, source_file_id='source-file-123456',
            source_fingerprint='c' * 64)
        self.identity = dict(
            login='approver_login', active_role='approver_login',
            database_name='rehearsal', server_now=datetime.now(timezone.utc),
            approver_member=True, owner_member=False, worker_member=False,
            collector_member=False, admitter_member=False,
            broad_worker_member=False, app_member=False, elevated=False,
            queue_usage=True, approve_execute=True, stage_execute=False,
            pipeline_dml=False)
        self.events = []
        self.sql_params = None
        self.write_files()

    def write_files(self):
        self.binding_path.write_text(json.dumps(self.binding), encoding='utf-8')
        self.claim_path.write_text(json.dumps(dict(
            self.receipt, claim_state='COMMIT_ACKNOWLEDGED')), encoding='utf-8')
        self.ready_path.write_text(json.dumps(self.receipt), encoding='utf-8')
        for path in (self.binding_path, self.claim_path, self.ready_path):
            os.chmod(path, 0o600)

    def invoke(self):
        file_fixture = (patch(
            'universal_video.private_one_job_approver._read_local_json',
            side_effect=lambda path: json.loads(path.read_text())
            if path.exists() else None) if sys.platform == 'win32'
            else nullcontext())
        with patch.dict(os.environ, {}, clear=True), file_fixture, \
             patch('universal_video.private_one_job_approver._read_approver_dsn',
                   return_value='postgresql://approver_login@127.0.0.1:5432/rehearsal'), \
             patch('psycopg.connect', return_value=FakeConnection(self)):
            return approve_once(str(self.binding_path))

    def test_exact_once_approval_after_ready_and_separate_identity(self):
        result = self.invoke()
        self.assertEqual(result['status'], 'APPROVED_ONCE')
        self.assertEqual(self.events, ['identity', 'approve', 'commit'])
        signal = json.loads(self.signal_path.read_text())
        self.assertEqual(signal['lease_token'], self.receipt['lease_token'])
        self.assertEqual(signal['lease_approval_id'], result['lease_approval_id'])
        self.assertEqual(self.sql_params[2:7], (
            self.binding['expected_job_id'], self.receipt['lease_token'],
            self.binding['worker_key'], 1, self.binding['source_fingerprint']))
        with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                    'ONE_JOB_APPROVER_SIGNAL_EXISTS'):
            self.invoke()
        self.assertEqual(self.events, ['identity', 'approve', 'commit'])

    def test_unconfirmed_claim_and_wrong_token_never_reach_sql(self):
        self.claim_path.write_text(json.dumps(dict(
            self.receipt, claim_state='PRECOMMIT_UNCONFIRMED')))
        with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                    'ONE_JOB_APPROVER_RECEIPT_NOT_EXACT'):
            self.invoke()
        self.assertEqual(self.events, [])
        self.write_files()
        self.ready_path.write_text(json.dumps(dict(
            self.receipt, lease_token=str(uuid4()))))
        with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                    'ONE_JOB_APPROVER_READY_NOT_EXACT'):
            self.invoke()
        self.assertEqual(self.events, [])

    def test_stale_ready_and_role_conflict_fail_closed(self):
        old = time.time() - 120
        os.utime(self.ready_path, (old, old))
        with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                    'ONE_JOB_APPROVER_READY_STALE'):
            self.invoke()
        self.write_files()
        self.identity['worker_member'] = True
        with self.assertRaisesRegex(PrivateOneJobConfigurationError,
                                    'ONE_JOB_APPROVER_IDENTITY_OR_ACL_INVALID'):
            self.invoke()
        self.assertEqual(self.events, ['identity', 'rollback'])
        self.assertFalse(self.signal_path.exists())


if __name__ == '__main__':
    unittest.main()
