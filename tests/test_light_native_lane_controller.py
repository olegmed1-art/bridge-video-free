"""Controller boundaries that were missing from isolated lane primitives."""
import base64
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import pytest

from ops import light_native_lane_controller as target
from ops import light_native_lane_owner_runner as runner


def test_controller_cleanup_imports_with_no_site_packages():
    root=Path(__file__).resolve().parents[1]
    script="import sys;sys.path.insert(0,sys.argv[1]);from ops import light_native_lane_controller;assert 'psycopg' not in sys.modules"
    result=subprocess.run([sys.executable,'-I','-S','-B','-c',script,str(root)],capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr.decode()


def test_private_package_is_independent_of_historical_runtime(monkeypatch):
    original=target.release.HELPERS
    monkeypatch.setattr(target.release.source,'git',lambda repo,*args:b'# exact committed helper\n')
    raw=target.package(Path('/not-read'),'a'*40)
    package=target.validate_package(raw,'a'*40,target.sha(raw))
    assert set(target.EXTRA)<=set(package['helpers'])
    assert target.release.HELPERS==original
    assert 'ops/light_native_lane_controller.py' not in original
    with pytest.raises(RuntimeError):target.validate_package(raw,'b'*40,target.sha(raw))
    with pytest.raises(RuntimeError):target.validate_package(raw+b' ','a'*40,target.sha(raw))
    bad=deepcopy(package);bad['helpers']['../../unexpected.py']=''
    wrong=target.encoded(bad)
    with pytest.raises(RuntimeError):target.validate_package(wrong,'a'*40,target.sha(wrong))


def test_bootstrap_rejects_unaccepted_bytes_before_import_or_effects():
    code=runner.bootstrap('a'*40,'b'*64,'c'*64,'d'*64,'e'*64,123,2)
    # Actual isolated interpreter: all hashes wrong, no import/host command can occur.
    wire=target.encoded(dict(controller=base64.b64encode(b'{}').decode(),runtime='',payload='',driver='',
                            credential='never-log-private-password',token='never-log-private-token'))
    result=subprocess.run([sys.executable,'-I','-S','-B','-c',code],input=wire,capture_output=True,timeout=10)
    assert result.returncode==2
    assert json.loads(result.stdout)==dict(audit='LIGHT_LANE_OWNER_REFUSED',reason='UNCLASSIFIED')
    assert not result.stderr and b'private' not in result.stdout


@pytest.mark.skipif(os.geteuid()!=0,reason='real root helper files')
def test_create_only_helper_tree_detects_replacement(tmp_path,monkeypatch):
    root=tmp_path/'owner';root.mkdir();(root/'controllers').mkdir()
    monkeypatch.setattr(target,'ROOT',root)
    monkeypatch.setattr(target.install,'root_parent',lambda path:None)
    monkeypatch.setattr(target.release.source,'git',lambda repo,*args:b'# accepted helper\n')
    raw=target.package(tmp_path,'a'*40);package=target.parse(raw)
    target.bootstrap_helpers(package)
    assert target.verify_helpers('a'*40,target.sha(raw))==root/'controllers'/('a'*40)
    with pytest.raises(FileExistsError):target.bootstrap_helpers(package)
    path=root/'controllers'/('a'*40)/'ops/light_native_lane_execution.py'
    path.write_text('# drift')
    with pytest.raises(RuntimeError,match='HELPER_CHANGED'):target.verify_helpers('a'*40,target.sha(raw))


def test_supervisor_loads_current_helpers_before_retained_runtime():
    raw=target.supervisor_source('a'*40).decode()
    compile(raw,'supervisor','exec')
    assert raw.index('sys.path.insert')<raw.index('sys.path.append')
    assert str(target.helpers_root('a'*40)) in raw
    assert target.install.RETAINED_SOURCE in raw
    assert 'credential' not in raw and 'token' not in raw


@pytest.mark.skipif(os.geteuid()!=0,reason='real root request metadata')
def test_launch_retains_no_credential_and_waits_for_cleanup_receipt(tmp_path,monkeypatch):
    install=target.install
    directory=tmp_path/'owner';directory.mkdir(mode=0o700)
    root=tmp_path/'execution';monkeypatch.setattr(target.execution,'ROOT',root)
    monkeypatch.setattr(install,'root_parent',lambda path:None)
    now=1000
    monkeypatch.setattr(target.time,'time',lambda:now)
    retain=target.retain
    retain(directory/'permit.json',target.encoded({'expires_at':1900}))
    row=dict(ActiveState='active',SubState='running',MainPID='123',InvocationID='a'*32,NRestarts='0')
    monkeypatch.setattr(install,'verify_hold_process',lambda *a:row)
    monkeypatch.setattr(target.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid()))
    prior=install.hold.HoldIdentity('autopilot-lite-vnic',123,'b'*32,'/prior','private')
    calls=[]
    def execute(command,**kwargs):
        wire=target.parse(kwargs['input'])
        assert wire=={'credential':'private-password','token':'private-token'}
        assert kwargs['capture_output'] and command[1:3]==['--wait','--pipe']
        digest=target.parse(target.read(directory/'execution.json'))['request_sha256']
        request=target.parse(target.read(root/digest/'request.json'))
        context=target.read(root/digest/'owner-context.json',request['owner_context_sha256'])
        assert target.sha(target.read(root/digest/'supervisor.py'))==request['supervisor_sha256']
        assert request['expires_at']==1420 and request['seconds']==420
        assert target.parse(context)['run_id']==123
        retain(root/digest/'stopped-hold.json',target.encoded(dict(state='STOPPED_HOLD')))
        calls.append('cleanup-complete')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(target.subprocess,'run',execute)
    guard=SimpleNamespace(run_id=123,attempt=1,assert_running=lambda:calls.append('origin-live'))
    value=dict(source='a'*40,accepted_controller_sha256='b'*64)
    result=target.launch(b'wheels','private-password','private-token',SimpleNamespace(digest='c'*64),directory,
        value,prior,dict(protected={},protected_sha256='d'*64),
        dict(cursor_sha256='e'*64,dispatch_id='12345678-1234-4234-8234-123456789012'),guard)
    assert result['state']=='STOPPED_HOLD' and calls==['origin-live','cleanup-complete']
    for path in tmp_path.rglob('*'):
        if path.is_file():assert b'private-password' not in path.read_bytes() and b'private-token' not in path.read_bytes()

