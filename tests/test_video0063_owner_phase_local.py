"""New local APIs only. Authority, Directory/fsync and DB rows are DOUBLES.

No PG/runtime/OS identity proof; original P01-P16 are not invoked here.
"""
import copy
from contextlib import contextmanager
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4
from types import SimpleNamespace

from tests import test_one_job_post_finish_entrypoint_local as _fixture
from universal_video import diana20_transport_protocol as p
from universal_video import private_one_job_entrypoint as entry
from universal_video import private_one_job_operator as operator
from universal_video.private_admission_retention import AdmissionRetention, TerminalHandoffRetention
from universal_video.private_result_handoff import recover_retained_terminal
from universal_video.terminal_finish_contract import Assignment
from universal_video.receipt_encoding import canonical_bytes
from database.video_post_finish_receipt import PostFinishReceiptReader, SQL_AUTH


def fixture():
    f = _fixture.PostFinishEntryPointTests()
    f.setUp()
    f.claim['processing_profile'] = 'synthetic-profile'
    j = p.Job(p.canonical({k: f.claim[k] for k in p.CLAIM}), '2'*64, '3'*64,
        '4'*64, 'synthetic-policy', 1, (p.Frame('one', p.digest(b'x'), 0, 1),))
    a = f.assignment.value
    a.update(source_sha256=p.SOURCE, manifest_sha256=j.manifest_sha256)
    f.assignment = Assignment.from_mapping(a)
    f.rows.update({entry._QUEUE_IDENTITY_SQL: f.base, entry._POST_FINISH_IDENTITY_SQL: f.extra})
    from database.video_post_finish_receipt import SQL_ASSIGNMENT
    f.rows[SQL_ASSIGNMENT] = {'assignment': f.assignment.value}
    a = f.assignment.value
    value = dict(schema='diana20-preclaim/v1', **{k: f.claim[k] for k in
        ('job_id','batch_id','source_file_id','attempt_count','algorithm_revision','processing_profile')},
        **{k: a[k] for k in ('admission_id','source_fingerprint','source_sha256','unit_sha256',
                           'binding_sha256','generation_sha256','manifest_sha256','policy_id','policy_epoch')},
        target_identity_sha256='9'*64, worker_login='synthetic-login', worker_key='synthetic-worker')
    intent = p.Intent(p.canonical(value))
    commit = p.canonical(dict(schema='claim-commit-evidence/v1', state='COMMIT_ACKNOWLEDGED',
        claim_sha256=p.digest(canonical_bytes(f.claim, max_bytes=16384)), journal_sha256='a'*64))
    enrolled = dict(schema='enrolled-assignment/v1', intent_hash=intent.sha256, policy_sha256='b'*64,
        claim_sha256=p.digest(canonical_bytes(f.claim, max_bytes=16384)),
        assignment_text=f.assignment.raw.decode(), assignment_sha256=f.assignment.sha256,
        selector_sha256=f.assignment.selector_sha256, target_identity_sha256='9'*64,
        principal='synthetic-login', issued_at_utc='2026-10-21T12:00:00.000000Z',
        expires_at_utc='2026-10-21T12:30:00.000000Z',
        admission_record_sha256='c'*64, admission_anchor_sha256='d'*64)
    return f, j, intent, commit, p.canonical(enrolled)


