"""New SOURCE_ONLY integration tests; no prior suites, SQL or app runtime."""
from pathlib import Path
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import ast,hashlib,importlib.util,json,sys,types,unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
_saved_namespaces=None
if not (ROOT/'bridge_contracts/__init__.py').exists():
    # The isolated additions-only stage has no base package. Do not replace a
    # real repository package when this file is collected after patch apply.
    _prefixes=('bridge_contracts','database')
    _saved_namespaces={k:v for k,v in sys.modules.items() if any(k==p or k.startswith(p+'.') for p in _prefixes)}
    for name in list(_saved_namespaces):sys.modules.pop(name)
    for name in _prefixes:
        package=types.ModuleType(name);package.__path__=[str(ROOT/name)];sys.modules[name]=package
from bridge_contracts import definition_v01_records as r,definition_v01_authority as a
from database.definition_contract_store import DefinitionContractStore,GATE_PROFILE_SHA256
from database.definition_contract_postgres import DefinitionPostgresBackend
from database import definition_contract_postgres as pg
from definition_store_memory import MemoryBackend,ReadOnlyPort
from definition_store_fixtures import SCOPE,ACTOR,sha
if _saved_namespaces is not None:
    for name in list(sys.modules):
        if any(name==p or name.startswith(p+'.') for p in _prefixes):sys.modules.pop(name)
    sys.modules.update(_saved_namespaces)


def audit(event,args):
    if event.startswith(('socket.','subprocess.','sqlite3.','os.spawn')) or event=='os.system':raise RuntimeError('OFFLINE_ONLY')
    if event=='import' and str(args[0]).split('.')[0] in {'sqlite3','psycopg','psycopg2'}:raise RuntimeError('NO_DB_DRIVER')
    if event=='open':
        mode,flags=args[1],args[2]
        if isinstance(mode,str) and any(c in mode for c in 'wax+') or isinstance(flags,int) and flags&0x643:raise RuntimeError('NO_FILE_WRITE')
    if event in ('os.remove','os.rename','os.mkdir','os.rmdir','os.truncate'):raise RuntimeError('NO_FS_MUTATION')


AUDIT_ACTIVE=False
def enable_offline_audit():
    global AUDIT_ACTIVE
    if not AUDIT_ACTIVE:sys.addaudithook(audit);AUDIT_ACTIVE=True


def manifest(b,version=1):
    return dict(evidence_mode='SOURCE_ONLY',contract='DEFINITION_TEST_STATE_PROPOSAL_0_1',
        candidate_hash=b.facts['candidate_hash'],scope=deepcopy(SCOPE),set_version=version,
        gate_profile_sha256=GATE_PROFILE_SHA256,max_attempt_us=1000,max_age_us=1000,
        cases=[dict(key='case:'+kind,version=1,kind=kind,input_sha256=sha(kind+'input'),
            oracle_sha256=sha(kind+'oracle'),executor_sha256=sha(kind+'executor'),
            source_binding_sha256=sha('source')) for kind in sorted(a.CASE_CHECK)])


def command(b,op,key,**extra):
    return dict(contract_version=a.VERSION,operation=op,request_key=key,candidate_hash=b.facts['candidate_hash'],scope=deepcopy(SCOPE),**extra)


def register(store,b,key='register:1',version=1):
    m=manifest(b,version);cmd=command(b,'REGISTER_SET',key,manifest_sha256=r.manifest_digest(m),set_version=version)
    return store.register_test_set(cmd,m),cmd,m


def reserve(store,b,m,kind='POSITIVE',key='reserve:1'):
    case=next(c for c in m['cases'] if c['kind']==kind)
    cmd=command(b,'RESERVE_ATTEMPT',key,manifest_sha256=r.manifest_digest(m),set_version=m['set_version'],
        case_key=case['key'],case_version=case['version'],case_kind=kind)
    return store.reserve_attempt(cmd),cmd,case


def finish(store,b,reserve_result,reserve_command,case,key='finish:1'):
    rec=reserve_result['record']
    cmd=dict(reserve_command,operation='RECORD_OUTCOME',request_key=key,attempt_key=rec['attempt_key'],
        reservation_sha256=rec['reservation_sha256'],result='PASS',artifact_sha256=sha(key))
    body=dict(finished_us=b.clock,observed_sha256=case['oracle_sha256'],executor_sha256=case['executor_sha256'])
    return store.record_outcome(cmd,body),cmd,body


