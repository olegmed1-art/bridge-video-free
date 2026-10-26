"""Explicit SOURCE_ONLY transactional adapter; never a PostgreSQL substitute."""
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
from bridge_contracts import definition_v01_records as r
from bridge_contracts import definition_v01_authority as a
from database.definition_contract_store import StoreDenied,OutcomeUnknown,need,GATE_PROFILE_SHA256
from definition_store_fixtures import fixture,restore_fixture,ACTOR,SCOPE,sha


class MemoryBackend:
    mutation_capable=True;mode='SOURCE_ONLY'
    def __init__(self):
        _,self.facts=fixture()
        self.facts['gate_profile_sha256']=GATE_PROFILE_SHA256
        for cap in set(a.CAPABILITY.values())|{'bridge_school_reader'}:
            if not any(m['principal']==ACTOR and m['capability']==cap for m in self.facts['memberships']):
                self.facts['memberships'].append(dict(principal=ACTOR,capability=cap,member=True,rolcanlogin=True))
        self.context=dict(evidence_mode='SOURCE_ONLY',school_snapshot_sha256=sha('snapshot'),source_policy_sha256=sha('policy'),
            membership_sha256=sha('members'),policy_from_us=0,policy_until_us=10000,candidate_active=True,set_active=True,source_active=True,attestors_active=True)
        self.document=None;self.audit_rows=[];self.canon=None;self.binding=None;self.restore_context=None
        self.clock=100;self.lock=RLock();self.root_no=0;self.fault=None;self.on_second=None;self.on_exit=None;self.on_load=None;self.suppress=False
        self.commits=0;self.rollbacks=0;self.authority_calls=0

    def bind_candidate(self,candidate):
        self.facts['candidate_hash']=candidate
        for row in self.facts['receipt_set']['checks']+self.facts['receipt_set']['assignments']:row['candidate_hash']=candidate
        self.facts['receipt_set']['candidate_hash']=candidate

    def seed_restore(self,predecessor=True):
        _,state,context,binding=restore_fixture(predecessor=predecessor)
        context['restorer_ref']=ACTOR
        context['school_snapshot_sha256']=self.facts['canon_snapshot_sha256']
        self.canon=deepcopy(state);self.binding=binding;self.restore_context=context;self.clock=200
        self.bind_candidate(binding['body']['candidate_hash'])
        self.facts['restore_binding_sha256']=binding['model_binding_sha256']
        self.facts['restore_context_sha256']=r.digest('DEFINITION_RESTORE_CONTEXT_0_1',context)
        self.facts['predecessor_present']=predecessor
        bundle=self.facts['receipt_set'];bundle['restore_binding_sha256']=binding['model_binding_sha256']
        bundle['restore_context_sha256']=self.facts['restore_context_sha256']
        for row in bundle['checks']+bundle['assignments']:row['restore_binding_sha256']=binding['model_binding_sha256']

    @contextmanager
    def transaction(self,command):
        with self.lock:
            self.root_no+=1
            tx=MemoryTx(self,False,self.root_no);start=a.digest('SOURCE_ONLY_FENCE',self.facts)
            try:
                yield tx
                if self.on_exit:self.on_exit(self)
                need(start==a.digest('SOURCE_ONLY_FENCE',self.facts),'SOURCE_ONLY_EXIT_REVOKE_FENCE')
                need(self.clock<self.facts['lease_until_us'],'SOURCE_ONLY_EXIT_EXPIRY')
                if self.fault=='BEFORE_COMMIT':raise StoreDenied('BEFORE_COMMIT_TEST_FAULT')
                self.document=tx.document;self.audit_rows=tx.audit_rows;self.canon=tx.canon;self.commits+=1
                if self.fault=='AFTER_COMMIT_ACK':raise OutcomeUnknown('SOURCE_ONLY_LOST_ACK')
            except OutcomeUnknown:raise
            except BaseException:
                self.rollbacks+=1
                if not self.suppress:raise

    @contextmanager
    def observer(self,query):
        with self.lock:
            context_before=deepcopy(self.context)
            need(query['scope']==self.facts['scope'] and query['candidate_hash']==self.facts['candidate_hash'],'READER_SCOPE')
            a.membership(self.facts,self.facts['session_user'],'bridge_school_reader')
            tx=MemoryTx(self,True,self.root_no)
            yield tx
            if self.on_exit:self.on_exit(self)
            need(context_before==self.context,'SOURCE_ONLY_READ_CONTEXT_REVOKED')
            a.membership(self.facts,self.facts['session_user'],'bridge_school_reader')


class ReadOnlyPort:
    mutation_capable=False
    def __init__(self,parent):self.parent=parent
    def observer(self,query):return self.parent.observer(query)


class MemoryTx:
    mode='SOURCE_ONLY'
    def __init__(self,b,readonly,root):
        self.b=b;self.readonly=readonly;self.root=root;self.reads=0
        self.document=deepcopy(b.document);self.audit_rows=deepcopy(b.audit_rows);self.canon=deepcopy(b.canon)
        self.auth_reads=0
    def now_us(self):return self.b.clock
    def load(self,query):
        self.reads+=1
        if self.b.on_load:self.b.on_load(self.b,self.reads)
        if self.reads==2 and self.b.fault=='READ_CHANGED':
            other=deepcopy(self.document);other['revision']+=1;return other
        return deepcopy(self.document)
    def authority(self,request):
        self.auth_reads+=1;self.b.authority_calls+=1
        if self.auth_reads==2 and self.b.on_second:self.b.on_second(self.b)
        facts=deepcopy(self.b.facts)
        facts['root']['transaction_id']='synthetic:root:'+str(self.root)
        if self.document and self.document['current_set']:
            manifest=self.document['sets'][self.document['current_set']]
            match=next((c for c in manifest['cases'] if c['key']==request.get('case_key')),None)
            if match:facts['case']=dict(key=match['key'],version=match['version'],kind=match['kind'],executor=self.b.facts['case']['executor'])
        return facts
    def test_context(self,request):return deepcopy(self.b.context)
    def restore_material(self,request):return dict(state=deepcopy(self.canon),binding=deepcopy(self.b.binding),context=deepcopy(self.b.restore_context))
    def current_restore_hash(self,request):
        body=self.b.binding['body'];return r.digest('RESTORED_PROJECTION_0_1',r.projection(self.canon,body['item_key'],request['scope']))
    def apply_restore(self,request,writes,postimage):
        need(not self.readonly,'READ_ONLY')
        for i,w in enumerate(writes):
            need(self.canon['activations'][w['key']]==postimage['activations'][w['key']],'ACTIVATION_CAS')
            self.canon['activations'][w['key']].update(w['set'])
            if i==0 and self.b.fault=='AFTER_FIRST_RESTORE_WRITE':raise StoreDenied('PARTIAL_RESTORE_TEST_FAULT')
    def put(self,request,document,*,expected_revision):
        need(not self.readonly,'READ_ONLY')
        need((0 if self.document is None else self.document['revision'])==expected_revision,'DOCUMENT_CAS')
        self.document=deepcopy(document)
        if self.b.fault=='AFTER_PUT':raise StoreDenied('AFTER_PUT_TEST_FAULT')
    def audit(self,request,event):
        need(not self.readonly,'READ_ONLY')
        if self.b.fault=='AUDIT_FAIL':raise StoreDenied('AUDIT_TEST_FAULT')
        need(not any(row[0]==request['request_key'] for row in self.audit_rows),'AUDIT_KEY_UNIQUE')
        self.audit_rows.append((request['request_key'],deepcopy(event)))