class PreclaimTests(unittest.TestCase):
    def ready(self, intent):
        h = p.PreclaimHandshake(intent, 'b'*64)
        packet = h.opening()
        self.assertNotIn('lease_token', p.parse(packet)['intent'])
        h.accept_ready(p.canonical(dict(version=3, intent_sha256=intent.sha256, scope_nonce='e'*64)))
        return h

    def test_real_codecs_bind_only_matching_post_commit_data_not_authority(self):
        f,j,i,c,e = fixture()
        h = self.ready(i)
        bound = p.parse(h.bind_data(job=j, claim=f.claim, commit_evidence_raw=c,
                                   assignment=f.assignment, enrollment_raw=e))
        self.assertEqual(h.state, 'DATA_BOUND')
        self.assertEqual(bound['assignment_sha256'], f.assignment.sha256)
        self.assertNotIn('lease_token', bound)
        with self.assertRaises(p.TransportError):
            h.bind_data(job=j, claim=f.claim, commit_evidence_raw=c, assignment=f.assignment, enrollment_raw=e)
        self.assertEqual(h.state, 'UNKNOWN')

    def test_intent_rejects_any_token_or_unknown_field_and_noncanonical_encoding(self):
        _,_,i,_,_ = fixture()
        for changes in ({'lease_token': str(uuid4())}, {'lease_token': None}, {'extra': True},
                        {'attempt_count': True}, {'worker_login': ''}):
            with self.subTest(changes=changes), self.assertRaises(p.TransportError):
                p.Intent(p.canonical(i.value | changes))
        for raw in (b' '+i.raw, i.raw[:-1]+b',"worker_key":"other"}', b'x'*4097):
            with self.assertRaises(p.TransportError): p.Intent(raw)

    def test_bind_before_ready_and_ready_v2_refuse_stickily(self):
        f,j,i,c,e = fixture()
        h = p.PreclaimHandshake(i, 'b'*64)
        with self.assertRaises(p.TransportError):
            h.bind_data(job=j, claim=f.claim, commit_evidence_raw=c, assignment=f.assignment, enrollment_raw=e)
        with self.assertRaises(p.TransportError): h.opening()
        h = p.PreclaimHandshake(i, 'b'*64); h.opening()
        with self.assertRaises(p.TransportError):
            h.accept_ready(p.canonical(dict(version=2, intent_sha256=i.sha256, scope_nonce='e'*64)))
        self.assertEqual(h.state, 'UNKNOWN')

    def test_rejects_precommit_wrong_target_principal_assignment_and_claim_metadata(self):
        f,j,i,c,e = fixture()
        variants = [dict(commit_evidence_raw=p.canonical(p.parse(c)|{'state':'PRECOMMIT_UNCONFIRMED'})),
                    dict(enrollment_raw=p.canonical(p.parse(e)|{'principal':'other'})),
                    dict(enrollment_raw=p.canonical(p.parse(e)|{'target_identity_sha256':'f'*64})),
                    dict(enrollment_raw=p.canonical(p.parse(e)|{'assignment_sha256':'f'*64})),
                    dict(claim=f.claim|{'processing_profile':'other'}),
                    dict(claim=f.claim|{'lease_token':str(uuid4())})]
        for change in variants:
            h = self.ready(i)
            with self.subTest(change=list(change)), self.assertRaises(Exception):
                h.bind_data(**(dict(job=j,claim=f.claim,commit_evidence_raw=c,
                                    assignment=f.assignment,enrollment_raw=e)|change))
            self.assertEqual(h.state, 'UNKNOWN')

    def test_bad_enrollment_dates_expose_fixed_code_only(self):
        f,j,i,c,e=fixture()
        for field in ('issued_at_utc','expires_at_utc'):
            h=self.ready(i)
            wrong=p.canonical(p.parse(e)|{field:'PRIVATE_DATE_SENTINEL'})
            with self.assertRaises(p.TransportError) as caught:
                h.bind_data(job=j,claim=f.claim,commit_evidence_raw=c,
                            assignment=f.assignment,enrollment_raw=wrong)
            self.assertEqual(str(caught.exception),'DIANA20_TRANSPORT_BIND_ENROLLMENT_DATE')
            self.assertNotIn('PRIVATE_DATE_SENTINEL',str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)
            self.assertTrue(caught.exception.__suppress_context__)
            self.assertEqual(h.state,'UNKNOWN')

    def test_owner_issuance_has_no_permissive_port_or_uid_flag(self):
        from universal_video.owner_application_channel import OwnerScope
        scope = OwnerScope(object())
        with self.assertRaisesRegex(Exception,'UID1001_ISSUER_CAPABILITY_AND_BINDING_UNPROVEN'):
            scope.issue_committed_assignment(b'{}', b'{}')
        self.assertEqual(scope.state,'FAILED')

    def test_broker_refuses_preclaim_before_legacy_authority_or_child(self):
        from universal_video.diana20_transport_service import UnixBroker
        _,_,i,_,_=fixture()
        raw=p.PreclaimHandshake(i,'b'*64).opening()
        front=Mock()
        front.read.return_value=(p.PRECLAIM,raw)
        control=Mock(records=front)
        control.start.return_value=control
        authority,child=Mock(),Mock()
        broker=UnixBroker(authority=authority,replay=Mock(),child_factory=child,
                          control_factory=Mock(return_value=control))
        with self.assertRaisesRegex(Exception,'UID1001_ISSUER_CAPABILITY_AND_BINDING_UNPROVEN'):
            broker._serve(front,Mock(),connection=object())
        authority.open.assert_not_called();child.assert_not_called()
        control.stop.assert_called_once()

    def test_original_journal_exact_ack_readback_is_only_data(self):
        f,_,_,_,_ = fixture()
        binding = dict(admission_id=f.args['admission_id'], expected_job_id=f.claim['job_id'],
                       expected_batch_id=f.claim['batch_id'])
        journal = operator.ClaimReceiptJournal(Path('UNUSED'),binding)
        saved = dict(claim_state='RESERVED', admission_id=binding['admission_id'],
                     job_id=binding['expected_job_id'], batch_id=binding['expected_batch_id'])
        def replace(_path,value): saved.clear(); saved.update(copy.deepcopy(value))
        with patch.object(operator,'_read_local_json',side_effect=lambda _:copy.deepcopy(saved)), \
             patch.object(operator,'_replace_json',side_effect=replace):
            journal.publish_precommit(f.claim,f.args['worker_key'],f.args['admission_id'],f.args['source_fingerprint'])
            with self.assertRaises(Exception): journal.committed_evidence(f.claim)
            journal.acknowledge_commit(f.claim)
            raw = journal.committed_evidence(f.claim)
            self.assertEqual(p.parse(raw)['claim_sha256'],p.digest(canonical_bytes(f.claim,max_bytes=16384)))
            for changed in (f.claim|{'lease_token':str(uuid4())}, f.claim|{'algorithm_revision':'other'}):
                with self.assertRaises(Exception): journal.committed_evidence(changed)
            saved['claim_state']='PRECOMMIT_UNCONFIRMED'
            with self.assertRaises(Exception): journal.committed_evidence(f.claim)