def restore_command(b,key='restore:1'):
    return command(b,'RESTORE_KV',key,restore_binding_sha256=b.binding['model_binding_sha256'],
        verification_bundle_sha256=b.facts['receipt_set']['verification_bundle_sha256'])


class StoreIntegrationTests(unittest.TestCase):
    def setUp(self):self.b=MemoryBackend();self.store=DefinitionContractStore(self.b)
    def denied(self,result):
        self.assertEqual(result['status'],'ABSTAIN',result);self.assertFalse(result['admission_allowed']);self.assertEqual(result['genuine_Canon_checks_created'],0)
    def test_unbound_native_and_read_lease_cannot_write(self):
        m=manifest(self.b);c=command(self.b,'REGISTER_SET','r',manifest_sha256=r.manifest_digest(m),set_version=1)
        for store in [DefinitionContractStore(),DefinitionContractStore(DefinitionPostgresBackend()),DefinitionContractStore(ReadOnlyPort(self.b))]:
            with self.subTest(backend=type(store.backend).__name__):self.denied(store.register_test_set(c,m))
        self.assertIsNone(self.b.document)

    def test_register_reserve_outcome_current_readback_and_no_answer_leak(self):
        first,_,m=register(self.store,self.b);self.assertEqual(first['status'],'SOURCE_ONLY_STORED',first)
        for i,kind in enumerate(a.CASE_CHECK):
            rr,rc,case=reserve(self.store,self.b,m,kind,key='reserve:'+str(i));self.assertEqual(rr['status'],'SOURCE_ONLY_STORED',rr)
            out,_,_=finish(self.store,self.b,rr,rc,case,key='finish:'+str(i));self.assertEqual(out['status'],'SOURCE_ONLY_STORED',out)
        result=self.store.read_test_state(dict(candidate_hash=self.b.facts['candidate_hash'],scope=SCOPE))
        self.assertTrue(result['structural_eligibility'],result);self.assertEqual(result['status'],'SOURCE_ONLY_READBACK')
        raw=json.dumps(result);self.assertNotIn('oracle_sha256',raw);self.assertNotIn('input_sha256',raw)
        self.assertFalse(result['ready']);self.assertEqual(result['FULL_CALL'],'ABSTAIN');self.assertIsNone(result['session_binding'])
        self.assertEqual(len(self.b.audit_rows),9)

    def test_pending_new_attempt_blocks_completed_previous_run(self):
        _,_,m=register(self.store,self.b)
        for i,kind in enumerate(a.CASE_CHECK):
            rr,rc,case=reserve(self.store,self.b,m,kind,'r'+str(i));finish(self.store,self.b,rr,rc,case,'f'+str(i))
        reserve(self.store,self.b,m,'POSITIVE','new-pending')
        out=self.store.read_test_state(dict(candidate_hash=self.b.facts['candidate_hash'],scope=SCOPE))
        self.assertFalse(out['structural_eligibility']);self.assertTrue(any(x.startswith('PENDING:') for x in out['blockers']))

    def test_rpc_hash_version_candidate_scope_and_profile_mismatches(self):
        m=manifest(self.b);c=command(self.b,'REGISTER_SET','r',manifest_sha256=r.manifest_digest(m),set_version=1)
        for mode in ('route','hash','version','candidate','scope','profile','mode'):
            with self.subTest(mode=mode):
                cc=deepcopy(c);mm=deepcopy(m)
                if mode=='route':cc['operation']='RESTORE_KV'
                if mode=='hash':cc['manifest_sha256']=sha('wrong')
                if mode=='version':cc['set_version']=True
                if mode=='candidate':cc['candidate_hash']=sha('wrong')
                if mode=='scope':cc['scope']=dict(SCOPE,school='foreign')
                if mode=='profile':mm['gate_profile_sha256']=sha('wrong');cc['manifest_sha256']=r.manifest_digest(mm)
                if mode=='mode':cc['evidence_mode']='OBSERVED'
                self.denied(self.store.register_test_set(cc,mm));self.assertIsNone(self.b.document)

    def test_atomic_rollback_for_put_audit_and_exit_failure(self):
        for fault in ('AFTER_PUT','AUDIT_FAIL','BEFORE_COMMIT'):
            with self.subTest(fault=fault):
                b=MemoryBackend();s=DefinitionContractStore(b);b.fault=fault
                out,_,_=register(s,b);self.denied(out)
                self.assertIsNone(b.document);self.assertEqual(b.audit_rows,[])

    def test_exact_retry_no_duplicate_and_conflicting_payload_refuses(self):
        first,c,m=register(self.store,self.b);saved=deepcopy(self.b.document);audits=deepcopy(self.b.audit_rows)
        again=self.store.register_test_set(c,m);self.assertEqual(again['status'],'PRIOR_EFFECT',again)
        self.assertEqual(saved,self.b.document);self.assertEqual(audits,self.b.audit_rows)
        different=deepcopy(m);different['max_age_us']=999
        self.denied(self.store.register_test_set(c,different));self.assertEqual(saved,self.b.document)

    def test_lost_commit_ack_resolved_by_readonly_observer_not_repeat(self):
        self.b.fault='AFTER_COMMIT_ACK';out,c,m=register(self.store,self.b)
        self.assertEqual(out['status'],'OUTCOME_UNKNOWN',out);self.assertEqual(len(self.b.audit_rows),1)
        self.b.fault=None
        query=dict(candidate_hash=c['candidate_hash'],scope=c['scope'])
        observed=DefinitionContractStore(ReadOnlyPort(self.b)).read_command(query,request_key=c['request_key'],expected_effect_sha256=out['effect_sha256'])
        self.assertEqual(observed['status'],'SOURCE_ONLY_COMMAND_OBSERVED',observed)
        self.assertEqual(len(self.b.audit_rows),1)
        missing=self.store.read_command(query,request_key='absent',expected_effect_sha256=out['effect_sha256'])
        self.assertEqual(missing['status'],'OUTCOME_UNRESOLVED')

    def test_same_actor_ownership_and_terminal_outcome_immutable(self):
        _,_,m=register(self.store,self.b);rr,rc,case=reserve(self.store,self.b,m)
        done,cmd,body=finish(self.store,self.b,rr,rc,case);self.assertEqual(done['status'],'SOURCE_ONLY_STORED',done)
        saved=deepcopy(self.b.document)
        changed=dict(cmd,request_key='another-outcome',result='FAIL')
        self.denied(self.store.record_outcome(changed,body));self.assertEqual(saved,self.b.document)
        retry=self.store.record_outcome(cmd,body);self.assertEqual(retry['status'],'PRIOR_EFFECT',retry)

    def test_revoke_and_stale_final_fences_rollback(self):
        for stage in ('prepare','exit'):
            b=MemoryBackend();s=DefinitionContractStore(b)
            def revoke(x):
                next(v for v in x.facts['memberships'] if v['principal']==ACTOR and v['capability']==a.VERIFIER)['member']=False
            if stage=='prepare':b.on_second=revoke
            else:b.on_exit=revoke
            out,_,_=register(s,b);self.denied(out);self.assertIsNone(b.document);self.assertEqual(b.audit_rows,[])

    def test_suppressed_failure_does_not_become_success(self):
        self.b.suppress=True;self.b.fault='AFTER_PUT'
        out,_,_=register(self.store,self.b);self.denied(out);self.assertIsNone(self.b.document)

    def test_two_concurrent_reservations_get_contiguous_sequences(self):
        _,_,m=register(self.store,self.b)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda key:reserve(self.store,self.b,m,key=key)[0],['parallel:1','parallel:2']))
        self.assertEqual(sorted(x['record']['attempt_seq'] for x in results),[1,2],results)
        self.assertEqual(len(self.b.document['attempts']),2)

    def test_read_context_revoke_or_changed_document_refuses(self):
        register(self.store,self.b);query=dict(candidate_hash=self.b.facts['candidate_hash'],scope=SCOPE)
        self.b.fault='READ_CHANGED';self.denied(self.store.read_test_state(query))
        self.b.fault=None;self.b.on_exit=lambda x:x.context.update(source_active=False)
        self.denied(self.store.read_test_state(query))

    def test_public_read_queries_are_frozen_and_mutation_is_refused(self):
        register(self.store,self.b)
        for method in ('state','command'):
            with self.subTest(method=method):
                query=deepcopy(dict(candidate_hash=self.b.facts['candidate_hash'],scope=SCOPE))
                self.b.on_load=lambda b,n:query['scope'].update(scope='foreign')
                if method=='state':result=self.store.read_test_state(query)
                else:result=self.store.read_command(query,request_key='register:1',expected_effect_sha256=self.b.document['commands']['register:1']['effect_sha256'])
                self.denied(result)
        self.b.on_load=None