@pytest.fixture
def cleanup_db(tmp_path,monkeypatch):
    from database import light_native_pilot_intake as intake
    before=dict(version=1,plan_sha256='a'*64,target=intake.EXPECTED_TARGET,
        native_config=dict(enabled=False,cutover_at=None),
        autopilot_role=dict(can_repair=True,updated_at='old'))
    receipt=dict(snapshot_sha256='b'*64,dispatch_id='dispatch',
        applied_config=dict(enabled=True,cutover_at='new'),
        applied_role=dict(can_repair=False,updated_at='new'))
    state=dict(config=deepcopy(receipt['applied_config']),role=deepcopy(receipt['applied_role']),writes=0,observations=0)
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def transaction(self):return self
        def execute(self,*args):pass
    driver=SimpleNamespace(connect=lambda **kwargs:Connection())
    monkeypatch.setattr(intake.engine,'load_manifest',lambda *args:before)
    monkeypatch.setattr(intake.engine,'identity',lambda *args:None)
    monkeypatch.setattr(intake,'terminal_evidence',lambda *args:{})
    def terminal(*args):state['observations']+=1;return True
    monkeypatch.setattr(intake,'_terminal_rows',terminal)
    monkeypatch.setattr(intake,'one',lambda conn,sql:deepcopy(state['config' if 'native_cli_config' in sql else 'role']))
    def restore(*args,**kwargs):
        kwargs['effect_guard']()
        state['writes']+=1
        state['config']=deepcopy(before['native_config']);state['role']=deepcopy(before['autopilot_role'])
        state['role']['updated_at']='restored'
    monkeypatch.setattr(intake,'restore_controls_after_terminal',restore)
    call=lambda:target.reconcile_controls(driver,{},SimpleNamespace(digest='a'*64),receipt,b'{}',tmp_path,
                                          SimpleNamespace(assert_running=lambda:None))
    return state,before,call


def test_controls_lost_ack_reconciles_without_repeating_sql(cleanup_db):
    state,before,call=cleanup_db
    first=call()
    assert state['writes']==1 and state['observations']==2 and first['controls_restored']
    assert call()==first
    assert state['writes']==1 and state['observations']==3


@pytest.mark.parametrize('drift',['config','role','mixed'])
def test_controls_reconciliation_refuses_partial_or_foreign_change(cleanup_db,drift):
    state,before,call=cleanup_db
    if drift=='mixed':state['config']=deepcopy(before['native_config'])
    else:state[drift]['unexpected']='foreign'
    with pytest.raises(RuntimeError,match='DB_RESTORE_CHANGED'):call()
    assert state['writes']==0


def test_restart_intent_never_authorizes_reapplying_database(cleanup_db,tmp_path):
    state,before,call=cleanup_db
    (tmp_path/'restart-intent.json').write_bytes(b'{}')
    with pytest.raises(RuntimeError,match='RESTART_WITH_APPLIED_DB'):call()
    assert state['writes']==0


