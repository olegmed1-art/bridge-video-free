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
    assert result.stdout==b'{"audit":"LIGHT_LANE_OWNER_REFUSED"}\n'
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
    retained=target.encoded(dict(source=target.install.RETAINED_SOURCE,runtime=dict(sha256='c'*64)))
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
    assert events.index('commit-proven')<events.index('broker')
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