class RestoreIntegrationTests(unittest.TestCase):
    def setUp(self):self.b=MemoryBackend();self.b.seed_restore();self.store=DefinitionContractStore(self.b);self.cmd=restore_command(self.b)
    def denied(self,result):self.assertEqual(result['status'],'ABSTAIN',result)
    def test_restore_exact_scope_projection_and_atomic_command_audit(self):
        before=deepcopy(self.b.canon);out=self.store.restore_kv(self.cmd)
        self.assertEqual(out['status'],'SOURCE_ONLY_STORED',out)
        for group in ('items','versions','sources'):self.assertEqual(before[group],self.b.canon[group])
        self.assertEqual(self.b.canon['activations']['synthetic:new']['status'],'revoked')
        self.assertEqual(self.b.canon['activations']['synthetic:old'],self.b.binding['body']['preimage']['activations']['synthetic:old'])
        self.assertEqual(len(self.b.audit_rows),1);self.assertFalse(out['admission_allowed'])
        saved=deepcopy(self.b.canon);again=self.store.restore_kv(self.cmd)
        self.assertEqual(again['status'],'PRIOR_EFFECT',again);self.assertEqual(saved,self.b.canon);self.assertEqual(len(self.b.audit_rows),1)

    def test_partial_restore_and_audit_failure_leave_canon_and_ledger_unchanged(self):
        for fault in ('AFTER_FIRST_RESTORE_WRITE','AFTER_PUT','AUDIT_FAIL','BEFORE_COMMIT'):
            with self.subTest(fault=fault):
                b=MemoryBackend();b.seed_restore();b.fault=fault;s=DefinitionContractStore(b);before=deepcopy(b.canon)
                self.denied(s.restore_kv(restore_command(b)));self.assertEqual(before,b.canon)
                self.assertIsNone(b.document);self.assertEqual(b.audit_rows,[])

    def test_missing_genuine_interface_or_any_check_refuses_without_writes(self):
        for check in sorted(a.CHECKS):
            with self.subTest(check=check):
                b=MemoryBackend();b.seed_restore();before=deepcopy(b.canon)
                b.facts['receipt_set']['checks']=[x for x in b.facts['receipt_set']['checks'] if x['check_id']!=check]
                self.denied(DefinitionContractStore(b).restore_kv(restore_command(b)))
                self.assertEqual(before,b.canon);self.assertIsNone(b.document)

    def test_native_provider_absence_scope_actor_context_and_source_pins(self):
        for mode in ('scope','candidate','actor','context','native-mode','preimage'):
            with self.subTest(mode=mode):
                self.setUp();before=deepcopy(self.b.canon)
                if mode=='scope':self.cmd['scope']=dict(SCOPE,scope='foreign')
                if mode=='candidate':self.cmd['candidate_hash']=sha('foreign')
                if mode=='actor':self.b.restore_context['restorer_ref']='other'
                if mode=='context':self.b.restore_context['dependency_sha256']=sha('foreign')
                if mode=='native-mode':self.b.canon['evidence_mode']='OBSERVED'
                if mode=='preimage':self.b.canon['sources']['synthetic:kv1'][0]['identity_sha256']=sha('replaced')
                self.denied(self.store.restore_kv(self.cmd))
                if mode!='native-mode' and mode!='preimage':self.assertEqual(before,self.b.canon)
                self.assertIsNone(self.b.document)

    def test_restore_ack_loss_and_historical_effect_do_not_reapply(self):
        self.b.fault='AFTER_COMMIT_ACK';out=self.store.restore_kv(self.cmd)
        self.assertEqual(out['status'],'OUTCOME_UNKNOWN',out);self.b.fault=None
        q=dict(candidate_hash=self.cmd['candidate_hash'],scope=SCOPE)
        obs=self.store.read_command(q,request_key=self.cmd['request_key'],expected_effect_sha256=out['effect_sha256'])
        self.assertEqual(obs['status'],'SOURCE_ONLY_COMMAND_OBSERVED',obs)
        self.b.canon['items']['synthetic:ki']['status']='retired';saved=deepcopy(self.b.canon)
        again=self.store.restore_kv(self.cmd);self.assertEqual(again['status'],'PRIOR_EFFECT_CURRENT_STATE_CHANGED',again)
        self.assertEqual(saved,self.b.canon);self.assertEqual(len(self.b.audit_rows),1)