@pytest.mark.skipif(os.geteuid()!=0,reason='real root recovery records')
@pytest.mark.parametrize('already_running',[False,True])
def test_finish_after_lost_restart_response_does_not_repeat_database_or_start(tmp_path,monkeypatch,already_running):
    from database import light_native_pilot_intake as intake
    monkeypatch.setattr(target.install,'root_parent',lambda *a:None)
    plan=SimpleNamespace(digest='a'*64)
    receipt={'dispatch_id':'dispatch'};terminal=b'{}';digest='d'*64
    value=dict(action='restore',accepted_terminal_sha256=target.sha(terminal))
    binding=target.encoded(dict(plan_sha256=plan.digest,request_sha256=digest,
        receipt_sha256=target.sha(target.encoded(receipt)),terminal_sha256=target.sha(terminal)))
    for name,raw in [('execution.json',target.encoded(dict(request_sha256=digest))),('terminal.json',terminal),
                     ('restore-intent.json',binding),('restart-intent.json',binding)]:target.retain(tmp_path/name,raw)
    events=[]
    monkeypatch.setattr(target.execution,'restore',lambda *a:pytest.fail('old invocation restore must not run'))
    monkeypatch.setattr(target.execution,'admission',lambda value:events.append(value))
    monkeypatch.setattr(target.execution,'stopped',lambda:events.append('transient-stopped'))
    monkeypatch.setattr(target,'reconcile_controls',lambda *a:dict(success=True,controls_restored=True))
    monkeypatch.setattr(target.install,'verify_unit',lambda *a:None)
    original_read=target.install.hold.read
    monkeypatch.setattr(target.install.hold,'read',lambda path,*a:target.install.render(target.install.RETAINED_SOURCE)
        if path==target.install.UNIT_FILE else original_read(path,*a))
    monkeypatch.setattr(target.install,'stopped',lambda:events.append('permanent-stopped'))
    monkeypatch.setattr(target.install,'show',lambda *a:dict(ActiveState='active' if already_running else 'inactive',
                                                          MainPID='123' if already_running else '0'))
    monkeypatch.setattr(target.switch,'command',lambda *a:events.append('start'))
    monkeypatch.setattr(target.install,'verify_hold_process',lambda *a:dict(MainPID='123'))
    monkeypatch.setattr(target.pwd,'getpwnam',lambda *a:None)
    result=target.finish(None,{},plan,receipt,tmp_path,value,SimpleNamespace(assert_running=lambda:None))
    assert result['state']=='HOLD'
    assert events.count('start')==int(not already_running)
    assert events[:2]==[b'HOLD\n','transient-stopped']

