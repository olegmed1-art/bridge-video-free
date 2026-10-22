"""Focused real adapter join tests with explicit owner/DB/native boundary doubles.

Runs no transport threads, PostgreSQL, media, network, credentials or production.
The existing Harness supplies synthetic ownership/CAS/window/journal capabilities.
Only while exercising the native completion branch, its principal descriptor is
an explicit boundary double. These tests do not establish any UID/DB authority.
"""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.native_video_recovery_fixture import Harness
from tests.test_native_video_terminal_lifecycle import PgCompletionProtocolDouble
from universal_video import diana20_transport_protocol as p
from universal_video import native_video_binding_provider as native
from universal_video.native_video_terminal import NativeVideoTerminalCompletion
from universal_video.owner_assignment_enrollment import EnrollmentTarget
from universal_video.owner_effects import OwnerEffectRefused
from universal_video.receipt_encoding import canonical_bytes, json_sha256, sha256, PROFILE
from universal_video.terminal_finish_contract import ARTIFACT_KEYS, project_private_output
from database.video_post_finish_receipt import StagedPostFinishReceiptReader


class NativeTerminalJoinTests(unittest.TestCase):
    def prepared(self):
        h = Harness(self)
        h.selected.update({k: ('artifact_id_001' if k.endswith('_id') else 'c'*64) for k in ARTIFACT_KEYS})
        h.start()
        h.bind()
        h.stage()
        h.finish()
        assignment = h.prepared.handoff.assignment
        v = assignment.value['video']
        queue = dict(job_status='REVIEW_READY',batch_status='REVIEW',released_jobs=0)
        body = dict(schema='diana20-terminal-finish/v1',encoding=PROFILE,
            selector_sha256=assignment.selector_sha256,assignment_sha256=assignment.sha256,
            job_id=v['job_id'],attempt_count=v['attempt_count'],
            former_video_lease_token=v['lease_token'],source_file_id=v['source_file_id'],
            generation_sha256=assignment.value['generation_sha256'],terminal_event_id=17,
            selected_terminal_sha256=json_sha256(h.selected),
            persisted_output_sha256=json_sha256(project_private_output(h.selected,h.bundle)),
            queue_result=queue,queue_result_sha256=json_sha256(queue),private_bundle=h.bundle,
            artifact={k:h.selected[k] for k in ARTIFACT_KEYS})
        raw = canonical_bytes(body)

        class CompletionPg(PgCompletionProtocolDouble):
            @contextmanager
            def transaction(self):
                with super().transaction():
                    yield self
                h.events.append('native.COMMIT_ACK')

        pg = CompletionPg()
        task = assignment.value['autopilot']
        pg.result.update(task_id=task['task_id'],lease_epoch=task['lease_epoch'],
            job_id=v['job_id'],selector_sha256=assignment.selector_sha256,
            assignment_sha256=assignment.sha256,terminal_receipt_sha256=sha256(raw))
        target = EnrollmentTarget('neondb',native.OWNER,native.WORKER,native.HOST,5432,'f'*64)
        session = native.NativeVideoSession(connection=pg,target=target)
        completer = NativeVideoTerminalCompletion(session=session,task_id=task['task_id'],
            lease_epoch=task['lease_epoch'],enabled=True)
        h.adapter.native_completion = h.adapter._completion_binding = completer
        return h, pg, raw

    @contextmanager
    def native_branch(self, h):
        """Principal boundary DOUBLE only; no changed production authority."""
        descriptor = SimpleNamespace(value=h.intent.value | {'worker_login':native.WORKER},
            sha256=h.intent.sha256)
        with patch.object(h.adapter,'intent',descriptor):
            yield

    @contextmanager
    def receipt_reader(self, h, raw):
        def read(reader,*_):
            original = reader._get()
            original._verified = (raw,sha256(raw))  # Explicit SQL/readback boundary double.
            h.counts['read'] += 1
            h.events.append('receipt.verified')
            return raw
        @contextmanager
        def ack_scope(_reader,_raw):
            h.events.append('db.auth.open')
            try:
                yield
            finally:
                h.events.append('db.auth.close')
        with patch.object(StagedPostFinishReceiptReader,'__call__',read), \
             patch.object(StagedPostFinishReceiptReader,'ack_scope',ack_scope):
            yield

    def ack_native(self, h, raw):
        # Retain the original synthetic head identity while publishing phases;
        # switch only the principal descriptor at the actual native branch.
        original = h.adapter._complete_native
        def complete(value,reader):
            with self.native_branch(h):
                return original(value,reader)
        def new_id(scope):
            h.events.append('ack.id')
            return h.new_id(scope)
        h.adapter.owner = replace(h.caps,new_ack_id=new_id)
        with self.receipt_reader(h,raw), patch.object(h.adapter,'_complete_native',side_effect=complete):
            h.adapter.ack(h.f.claim,h.selected,{},h.adapter.reader,h.transport())

    def test_real_completion_commits_after_verified_receipt_before_any_ack(self):
        h, pg, raw = self.prepared()
        self.ack_native(h,raw)
        self.assertLess(h.events.index('receipt.verified'),h.events.index('native.COMMIT_ACK'))
        self.assertLess(h.events.index('READ_VERIFIED'),h.events.index('native.COMMIT_ACK'))
        self.assertLess(h.events.index('native.COMMIT_ACK'),h.events.index('ack.id'))
        self.assertLess(h.events.index('native.COMMIT_ACK'),h.events.index('ack.send'))
        self.assertEqual(h.counts['ack'],1)
        self.assertEqual(pg.events.count('COMMIT_ACK'),1)
        self.assertEqual(p.parse(h.adapter.head_raw)['phase'],'ACKED')

    def test_completion_unknown_never_allocates_or_sends_ack_and_cannot_replay(self):
        h, pg, raw = self.prepared()
        pg.fail_commit = True
        with self.assertRaisesRegex(OwnerEffectRefused,'UNKNOWN_NO_RETRY'):
            self.ack_native(h,raw)
        self.assertEqual(h.counts['ids'],0)
        self.assertEqual(h.counts['ack'],0)
        self.assertNotIn('ack.send',h.events)
        self.assertEqual(p.parse(h.adapter.head_raw)['phase'],'READ_VERIFIED')
        before = len(pg.events)
        with self.assertRaisesRegex(RuntimeError,'ADAPTER_UNKNOWN_NO_RETRY'):
            self.ack_native(h,raw)
        self.assertEqual(len(pg.events),before)

    def test_helper_refuses_unverified_or_substituted_receipt_before_sql(self):
        h, pg, raw = self.prepared()
        original = h.adapter.reader._get()
        original._verified = (raw,sha256(raw))
        with self.native_branch(h):
            with self.assertRaisesRegex(RuntimeError,'VERIFIED_RECEIPT_REQUIRED'):
                h.adapter._complete_native(raw+b' ',h.adapter.reader)
            original._verified = None
            with self.assertRaisesRegex(RuntimeError,'VERIFIED_RECEIPT_REQUIRED'):
                h.adapter._complete_native(raw,h.adapter.reader)
        self.assertEqual(pg.events,[])

    def test_helper_requires_original_typed_completer_and_reader_handoff(self):
        h, pg, raw = self.prepared()
        with self.native_branch(h):
            with self.assertRaisesRegex(RuntimeError,'COMPLETION_BINDING'):
                h.adapter._complete_native(raw,SimpleNamespace(handoff=h.prepared.handoff))
            with patch.object(h.adapter,'native_completion',object()):
                with self.assertRaisesRegex(RuntimeError,'COMPLETION_BINDING'):
                    h.adapter._complete_native(raw,h.adapter.reader)
        self.assertEqual(pg.events,[])

    def test_native_constructor_refuses_missing_or_disabled_completion_before_arm(self):
        h = Harness(self)
        native_intent = p.Intent(p.canonical(h.intent.value | {'worker_login':native.WORKER}))
        with self.assertRaisesRegex(RuntimeError,'NATIVE_COMPLETION_REQUIRED'):
            h.new_adapter(intent=native_intent)
        self.assertEqual(h.heads,{})
        self.assertEqual(h.counts['claim'],0)

    def test_owner_factory_supplies_preclaim_port_without_assignment_or_sql(self):
        from universal_video.diana20_terminal_queue import TerminalRecoveryAdapter
        h = Harness(self)
        pg = PgCompletionProtocolDouble()
        target = EnrollmentTarget('neondb',native.OWNER,native.WORKER,native.HOST,5432,'f'*64)
        session = native.NativeVideoSession(connection=pg,target=target)
        task = h.f.assignment.value['autopilot']['task_id']
        port = session.terminal_completion(task)
        self.assertIs(type(port),NativeVideoTerminalCompletion)
        self.assertFalse(port.enabled)
        native_intent = p.Intent(p.canonical(h.intent.value | {'worker_login':native.WORKER}))
        args = dict(owner=h.caps,retention=h.adapter.retention,journal=h.journal,
            intent=native_intent,namespace='synthetic-approved-namespace',recovery_ports=h.adapter.ports)
        with self.assertRaisesRegex(RuntimeError,'NATIVE_COMPLETION_REQUIRED'):
            TerminalRecoveryAdapter(**args,native_completion=port)
        enabled = session.terminal_completion(task,enabled=True)
        joined = TerminalRecoveryAdapter(**args,native_completion=enabled)
        self.assertIs(joined.native_completion,enabled)
        self.assertIsNone(joined.head_raw)
        self.assertIsNone(joined.prepared_job)
        self.assertEqual(h.heads,{})
        self.assertEqual(pg.events,[])
        worker = native.NativeVideoSession(connection=pg,target=target,worker=True)
        with self.assertRaisesRegex(RuntimeError,'OWNER_SESSION_REQUIRED'):
            worker.terminal_completion(task,enabled=True)
        self.assertEqual(pg.events,[])


if __name__ == '__main__':
    unittest.main()