class ProtocolConnection:
    """SOURCE_ONLY DB-API command recorder, no SQL engine or connection."""
    def __init__(self):
        self.info=types.SimpleNamespace(transaction_status=0);self.autocommit=False
        self.xid=0;self.readonly=False;self.closed=False;self.commits=0;self.rollbacks=0
        self.pending={};self.committed={};self.member=True;self.commit_fault=False;self.close_fault=False
    def execute(self,sql):
        if sql.startswith('BEGIN'):
            self.xid+=1;self.info.transaction_status=1;self.readonly='READ ONLY' in sql
    def cursor(self):return ProtocolCursor(self)
    def commit(self):
        self.committed.update(self.pending);self.pending={};self.commits+=1;self.info.transaction_status=0
        if self.commit_fault:raise ConnectionError('SOURCE_ONLY sensitive message must not escape')
    def rollback(self):self.pending={};self.rollbacks+=1;self.info.transaction_status=0
    def close(self):
        if self.close_fault:raise ConnectionError('SOURCE_ONLY close lost')
        self.closed=True


class ProtocolCursor:
    def __init__(self,conn):self.conn=conn;self.description=[];self.row=None
    def execute(self,sql,args):
        c=self.conn
        if sql==pg.ROOT_SQL:
            row=dict(database_name='synthetic-db',database_oid='123',system_identifier='456',server_version_num=180000,
                session_user=ACTOR,current_role='synthetic:existing-role',backend_pid=9,transaction_id=str(c.xid),isolation='read committed',
                read_only='on' if c.readonly else 'off',in_recovery=False,rolcanlogin=True)
        elif sql==pg.MEMBER_SQL:row=dict(rolcanlogin=True,member=c.member)
        elif 'pg_advisory_xact_lock' in sql:row={'locked':None}
        else:raise AssertionError('Unexpected SOURCE_ONLY protocol command')
        self.description=[(x,) for x in row];self.row=tuple(row.values())
    def fetchone(self):return self.row
    def close(self):pass


