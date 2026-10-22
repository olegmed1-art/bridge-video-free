"""Offline client/source regressions; no PostgreSQL or production authority proof."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from universal_video import native_video_terminal as terminal
from universal_video import native_video_binding_provider as native
from universal_video.owner_assignment_enrollment import EnrollmentTarget, LOCAL_SETTINGS, SQL_IDENTITY
from universal_video.receipt_encoding import canonical_bytes, sha256, json_sha256, PROFILE
from universal_video.terminal_finish_contract import (
    Assignment, PINS, ARTIFACT_KEYS, TerminalFinishRefused, project_private_output)


TASK = '00000000-0000-4000-8000-000000000080'
JOB = '00000000-0000-4000-8000-000000000081'
RAW = canonical_bytes({'proof': 'verified by the original terminal verifier'})


class PgCompletionProtocolDouble:
    def __init__(self):
        self.info = SimpleNamespace(host=native.HOST, port=5432,
            transaction_status=SimpleNamespace(name='IDLE'))
        self.events = []
        self.rows = []
        self.fail_commit = False
        self.principal = native.OWNER
        self.result = dict(schema='native-video-completion/v1', status='DONE',
            task_id=TASK, lease_epoch=1, job_id=JOB, selector_sha256='1'*64,
            assignment_sha256='2'*64, terminal_receipt_sha256=sha256(RAW),
            completed_at_utc='2026-10-22T00:00:00.000000Z',
            processing_persisted=True, delivery_state='NOT_ATTESTED')

    @contextmanager
    def transaction(self):
        self.events.append('BEGIN')
        self.info.transaction_status.name = 'INTRANS'
        try:
            yield self
            self.events.append('COMMIT_ATTEMPT')
            if self.fail_commit:
                raise OSError('private lost COMMIT response')
            self.events.append('COMMIT_ACK')
        finally:
            self.info.transaction_status.name = 'IDLE'

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, sql, params=None):
        self.events.append((sql, params))
        if sql in LOCAL_SETTINGS:
            self.rows = []
        elif sql == SQL_IDENTITY:
            self.rows = [dict(database_name='neondb', session_login=self.principal,
                effective_login=self.principal, backend_pid=123)]
        elif sql == terminal.COMPLETE_SQL:
            self.rows = [{'result': self.result.copy()}]
        else:
            raise AssertionError('Unexpected SQL in offline protocol test')

    def fetchall(self):
        return self.rows


class TerminalLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.pg = PgCompletionProtocolDouble()
        target = EnrollmentTarget('neondb', native.OWNER, native.WORKER, native.HOST, 5432, 'f'*64)
        self.session = native.NativeVideoSession(connection=self.pg, target=target)
        self.assignment = SimpleNamespace(selector_sha256='1'*64, sha256='2'*64,
            value={'video': {'job_id': JOB}, 'autopilot': {'task_id': TASK, 'lease_epoch': 1}})
        self.verify = patch.object(terminal, 'verify_terminal_response', return_value=(RAW, {}))
        self.verifier = self.verify.start()
        self.addCleanup(self.verify.stop)

    def adapter(self, enabled=True):
        return terminal.NativeVideoTerminalCompletion(session=self.session, task_id=TASK,
            lease_epoch=1, enabled=enabled)

    def complete(self, adapter):
        return adapter.complete({'response': 'fixture'}, assignment=self.assignment,
            selected={'selected': True}, bundle={'bundle': True})

    def test_disabled_default_has_no_sql(self):
        adapter = terminal.NativeVideoTerminalCompletion(session=self.session, task_id=TASK, lease_epoch=1)
        with self.assertRaisesRegex(terminal.NativeCompletionRefused, 'DISABLED'):
            self.complete(adapter)
        self.assertEqual(self.pg.events, [])

    def test_actual_verifier_precedes_sql_and_exact_parameterized_rpc(self):
        result = self.complete(self.adapter())
        self.verifier.assert_called_once()
        calls = [e for e in self.pg.events if isinstance(e, tuple) and e[0] == terminal.COMPLETE_SQL]
        self.assertEqual(calls, [(terminal.COMPLETE_SQL, (TASK, 1, RAW.decode(), sha256(RAW)))])
        self.assertEqual(result, self.pg.result)
        self.assertTrue(result['processing_persisted'])
        self.assertEqual(result['delivery_state'], 'NOT_ATTESTED')
        self.assertEqual(self.pg.events[-1], 'COMMIT_ACK')

    def test_wrong_task_or_epoch_never_calls_sql(self):
        self.assignment.value['autopilot']['lease_epoch'] = 2
        with self.assertRaisesRegex(terminal.NativeCompletionRefused, 'ASSIGNMENT'):
            self.complete(self.adapter())
        self.assertEqual(self.pg.events, [])

    def test_known_completion_duplicate_returns_detached_data_without_second_effect(self):
        adapter = self.adapter()
        first = self.complete(adapter)
        first['status'] = 'tampered'
        second = self.complete(adapter)
        self.assertEqual(second['status'], 'DONE')
        self.assertEqual(self.pg.events.count('COMMIT_ACK'), 1)

    def test_lost_commit_is_unknown_and_cannot_replay(self):
        adapter = self.adapter()
        self.pg.fail_commit = True
        with self.assertRaisesRegex(terminal.NativeCompletionRefused, 'UNKNOWN_NO_RETRY'):
            self.complete(adapter)
        before = len(self.pg.events)
        with self.assertRaisesRegex(terminal.NativeCompletionRefused, 'UNKNOWN_NO_RETRY'):
            self.complete(adapter)
        self.assertEqual(len(self.pg.events), before)
        self.assertTrue(self.session.failed)

    def test_response_binding_tamper_poisoned_before_commit(self):
        self.pg.result['lease_epoch'] = 2
        with self.assertRaisesRegex(terminal.NativeCompletionRefused, 'UNKNOWN_NO_RETRY'):
            self.complete(self.adapter())
        self.assertNotIn('COMMIT_ACK', self.pg.events)

    def test_worker_and_rebound_connection_refused(self):
        self.session.worker = True
        with self.assertRaisesRegex(terminal.NativeCompletionRefused, 'OWNER'):
            self.adapter()
        self.assertEqual(self.pg.events, [])

    def test_delivery_confirmation_cannot_be_invented_by_rpc_response(self):
        self.pg.result['delivery_state'] = 'DELIVERED'
        with self.assertRaisesRegex(terminal.NativeCompletionRefused, 'UNKNOWN_NO_RETRY'):
            self.complete(self.adapter())


class TerminalSqlSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = (Path(__file__).resolve().parents[1] / 'database/proposals/0065_native_video_terminal_v1.sql').read_text()

    def test_server_identity_and_no_grant(self):
        self.assertIn('PERFORM autopilot._native_guard_v1(true)', self.sql)
        self.assertIn('SECURITY DEFINER SET search_path=pg_catalog,pg_temp', self.sql)
        self.assertIn('REVOKE ALL ON FUNCTION autopilot.complete_native_video_task_v1', self.sql)
        self.assertNotRegex(self.sql, r'(?im)^\s*GRANT\s')

    def test_completion_requires_exact_receipt_and_live_first_lease(self):
        for token in ['NATIVE_TERMINAL_RECEIPT', 'NATIVE_TERMINAL_TASK_LEASE',
            'NATIVE_TERMINAL_REVOKED', "pg_xact_status(n.binding_xid)",
            "envelope->>'receipt_text' IS DISTINCT FROM p_receipt_text",
            "a#>>'{autopilot,task_id}' IS DISTINCT FROM p_task::text",
            "a#>'{autopilot,lease_epoch}' IS DISTINCT FROM to_jsonb(p_epoch)",
            "SET status='DONE'", "'NOT_ATTESTED'", "t.status='DONE'"]:
            self.assertIn(token, self.sql)
        self.assertLess(self.sql.index("IF t.status='DONE'"), self.sql.index("RAISE EXCEPTION 'NATIVE_TERMINAL_TASK_LEASE'"))

    def test_no_finish_claim_or_retry_effect_in_completion_rpc(self):
        body = self.sql[self.sql.index('CREATE FUNCTION autopilot.complete_native_video_task_v1'):]
        for call in ['finish_one_job_private_result_v2(', 'subset_claim_job(', 'claim_native_video_job_v1(', 'retry_subset_job(']:
            self.assertNotIn(call, body)

    def test_replacement_immutable_guard_preserves_original_protected_fields(self):
        original = (Path(__file__).resolve().parents[1] / 'database/proposals/0064_native_video_binding_v1.sql').read_text()
        for field in ['task_claim_xid','video_claim_xid','video_claim','binding_text','binding_sha256',
            'claim_ack_sha256','binding_xid','expires_at','assignment_text','assignment_sha256','revoked_at']:
            self.assertIn("'" + field + "'", self.sql)
            self.assertIn("'" + field + "'", original)
        self.assertIn("oldj->k<>'null'::jsonb AND oldj->k IS DISTINCT FROM newj->k", self.sql)


class RealTerminalVerifierJoinTests(unittest.TestCase):
    """Actual Assignment/hash/projection checks, synthetic data; no DB authority."""
    def setUp(self):
        a = {k: str(i % 10)*64 for i,k in enumerate(PINS)}
        a.update(schema='diana20-owner-assignment/v1', unit='school-autopilot-production-light.service',
            autopilot=dict(task_id=TASK,worker_id=native.WORKER_KEY,lease_epoch=1,
                intake_source_revision='sha256:'+'a'*64),
            video=dict(job_id=JOB,batch_id='00000000-0000-4000-8000-000000000082',
                source_file_id='source_file_001',lease_token='00000000-0000-4000-8000-000000000083',
                attempt_count=1,worker_key=native.WORKER_KEY,stable_job_key='b'*32,
                algorithm_revision='synthetic-test-only',output_folder_id='output_folder_001'),
            admission_id='00000000-0000-4000-8000-000000000084',policy_id='synthetic-test-only',policy_epoch=1)
        self.assignment = Assignment.from_mapping(a)
        self.selected = {k: ('artifact_id_001' if k.endswith('_id') else 'c'*64) for k in ARTIFACT_KEYS}
        self.selected.update(result_mode='SHADOW_REVIEW_ONLY',database_persistence_allowed=False,
            canonical_promotion_allowed=False)
        self.bundle = dict(status='PRIVATE_RESULT_STAGED',receipt_id='00000000-0000-4000-8000-000000000085',
            bundle_sha256='d'*64,master_json_sha256='e'*64,ai_done_sha256='f'*64)
        queue = dict(job_status='REVIEW_READY',batch_status='REVIEW',released_jobs=0)
        body = dict(schema='diana20-terminal-finish/v1',encoding=PROFILE,
            selector_sha256=self.assignment.selector_sha256,assignment_sha256=self.assignment.sha256,
            job_id=JOB,attempt_count=1,former_video_lease_token=a['video']['lease_token'],
            source_file_id=a['video']['source_file_id'],generation_sha256=a['generation_sha256'],
            terminal_event_id=17,selected_terminal_sha256=json_sha256(self.selected),
            persisted_output_sha256=json_sha256(project_private_output(self.selected,self.bundle)),
            queue_result=queue,queue_result_sha256=json_sha256(queue),
            private_bundle=self.bundle,artifact={k:self.selected[k] for k in ARTIFACT_KEYS})
        self.raw = canonical_bytes(body)
        self.response = dict(status='COMMITTED_TERMINAL_RESULT',receipt_text=self.raw.decode(),receipt_sha256=sha256(self.raw))
        self.pg = PgCompletionProtocolDouble()
        self.pg.result.update(selector_sha256=self.assignment.selector_sha256,
            assignment_sha256=self.assignment.sha256,terminal_receipt_sha256=sha256(self.raw))
        target = EnrollmentTarget('neondb',native.OWNER,native.WORKER,native.HOST,5432,'f'*64)
        session = native.NativeVideoSession(connection=self.pg,target=target)
        self.adapter = terminal.NativeVideoTerminalCompletion(session=session,task_id=TASK,lease_epoch=1,enabled=True)

    def test_real_verified_receipt_reaches_exact_owner_rpc(self):
        result = self.adapter.complete(self.response,assignment=self.assignment,selected=self.selected,bundle=self.bundle)
        self.assertEqual(result['terminal_receipt_sha256'],sha256(self.raw))
        self.assertEqual(self.pg.events.count('COMMIT_ACK'),1)

    def test_selected_artifact_substitution_refuses_before_sql(self):
        self.selected['ai_done_drive_id']='different_artifact_001'
        with self.assertRaisesRegex(TerminalFinishRefused,'TERMINAL_OUTPUT_BINDING'):
            self.adapter.complete(self.response,assignment=self.assignment,selected=self.selected,bundle=self.bundle)
        self.assertEqual(self.pg.events,[])

    def test_response_hash_substitution_refuses_before_sql(self):
        self.response['receipt_sha256']='0'*64
        with self.assertRaisesRegex(TerminalFinishRefused,'TERMINAL_RECEIPT_HASH'):
            self.adapter.complete(self.response,assignment=self.assignment,selected=self.selected,bundle=self.bundle)
        self.assertEqual(self.pg.events,[])


if __name__ == '__main__':
    unittest.main()