class PreparedTests(unittest.TestCase):
    def test_prepare_has_no_work_and_run_reuses_exact_handoff_once(self):
        f,_,_,_,_ = fixture()
        args = f.args|{'assignment':f.assignment}
        with patch.object(entry,'_connect_queue',side_effect=f.connect), \
             patch.object(entry,'_process_private_claim',return_value='done') as process:
            prepared=entry.prepare_private_one_job(**args)
            process.assert_not_called()
            reader=prepared.handoff.post_finish_reader(connection_validator=lambda _:None,access_token=lambda:'SYNTHETIC')
            self.assertIs(reader.handoff,prepared.handoff)
            self.assertEqual(entry.run_private_one_job(**args,prepared=prepared),'done')
            self.assertIs(process.call_args.kwargs['staging_handoff'],reader.handoff)
            with self.assertRaisesRegex(Exception,'REPLAY_NO_RETRY'):
                entry.run_private_one_job(**args,prepared=prepared)
            self.assertEqual(process.call_count,1)

    def test_reused_prepare_revalidates_enrollment_before_any_work(self):
        from database.video_post_finish_receipt import SQL_ASSIGNMENT
        f,_,_,_,_=fixture();args=f.args|{'assignment':f.assignment}
        with patch.object(entry,'_connect_queue',side_effect=f.connect), \
             patch.object(entry,'_process_private_claim') as process:
            prepared=entry.prepare_private_one_job(**args)
            f.rows[SQL_ASSIGNMENT]={'assignment':None}
            with self.assertRaises(Exception):entry.run_private_one_job(**args,prepared=prepared)
            process.assert_not_called()

    def test_changed_inputs_consume_handle_without_work(self):
        f,_,_,_,_=fixture();args=f.args|{'assignment':f.assignment}
        with patch.object(entry,'_connect_queue',side_effect=f.connect), \
             patch.object(entry,'_process_private_claim') as process:
            prepared=entry.prepare_private_one_job(**args)
            with self.assertRaises(Exception):
                entry.run_private_one_job(**(args|{'claim':f.claim|{'algorithm_revision':'other'}}),prepared=prepared)
            with self.assertRaisesRegex(Exception,'REPLAY_NO_RETRY'):
                entry.run_private_one_job(**args,prepared=prepared)
            process.assert_not_called()

    def test_replaced_handoff_or_validator_cannot_reuse_checked_handle(self):
        for which in ('handoff','validator'):
            f,_,_,_,_=fixture();args=f.args|{'assignment':f.assignment}
            with patch.object(entry,'_connect_queue',side_effect=f.connect), \
                 patch.object(entry,'_process_private_claim') as process:
                prepared=entry.prepare_private_one_job(**args)
                if which=='handoff':prepared.handoff=object()
                else:prepared.adapter.connection_validator=lambda _:None
                with self.assertRaisesRegex(Exception,'COMPOSITION_CHANGED'):
                    entry.run_private_one_job(**args,prepared=prepared)
                process.assert_not_called()