class ProtocolContextProvider:
    def __init__(self,mode=None):self.mode=mode;self.on_fence=None
    @contextmanager
    def lease(self,conn,query,*,readonly):
        context=types.SimpleNamespace(test_context=lambda *a:{})
        def fence(c):
            if self.on_fence:self.on_fence(c)
            if self.mode=='ROOT_REPLACEMENT':c.commit();c.execute('BEGIN ISOLATION LEVEL READ COMMITTED READ WRITE')
            if self.mode=='REVOKE':c.member=False
            if self.mode=='AUTHORITY_FLAG_RETURN':return {'authorized':True}
        context.fence=fence
        try:yield context
        except Exception:
            if self.mode!='SUPPRESS':raise
        if self.mode=='EXIT_FAILURE':raise ValueError('SOURCE_ONLY postcommit lease failure')


class NativeProtocolTests(unittest.TestCase):
    def backend(self,mode=None):
        c=ProtocolConnection()
        pin=pg.DefinitionTargetPin('synthetic-db','123','456',180000,ACTOR,'synthetic:existing-role','synthetic:owner',sha('candidate'),SCOPE)
        b=pg.DefinitionPostgresBackend(connection_provider=lambda:c,context_provider=ProtocolContextProvider(mode),target=pin)
        q=dict(operation='REGISTER_SET',candidate_hash=sha('candidate'),scope=SCOPE)
        return c,b,q
    def test_same_root_commit_and_body_rollback(self):
        c,b,q=self.backend()
        with b.transaction(q):c.pending['row']='source-only'
        self.assertEqual(c.committed,{'row':'source-only'});self.assertTrue(c.closed)
        c,b,q=self.backend()
        with self.assertRaises(pg.StoreDenied):
            with b.transaction(q):c.pending['row']='source-only';raise pg.StoreDenied('SOURCE_ONLY_BODY_FAILURE')
        self.assertEqual(c.committed,{});self.assertEqual(c.rollbacks,1)
    def test_root_replacement_after_body_is_unknown_even_if_new_root_rolls_back(self):
        c,b,q=self.backend('ROOT_REPLACEMENT')
        with self.assertRaises(pg.OutcomeUnknown):
            with b.transaction(q):c.pending['row']='source-only committed by bad context'
        self.assertEqual(c.commits,1);self.assertIn('row',c.committed);self.assertEqual(c.rollbacks,1)
    def test_commit_ack_and_postcommit_context_failure_are_unknown(self):
        for mode in ('ACK','EXIT_FAILURE'):
            with self.subTest(mode=mode):
                c,b,q=self.backend(None if mode=='ACK' else mode);c.commit_fault=mode=='ACK'
                with self.assertRaises(pg.OutcomeUnknown) as caught:
                    with b.transaction(q):c.pending['row']='source-only'
                self.assertIn('row',c.committed);self.assertNotIn('sensitive',str(caught.exception))
    def test_final_native_membership_revoke_rolls_back(self):
        c,b,q=self.backend('REVOKE')
        with self.assertRaises(pg.StoreDenied):
            with b.transaction(q):c.pending['row']='source-only'
        self.assertEqual(c.committed,{});self.assertEqual(c.rollbacks,1)
    def test_suppressing_context_cannot_commit_or_return_success(self):
        c,b,q=self.backend('SUPPRESS')
        with self.assertRaises(pg.StoreDenied):
            with b.transaction(q):c.pending['row']='source-only';raise pg.StoreDenied('SOURCE_ONLY_BODY_FAILURE')
        self.assertEqual(c.committed,{});self.assertEqual(c.commits,0)
    def test_unconfirmed_close_quarantines_owned_connection(self):
        c,b,q=self.backend();c.close_fault=True
        try:
            with self.assertRaises(pg.OutcomeUnknown):
                with b.transaction(q):pass
            self.assertIs(pg._OWNED.get(id(c)),c)
        finally:
            # Dispose only this test double; not a live ownership recovery path.
            c.close_fault=False;c.close();pg._OWNED.pop(id(c),None)
    def test_borrowed_connection_or_foreign_scope_not_touched(self):
        c,b,q=self.backend();c.info.transaction_status=1
        with self.assertRaises(pg.StoreDenied):
            with b.transaction(q):pass
        self.assertFalse(c.closed);self.assertEqual(c.rollbacks,0)
        c,b,q=self.backend();q['scope']=dict(SCOPE,school='foreign')
        with self.assertRaises(pg.StoreDenied):
            with b.transaction(q):pass
        self.assertFalse(c.closed)
    def test_read_context_is_not_a_mutation_authority_provider(self):
        c,b,q=self.backend()
        with self.assertRaises(pg.StoreDenied):
            with b.transaction(q) as tx:tx.authority(q)
        self.assertEqual(c.committed,{});self.assertEqual(c.commits,0)
    def test_fence_returned_authority_flag_is_rejected(self):
        c,b,q=self.backend('AUTHORITY_FLAG_RETURN')
        with self.assertRaises(pg.StoreDenied):
            with b.transaction(q):c.pending['row']='source-only'
        self.assertEqual(c.committed,{});self.assertEqual(c.commits,0)
    def test_native_target_pin_and_per_operation_scope_fence(self):
        c,b,q=self.backend();b.context_provider.on_fence=lambda conn:b.target.scope.update(scope='foreign')
        with self.assertRaises(pg.StoreDenied):
            with b.transaction(q):c.pending['row']='source-only'
        self.assertEqual(c.committed,{})
        c,b,q=self.backend()
        with self.assertRaises(pg.StoreDenied):
            with b.transaction(q) as tx:tx.key(dict(q,scope=dict(SCOPE,scope='foreign')))
        self.assertEqual(c.commits,0)


