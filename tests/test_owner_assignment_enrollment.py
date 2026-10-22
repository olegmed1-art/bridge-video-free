"""Physical SQLite fencing + explicit PG protocol doubles, never PG/native proof.

No network, credentials, installed service, SQL server or P01-P16 invocation.
The tested driver is real; only its already-open PG connection is a DOUBLE.
"""
from contextlib import contextmanager
from dataclasses import replace
import inspect
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.test_video0063_owner_phase_local import fixture
from tests.test_owner_terminal_store import writer, reader, raw, sha
from universal_video import owner_assignment_enrollment as e
from universal_video import owner_terminal_store as s
from universal_video import diana20_transport_protocol as p
from universal_video.owner_application_channel import OwnerScope

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT.parent


class PgDouble:
    """Explicit transaction/row/COMMIT-loss double, not SQL semantics."""
    def __init__(self, test, path, inputs):
        self.test, self.path, self.inputs = test, path, inputs
        self.info = SimpleNamespace(host='accepted.invalid',port=5432,
                                    transaction_status=SimpleNamespace(name='IDLE'))
        self.transactions = 0
        self.commits = 0
        self.inserts = 0
        self.log = []
        self.mode = None
        self.change = None
        self.committed = None
        self.pending = None
        self.rows = []
        self.description = None

    @contextmanager
    def transaction(self):
        self.test.assertEqual(self.info.transaction_status.name,'IDLE')
        self.transactions += 1
        self.info.transaction_status.name='INTRANS'
        self.log.append('BEGIN')
        try:
            yield self
            self.log.append('COMMIT_ATTEMPT')
            if self.mode=='commit_before' and self.transactions==1:
                raise OSError('PRIVATE_LOST_COMMIT_ACK')
            if self.pending is not None:
                self.committed=self.pending.copy()
                self.pending=None
            self.commits += 1
            if self.mode=='commit_after' and self.transactions==1:
                raise OSError('PRIVATE_LOST_COMMIT_ACK')
            if self.mode=='read_commit_after' and self.transactions==2:
                raise OSError('PRIVATE_LOST_READ_COMMIT_ACK')
            self.log.append('COMMIT_ACK')
        except BaseException:
            self.pending=None
            self.log.append('ROLLBACK_OR_UNKNOWN')
            raise
        finally:
            self.info.transaction_status.name='IDLE'

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, sql, params=None):
        self.test.assertEqual(self.info.transaction_status.name,'INTRANS')
        self.log.append(sql)
        if sql in e.LOCAL_SETTINGS:
            self.rows=[]
            return
        if sql==e.SQL_IDENTITY:
            row=dict(database_name='accepted_db',session_login='accepted_owner',
                     effective_login='accepted_owner',backend_pid=123)
        elif sql==e.SQL_LOCK_JOB:
            # A different real SQLite connection must observe durable reservation
            # before *any* PG enrollment transaction effects.
            self.test.assertEqual(reader(self.path).inspect_enrollment(self.test.work).state,'ATTEMPTED')
            row={k:params[k] for k in ('job_id','batch_id','source_file_id',
                                      'stable_job_key','attempt_count','lease_token')}
            row.update(lease_owner=params['worker_key'],lease_expires_at='PG_CLOCK_GUARDED')
        elif sql==e.SQL_LOCK_BATCH:
            row={k:params[k] for k in ('batch_id','processing_profile','algorithm_revision','output_folder_id')}
        elif sql==e.SQL_INSERT:
            self.inserts+=1
            row={k:params[k] for k in ('selector_sha256','assignment_sha256','assignment_text')}
            row.update(principal=params['worker_login'],issued_at_utc='2026-10-21T12:00:00.000000Z',
                       expires_at_utc=params['approved_expires_at'])
            self.pending=row.copy()
        elif sql==e.SQL_READBACK:
            self.test.assertEqual(self.commits,1)
            self.test.assertEqual(reader(self.path).inspect_enrollment(self.test.work).state,'ATTEMPTED')
            self.test.assertIsNotNone(self.committed)
            row=self.committed.copy()
        else:
            raise AssertionError('UNEXPECTED_SQL')
        if self.change is not None:
            row=self.change(sql,row)
        if row is None:
            self.rows=[]
        elif self.mode=='duplicate' and sql==e.SQL_INSERT:
            self.rows=[row,row.copy()]
        elif self.mode=='tuple':
            self.description=[SimpleNamespace(name=k) for k in row]
            self.rows=[tuple(row.values())]
        else:
            self.rows=[row]

    def fetchall(self):
        return self.rows


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='enrollment-',dir=os.environ.get('OWNER_TEST_TEMP_ROOT', ROOT/'tests'))
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name).resolve()/'owner.sqlite3'
        self.path.touch()
        self.ledger=writer(self.path)
        self.ledger.initialize()
        if hasattr(self,'args'):
            f=SimpleNamespace(claim=self.args['claim'],assignment=self.args['assignment'])
            intent=self.args['intent']
        else:
            f,_,intent,_,_=fixture()
        self.args=dict(work_key=e.terminal_work_key(intent,'synthetic-enrollment'),scope_nonce_hash='2'*64,intent=intent,
            policy_sha256='b'*64,claim=f.claim,assignment=f.assignment,
            admission_receipt_raw=p.canonical(dict(state='COMMITTED',durability_confirmed=True,
                record_sha256='c'*64,anchor_sha256='d'*64,request_sha256=f.assignment.value['request_sha256'],
                claim_sha256=p.digest(p.canonical({k:f.claim[k] for k in p.CLAIM})))),
            approved_expires_at='2026-10-21T12:30:00.000000Z')
        self.target=e.EnrollmentTarget('accepted_db','accepted_owner',intent.value['worker_login'],
                                       'accepted.invalid',5432,intent.value['target_identity_sha256'])
        self.work=self.args['work_key']
        h=dict.fromkeys(s.HEAD_FIELDS)
        h.update(schema='terminal-queue-head/v1',work_key=e.terminal_work_key(intent,'synthetic-enrollment'),intent_sha256=intent.sha256,
            target_identity_sha256=intent.value['target_identity_sha256'],principal=intent.value['worker_login'],
            seq=1,phase='ARMED')
        first=raw(h)
        self.ledger.compare_and_swap(self.work,None,first)
        h.update(seq=2,phase='CLAIM_COMMITTED',previous_sha256=sha(first),
                 claim_sha256=p.digest(p.canonical(f.claim)))
        self.head=raw(h)
        self.ledger.compare_and_swap(self.work,sha(first),self.head)
        self.pg=PgDouble(self,self.path,self.args)

    def engine(self,ledger=None,pg=None):
        return e.EnrollmentTransaction(ledger=ledger or self.ledger,connection=pg or self.pg,
                                       target=self.target,namespace='synthetic-enrollment',enabled=True)

    def rejected(self,engine=None,**changes):
        with self.assertRaisesRegex(e.EnrollmentError,'UNKNOWN_NO_RETRY') as caught:
            (engine or self.engine()).enroll(**(self.args|changes))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertNotIn('PRIVATE',str(caught.exception))

    def bound(self,receipt):
        h=json.loads(self.head)
        h.update(seq=3,phase='BOUND',previous_sha256=sha(self.head),
            selector=self.args['assignment'].selector_sha256,assignment_sha256=self.args['assignment'].sha256,
            enrollment_sha256=sha(receipt),admission_record_sha256='c'*64,admission_anchor_sha256='d'*64)
        return raw(h)

    def test_real_driver_commits_then_readbacks_then_persists_bounded_descriptor(self):
        receipt=self.engine().enroll(**self.args)
        body=json.loads(receipt)
        self.assertEqual(body['assignment_text'],self.args['assignment'].raw.decode())
        self.assertEqual(self.pg.commits,2)
        self.assertEqual(self.pg.inserts,1)
        statements=[q for q in self.pg.log if q in (e.SQL_LOCK_JOB,e.SQL_LOCK_BATCH,e.SQL_INSERT,e.SQL_READBACK)]
        self.assertEqual(statements,[e.SQL_LOCK_JOB,e.SQL_LOCK_BATCH,e.SQL_INSERT,e.SQL_READBACK])
        status=reader(self.path).inspect_enrollment(self.work)
        self.assertEqual(status.state,'COMMITTED')
        descriptor=json.loads(status.commit_raw)
        self.assertNotIn('assignment_text',descriptor)
        self.assertEqual(descriptor['receipt_sha256'],sha(receipt))
        self.assertLessEqual(len(status.commit_raw),4096)
        self.ledger.compare_and_swap(self.work,sha(self.head),self.bound(receipt))
        self.assertEqual(json.loads(reader(self.path).read_head(self.work))['phase'],'BOUND')
        self.assertFalse(any('terminal_read_grant' in q for q in self.pg.log))

    def test_tuple_row_contract_is_supported_without_replacing_driver(self):
        self.pg.mode='tuple'
        self.engine().enroll(**self.args)
        self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'COMMITTED')

    def test_default_disabled_and_original_scope_hard_refusal(self):
        with self.assertRaisesRegex(e.EnrollmentError,'DISABLED'):
            e.EnrollmentTransaction(ledger=self.ledger,connection=self.pg,target=self.target,namespace='synthetic-enrollment')
        scope=OwnerScope(object())
        with self.assertRaisesRegex(Exception,'ISSUER_CAPABILITY_AND_BINDING_UNPROVEN'):
            scope.issue_committed_assignment(b'{}',b'{}')
        self.assertEqual(scope.state,'FAILED')
        self.assertEqual(self.pg.transactions,0)

    def test_preflight_input_mismatches_cause_zero_pg_transactions(self):
        variants=[dict(claim=self.args['claim']|{'lease_token':'00000000-0000-4000-8000-000000000000'}),
            dict(claim=self.args['claim']|{'processing_profile':'wrong'}),
            dict(intent=p.Intent(p.canonical(self.args['intent'].value|{'worker_login':'other'}))),
            dict(admission_receipt_raw=p.canonical(dict(json.loads(self.args['admission_receipt_raw']),state='PENDING'))),
            dict(admission_receipt_raw=p.canonical(dict(json.loads(self.args['admission_receipt_raw']),claim_sha256='e'*64))),
            dict(approved_expires_at='2026-10-21T12:30:00Z')]
        for v in variants:
            with self.subTest(fields=list(v)):
                self.rejected(**v)
                self.assertEqual(self.pg.transactions,0)
                self.assertIsNone(reader(self.path).inspect_enrollment(self.work))

    def test_host_and_nested_transaction_refuse_before_reservation(self):
        for host,status in (('wrong.invalid','IDLE'),('accepted.invalid','INTRANS')):
            with self.subTest(host=host,status=status):
                self.pg.info.host=host
                self.pg.info.transaction_status.name=status
                self.rejected()
                self.assertEqual(self.pg.transactions,0)
                self.assertIsNone(reader(self.path).inspect_enrollment(self.work))

    def test_guard_cannot_replace_original_target_connection_or_ledger(self):
        for field in ('target','connection','ledger','namespace'):
            with self.subTest(field=field):
                self.setUp()
                engine=self.engine()
                replacement={'target':replace(self.target,worker_login='other-login'),
                             'connection':PgDouble(self,self.path,self.args),
                             'ledger':writer(self.path),'namespace':'different-namespace'}[field]
                def guard():setattr(engine,field,replacement)
                self.ledger.guard=guard
                self.rejected(engine)
                self.assertEqual(self.pg.transactions,0)
                self.assertIsNone(reader(self.path).inspect_enrollment(self.work))

    def test_binding_change_inside_pg_cannot_return_mislabeled_receipt(self):
        for sql in (e.SQL_LOCK_BATCH,e.SQL_INSERT,e.SQL_READBACK):
            with self.subTest(sql=sql):
                self.setUp()
                engine=self.engine()
                def change(q,row):
                    if q==sql:engine.target=replace(self.target,worker_login='other-login')
                    return row
                self.pg.change=change
                self.rejected(engine)
                self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'UNKNOWN')
                self.assertLessEqual(self.pg.inserts,1)

    def test_receipt_must_match_attempt_principal_not_current_target_field(self):
        # A SQL response naming another worker is never relabeled as the
        # reserved worker, even if a caller mutates the engine during execute.
        engine=self.engine()
        def change(q,row):
            if q==e.SQL_INSERT:
                engine.target=replace(self.target,worker_login='other-login')
                return row|{'principal':'other-login'}
            return row
        self.pg.change=change
        self.rejected(engine)
        self.assertIsNone(self.pg.committed)
        self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'UNKNOWN')

    def test_actual_session_identity_mismatch_consumes_attempt_without_insert(self):
        self.pg.change=lambda q,r:r|{'effective_login':'wrong'} if q==e.SQL_IDENTITY else r
        self.rejected()
        self.assertEqual(self.pg.inserts,0)
        self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'UNKNOWN')
        self.rejected(self.engine(writer(self.path)))
        self.assertEqual(self.pg.transactions,1)

    def test_wrong_typed_attempt_row_refuses_before_insert(self):
        self.pg.change=lambda q,r:r|{'attempt_count':str(r['attempt_count'])} if q==e.SQL_LOCK_JOB else r
        self.rejected()
        self.assertEqual(self.pg.inserts,0)
        self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'UNKNOWN')

    def test_absent_lease_or_batch_refuses_before_insert(self):
        for sql in (e.SQL_LOCK_JOB,e.SQL_LOCK_BATCH):
            with self.subTest(sql=sql):
                # Each scenario needs a separate stable work-key database.
                self.setUp()
                self.pg.change=lambda q,r:None if q==sql else r
                self.rejected()
                self.assertEqual(self.pg.inserts,0)
                self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'UNKNOWN')

    def test_insert_duplicate_or_mismatched_receipt_rolls_back_and_never_retries(self):
        for mode in ('duplicate','principal','dates','hash'):
            with self.subTest(mode=mode):
                self.setUp()
                if mode=='duplicate':self.pg.mode=mode
                else:
                    change={'principal':{'principal':'wrong'},'dates':{'expires_at_utc':'2026-10-21T14:30:00.000000Z'},
                            'hash':{'assignment_sha256':'e'*64}}[mode]
                    self.pg.change=lambda q,r:r|change if q==e.SQL_INSERT else r
                self.rejected()
                self.assertEqual(self.pg.commits,0)
                self.assertIsNone(self.pg.committed)
                self.rejected(self.engine(writer(self.path)))
                self.assertEqual(self.pg.inserts,1)

    def test_commit_outcomes_before_after_and_read_ack_loss_are_unknown_not_replayable(self):
        for mode in ('commit_before','commit_after','read_commit_after'):
            with self.subTest(mode=mode):
                self.setUp()
                self.pg.mode=mode
                self.rejected()
                self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'UNKNOWN')
                self.assertEqual(self.pg.committed is not None,mode!='commit_before')
                self.rejected(self.engine(writer(self.path)))
                self.assertEqual(self.pg.inserts,1)

    def test_revocation_or_mismatched_post_commit_readback_is_unknown(self):
        self.pg.change=lambda q,r:None if q==e.SQL_READBACK else r
        self.rejected()
        self.assertIsNotNone(self.pg.committed)
        self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'UNKNOWN')
        self.rejected(self.engine(writer(self.path)))
        self.assertEqual(self.pg.inserts,1)

    def test_lost_reservation_barrier_has_no_pg_effect_and_consumes_key(self):
        with patch.object(self.ledger,'_barrier',side_effect=OSError('PRIVATE_FSYNC')):
            self.rejected()
        self.assertEqual(self.pg.transactions,0)
        self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'ATTEMPTED')
        self.rejected(self.engine(writer(self.path)))
        self.assertEqual(self.pg.inserts,0)

    def test_lost_final_barrier_retains_descriptor_without_success_or_repeat(self):
        original=self.ledger._barrier
        calls=[]
        def barrier():
            calls.append(1)
            original()
            if len(calls)==2:raise OSError('PRIVATE_FSYNC_AFTER_PG')
        with patch.object(self.ledger,'_barrier',barrier):
            self.rejected()
        self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'COMMITTED')
        self.rejected(self.engine(writer(self.path)))
        self.assertEqual(self.pg.inserts,1)

    def test_successful_replay_same_or_fresh_object_is_read_only(self):
        engine=self.engine()
        engine.enroll(**self.args)
        self.rejected(engine)
        self.rejected(self.engine(writer(self.path)))
        self.assertEqual(self.pg.inserts,1)
        self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'COMMITTED')

    def test_claim_head_is_required_not_just_data(self):
        a=self.args
        self.rejected(work_key='f'*64)
        self.assertEqual(self.pg.transactions,0)
        self.assertIsNone(reader(self.path).inspect_enrollment(a['work_key']))

    def test_alternate_matching_head_does_not_bypass_stable_work_key(self):
        h=json.loads(self.head)
        h.update(work_key='f'*64,phase='ARMED',seq=1,previous_sha256=None,claim_sha256=None)
        first=raw(h)
        self.ledger.compare_and_swap('f'*64,None,first)
        h.update(phase='CLAIM_COMMITTED',seq=2,previous_sha256=sha(first),
                 claim_sha256=json.loads(self.head)['claim_sha256'])
        self.ledger.compare_and_swap('f'*64,sha(first),raw(h))
        self.rejected(work_key='f'*64)
        self.assertEqual(self.pg.transactions,0)
        self.assertIsNone(reader(self.path).inspect_enrollment('f'*64))

    def test_shared_recovery_formula_is_stable_for_retries_not_source_changes(self):
        i=self.args['intent']
        for change in ({'attempt_count':3},{'worker_key':'different-worker'},
                       {'policy_epoch':2},{'worker_login':'other-login'}):
            self.assertEqual(e.terminal_work_key(p.Intent(p.canonical(i.value|change)),
                                               'synthetic-enrollment'),self.work)
        for change in ({'source_fingerprint':'e'*64},{'generation_sha256':'e'*64},
                       {'source_file_id':'different_source_id'}):
            self.assertNotEqual(e.terminal_work_key(p.Intent(p.canonical(i.value|change)),
                                                  'synthetic-enrollment'),self.work)

    def test_no_claim_head_and_readonly_ledger_never_start_pg(self):
        other=self.path.parent/'other.sqlite3';other.touch()
        empty=writer(other);empty.initialize()
        self.rejected(self.engine(empty))
        self.rejected(self.engine(reader(self.path)))
        self.assertEqual(self.pg.transactions,0)

    def test_unknown_cannot_be_committed_with_forged_receipt(self):
        self.pg.mode='commit_after'
        self.rejected()
        status=reader(self.path).inspect_enrollment(self.work)
        with self.assertRaises(s.TerminalStoreError):
            writer(self.path).commit_enrollment(self.work,sha(status.attempt_raw),b'{}')
        self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'UNKNOWN')

    def test_unknown_or_attempted_cannot_be_bound_or_promoted(self):
        self.pg.mode='commit_after'
        self.rejected()
        status=reader(self.path).inspect_enrollment(self.work)
        with self.assertRaisesRegex(s.TerminalStoreError,'BOUND_BEFORE_COMMIT'):
            writer(self.path).compare_and_swap(self.work,sha(self.head),self.bound(b'never-received'))
        with self.assertRaisesRegex(s.TerminalStoreError,'ALREADY_FINAL_READ_ONLY'):
            writer(self.path).commit_enrollment(self.work,sha(status.attempt_raw),b'{}')
        self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'UNKNOWN')

    def test_changed_bound_receipt_hash_refuses(self):
        receipt=self.engine().enroll(**self.args)
        changed=json.loads(self.bound(receipt))|{'enrollment_sha256':'f'*64}
        with self.assertRaisesRegex(s.TerminalStoreError,'BOUND_CHANGED'):
            self.ledger.compare_and_swap(self.work,sha(self.head),raw(changed))
        self.assertEqual(reader(self.path).read_head(self.work),self.head)

    def test_process_death_after_reservation_stays_consumed_on_restart(self):
        script="""import os,sys
from pathlib import Path
from universal_video.owner_terminal_store import *
q=Path(sys.argv[1]);s=SQLiteTerminalLedger(path=q,storage_identity=StorageIdentity.observe(q),owner_guard=lambda:None,write_enabled=True)
s.reserve_enrollment(sys.argv[2].encode());os._exit(73)
"""
        # Obtain the exact attempt descriptor from the real driver without SQL.
        self.pg.mode='commit_before'
        self.rejected()
        attempt=reader(self.path).inspect_enrollment(self.work).attempt_raw
        self.setUp()
        done=subprocess.run([sys.executable,'-B','-c',script,str(self.path),attempt.decode()],
                            cwd=ROOT,capture_output=True,timeout=15,check=False)
        self.assertEqual(done.returncode,73,done.stderr.decode())
        self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'ATTEMPTED')
        self.rejected()
        self.assertEqual(self.pg.transactions,0)

    def test_competing_processes_have_one_reservation_winner(self):
        self.pg.mode='commit_before'
        self.rejected()
        attempt=reader(self.path).inspect_enrollment(self.work).attempt_raw
        self.setUp()
        script="""import sys
from pathlib import Path
from universal_video.owner_terminal_store import *
try:
 q=Path(sys.argv[1]);s=SQLiteTerminalLedger(path=q,storage_identity=StorageIdentity.observe(q),owner_guard=lambda:None,write_enabled=True)
 s.reserve_enrollment(sys.argv[2].encode());sys.exit(0)
except Exception:sys.exit(2)
"""
        argv=[sys.executable,'-B','-c',script,str(self.path),attempt.decode()]
        a=subprocess.Popen(argv,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        b=subprocess.Popen(argv,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        a.communicate(timeout=15);b.communicate(timeout=15)
        self.assertEqual(sorted([a.returncode,b.returncode]),[0,2])
        self.assertEqual(reader(self.path).inspect_enrollment(self.work).state,'ATTEMPTED')
        self.rejected()
        self.assertEqual(self.pg.transactions,0)

    def test_sql_source_is_exact_reviewed_template_and_unchanged_8192_event_bound(self):
        template=ROOT/'tests/fixtures/native-video/PREPARED-ENROLLMENT.sql.template'
        data=template.read_bytes()
        self.assertEqual(sha(data),e.TEMPLATE_SHA256)
        lines=data.decode().splitlines()
        self.assertEqual(e.SQL_LOCK_JOB,'\n'.join(lines[24:33]))
        self.assertEqual(e.SQL_LOCK_BATCH,'\n'.join(lines[35:42]))
        self.assertEqual(e.SQL_INSERT,'\n'.join(lines[48:87]))
        self.assertIn('j.lease_expires_at>clock_timestamp()',e.SQL_INSERT)
        self.assertIn("interval '1 hour'",e.SQL_INSERT)
        self.assertNotIn('ON CONFLICT',e.SQL_INSERT)
        self.assertIn('0 < len(raw) <= 8192',inspect.getsource(s.SQLiteTerminalLedger._history))


if __name__=='__main__':
    unittest.main()
