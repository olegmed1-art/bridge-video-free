"""Offline invariants; no live DB, systemd, cloud provider or credential use."""
from contextlib import contextmanager
import copy
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from ops import light_ci_once as ci
from ops import light_ci_once_admin as admin
from oracle_autopilot import contract


def fixture_root():
    """Bound cleanup to an explicit root or a directory in this checkout."""
    return Path(os.environ.get('LIGHT_CI_TEST_TMP') or (
        Path(__file__).resolve().parents[1]/'.light-ci-test-tmp')).resolve()


def runtime_manifest():
    """Use a retained public source fixture, including in shallow CI checkouts."""
    return os.environ.get('LIGHT_CI_RUNTIME_MANIFEST') or str(
        Path(__file__).resolve().parent/'fixtures/light_ci/ORIGINAL_RUNTIME.json')


def plan():
    now = ci.utc()
    key = 'light-ci-audit-pr2180-test-v1'
    return {'version':1,'source_main':'caae3b8d19d8fff1011d325d30dc5de1d5e94a3a','task_key':key,
            'goal':{'repository':ci.REPOSITORY,'pr_number':2180,'expected_head_sha':'fcf8b8a26e69523ea8c79ddef79bababad880153','require_draft':True},
            'target':dict(ci.TARGET),'unit':'school-autopilot-light-ci-'+ci.sha(key.encode())[:16]+'.service',
            'not_before':(now-dt.timedelta(seconds=1)).isoformat(),
            'expires_at':(now+dt.timedelta(minutes=20)).isoformat(),'max_seconds':180,'cost_cap_microusd':0}


def row(p, task_id):
    return dict(task_id=task_id,goal_type=ci.KIND,goal_json=p['goal'],current_step_key='github.ci.snapshot',
                step_cursor=0,lease_epoch=1,attempts=1,max_attempts=1,cost_cap_microusd=0,cost_reserved_microusd=0)


class Cursor:
    def __init__(self,value): self.value=value
    def fetchone(self): return self.value


class Database:
    """Transactional port: mutation staged by claim; commit failure can lose ACK."""
    def __init__(self, returned, lost=False):
        self.returned=returned; self.lost=lost; self.claims=0; self.commits=0; self.rollbacks=0; self.state='READY'
    def __enter__(self): return self
    def __exit__(self,*args): return False
    @contextmanager
    def transaction(self):
        before=self.state
        try:
            yield self
        except BaseException:
            self.state=before; self.rollbacks+=1
            raise
        self.commits+=1
        if self.lost: raise OSError('private credential text must never escape')
    def execute(self,sql,params=None):
        if 'claim_next_task' in sql:
            self.claims+=1; self.state='RUNNING'
            return Cursor(self.returned)
        return Cursor(None)


class Runtime:
    def __init__(self,p,task_id,db):
        self.p=p; self.task_id=task_id; self.db=db; self.fetches=0; self.completions=0; self.failures=0
        self.fetch_error=False; self.complete_error=False; self.read_error=False; self.after_commit_error=False
        self.summary=None; self.terminal=False
    def claim(self,p,task_id):
        return ci.claim_exact(lambda:self.db,contract,SimpleNamespace(worker_id='oracle-autopilot-light-1',lease_seconds=60),p,task_id)
    @contextmanager
    def lease(self,task): yield
    def fetch(self,goal):
        self.fetches+=1
        if self.fetch_error: raise OSError('dsn/password must not be serialized')
        return {'head_sha':goal['expected_head_sha'],'model_calls':0,'cost_actual_microusd':0,
                'production_mutation':False,'http_method':'GET','overall_state':'PASS'}
    def complete(self,task,summary):
        self.completions+=1
        if self.complete_error: raise OSError('ack missing before knowing commit')
        self.terminal=True; self.summary=summary
        if self.after_commit_error: raise OSError('commit happened but ack was lost')
    def status(self,task_id):
        if self.read_error: raise OSError('readback unavailable')
        return dict(task_id=task_id,task_key=self.p['task_key'],goal_type=ci.KIND,status='DONE',attempts=1,
                    lease_epoch=1,max_attempts=1,cost_cap_microusd=0,cost_reserved_microusd=0,
                    cost_actual_microusd=0,safe_summary_json=self.summary)
    def fail_known(self,task): self.failures+=1