@pytest.fixture
def phase_context(tmp_path,monkeypatch):
    from ops import native_maintenance_owner_host as host
    from ops import native_maintenance_owner_attest as attest
    from ops import native_maintenance_agreement as agreements
    from ops import light_native_pilot_owner as owner
    from ops import light_native_lane_feed as feed
    events=[];lock={'held':False}
    class Connection:
        autocommit=True
        def __enter__(self):return self
        def __exit__(self,*args):pass
    driver=SimpleNamespace(connect=lambda **kwargs:Connection())
    @contextmanager
    def loaded(wheels):
        assert not lock['held'];lock['held']=True;events.append('driver-acquired')
        try:yield driver,None
        finally:lock['held']=False;events.append('driver-released')
    monkeypatch.setattr(host,'loaded_runtime',loaded)
    monkeypatch.setattr(attest,'parameters',lambda credential:{})
    monkeypatch.setattr(agreements,'Agreement',lambda *a:SimpleNamespace(end=9999999999))
    monkeypatch.setattr(target.os,'geteuid',lambda:0)
    monkeypatch.setattr(target.os,'uname',lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
    monkeypatch.setattr(target,'validate_package',lambda *a:{})
    monkeypatch.setattr(target.release,'validate',lambda *a:None)
    monkeypatch.setattr(target.release.staging,'verify_release',lambda *a:None)
    monkeypatch.setattr(target,'verify_helpers',lambda source,digest:events.append(('helpers',source,digest)))
    monkeypatch.setattr(target,'guard',lambda *a:events.append('guard'))
    monkeypatch.setattr(target.switch,'unchanged_files',lambda *a:None)
    monkeypatch.setattr(target,'read',lambda path,*a:path.read_bytes())
    monkeypatch.setattr(target,'retain',lambda path,raw:path.write_bytes(raw))
    plan=SimpleNamespace(digest='a'*64,raw=b'plan',scope={},scope_digest='b'*64)
    monkeypatch.setattr(target,'scope',lambda value:(plan,tmp_path))
    retained=target.release.encoded(dict(source=target.install.RETAINED_SOURCE,
        runtime=dict(sha256='c'*64,source_comment='retained Unicode \u2192 exact bytes')))
    assert retained!=target.encoded(json.loads(retained))
    value=dict(version=1,action='publish',source='d'*40,accepted_controller_sha256='e'*64,
        accepted_runtime_sha256=target.sha(retained),plan_base64='unused',accepted_plan_sha256=plan.digest,
        agreement={},accepted_agreement_sha256='f'*64,accepted_receipt_sha256='1'*64,
        accepted_discovery_sha256='2'*64,accepted_permit_sha256='3'*64,accepted_terminal_sha256='4'*64)
    prior=target.install.hold.HoldIdentity('autopilot-lite-vnic',123,'a'*32,'/prior','b'*64)
    files={'prepare.json':target.encoded(value),'plan.json':plan.raw,
           'baseline.json':target.encoded(dict(prior=target.asdict(prior),protected={},protected_sha256='5'*64)),
           'intake.json':target.encoded(dict(plan_sha256=plan.digest,dispatch_id='dispatch')),
           'discovery.json':b'{}','broker.json':b'{}',
           'permit.json':target.encoded(dict(expires_at=9999999999))}
    for name,raw in files.items():(tmp_path/name).write_bytes(raw)
    monkeypatch.setattr(owner,'observed_target',lambda *a:events.append('fresh-pr'))
    monkeypatch.setattr(owner,'discovery',lambda *a:{})
    monkeypatch.setattr(owner,'broker_publish',lambda *a:events.append('broker') or {})
    monkeypatch.setattr(target,'refresh_owner_claim',lambda *a:events.append('claim-refreshed'))
    from database import light_native_pilot_intake as intake
    monkeypatch.setattr(intake,'assert_publication_claim',lambda *a,**kw:events.append('lease-readback'))
    monkeypatch.setattr(feed,'publish_first',lambda *a:events.append('feed') or {})
    def launch(*args):
        assert not lock['held'];events.append('launch');return {'launched':True}
    monkeypatch.setattr(target,'launch',launch)
    run=SimpleNamespace(assert_current=lambda:None,assert_running=lambda:None)
    def call():
        payload=target.encoded(value)
        return target.phase(b'wheels','credential','token',b'{}',retained,payload,target.sha(payload),run)
    return value,events,call,tmp_path


def test_publish_refuses_uncommitted_durable_receipt_before_any_broker_intent(phase_context,monkeypatch):
    value,events,call,directory=phase_context
    def refuse(*a):events.append('commit-absent');raise RuntimeError('not committed')
    monkeypatch.setattr(target,'committed_intake',refuse)
    with pytest.raises(RuntimeError,match='not committed'):call()
    assert 'broker' not in events and not (directory/'publication-intent.json').exists()
    assert events[-1]=='driver-released'


def test_publish_accepts_fresh_committed_graph_after_lost_ack(phase_context,monkeypatch):
    value,events,call,directory=phase_context
    monkeypatch.setattr(target,'committed_intake',lambda *a:events.append('commit-proven'))
    assert call()['phase']=='publish'
    assert events.index('commit-proven')<events.index('claim-refreshed')<events.index('lease-readback')<events.index('broker')
    assert (directory/'publication-intent.json').exists()


def test_execute_releases_driver_flock_before_supervisor(phase_context):
    value,events,call,_=phase_context;value['action']='execute'
    assert call()=={'launched':True}
    assert events.index('feed')<events.index('driver-released')<events.index('launch')


def test_cleanup_on_new_main_verifies_old_helpers_but_uses_new_authority(phase_context,monkeypatch):
    value,events,call,_=phase_context;value['action']='restore';value['source']='6'*40
    value['accepted_controller_sha256']='7'*64
    monkeypatch.setattr(target,'finish',lambda *a:dict(source=a[-2]['source']))
    assert call()=={'source':'6'*40}
    assert ('helpers','d'*40,'e'*64) in events


def test_containment_intent_blocks_later_publication(phase_context):
    value,events,call,directory=phase_context;(directory/'contain-intent.json').write_bytes(b'{}')
    with pytest.raises(RuntimeError,match='CONTAINMENT_PENDING'):call()
    assert 'broker' not in events


def test_late_execute_refuses_before_staging_feed(phase_context,monkeypatch):
    value,events,call,_=phase_context;value['action']='execute'
    monkeypatch.setattr(target.time,'time',lambda:9999999500)
    with pytest.raises(RuntimeError,match='EXECUTION_WINDOW'):call()
    assert 'feed' not in events and 'launch' not in events


def test_missing_terminal_migration_refuses_without_querying_absent_table():
    queries=[]
    def query(sql):
        queries.append(sql)
        return SimpleNamespace(fetchone=lambda:(False,))
    with pytest.raises(RuntimeError,match='TERMINAL_POLICY_MISSING'):
        target.verify_terminal_policy(SimpleNamespace(execute=query))
    assert len(queries)==1 and 'to_regclass' in queries[0]


def test_diagnostics_never_forward_credentials_or_arbitrary_remote_text():
    secret='postgres://owner:private-password@host/db token=private-token'
    assert runner.failure(RuntimeError(secret))['reason']=='UNCLASSIFIED'
    for raw in (secret.encode(),json.dumps({'audit':'LIGHT_LANE_OWNER_REFUSED','reason':secret}).encode(),
                json.dumps({'audit':'LIGHT_LANE_OWNER_REFUSED','reason':'UNCLASSIFIED','extra':secret}).encode()):
        assert secret not in json.dumps(runner.remote_failure(raw))
    safe=dict(audit='LIGHT_LANE_OWNER_REFUSED',reason='LANE_OWNER_TERMINAL_POLICY_MISSING')
    assert runner.remote_failure(json.dumps(safe).encode())==safe


def test_prepare_policy_failure_precedes_root_records(phase_context,monkeypatch):
    value,events,call,directory=phase_context
    value['action']='prepare'
    from ops import light_native_pilot_owner as owner
    prior=target.install.hold.HoldIdentity('autopilot-lite-vnic',123,'a'*32,'/prior','b'*64)
    monkeypatch.setattr(target.install.hold,'attest',lambda:prior)
    monkeypatch.setattr(target.install,'verify_running',lambda *a:None)
    monkeypatch.setattr(target.pwd,'getpwnam',lambda *a:None)
    monkeypatch.setattr(owner,'observed_target',lambda *a:{'head':{'sha':'a'*40,'ref':'branch'}})
    from database import light_native_pilot_intake as intake
    monkeypatch.setattr(intake.engine,'privileges',lambda *a:None)
    def refuse(*a):raise RuntimeError('LANE_OWNER_TERMINAL_POLICY_MISSING')
    monkeypatch.setattr(target,'verify_terminal_policy',refuse)
    monkeypatch.setattr(target,'bootstrap_helpers',lambda *a:pytest.fail('must not retain helper state'))
    before=set(directory.iterdir())
    with pytest.raises(RuntimeError,match='TERMINAL_POLICY_MISSING'):call()
    assert set(directory.iterdir())==before


@pytest.mark.parametrize('fault', ['version', 'negative', 'boolean', 'limit', 'digest', 'reuse', 'extra'])
def test_predecessor_schema_rejects_ambiguous_or_reused_sequence(fault):
    value=dict(version=2, accepted_plan_sha256='a'*64,
               predecessor=dict(plan_sha256='b'*64, terminal_sha256='c'*64, sequence=0))
    assert target.sequence(value)==1
    if fault=='version': value['version']=1
    elif fault=='negative': value['predecessor']['sequence']=-1
    elif fault=='boolean': value['predecessor']['sequence']=True
    elif fault=='limit': value['predecessor']['sequence']=9999
    elif fault=='digest': value['predecessor']['terminal_sha256']='bad'
    elif fault=='reuse': value['predecessor']['plan_sha256']='a'*64
    else: value['predecessor']['extra']=1
    with pytest.raises(RuntimeError): target.sequence(value)
    assert target.sequence(dict(version=1))==0


@pytest.mark.parametrize('action', ['publish', 'permit', 'execute'])
def test_successor_drift_blocks_each_stage_before_effect(phase_context, monkeypatch, action):
    value, events, call, directory = phase_context
    value.update(version=2, action=action, predecessor=dict(plan_sha256='b'*64, terminal_sha256='c'*64, sequence=0))
    (directory/'prepare.json').write_bytes(target.encoded(value))
    def drift(*args): raise RuntimeError('predecessor drift')
    monkeypatch.setattr(target, 'verify_previous', drift)
    with pytest.raises(RuntimeError, match='predecessor drift'): call()
    assert 'broker' not in events and 'feed' not in events
    assert not (directory/'publication-intent.json').exists()
    assert not (directory/'permit-intent.json').exists()


"""Incident recovery: no provider result, replay, scope widening or silent drift."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
import json
import pytest
from ops import light_native_lane_controller as target
from database import light_native_pilot_intake as intake
from ops import light_native_pilot_owner as owner


@pytest.fixture
def recovery(tmp_path,monkeypatch):
    if os.geteuid()!=0:
        pytest.skip('real root-owned recovery records; exercised by the workflow root-metadata step')
    task='036bda80-b063-4578-a87b-61e9d556d5b2'
    dispatch='6ac74f4e-d3fa-4140-a700-904cc6d408c7'
    work='0ede25b2-02e4-437b-85e0-7eb4882bc3d0'
    receipt=dict(task_id=task,dispatch_id=dispatch,work_item_id=work,claim_epoch=1,
        snapshot_sha256='a'*64,goal_json_sha256=target.sha(b'{}'),
        applied_config=dict(enabled=True,cutover_at='new'),applied_role=dict(can_repair=False,updated_at='old'),
        dispatch=dict(task_fingerprint='fingerprint'))
    value=dict(version=2,accepted_plan_sha256=target.RECOVERY_PLAN,
        predecessor=dict(plan_sha256='71706f2ccb9054a0fa955f51cd8289810775253b5ca87b981726b7a7583d6af4',
            terminal_sha256='517273e66872c271c7eea70e89c7d792419d5df4474c3e63c71fc52a5ac2fa91',sequence=0),
        accepted_receipt_sha256='c328bdb8660885f588c5ea07c10803726608cc1f3ea1017db420789db351a2e9',
        accepted_discovery_sha256='5d2133d05579483409993d18bd831a474d95511fc4fb3dd0d04a452200b996fb')
    plan=SimpleNamespace(digest=target.RECOVERY_PLAN,worker='worker',value=dict(work_key='work',task_spec_json={},target_pr=1993,expected_head_sha='b'*40))
    before=dict(plan_sha256=plan.digest,target=intake.EXPECTED_TARGET,native_config=dict(enabled=False,cutover_at='old'),
                autopilot_role=dict(can_repair=True,updated_at='old'))
    rows=dict(task=dict(task_id=task,status='WAITING_EXTERNAL',attempts=1,max_attempts=3,lease_owner=None,
        lease_until=None,completed_at=None,terminal_reason_code=None,safe_summary_json={},goal_json={},updated_at='old',
        cost_actual_microusd=0,cost_reserved_microusd=0,cost_cap_microusd=0),
        step=dict(task_id=task,step_attempt_id='step',status='WAITING_EXTERNAL',completed_at=None,error_code=None,result_summary_json={}),
        work=dict(work_item_id=work,work_key='work',state='ACTIVE',last_task_id=task,generation=1,task_spec_json={},
            hold_reason=None,hold_until=None,probe_lease_owner=None,result_code=None,result_summary=None,completed_at=None,updated_at='old',not_before='old'),
        outbox=dict(dispatch_id=dispatch,status='CLAIMED',claim_owner='worker',claim_epoch=1,attempts=1,max_attempts=5,
            task_id=task,mode='READ_ONLY',delivery_contract_version=3,target_pr=1993,expected_head_sha='b'*40,
            task_fingerprint='fingerprint',claim_until='expired',updated_at='old',last_error_code=None),
        config=dict(receipt['applied_config'],enabled=False),role=deepcopy(receipt['applied_role']),
        mapping=[dict(task_id=task,run_kind='AUDIT')],receipts=0,successors=0,counts=[2,2,1,2],event=[],active_tasks=[task],
        planner=dict(decision_count=7,last_decision_code='OLD',last_work_item_id=None,last_decision_at=None,enabled=False))
    rows['outbox'].update(dict.fromkeys(('published_at','github_dispatch_comment_id','dispatch_body_sha256','executor_id',
        'codex_ack_at','delivered_at','completed_at','prior_task_id','origin_task_id','codex_command_pr','codex_command_comment_id','sent_at')))
    db=SimpleNamespace(rows=rows,writes=0,fail_update=None,fail_commit=False,guards=0,fail_guard=None,readback_drift=False)
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        @contextmanager
        def transaction(self):
            snapshot=deepcopy(db.rows)
            start=db.writes
            try:yield
            except BaseException:
                db.rows=snapshot
                raise
            if db.fail_commit and db.writes>start:raise RuntimeError('COMMIT_ACK_LOST')
        def execute(self,sql,args=()):
            if sql.startswith('UPDATE'):
                db.writes+=1
                if db.fail_update==db.writes:return SimpleNamespace(rowcount=0)
                r=db.rows
                if 'role_dispatch_outbox SET' in sql:
                    r['outbox'].update(status='FAILED_CLOSED',claim_owner=None,claim_until=None,last_error_code=args[0],completed_at='now',updated_at='now')
                elif 'step_attempt SET' in sql:
                    r['step'].update(status='FAILED_CLOSED',error_code=args[0],result_summary_json=json.loads(args[1]),completed_at='now')
                elif 'autopilot.task SET' in sql:
                    r['task'].update(status='FAILED_CLOSED',terminal_reason_code=args[0],safe_summary_json=json.loads(args[1]),completed_at='now',updated_at='now')
                    # Deployed work trigger, not a native terminal receipt.
                    r['work'].update(state='BLOCKED',not_before='later')
                    r['active_tasks']=[]
                    r['planner'].update(decision_count=r['planner']['decision_count']+1,last_decision_code='WORK_ITEM_BLOCKED_CONTINUE',last_work_item_id=work,last_decision_at='now')
                elif 'project_work_item SET' in sql:
                    r['work'].update(state='PAUSED',hold_reason='OWNER_HOLD',hold_until=None,result_code=args[0],result_summary=args[1],completed_at=None,updated_at='now')
                elif 'native_cli_config SET' in sql:r['config'].update(enabled=args[0],cutover_at=args[1])
                elif 'role_registry SET' in sql:r['role'].update(can_repair=args[0])
                else:pytest.fail(sql)
                return SimpleNamespace(rowcount=1)
            if 'record_event(' in sql:
                db.rows['event']=[dict(task_id=task,event_type='TASK_FAILED_CLOSED')]
            if 'claim_until<clock_timestamp' in sql:return SimpleNamespace(fetchone=lambda:(True,))
    driver=SimpleNamespace(connect=lambda **kwargs:Connection())
    monkeypatch.setattr(intake.engine,'identity',lambda *args:None)
    monkeypatch.setattr(intake.engine,'load_manifest',lambda *args:before)
    monkeypatch.setattr(target,'recovery_rows',lambda conn,receipt,**kwargs:deepcopy(db.rows))
    monkeypatch.setattr(target,'verify_terminal_policy',lambda conn:None)
    monkeypatch.setattr(owner,'discovery',lambda *args:{})
    monkeypatch.setattr(target.install,'root_parent',lambda *args:None)
    real_read=target.read
    monkeypatch.setattr(target,'read',lambda path,accepted=None,*args:real_read(path,None,*args))
    def guard(*args):
        db.guards+=1
        if db.guards==db.fail_guard:raise RuntimeError('HOST_CHANGED')
    monkeypatch.setattr(target,'containment_host',guard)
    target.retain(tmp_path/'contained.json',target.encoded(dict(audit='LIGHT_LANE_OWNER',phase='contain',
        state='CONTAINED_UNRESOLVED',plan_sha256=plan.digest,controls_restored=False,task_success=False,queue_retry_authorized=False)))
    target.retain(tmp_path/'contain-intent.json',target.encoded(dict(plan_sha256=plan.digest,action='CONTAIN_PREEXECUTION')))
    for name in ('discovery.json','broker.json'):target.retain(tmp_path/name,b'{}')
    run=lambda:target.recover(driver,{},plan,receipt,tmp_path,value,SimpleNamespace(assert_running=lambda:None),None)
    return SimpleNamespace(db=db,plan=plan,receipt=receipt,value=value,before=before,directory=tmp_path,run=run)


def test_retirement_preserves_attempts_and_never_fabricates_receipt(recovery):
    original=deepcopy(recovery.db.rows)
    result=recovery.run()
    assert result['controls_restored'] and not result['task_success'] and not result['provider_started']
    assert recovery.db.rows['work']['state']=='PAUSED' and recovery.db.rows['receipts']==0
    assert recovery.db.rows['outbox']['attempts']==original['outbox']['attempts']==1
    assert recovery.db.rows['outbox']['claim_epoch']==1 and recovery.db.rows['outbox']['max_attempts']==5
    assert 'status' not in recovery.db.rows['task']['safe_summary_json']
    assert recovery.run()==result and recovery.db.writes==6


@pytest.mark.parametrize('change',['task','plan','predecessor','discovery','version'])
def test_other_incidents_are_not_authorized(recovery,change):
    if change=='task':recovery.receipt['task_id']='other'
    elif change=='plan':recovery.plan.digest='a'*64
    elif change=='predecessor':recovery.value['predecessor']['sequence']=1
    elif change=='discovery':recovery.value['accepted_discovery_sha256']='b'*64
    else:recovery.value['version']=1
    with pytest.raises(RuntimeError):recovery.run()
    assert recovery.db.writes==0


@pytest.mark.parametrize('table,key,value',[
    ('outbox','attempts',2),('outbox','claim_epoch',2),('outbox','executor_id','provider'),
    ('outbox','published_at','time'),('outbox','status','PUBLISHED'),('task','cost_actual_microusd',1),
    ('task','status','RUNNING'),('work','generation',2),('work','hold_reason','foreign'),
    ('role','can_repair',True),('config','enabled',True)])
def test_changed_graph_refuses_before_intent(recovery,table,key,value):
    recovery.db.rows[table][key]=value
    with pytest.raises(RuntimeError):recovery.run()
    assert recovery.db.writes==0 and not (recovery.directory/'recovery-intent.json').exists()


@pytest.mark.parametrize('field,value',[('receipts',1),('successors',1),('mapping',[]),('event',[{}])])
def test_new_activity_refuses(recovery,field,value):
    recovery.db.rows[field]=value
    with pytest.raises(RuntimeError):recovery.run()
    assert recovery.db.writes==0


@pytest.mark.parametrize('write',range(1,7))
def test_each_missing_update_rolls_back_and_never_blindly_retries(recovery,write):
    original=deepcopy(recovery.db.rows)
    recovery.db.fail_update=write
    with pytest.raises(RuntimeError,match='ROWCOUNT'):recovery.run()
    assert recovery.db.rows==original
    count=recovery.db.writes
    with pytest.raises(FileNotFoundError):recovery.run()
    assert recovery.db.writes==count


@pytest.mark.parametrize('point',[1,2,3])
def test_host_drift_before_commit_leaves_no_terminal(recovery,point):
    original=deepcopy(recovery.db.rows)
    recovery.db.fail_guard=point
    with pytest.raises(RuntimeError,match='HOST_CHANGED'):recovery.run()
    assert recovery.db.rows==original


def test_lost_commit_ack_classifies_without_sql_replay(recovery):
    recovery.db.fail_commit=True
    with pytest.raises(RuntimeError,match='COMMIT_ACK_LOST'):recovery.run()
    assert recovery.db.rows['task']['status']=='FAILED_CLOSED'
    assert recovery.run()['controls_restored']
    assert recovery.db.writes==6


def test_unknown_ack_plus_drift_requires_incident_not_retry(recovery):
    recovery.db.fail_commit=True
    with pytest.raises(RuntimeError):recovery.run()
    recovery.db.rows['work']['last_task_id']='foreign'
    with pytest.raises(RuntimeError,match='OUTCOME_UNKNOWN'):recovery.run()
    assert recovery.db.writes==6


@pytest.mark.parametrize('table,key,value',[
    ('outbox','attempts',2),('outbox','claim_epoch',2),('outbox','max_attempts',1),
    ('step','task_id','foreign'),('work','generation',2),('task','cost_actual_microusd',1),('planner','enabled',True)])
def test_post_commit_unexpected_delta_rejected(recovery,table,key,value):
    original=deepcopy(recovery.db.rows)
    recovery.run()
    after=deepcopy(recovery.db.rows);after[table][key]=value
    with pytest.raises(RuntimeError,match='UNEXPECTED_DELTA'):
        target.validate_recovery_delta(original,after,recovery.before,recovery.receipt)


@pytest.fixture
def claim_refresh_records(tmp_path,monkeypatch):
    if os.geteuid()!=0:pytest.skip('root-owned refresh intent; covered by required root CI step')
    from database import light_native_pilot_intake as intake
    state=SimpleNamespace(writes=0,ack_lost=False,no_after=False,readback=0,expired=False)
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
    driver=SimpleNamespace(connect=lambda **kwargs:Connection())
    monkeypatch.setattr(target.install,'root_parent',lambda *args:None)
    monkeypatch.setattr(target,'containment_host',lambda *args:None)
    def refresh(conn,plan,agreement,receipt,**kw):
        kw['effect_guard']();kw['durable_before']({'before':True});state.writes+=1
        if state.no_after:raise RuntimeError('DISK_FULL')
        kw['durable_after']({'accepted_after':True})
        if state.ack_lost:raise RuntimeError('ACK_LOST')
        return {'accepted_after':True}
    def readback(conn,plan,agreement,receipt,expected,**kw):
        state.readback+=1
        assert conn.read_only and expected=={'accepted_after':True} and kw['minimum_seconds']==60
        if state.expired:raise RuntimeError('EXPIRED')
    monkeypatch.setattr(intake,'refresh_publication_claim',refresh)
    monkeypatch.setattr(intake,'assert_publication_claim',readback)
    value=dict(action='publish',accepted_receipt_sha256='a'*64,accepted_agreement_sha256='b'*64)
    plan=SimpleNamespace(digest='c'*64,scope_digest='d'*64)
    agreement=SimpleNamespace(assert_held=lambda *args:None)
    call=lambda:target.refresh_owner_claim(driver,{},plan,agreement,{},tmp_path,value,SimpleNamespace(assert_running=lambda:None))
    return state,value,call,tmp_path


def test_owner_refresh_lost_ack_reads_record_without_second_extension(claim_refresh_records):
    state,value,call,directory=claim_refresh_records
    state.ack_lost=True
    with pytest.raises(RuntimeError,match='ACK_LOST'):call()
    assert call()=={'accepted_after':True} and state.writes==1
    assert (directory/'publish-claim-refresh-intent.json').exists()
    state.expired=True
    with pytest.raises(RuntimeError,match='EXPIRED'):call()
    assert state.writes==1


def test_owner_refresh_missing_after_never_retries_sql(claim_refresh_records):
    state,_,call,_=claim_refresh_records
    state.no_after=True
    with pytest.raises(RuntimeError,match='DISK_FULL'):call()
    with pytest.raises(FileNotFoundError):call()
    assert state.writes==1


def test_owner_refresh_rejects_changed_binding(claim_refresh_records):
    state,value,call,_=claim_refresh_records
    call();value['accepted_receipt_sha256']='e'*64
    with pytest.raises(RuntimeError,match='INTENT_CHANGED'):call()
    assert state.writes==1


def test_uncertain_publication_never_refreshes_or_republishes(phase_context,monkeypatch):
    value,events,call,directory=phase_context
    (directory/'publication-intent.json').write_bytes(b'{}')
    with pytest.raises(RuntimeError,match='PUBLICATION_UNCERTAIN'):call()
    assert 'claim-refreshed' not in events and 'broker' not in events


def test_refresh_refusal_precedes_external_broker(phase_context,monkeypatch):
    value,events,call,directory=phase_context
    monkeypatch.setattr(target,'committed_intake',lambda *args:None)
    monkeypatch.setattr(target,'refresh_owner_claim',lambda *args:(_ for _ in ()).throw(RuntimeError('EXPIRED')))
    with pytest.raises(RuntimeError,match='EXPIRED'):call()
    assert 'broker' not in events and not (directory/'publication-intent.json').exists()