class DirectoryDouble:
    """Models ORIGINAL Directory create/fsync ACK; not a Linux FS acceptance."""
    fd=123
    def __init__(self):self.files={};self.fail=None
    def check(self):pass
    def read(self,name,*,limit):
        if name not in self.files:raise FileNotFoundError(name)
        return self.files[name],None
    def create(self,name,raw,*,check):
        check()
        if name in self.files:raise FileExistsError(name)
        self.files[name]=raw
        if self.fail is not None and self.fail(name):raise OSError('MODEL_ACK_LOST_AFTER_WRITE')


def storage():
    r=AdmissionRetention(records=DirectoryDouble(),anchors=DirectoryDouble(),
                         context_sha='2'*64,owner_guard=lambda _:None)
    # Explicit FS doubles. All retention encoding/hash/phase code remains real.
    r._confirm=lambda *_:None
    r._check_confirmed=lambda:None
    return TerminalHandoffRetention(r)


def prepare(store, selected=None):
    f,_,intent,_,_=fixture()
    bundle=dict(status='PRIVATE_RESULT_STAGED',receipt_id=str(uuid4()),
                bundle_sha256='a'*64,master_json_sha256='b'*64,ai_done_sha256='c'*64)
    metadata=dict(intent_hash=intent.sha256,scope_nonce_hash='e'*64,principal='synthetic-login',
        target_identity_sha256='9'*64,enrollment_sha256='b'*64,
        admission_record_sha256='c'*64,admission_anchor_sha256='d'*64)
    mark=store.prepare(assignment=f.assignment,claim=f.claim,selected={'test':'Юникод'} if selected is None else selected,
                       bundle=bundle,metadata=metadata)
    return mark,f,metadata


