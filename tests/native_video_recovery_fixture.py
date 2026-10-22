"""Crash/restart tests of real adapter, journal and handoff code.

Owner CAS/windows/ACK, DB, filesystem durability and native identity are
explicit DOUBLES. No model is evidence of the real UID1001 capability.
"""
from contextlib import ExitStack,contextmanager,nullcontext
import copy
from dataclasses import replace
from pathlib import Path
import threading
from types import SimpleNamespace,ModuleType
import sys
import unittest
from unittest.mock import Mock,patch
from uuid import uuid4

from tests import test_video0063_owner_phase_local as fixtures
from universal_video import diana20_transport_protocol as p
from universal_video import private_one_job_entrypoint as entry
from universal_video import private_one_job_operator as operator
# The optional requests package is not installed in this local runtime. Import
# the REAL worker with a fail-fast HTTP dependency double; never install it.
_http=ModuleType('requests')
for _name in ('get','post','put','patch','delete','request','Session'):
    setattr(_http,_name,Mock(side_effect=AssertionError('HTTP_FORBIDDEN')))
_fcntl=ModuleType('fcntl')
_fcntl.flock=Mock(side_effect=AssertionError('NATIVE_FLOCK_NOT_QUALIFIED'))
with patch.dict(sys.modules,{'requests':_http,'fcntl':_fcntl}):
    from universal_video import neon_worker
from universal_video.diana20_terminal_queue import TerminalAckQueue,TerminalRecoveryAdapter
from universal_video.diana20_application_transport import ApplicationTransport
from universal_video.owner_application_channel import TerminalOwnerCapabilities
from universal_video.private_admission_retention import TerminalHandoffRetention
from database.video_post_finish_receipt import StagedPostFinishReceiptReader


