"""Actual journal IO + explicit PG/platform metadata doubles; no SQL/native proof.

Does not import/connect psycopg or execute PostgreSQL, Docker, network or CI.
Server authorization/SQL/locks/XID/ACL need the separately approved engine test.
"""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from universal_video import native_video_binding_provider as n
from universal_video import owner_assignment_enrollment as e
from universal_video.private_one_job_operator import ClaimReceiptJournal
from universal_video.receipt_encoding import canonical_bytes, sha256
from universal_video.owner_application_channel import OwnerScope
from tests.test_video0063_owner_phase_local import fixture


class PgProtocolDouble:
    def __init__(self):
        self.info=SimpleNamespace(host=n.HOST,port=5432,
                                  transaction_status=SimpleNamespace(name='IDLE'))
        f,*_=fixture()
        self.claim=f.claim | {'source_name':'\u0414\u0438\u0430\u043d\u0430 20.mp4'}
        self.task=dict(task_id='00000000-0000-4000-8000-000000000080',lease_epoch=1,
            worker_id=n.WORKER_KEY,intake_sha256=n.INTAKE,
            job_id=self.claim['job_id'],batch_id=self.claim['batch_id'])
        self.events=[]; self.rows=[]; self.count=0; self.fail_commit=0; self.principal=n.WORKER
        self.journal=None

    @contextmanager
    def transaction(self):
        if self.info.transaction_status.name!='IDLE':raise AssertionError('nested transaction')
        self.count+=1; self.events.append('BEGIN'); self.info.transaction_status.name='INTRANS'
        try:
            yield self
            if self.count==2 and self.journal is not None:
                if self.journal.inspect_state()!='PRECOMMIT_UNCONFIRMED':raise AssertionError('no precommit journal')
            self.events.append('COMMIT_ATTEMPT')
            if self.count==self.fail_commit:raise OSError('PRIVATE_LOST_ACK')
            self.events.append('COMMIT_ACK')
        finally:self.info.transaction_status.name='IDLE'

    @contextmanager
    def cursor(self):yield self

    def execute(self,sql,params=None):
        self.events.append(sql)
        if sql in e.LOCAL_SETTINGS:self.rows=[]
        elif sql==e.SQL_IDENTITY:self.rows=[dict(database_name='neondb',session_login=self.principal,
            effective_login=self.principal,backend_pid=123)]
        elif sql==n.SQL['task']:self.rows=[{'result':self.task.copy()}]
        elif sql==n.SQL['video']:self.rows=[{'result':self.claim.copy()}]
        elif sql==n.SQL['inspect']:self.rows=[{'result':{'observed':'data-only'}}]
        elif sql==n.SQL['heartbeat']:self.rows=[{'result':{'renewed':True}}]
        else:raise AssertionError('unexpected SQL')

    def fetchall(self):return self.rows


class NativeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='native64-',dir=os.environ['OWNER_TEST_TEMP_ROOT'])
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'claim.json'
        self.pg=PgProtocolDouble()
        self.target=e.EnrollmentTarget('neondb',n.OWNER,n.WORKER,n.HOST,5432,'1'*64)
        self.session=n.NativeVideoSession(connection=self.pg,target=self.target,worker=True)
        self.journal=ClaimReceiptJournal(self.path,dict(admission_id='00000000-0000-4000-8000-000000000090',
            expected_job_id=self.pg.claim['job_id'],expected_batch_id=self.pg.claim['batch_id'],
            source_fingerprint='2'*64))
        self.pg.journal=self.journal
        self.metadata_double=None
        if os.name=='nt':
            # Windows mode bits cannot establish the existing Linux600 guard.
            # Test protocol/file contents only; never weaken production code.
            def read_json(path):
                try:return json.loads(path.read_text(encoding='utf8'))
                except FileNotFoundError:return None
            self.metadata_double=patch('universal_video.private_one_job_operator._read_local_json',read_json)
            self.metadata_double.start();self.addCleanup(self.metadata_double.stop)

    def driver(self):return n.NativeVideoWorkerClaim(session=self.session,journal=self.journal)

    def test_actual_journal_task_commit_before_video_publish_before_commit_ack(self):
        result=self.driver().claim_one()
        self.assertEqual(self.journal.inspect_state(),'COMMIT_ACKNOWLEDGED')
        self.assertLess(self.pg.events.index('COMMIT_ACK'),self.pg.events.index(n.SQL['video']))
        self.assertEqual(self.pg.events.count('COMMIT_ACK'),2)
        c=json.loads(result.claim_raw); evidence=json.loads(result.commit_evidence_raw)
        self.assertEqual(c,self.pg.claim)
        self.assertEqual(evidence['claim_sha256'],sha256(canonical_bytes(self.pg.claim)))
        self.assertEqual(json.loads(result.task_raw),self.pg.task)

    def test_lost_task_commit_keeps_reservation_no_video_or_replay(self):
        self.pg.fail_commit=1
        with self.assertRaisesRegex(n.NativeBindingRefused,'UNKNOWN_NO_RETRY'):self.driver().claim_one()
        self.assertEqual(self.journal.inspect_state(),'RESERVED')
        self.assertNotIn(n.SQL['video'],self.pg.events)
        before=len(self.pg.events)
        with self.assertRaises(Exception):self.driver().claim_one()
        self.assertEqual(len(self.pg.events),before)

    def test_lost_video_commit_keeps_precommit_and_suppresses_ack(self):
        self.pg.fail_commit=2
        with self.assertRaisesRegex(n.NativeBindingRefused,'UNKNOWN_NO_RETRY'):self.driver().claim_one()
        self.assertEqual(self.journal.inspect_state(),'PRECOMMIT_UNCONFIRMED')
        with self.assertRaises(Exception):self.journal.committed_evidence(self.pg.claim)
        self.assertTrue(self.session.failed)

    def test_second_driver_after_success_cannot_claim_again(self):
        self.driver().claim_one(); before=len(self.pg.events)
        with self.assertRaises(Exception):self.driver().claim_one()
        self.assertEqual(len(self.pg.events),before)

    def test_task_wrong_worker_is_not_forwarded_to_video_claim(self):
        self.pg.task['worker_id']='configured-worker'
        with self.assertRaisesRegex(n.NativeBindingRefused,'TASK_CHANGED'):self.driver().claim_one()
        self.assertNotIn(n.SQL['video'],self.pg.events)

    def test_session_identity_admin_is_refused_before_rpc(self):
        self.pg.principal='neondb_owner'
        with self.assertRaises(n.NativeBindingRefused):self.driver().claim_one()
        self.assertNotIn(n.SQL['task'],self.pg.events)

    def test_connection_replacement_and_nested_tx_refuse_no_sql(self):
        for field,value in (('connection',PgProtocolDouble()),('target',replace(self.target,host='other.invalid'))):
            s=n.NativeVideoSession(connection=self.pg,target=self.target,worker=True)
            setattr(s,field,value)
            with self.assertRaises(n.NativeBindingRefused):s.call('task',(120,))
        self.pg.info.transaction_status.name='INTRANS'
        with self.assertRaises(n.NativeBindingRefused):self.session.call('task',(120,))
        self.assertEqual(self.pg.events,[])

    def test_worker_cannot_use_owner_rpc_client(self):
        with self.assertRaises(n.NativeBindingRefused):self.session.call('create',(n.INTAKE,self.pg.claim['batch_id']))
        self.assertEqual(self.pg.events,[])

    def test_wrong_target_admin_or_pooled_descriptor_refused(self):
        for changed in (replace(self.target,owner_login='neondb_owner'),replace(self.target,database='other'),
                        replace(self.target,host='ep-pooler.example')):
            with self.assertRaises(n.NativeBindingRefused):n.NativeVideoSession(connection=self.pg,target=changed)

    def test_initial_boundary_default_original_issuer_stays_closed(self):
        s=OwnerScope(object())
        with self.assertRaisesRegex(Exception,'CAPABILITY_AND_BINDING_UNPROVEN'):
            s.issue_committed_assignment(b'{}',b'{}')
        self.assertEqual(s.state,'FAILED')
        with self.assertRaises(n.NativeBindingRefused):
            n.OriginalNativeVideoProvider(session=self.session,source=object(),task_id=self.pg.task['task_id'],
                native=object(),store=object(),intent=object())

    def test_owner_rpc_result_is_detached_data_not_authority(self):
        self.pg.principal=n.OWNER
        s=n.NativeVideoSession(connection=self.pg,target=self.target)
        value=s.call('inspect',('0'*64,))
        self.assertEqual(value,{'observed':'data-only'})
        self.assertNotIsInstance(value,n.IssuedNativeAssignment)

    def test_unknown_error_redacts_private_db_exception(self):
        self.pg.fail_commit=1
        try:self.session.call('task',(120,))
        except n.NativeBindingRefused as error:
            self.assertIsNone(error.__cause__)
            self.assertTrue(error.__suppress_context__)
            self.assertNotIn('PRIVATE',str(error))
        else:self.fail('expected refusal')

    @unittest.skipUnless(os.name=='nt','Windows-only unchanged mode guard evidence')
    def test_unchanged_private_file_mode_guard_refuses_windows_metadata(self):
        self.metadata_double.stop()
        self.journal.reserve()
        with self.assertRaisesRegex(Exception,'LOCAL_FILE_UNSAFE'):self.journal.inspect_state()