class OnceTests(unittest.TestCase):
    def setUp(self):
        self.p=plan(); self.task_id=str(uuid.uuid4())
        base=fixture_root()
        base.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='fixture-',dir=base)
        self.directory=Path(self.temp.name).resolve()
        # All recursive fixture cleanup remains inside the explicitly named root.
        assert self.directory.parent==base
        self.addCleanup(self.temp.cleanup)
        self.journal=ci.Journal(self.directory)
        self.db=Database(row(self.p,self.task_id))
        self.rt=Runtime(self.p,self.task_id,self.db)

    def test_real_contract_validates_claim(self):
        result=ci.run_one(self.p,self.task_id,self.rt,self.journal)
        self.assertEqual(result['status'],'DONE_RUNTIME_READBACK')
        self.assertTrue(result['independent_owner_acceptance_required'])
        self.assertEqual((self.db.claims,self.db.commits,self.rt.fetches,self.rt.completions,self.rt.failures),(1,1,1,1,0))
        self.assertTrue((self.directory/'terminal.json').exists())

    def test_wrong_uuid_rolls_back_before_commit_and_no_get(self):
        self.db.returned['task_id']=str(uuid.uuid4())
        with self.assertRaisesRegex(ci.Blocked,'CLAIM_NOT_CONFIRMED'):
            ci.run_one(self.p,self.task_id,self.rt,self.journal)
        self.assertEqual((self.db.state,self.db.commits,self.db.rollbacks,self.rt.fetches),('READY',0,1,0))

    def test_wrong_goal_cannot_execute_any_other_capability(self):
        for field,value in [('goal_type','GITHUB_DRAFT_REPAIR_V1'),('cost_cap_microusd',1),('attempts',2),('max_attempts',3)]:
            db=Database({**row(self.p,self.task_id),field:value})
            with self.assertRaises(ci.Blocked):
                ci.claim_exact(lambda:db,contract,SimpleNamespace(worker_id='w',lease_seconds=60),self.p,self.task_id)
            self.assertEqual((db.state,db.commits),('READY',0))

    def test_empty_claim_is_not_success(self):
        self.db.returned=None
        with self.assertRaises(ci.Blocked): ci.run_one(self.p,self.task_id,self.rt,self.journal)
        self.assertEqual(self.db.commits,0)

    def test_lost_claim_commit_ack_never_executes_or_reclaims(self):
        self.db.lost=True
        with self.assertRaisesRegex(ci.Blocked,'CLAIM_NOT_CONFIRMED'):
            ci.run_one(self.p,self.task_id,self.rt,self.journal)
        self.assertEqual((self.db.state,self.db.claims,self.rt.fetches),('RUNNING',1,0))
        with self.assertRaises(FileExistsError): ci.run_one(self.p,self.task_id,self.rt,self.journal)
        self.assertEqual(self.db.claims,1)

    def test_read_failure_is_known_before_completion(self):
        self.rt.fetch_error=True
        with self.assertRaisesRegex(ci.Blocked,'READ_FAILED_NO_RETRY'):
            ci.run_one(self.p,self.task_id,self.rt,self.journal)
        self.assertEqual((self.rt.failures,self.rt.completions),(1,0))

    def test_ambiguous_completion_does_not_fail_or_repeat(self):
        for lost_after_commit in (False,True):
            directory=self.directory/str(lost_after_commit); directory.mkdir()
            rt=Runtime(self.p,self.task_id,Database(row(self.p,self.task_id)))
            rt.complete_error=not lost_after_commit; rt.after_commit_error=lost_after_commit
            with self.assertRaisesRegex(ci.Blocked,'COMPLETION_NOT_CONFIRMED'):
                ci.run_one(self.p,self.task_id,rt,ci.Journal(directory))
            self.assertEqual((rt.completions,rt.failures),(1,0))
            self.assertEqual(rt.terminal,lost_after_commit)
            self.assertFalse((directory/'terminal.json').exists())

    def test_lost_independent_status_ack_never_calls_fail(self):
        self.rt.read_error=True
        with self.assertRaises(ci.Blocked): ci.run_one(self.p,self.task_id,self.rt,self.journal)
        self.assertTrue(self.rt.terminal)
        self.assertEqual(self.rt.failures,0)

    def test_duplicate_terminal_invocation_has_zero_new_effects(self):
        ci.run_one(self.p,self.task_id,self.rt,self.journal)
        with self.assertRaises(FileExistsError): ci.run_one(self.p,self.task_id,self.rt,self.journal)
        self.assertEqual((self.db.claims,self.rt.fetches,self.rt.completions),(1,1,1))

    def test_partial_intent_latches_without_parsing_or_overwrite(self):
        (self.directory/'claim-intent.json').write_bytes(b'{partial')
        with self.assertRaises(FileExistsError): ci.run_one(self.p,self.task_id,self.rt,self.journal)
        self.assertEqual(self.db.claims,0)

    def test_two_simultaneous_invocations_only_one_claim(self):
        barrier=threading.Barrier(2); errors=[]
        def run():
            barrier.wait()
            try: ci.run_one(self.p,self.task_id,self.rt,self.journal)
            except FileExistsError: errors.append('latched')
        threads=[threading.Thread(target=run) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(errors,['latched'])
        self.assertEqual((self.db.claims,self.rt.completions),(1,1))

    def test_done_requires_exact_retained_summary_and_zero_cost(self):
        summary={'one':'result'}; self.rt.summary=summary
        r=self.rt.status(self.task_id)
        ci.done_status(r,self.task_id,self.p,summary)
        for field,value in [('status','RUNNING'),('attempts',2),('cost_actual_microusd',1),('safe_summary_json',{})]:
            with self.assertRaises(ci.Blocked): ci.done_status({**r,field:value},self.task_id,self.p,summary)

    def test_expired_or_short_window_has_no_intent_or_claim(self):
        p={**self.p,'expires_at':(ci.utc()+dt.timedelta(seconds=179)).isoformat()}
        with self.assertRaises(ci.Blocked): ci.run_one(p,self.task_id,self.rt,self.journal)
        self.assertEqual(list(self.directory.iterdir()),[])

    def test_goal_cannot_add_urls_native_or_spend(self):
        for path,value in [('cost_cap_microusd',1),('target',{}),('unit','other.service')]:
            with self.assertRaises(ci.Blocked): ci.validate_plan({**self.p,path:value})
        for goal in [{**self.p['goal'],'url':'https://example.com'}, {**self.p['goal'],'require_draft':False},
                     {**self.p['goal'],'pr_number':True}, {**self.p['goal'],'repository':'other/repo'}]:
            with self.assertRaises(ci.Blocked): ci.validate_plan({**self.p,'goal':goal})

    def test_duplicate_json_keys_refused(self):
        with self.assertRaises(ci.Blocked): ci.strict_json('{"goal":1,"goal":2}')

    def test_registration_binds_owner_plan_and_exact_new_task(self):
        r=dict(version=1,plan_sha256=ci.sha(ci.canonical(self.p)),task_id=self.task_id,task_key=self.p['task_key'],
               goal=self.p['goal'],status='READY',attempts=0,lease_epoch=0,max_attempts=1,cost_cap_microusd=0,
               observed_at=ci.utc().isoformat(),source='CI_ONCE_OWNER_REGISTRATION_V1')
        self.assertEqual(ci.registration(r,self.p),self.task_id)
        for field,value in [('status','DONE'),('attempts',1),('max_attempts',3),('plan_sha256','0'*64)]:
            with self.assertRaises(ci.Blocked): ci.registration({**r,field:value},self.p)

    def test_owner_registration_is_single_transaction_no_new_rights(self):
        requests=ci.owner_requests(self.p)
        q='\n'.join(requests['register']['sql_statements'])
        self.assertIn('CI_ONCE_KEY_ALREADY_EXISTS_INSPECT_ONLY',q)
        self.assertIn('max_attempts=1',q)
        self.assertIn('r.created IS DISTINCT FROM true',q)
        self.assertIn('lease_epoch=0',q)
        self.assertNotIn('GRANT ',q)
        self.assertNotIn('CREATE FUNCTION',q)
        self.assertNotIn('LOCK TABLE',q)
        self.assertNotIn('pg_stat_activity',q) # book transactions need not stop
        self.assertIn('READ ONLY',requests['inspect']['sql_statements'][0])

    def test_systemd_contract_limits_whole_cgroup_and_never_enables(self):
        path=Path('/var/lib/bridge-light-ci-once/packages/p/control.json')
        argv=admin.command(self.p,Path('/var/lib/bridge-light-ci-once/jobs/x'),path,'a'*64,probe=False)
        self.assertIn('--property=Type=exec',argv)
        self.assertIn('--property=RuntimeMaxSec=2min 45s',argv)
        self.assertIn('--property=KillMode=control-group',argv)
        self.assertIn('--property=Restart=no',argv)
        self.assertNotIn('--scope',argv)
        self.assertFalse(any('enable' in x or 'restart'==x for x in argv))
        self.assertIn('--property=EnvironmentFile=/etc/school-autopilot-production-light.env',argv)
        self.assertIn('--property=CPUQuota=25%',argv)
        self.assertFalse(any('CPUQuotaPerSecUSec=' in x for x in argv))
        properties=[x.split('=',2)[1] for x in argv if x.startswith('--property=')]
        self.assertEqual(len(properties),len(set(properties)))
        self.assertFalse(any(x.startswith('--setenv=AUTOPILOT_DATABASE_URL') for x in argv))
        self.assertEqual(admin.child_environment(),{'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
        self.assertFalse(any('postgres://' in x or 'postgresql://' in x or 'SECRET_SENTINEL' in x for x in argv))
        self.assertLessEqual(165+5+5,self.p['max_seconds'])

    def test_effective_sandbox_drift_refuses_before_any_db(self):
        props=ci.unit_properties(self.p,Path('/state'),probe=False)
        props.update(MainPID=str(os.getpid()),InvocationID='a'*32,ActiveState='active',SubState='running',
                     ControlGroup='/system.slice/'+self.p['unit'],
                     Environment='AUTOPILOT_ADMISSION_MODE=ACTIVE PYTHONDONTWRITEBYTECODE=1')
        def output(values):return SimpleNamespace(stdout='\n'.join(k+'='+v for k,v in values.items()))
        files=SimpleNamespace(stdout='a(sb) 1 "'+ci.ENV_FILE+'" false\n')
        with patch.dict(os.environ,{'INVOCATION_ID':'a'*32}),patch.object(ci.subprocess,'run',side_effect=[output(props),files]):
            self.assertEqual(ci.attest_unit(self.p,Path('/state'),probe=False)['invocation_id'],'a'*32)
        for field,value in [('KillMode','process'),('RuntimeMaxUSec','infinity'),('NoNewPrivileges','no'),
                            ('MainPID','0'),('CapabilityBoundingSet','cap_sys_admin'),('MemoryMax','infinity'),
                            ('ControlGroup','/system.slice/other.service'),
                            ('Environment',props['Environment']+' AUTOPILOT_DATABASE_URL=SECRET_SENTINEL')]:
            with patch.dict(os.environ,{'INVOCATION_ID':'a'*32}),patch.object(ci.subprocess,'run',side_effect=[output({**props,field:value}),files]):
                with self.assertRaises((ci.Blocked,ValueError)):ci.attest_unit(self.p,Path('/state'),probe=False)
        for file_value in ('a(sb) 0','a(sb) 1 "/tmp/untrusted.env" false',
                           'a(sb) 1 "'+ci.ENV_FILE+'" true'):
            with patch.dict(os.environ,{'INVOCATION_ID':'a'*32}),patch.object(ci.subprocess,'run',side_effect=[output(props),SimpleNamespace(stdout=file_value)]):
                with self.assertRaisesRegex(ci.Blocked,'UNIT_ENVIRONMENT_FILES'):
                    ci.attest_unit(self.p,Path('/state'),probe=False)

    def test_server_binding_requires_non_session_target_provenance(self):
        tags=[dict(name=k,setting=v[0],context=v[1],source='configuration file',reset_val=v[0],pending_restart=False)
              for k,v in ci.SERVER_TAGS.items()]
        ci.server_binding(tags)
        with self.assertRaises(ci.Blocked):ci.server_binding(tags[:2])
        for field,value in [('setting','other-target'),('context','user'),('source','session'),
                            ('reset_val','other-target'),('pending_restart',True),('pending_restart',None)]:
            with self.assertRaises(ci.Blocked):ci.server_binding([{**tags[0],field:value},*tags[1:]])
        q='\n'.join(ci.owner_requests(self.p)['register']['sql_statements'])
        self.assertIn("source='configuration file'",q)
        self.assertIn("CI_ONCE_OWNER_SERVER_BINDING",q)

    def test_environment_file_secrets_do_not_reach_installed_import_or_subprocesses(self):
        inherited={'AUTOPILOT_DATABASE_URL':'SECRET_SENTINEL','AUTOPILOT_RUNTIME_MODE':'SHADOW',
                   'AUTOPILOT_ADMISSION_MODE':'ACTIVE','AUTOPILOT_WORKER_ID':'oracle-autopilot-light-1',
                   'AUTOPILOT_TOKEN_BROKER_SECRET':'secret-broker','OPENAI_API_KEY':'secret-model',
                   'AWS_SECRET_ACCESS_KEY':'secret-aws','HTTPS_PROXY':'https://untrusted.example',
                   'AUTOPILOT_EXPECTED_DB_USER':'owner','PYTHONUSERBASE':'/untrusted'}
        with patch.dict(os.environ,inherited,clear=True):
            ci.restrict_environment()
            self.assertEqual(os.environ['AUTOPILOT_DATABASE_URL'],'SECRET_SENTINEL')
            self.assertEqual(os.environ['AUTOPILOT_EXPECTED_DB_USER'],ci.ROLE)
            self.assertFalse(any(k in os.environ for k in ('AUTOPILOT_TOKEN_BROKER_SECRET','OPENAI_API_KEY',
                                  'AWS_SECRET_ACCESS_KEY','HTTPS_PROXY','PYTHONUSERBASE')))
            self.assertNotIn('SECRET_SENTINEL',str(admin.child_environment()))
        for overrides in ({'PYTHONPATH':'/tmp/inject'},{'AUTOPILOT_DB_BACKEND':'postgresql'}):
            with patch.dict(os.environ,overrides,clear=True):
                with self.assertRaises(ci.Blocked):ci.restrict_environment()

    def test_exit_acceptance_requires_no_process_and_same_cgroup_invocation(self):
        identity={'unit':self.p['unit'],'invocation_id':'a'*32,'control_group':'/system.slice/'+self.p['unit']}
        props={'LoadState':'loaded','ActiveState':'inactive','MainPID':'0','ControlPID':'0','InvocationID':'a'*32}
        def output(values):return SimpleNamespace(stdout='\n'.join(k+'='+v for k,v in values.items()),returncode=0)
        with patch.object(admin.subprocess,'run',return_value=output(props)),patch.object(Path,'exists',return_value=False):
            self.assertTrue(admin.exited_unit(identity,self.p,probe=False)['cgroup_empty'])
        for field,value in [('MainPID','123'),('ControlPID','123'),('InvocationID','b'*32),('ActiveState','active')]:
            with patch.object(admin.subprocess,'run',return_value=output({**props,field:value})):
                with self.assertRaises(ci.Blocked):admin.exited_unit(identity,self.p,probe=False)

    def test_packet_preparation_is_create_only_and_source_tamper_is_rejected(self):
        packet=self.directory/'packet'
        result=admin.prepare(self.p,packet,runtime_manifest())
        with patch.object(ci,'read_root',side_effect=lambda path:Path(path).read_bytes()):
            accepted=admin.accepted_control(packet,result['package_sha256'],result['plan_sha256'])
            self.assertEqual(accepted['plan'],self.p)
            raw=(packet/'ops/light_ci_once.py').read_bytes()
            (packet/'ops/light_ci_once.py').write_bytes(b'# tampered')
            with self.assertRaisesRegex(ci.Blocked,'PACKAGE_SOURCE_DRIFT'):
                admin.accepted_control(packet,result['package_sha256'],result['plan_sha256'])
            (packet/'ops/light_ci_once.py').write_bytes(raw)
            (packet/'register.arguments.json').write_bytes(b'{"sql_statements":["unsafe"]}')
            with self.assertRaisesRegex(ci.Blocked,'OWNER_REQUEST_DRIFT'):
                admin.accepted_control(packet,result['package_sha256'],result['plan_sha256'])
        with self.assertRaises(FileExistsError):admin.prepare(self.p,packet,runtime_manifest())

    def test_owner_acceptance_rejects_unrelated_writes_and_missing_durable_evidence(self):
        names=('other_tasks','outbox','native_receipts','callback_receipts','terminal_receipts',
               'delivery_proofs','send_intents','publication_permits','protected_work','mailbox_registry','native_config')
        before={k:{'count':506 if k=='outbox' else 0,'sha256':'a'*64} for k in names}
        summary={'task_id':self.task_id,'task_kind':ci.KIND,'repository':ci.REPOSITORY,'pr_number':2180,
                 'head_sha':self.p['goal']['expected_head_sha'],'model_calls':0,'cost_actual_microusd':0,
                 'http_method':'GET','production_mutation':False,'runtime':'ORACLE_RESIDENT','overall_state':'FAIL'}
        self.rt.summary=summary
        task={**self.rt.status(self.task_id),'goal_json':self.p['goal'],'created_by':'DIRECTOR','source':'CHATGPT'}
        step={'task_id':self.task_id,'step_attempt_id':str(uuid.uuid4()),'status':'COMPLETED',
              'attempt_no':1,'lease_epoch':1,'step_key':'github.ci.snapshot','capability_name':'github.ci.snapshot',
              'executor_type':'ORACLE_RESIDENT','result_summary_json':summary}
        evidence={'task_id':self.task_id,'step_attempt_id':step['step_attempt_id'],'evidence_class':ci.EVIDENCE,
                  'provider':'ORACLE_RESIDENT','retained':True,'metadata_json':summary,'content_sha256':ci.sha(ci.canonical(summary))}
        result=dict(task=task,source_cardinality=1,active_tasks=0,outbox_active=0,outbox_archive=506,
                    native_enabled=False,steps=[step],evidence=[evidence],done_events=1)
        accepted=ci.accept_owner(self.p,self.task_id,before,copy.deepcopy(before),result)
        self.assertEqual(accepted['status'],'OWNER_TERMINAL_ACCEPTANCE_PASS') # useful negative CI report
        changed=copy.deepcopy(before); changed['send_intents']['sha256']='b'*64
        with self.assertRaisesRegex(ci.Blocked,'OWNER_UNRELATED_DRIFT'):
            ci.accept_owner(self.p,self.task_id,before,changed,result)
        for field,value in [('done_events',0),('evidence',[]),('steps',[step,step]),
                            ('source_cardinality',2),('native_enabled',True),('active_tasks',1)]:
            with self.assertRaises(ci.Blocked):ci.accept_owner(self.p,self.task_id,before,before,{**result,field:value})
        for field,value in [('retained',False),('content_sha256','b'*64),('metadata_json',{})]:
            with self.assertRaises(ci.Blocked):ci.accept_owner(self.p,self.task_id,before,before,
                         {**result,'evidence':[{**evidence,field:value}]})
        for field,value in [('capability_name','shadow.noop'),('executor_type','other'),('result_summary_json',{})]:
            with self.assertRaises(ci.Blocked):ci.accept_owner(self.p,self.task_id,before,before,
                         {**result,'steps':[{**step,field:value}]})

    def test_timeout_during_completion_is_ambiguous_and_cannot_replay(self):
        with patch.object(self.rt,'complete',side_effect=TimeoutError('secret connection details')):
            with self.assertRaisesRegex(ci.Blocked,'COMPLETION_NOT_CONFIRMED_INSPECT_ONLY'):
                ci.run_one(self.p,self.task_id,self.rt,self.journal)
        self.assertEqual(self.rt.failures,0)
        with self.assertRaises(FileExistsError):ci.run_one(self.p,self.task_id,self.rt,self.journal)
        self.assertEqual(self.db.claims,1)

    def test_local_package_source_and_runtime_pins(self):
        # Retained public sources and the original bundle formula, offline only.
        runtime=admin.original_runtime(Path(__file__).resolve().parents[1],runtime_manifest())
        self.assertEqual(runtime['sha256'],'3f7736a4fa7a6796eb9fa7be10e1d01e6cd04cb803d426991c942a91f7eb1c07')


if __name__=='__main__':unittest.main()