@unittest.skipUnless(importlib.util.find_spec('pglast') is not None,'Static SQL parser unavailable; PostgreSQL qualification remains NOT_RUN')
class StaticSchemaTests(unittest.TestCase):
    def test_postgres_outer_syntax_and_no_grants_roles_or_bidding_runtime_writes(self):
        import pglast,re
        source=(ROOT/'database/migrations/0375_workflow_definition_contract_store.sql').read_text(encoding='utf-8')
        self.assertGreater(len(pglast.parse_sql(source)),0)
        self.assertGreater(len(pglast.parse_sql(pg.ROOT_SQL)),0)
        self.assertEqual(len(pglast.parse_plpgsql(source)),3)
        self.assertIsNone(re.search(r'(?mi)^\s*(GRANT|CREATE\s+ROLE)\b',source))
        self.assertEqual(source.count('BEFORE TRUNCATE'),2)
        self.assertNotIn('bidding.runtime_activation',source);self.assertNotIn('bidding.rule_test',source)


class CountingResult(unittest.TextTestResult):
    subcases=0
    def addSubTest(self,test,subtest,err):self.subcases+=1;super().addSubTest(test,subtest,err)


if __name__=='__main__':
    enable_offline_audit()
    result=unittest.TextTestRunner(verbosity=2,resultclass=CountingResult).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print(json.dumps(dict(status='SOURCE_ONLY_NOT_PG_QUALIFICATION',methods=result.testsRun,subcases=result.subcases,
        failures=len(result.failures),errors=len(result.errors),live_SQL=0,genuine_checks=0,offline_audit='ENFORCED')))
    raise SystemExit(0 if result.wasSuccessful() else 1)