class RetentionTests(unittest.TestCase):
    def test_restart_recovers_exact_canonical_inputs_from_external_watermark(self):
        store=storage();mark,f,_=prepare(store)
        restarted=TerminalHandoffRetention(store.retention)
        value=restarted.load(mark)
        self.assertEqual(value['assignment'].raw,f.assignment.raw)
        self.assertEqual(value['claim'],f.claim)
        self.assertEqual(value['selected'],{'test':'Юникод'})
        self.assertEqual(value['watermark']['phase'],'PREPARED')
        with self.assertRaises(Exception):restarted.load(None)

    def test_full_chain_binds_one_ack_id_and_requires_evidence(self):
        store=storage();mark,_,_=prepare(store)
        for phase,opts in [('FINISH_ATTEMPTED',{}),('READ_VERIFIED',{'evidence_raw':b'receipt'}),
                           ('ACK_ATTEMPTED',{'ack_attempt_id':'f'*64}),
                           ('ACKED',{'evidence_raw':b'owner-acked'}),('CLOSED',{})]:
            mark=store.advance(mark,phase,**opts)
        value=store.load(mark)
        self.assertEqual(value['watermark']['phase'],'CLOSED')
        self.assertEqual(value['watermark']['ack_attempt_id'],'f'*64)
        with self.assertRaises(Exception):store.advance(mark,'ACK_ATTEMPTED',ack_attempt_id='a'*64)

    def test_unknown_is_read_only_no_new_phase_or_new_attempt(self):
        store=storage();mark,_,_=prepare(store)
        mark=store.advance(mark,'FINISH_ATTEMPTED')
        mark=store.advance(mark,'UNKNOWN')
        restarted=TerminalHandoffRetention(store.retention)
        self.assertEqual(restarted.load(mark)['watermark']['phase'],'UNKNOWN')
        before=copy.deepcopy(store.retention.records.files)
        with self.assertRaises(Exception):restarted.advance(mark,'FINISH_ATTEMPTED')
        self.assertEqual(before,store.retention.records.files)

    def test_old_watermark_and_missing_or_tampered_anchor_fail(self):
        store=storage();first,_,_=prepare(store)
        second=store.advance(first,'FINISH_ATTEMPTED')
        with self.assertRaisesRegex(Exception,'STALE_WATERMARK'):store.load(first)
        name='terminal-'+p.parse(second)['selector']+'.step-2'
        raw=store.retention.anchors.files.pop(name)
        with self.assertRaises(Exception):store.load(second)
        store.retention.anchors.files[name]=raw+b' '
        with self.assertRaises(Exception):store.load(second)

    def test_lost_phase_write_ack_never_retries_even_when_bytes_exist(self):
        store=storage();mark,_,_=prepare(store)
        store.retention.records.fail=lambda name:name.endswith('.step-2')
        with self.assertRaises(OSError):store.advance(mark,'FINISH_ATTEMPTED')
        count=len(store.retention.records.files)
        store.retention.records.fail=None
        with self.assertRaisesRegex(Exception,'SESSION_UNKNOWN'):store.advance(mark,'FINISH_ATTEMPTED')
        self.assertEqual(count,len(store.retention.records.files))
        with self.assertRaises(Exception):TerminalHandoffRetention(store.retention).load(mark)

    def test_partial_prepare_and_oversize_are_not_promoted_or_repaired(self):
        store=storage();store.retention.anchors.fail=lambda _:True
        with self.assertRaises(OSError):prepare(store)
        before=copy.deepcopy(store.retention.records.files)
        with self.assertRaises(Exception):prepare(store)
        self.assertEqual(before,store.retention.records.files)
        store=storage()
        with self.assertRaises(Exception):prepare(store,{'x':'x'*4194304})
        self.assertEqual(store.retention.records.files,{})

    def test_selected_exact_four_mib_boundary_and_one_byte_over(self):
        # UTF-8 canonical object {"x":"..."} overhead=8 bytes.
        store=storage();mark,_,_=prepare(store,{'x':'x'*(4194304-8)})
        self.assertEqual(len(canonical_bytes(store.load(mark)['selected'],max_bytes=4194304)),4194304)
        with self.assertRaises(Exception):prepare(storage(),{'x':'x'*(4194304-7)})

    def test_recovery_calls_only_existing_reader_with_original_inputs(self):
        store=storage();mark,f,_=prepare(store)
        mark=store.advance(mark,'FINISH_ATTEMPTED')
        with patch('database.video_post_finish_receipt.PostFinishReceiptReader') as reader:
            reader.return_value.recover.return_value=('READ_ONLY',{})
            result=recover_retained_terminal(store,mark,open_connection=Mock(),
                connection_validator=Mock(),access_token=Mock())
            self.assertEqual(result,('READ_ONLY',{}))
            self.assertEqual(reader.call_args.kwargs['assignment'].raw,f.assignment.raw)
            self.assertEqual(reader.call_args.kwargs['claim'],f.claim)
            self.assertEqual(reader.return_value.method_calls,[unittest.mock.call.recover()])

    def test_transition_relation_exhaustive_against_separate_contract_edges(self):
        # Independently enumerated relation: no product PHASES or next-index used.
        edges={'PREPARED':{'FINISH_ATTEMPTED','UNKNOWN'},
               'FINISH_ATTEMPTED':{'READ_VERIFIED','UNKNOWN'},
               'READ_VERIFIED':{'ACK_ATTEMPTED','UNKNOWN'},
               'ACK_ATTEMPTED':{'ACKED','UNKNOWN'},'ACKED':{'CLOSED','UNKNOWN'},
               'CLOSED':set(),'UNKNOWN':set()}
        prefix=['FINISH_ATTEMPTED','READ_VERIFIED','ACK_ATTEMPTED','ACKED','CLOSED']
        for source,allowed in edges.items():
            for dest in edges:
                with self.subTest(source=source,dest=dest):
                    store=storage();mark,_,_=prepare(store)
                    path=[] if source in ('PREPARED','UNKNOWN') else prefix[:prefix.index(source)+1]
                    for phase in path+(['UNKNOWN'] if source=='UNKNOWN' else []):
                        opts={'evidence_raw':b'e'} if phase in ('READ_VERIFIED','ACKED') else {}
                        if phase=='ACK_ATTEMPTED':opts['ack_attempt_id']='f'*64
                        mark=store.advance(mark,phase,**opts)
                    opts={'evidence_raw':b'e'} if dest in ('READ_VERIFIED','ACKED') else {}
                    if dest=='ACK_ATTEMPTED':opts['ack_attempt_id']='f'*64
                    if dest in allowed:
                        newer=store.advance(mark,dest,**opts)
                        self.assertEqual(p.parse(newer)['phase'],dest)
                    else:
                        with self.assertRaises(Exception):store.advance(mark,dest,**opts)