class Harness:
    def __init__(self, test):
        self.f,self.job,self.intent,_,self.enrolled=fixtures.fixture()
        self.journal_bytes=None;self.heads={};self.owner_acks={};self.events=[]
        self.counts=dict(claim=0,work=0,finish=0,ack=0,read=0,bind=0,ids=0)
        self.fail_before=self.fail_after=self.ack_fault=None
        self.db_receipt=False;self.live=True;self.cas_lock=threading.Lock()
        self.store=fixtures.storage();self.prepared=None
        self.bundle=dict(status='PRIVATE_RESULT_STAGED',receipt_id=str(uuid4()),
                         bundle_sha256='a'*64,master_json_sha256='b'*64,ai_done_sha256='c'*64)
        self.selected=dict(saved_frame_observations={},completed_report_baseline={},
            result_mode='SHADOW_REVIEW_ONLY',database_persistence_allowed=False,
            canonical_promotion_allowed=False)
        self.meta=dict(intent_hash=self.intent.sha256,scope_nonce_hash='e'*64,principal='synthetic-login',
            target_identity_sha256='9'*64,enrollment_sha256=p.digest(self.enrolled),
            admission_record_sha256='c'*64,admission_anchor_sha256='d'*64)
        self.binding=dict(admission_id=self.f.args['admission_id'],expected_job_id=self.f.claim['job_id'],
                          expected_batch_id=self.f.claim['batch_id'])
        self.journal=operator.ClaimReceiptJournal(Path('UNUSED_SYNTHETIC'),self.binding)
        test.enterContext(patch.object(operator,'_read_local_json',side_effect=lambda _:copy.deepcopy(self.journal_bytes)))
        test.enterContext(patch.object(operator,'_write_json_exclusive',side_effect=self.write_journal))
        test.enterContext(patch.object(operator,'_replace_json',side_effect=self.replace_journal))
        test.enterContext(patch.object(operator.ClaimReceiptJournal,'preflight',return_value=None))
        test.enterContext(patch.object(entry,'_connect_queue',side_effect=self.f.connect))
        self.caps=TerminalOwnerCapabilities(self.guard,self.read_head,self.cas,self.bind_port,
            self.window,self.window,self.new_id,self.send_ack,self.read_ack)
        self.adapter=self.new_adapter()

    def write_journal(self,_path,value):
        if self.journal_bytes is not None:raise FileExistsError('MODEL_JOURNAL_EXISTS')
        self.journal_bytes=copy.deepcopy(value)
    def replace_journal(self,_path,value):self.journal_bytes=copy.deepcopy(value)
    def guard(self,scope):
        if not self.live:raise RuntimeError('MODEL_OWNER_REVOKED')
        self.events.append('guard')
    def read_head(self,key):return self.heads.get(key)
    def cas(self,key,expected,raw):
        phase=p.parse(raw)['phase']
        if self.fail_before==phase:raise OSError('MODEL_CAS_BEFORE')
        with self.cas_lock:
            prior=self.heads.get(key)
            if (None if prior is None else p.digest(prior))!=expected:raise OSError('MODEL_CAS_CONFLICT')
            self.heads[key]=raw
            self.events.append(phase)
        if self.fail_after==phase:raise OSError('MODEL_CAS_ACK_LOST')
    @contextmanager
    def window(self,_query):
        self.guard(b'fixture');self.events.append('window.open')
        try:yield
        finally:
            self.events.append('window.close')
            if self.ack_fault=='window-close':raise OSError('MODEL_WINDOW_CLOSE_LOST')
    def new_id(self,_scope):
        self.counts['ids']+=1;return 'f'*64
    def send_ack(self,query,deliver_once):
        self.counts['ack']+=1
        assert p.parse(self.heads[self.adapter.work_key])['phase']=='ACK_ATTEMPTED'
        self.events.append('ack.send')
        if self.ack_fault=='before-record':raise OSError('MODEL_ACK_BEFORE_RECORD')
        if self.ack_fault!='skip-wire':deliver_once()
        if self.ack_fault=='after-wire-before-record':raise OSError('MODEL_OWNER_RECORD_AFTER_WIRE_GAP')
        raw=p.canonical(dict(schema='terminal-owner-acked/v1',state='ACKED',binding=p.parse(query)))
        self.owner_acks[query]=raw
        if self.ack_fault=='lost-reply':raise OSError('MODEL_ACK_REPLY_LOST')
        if self.ack_fault=='wrong-binding':return raw+b' '
        return raw
    def read_ack(self,query):return self.owner_acks.get(query)
    def bind_port(self,claim_raw,evidence):
        self.counts['bind']+=1
        assert p.parse(evidence)['state']=='COMMIT_ACKNOWLEDGED'
        self.prepared=entry.prepare_private_one_job(**(self.f.args|{'assignment':self.f.assignment}))
        return self.prepared,self.meta
    @contextmanager
    def open_connection(self):yield self.f.connect('synthetic')
    def new_adapter(self,intent=None):
        # New process-local retention object; same modeled private directories.
        return TerminalRecoveryAdapter(owner=self.caps,retention=TerminalHandoffRetention(self.store.retention),
            journal=operator.ClaimReceiptJournal(self.journal.path,self.binding),intent=intent or self.intent,
            namespace='synthetic-approved-namespace',recovery_ports=dict(open_connection=self.open_connection,
                connection_validator=lambda _:None,access_token=lambda:'SYNTHETIC_TOKEN'))
    def start(self):
        # Use ORIGINAL journal instance for its immutable pre-COMMIT snapshot.
        self.adapter.journal=self.journal
        self.adapter.arm();self.commit()
    def commit(self,ack=True):
        self.journal.reserve()
        self.journal.publish_precommit(self.f.claim,self.f.args['worker_key'],self.f.args['admission_id'],
                                       self.f.args['source_fingerprint'])
        self.counts['claim']+=1;self.events.append('sql.claim.commit')
        if ack:self.journal.acknowledge_commit(self.f.claim)
    def bind(self):
        self.adapter.claim_committed(self.f.claim)
        return self.adapter.bind_committed(self.f.claim)
    def stage(self):
        self.counts['work']+=1
        with patch('universal_video.sealed_private_staging.stage_selected_drive_result_private',return_value=self.bundle):
            self.prepared.handoff.stage_selected_terminal(self.f.claim,self.f.args['worker_key'],
                                                         'SYNTHETIC_TOKEN',self.selected)
    def finish(self,lost=False):
        self.prepared.handoff.before_terminal_finish()
        self.events.append('sql.finish.commit');self.counts['finish']+=1;self.db_receipt=True
        if lost:raise OSError('MODEL_FINISH_REPLY_LOST')
    @contextmanager
    def readers(self):
        h=self
        def read(_reader,*_args):h.counts['read']+=1;return b'fixture-receipt'
        @contextmanager
        def ack_scope(_reader,_raw):
            h.events.append('db.auth.open')
            try:yield
            finally:h.events.append('db.auth.close')
        with patch.object(StagedPostFinishReceiptReader,'__call__',read), \
             patch.object(StagedPostFinishReceiptReader,'ack_scope',ack_scope):yield
    def ack(self):
        with self.readers():self.adapter.ack(self.f.claim,self.selected,{},self.adapter.reader,self.transport())
    def transport(self):
        t=ApplicationTransport(job=self.job,ticket=b'EXPLICIT_MODEL',frame_reader=Mock(),connect=Mock(),owner_assert=Mock())
        t._guard=Mock(return_value=None)
        t.control=Mock()
        t.completed=True;t.sealed_stream=b'EXPLICIT_STREAM_MODEL'
        t.result_raw=p.canonical(self.selected)
        return t
    def recover(self):
        h=self
        def read(_reader):
            h.counts['read']+=1
            if not h.db_receipt:raise RuntimeError('MODEL_TERMINAL_RECEIPT_MISSING')
            return b'fixture-receipt',{'job_status':'REVIEW_READY'}
        with patch('database.video_post_finish_receipt.PostFinishReceiptReader.recover',read):
            return self.new_adapter().recover()