class ExpiryTests(unittest.TestCase):
    def test_enrollment_never_outlives_db_native_policy_or_monotonic_scope(self):
        stamp=lambda x:datetime.strptime(x,'%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc).timestamp()
        wall=stamp('2026-10-21T12:00:00.000000Z')
        for db,native,policy,deadline,expected in (
            (100,200,300,400,99),(400,100,300,200,99),
            (400,300,100,200,99),(400,300,200,100,99)):
            with self.subTest(shortest=(db,native,policy,deadline)):
                scope=SimpleNamespace(channel=SimpleNamespace(wall=lambda:wall,clock=lambda:10),
                    _native_expires_wall_ms=(wall+native)*1000,deadline=10+deadline,
                    policy={'not_after_UTC':datetime.fromtimestamp(wall+policy,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})
                dbdate=datetime.fromtimestamp(wall+db,timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                self.assertEqual(stamp(n.bounded_expiry(dbdate,scope)),wall+expected)

    def test_expired_native_authority_is_not_extended_to_db_lease(self):
        scope=SimpleNamespace(channel=SimpleNamespace(wall=lambda:1000,clock=lambda:10),
            _native_expires_wall_ms=999000,deadline=100,
            policy={'not_after_UTC':'2026-10-21T12:00:00Z'})
        with self.assertRaises(n.NativeBindingRefused):n.bounded_expiry('2026-10-21T13:00:00.000000Z',scope)


class SqlSourceBoundaryTests(unittest.TestCase):
    """Source regression only. These checks cannot compile or qualify SQL."""
    def test_fixed_source_and_no_runtime_role_creation(self):
        root=Path(__file__).resolve().parents[1]
        sql=(root/'database/proposals/0064_native_video_binding_v1.sql').read_text()
        self.assertEqual(sql.count('CREATE TABLE '),2)
        self.assertNotIn('CREATE ROLE',sql)
        self.assertNotIn('CREATE OR REPLACE',sql)
        self.assertIn(n.INTAKE,sql)
        self.assertIn('task_lease_until',sql)
        self.assertIn("pg_xact_status(n.binding_xid) IS DISTINCT FROM 'committed'",sql)

    def test_every_rpc_guards_before_row_locks_and_all_definers_fixed_path(self):
        import re
        sql=(Path(__file__).resolve().parents[1]/'database/proposals/0064_native_video_binding_v1.sql').read_text()
        bodies=re.findall(r'CREATE FUNCTION (\w+\.\w+)\([^;]*?AS \$\$(.*?)END \$\$;',sql,re.S)
        rpc_bodies=[b for name,b in bodies if not name.split('.')[1].startswith('_')]
        self.assertEqual(len(rpc_bodies),8)
        for prefix in re.findall(r'CREATE FUNCTION [\s\S]*?AS \$\$',sql):
            if 'SECURITY DEFINER' in prefix:self.assertIn('SET search_path=pg_catalog,pg_temp AS $$',prefix)
        for body in rpc_bodies:
            statements=body.split('\nBEGIN\n',1)[1].strip()
            self.assertTrue(statements.startswith('PERFORM autopilot._native_guard_v1('))

    def test_catalog_temp_shadow_and_unrelated_acl_adoption_regressions(self):
        import re
        root=Path(__file__).resolve().parents[1]/'database/proposals'
        sql=(root/'0064_native_video_binding_v1.sql').read_text()
        self.assertEqual(re.findall(r'\b(?:FROM|JOIN) (pg_\w+)\b(?!\.)',sql),[])
        acl=sql[sql.index('DO $acl$'):]
        self.assertNotIn('LIKE',acl)
        self.assertIn("'autopilot._native_guard_v1(boolean)'",acl)
        profile=(root/'0064_native_video_runtime_acl_v1.sql').read_text()
        self.assertIn('has_sequence_privilege',profile)
        self.assertIn('NATIVE_OWNER_UNEXPECTED_EFFECTIVE_APPLICATION_EXECUTE',profile)
        self.assertNotIn('REVOKE',profile)  # Refuse ambient privileges, never global repair.


class EnrollmentRetentionTests(unittest.TestCase):
    """Run actual new retention algorithm with explicit Directory IO doubles.

    These tests prove order/conflict/failure behavior, not RC2 native fsync/UID.
    Original native Directory/owner guards remain required and unchanged.
    """
    def setUp(self):
        from universal_video.private_admission_retention import AdmissionRetention
        from universal_video.owner_terminal_store import ENROLLMENT_RECEIPT_FIELDS
        f,*_=fixture()
        body=dict.fromkeys(ENROLLMENT_RECEIPT_FIELDS,'1'*64)
        body.update(schema='enrolled-assignment/v1',principal=n.WORKER,
            assignment_text=f.assignment.raw.decode(),assignment_sha256=f.assignment.sha256,
            issued_at_utc='2026-10-21T12:00:00.000000Z',expires_at_utc='2026-10-21T12:01:00.000000Z')
        self.receipt=n.p.canonical(body);self.work='9'*64
        self.r=object.__new__(AdmissionRetention)
        self.r.records='existing-record-directory';self.r.anchors='existing-anchor-directory';self.r.context_sha='2'*64
        self.data={};self.log=[];self.fail_at=None
        self.r.check=lambda:None;self.r._check_confirmed=lambda:None
        self.r._read=lambda d,k:self.data.get((d,k))
        def put(d,k,raw):
            self.log.append(k)
            if self.fail_at==len(self.log):raise OSError('test uncertain Directory.create')
            self.data[(d,k)]=raw
        self.r._put=put

    def test_full_receipt_prepared_anchor_committed_and_exact_duplicate_denied(self):
        raw=self.r.record_enrollment(self.work,self.receipt)
        prefix='enrollment-'+self.work
        self.assertEqual(self.log,[prefix+'.prepared',prefix+'.anchor',prefix+'.committed'])
        self.assertEqual(self.data[(self.r.records,prefix+'.prepared')],self.receipt)
        self.assertEqual(json.loads(raw)['receipt_sha256'],sha256(self.receipt))
        with self.assertRaisesRegex(Exception,'RECONCILE_NO_REPLAY'):self.r.record_enrollment(self.work,self.receipt)
        self.assertEqual(len(self.log),3)

    def test_uncertain_anchor_keeps_prepared_receipt_and_no_success_or_replay(self):
        self.fail_at=2
        with self.assertRaises(OSError):self.r.record_enrollment(self.work,self.receipt)
        self.assertEqual(len(self.data),1)
        with self.assertRaisesRegex(Exception,'RECONCILE_NO_REPLAY'):self.r.record_enrollment(self.work,self.receipt)

    def test_tampered_receipt_and_oversize_refuse_before_directory_write(self):
        for raw in (self.receipt.replace(b'"assignment_sha256":"',b'"assignment_sha256":"0'),b'x'*65537):
            with self.assertRaises(Exception):self.r.record_enrollment(self.work,raw)
            self.assertEqual(self.data,{})


class NativeBackendProtocolTests(unittest.TestCase):
    def test_real_backend_requires_bind_commit_before_enroll_and_separate_readback(self):
        # Scope authorization is explicitly doubled here. No claim of actual
        # owner realm or SQL authorization; original .check is not edited.
        pg=PgProtocolDouble();pg.journal=None;pg.principal=n.OWNER
        provider=object.__new__(n.OriginalNativeVideoProvider);provider.task_id=pg.task['task_id']
        req=canonical_bytes(dict(assignment={'autopilot':{'lease_epoch':1}},journal_sha256='1'*64))
        backend=n.NativeEnrollmentBackend(provider=provider,scope=object(),request_raw=req)
        backend.check=lambda:None
        binding=(None,pg,None,None,backend)
        transaction=SimpleNamespace(_connection=lambda b:None,_identity=lambda c,b:None)
        returned={'selector_sha256':'3'*64,'assignment_sha256':'4'*64,'assignment_text':'{}',
                  'principal':n.WORKER,'issued_at_utc':'a','expires_at_utc':'b'}
        old=pg.execute
        def execute(sql,params=None):
            if sql==n.SQL['bind']:
                pg.events.append(sql);pg.rows=[{'result':dict(binding_sha256=backend.binding_sha256,task_id=provider.task_id)}]
            elif sql==n.SQL['enroll']:
                self.assertIn('COMMIT_ACK',pg.events)
                pg.events.append(sql);pg.rows=[{'result':returned}]
            elif sql==n.SQL['inspect']:
                pg.events.append(sql);pg.rows=[{'result':dict(binding_sha256=backend.binding_sha256,
                    assignment_sha256='4'*64,enrollment=returned)}]
            else:old(sql,params)
        pg.execute=execute
        backend.bind(transaction,binding)
        with pg.transaction():
            with pg.cursor() as cursor:self.assertEqual(backend.enroll(cursor,{'assignment_text':'{}'}),returned)
        with pg.transaction():
            with pg.cursor() as cursor:self.assertEqual(backend.readback(cursor,{'assignment_sha256':'4'*64}),returned)
        self.assertEqual(pg.events.count('COMMIT_ACK'),3)
        self.assertLess(pg.events.index('COMMIT_ACK'),pg.events.index(n.SQL['enroll']))


if __name__=='__main__':unittest.main()