class AckScopeTests(unittest.TestCase):
    def reader(self, authority=True, close_error=False):
        f,_,_,_,_=fixture(); events=[]
        class Conn:
            def __enter__(self):events.append('open');return self
            def __exit__(self,*_):
                events.append('close')
                if close_error:raise OSError('MODEL_CLOSE_UNKNOWN')
            @contextmanager
            def transaction(self):
                events.append('begin')
                try:yield
                finally:events.append('end')
            @contextmanager
            def cursor(self):yield SimpleNamespace(execute=self.execute,fetchone=self.fetchone)
            def execute(self,sql,params):
                assert sql==SQL_AUTH
                events.append('auth')
            def fetchone(self):return {'authority':authority}
        @contextmanager
        def connection():
            with Conn() as c:yield c
        bundle=dict(status='PRIVATE_RESULT_STAGED',receipt_id=str(uuid4()),bundle_sha256='a'*64,
                    master_json_sha256='b'*64,ai_done_sha256='c'*64)
        r=PostFinishReceiptReader(assignment=f.assignment,claim=f.claim,selected={},bundle=bundle,
            open_connection=connection,connection_validator=lambda _:None,access_token=lambda:'SYNTHETIC')
        r._verified=(b'receipt',p.digest(b'receipt'))
        r._guard=lambda:None  # Explicit owner authority double, not a production bridge.
        return r,events

    def test_transaction_spans_ack_and_remains_single_use(self):
        r,e=self.reader()
        with r.ack_scope(b'receipt'):
            self.assertNotIn('end',e);e.append('ACK')
        self.assertEqual(e,['open','begin','auth','ACK','end','close'])
        with self.assertRaisesRegex(Exception,'REPLAY_NO_RETRY'):
            with r.ack_scope(b'receipt'):self.fail('RESENT')

    def test_strict_false_null_integer_denial_cannot_enter_ack(self):
        for value in (False,None,1,''):
            r,_=self.reader(authority=value)
            with self.subTest(value=value),self.assertRaises(Exception):
                with r.ack_scope(b'receipt'):self.fail('ACK_WITHOUT_AUTH')

    def test_lost_close_after_ack_propagates_and_never_resends(self):
        r,e=self.reader(close_error=True)
        with self.assertRaises(OSError):
            with r.ack_scope(b'receipt'):e.append('ACK')
        with self.assertRaisesRegex(Exception,'REPLAY_NO_RETRY'):
            with r.ack_scope(b'receipt'):e.append('ACK')
        self.assertEqual(e.count('ACK'),1)

    def test_over_budget_completion_stays_unknown_without_resend(self):
        r,e=self.reader()
        with patch('database.video_post_finish_receipt.time.monotonic',side_effect=[1,4]):
            with self.assertRaisesRegex(Exception,'DEADLINE_UNKNOWN_NO_RETRY'):
                with r.ack_scope(b'receipt'):e.append('ACK')
        with self.assertRaisesRegex(Exception,'REPLAY_NO_RETRY'):
            with r.ack_scope(b'receipt'):e.append('ACK')
        self.assertEqual(e.count('ACK'),1)


if __name__=='__main__':unittest.main()
