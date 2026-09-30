"""Finite orchestration, real durable journal, crash and authority boundaries."""
import base64
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import json
import os
from types import SimpleNamespace

import pytest

from database.light_native_pilot_intake import Plan,digest
from ops import light_native_lane_controller as owner
from ops import light_native_lane_cycle as cycle
from ops.native_maintenance_agreement import COVERAGE
from oracle_autopilot.light_native_adapter import FALSE_FLAGS


@pytest.fixture
def request_bytes(monkeypatch):
    source='a'*40
    spec=dict(assignment_schema='SLAVIK_DISPATCH_ASSIGNMENT_V1',repository='olegmed1-art/bridge-video-free',
        target_pr=1847,expected_head_sha='b'*40,execution_mode='READ_ONLY',exact_head_binding=True,
        cost_cap_microusd=0,max_repair_attempts=0,**dict.fromkeys(FALSE_FLAGS,False))
    raw=owner.encoded(dict(version=1,source=owner.install.RETAINED_SOURCE,repository=spec['repository'],
        target_pr=1847,expected_head_sha='b'*40,branch='codex/audit-one',work_key='cycle-one',
        objective='Read-only exact-head audit; no cloud operations.',priority=0,task_spec_json=spec))
    plan=Plan(raw,owner.sha(raw))
    now=datetime.now(timezone.utc).replace(microsecond=0)
    stamp=lambda x:x.strftime('%Y-%m-%dT%H:%M:%SZ')
    agreement=dict(version=1,owner='olegmed1-art',operation_digest=plan.scope_digest,
        not_before=stamp(now-timedelta(seconds=1)),expires_at=stamp(now+timedelta(seconds=1200)),
        coverage=COVERAGE,evidence='Accepted exact finite automatic cycle and deterministic derivation.')
    helpers=dict.fromkeys((*owner.release.HELPERS,*owner.EXTRA),'# fixture')
    controller=owner.encoded(dict(version=1,kind='LIGHT_LANE_CONTROLLER',source=source,helpers=helpers))
    runtime=b'accepted runtime bytes checked again by each real phase'
    prepare=dict(version=2,action='prepare',source=source,accepted_controller_sha256=owner.sha(controller),
        accepted_runtime_sha256=owner.sha(runtime),plan_base64=base64.b64encode(raw).decode(),
        accepted_plan_sha256=plan.digest,agreement=agreement,accepted_agreement_sha256=digest(agreement),
        accepted_receipt_sha256=None,accepted_discovery_sha256=None,accepted_permit_sha256=None,
        accepted_terminal_sha256=None,predecessor=dict(plan_sha256='c'*64,terminal_sha256='d'*64,sequence=1))
    payload=owner.encoded(dict(version=1,action='cycle',prepare=prepare))
    guard=SimpleNamespace(assert_current=lambda:None,assert_running=lambda:None)
    monkeypatch.setattr(owner.release.staging,'require_current_main',lambda source:None)
    return SimpleNamespace(prepare=prepare,controller=controller,runtime=runtime,raw=payload,
        digest=owner.sha(payload),guard=guard)


def test_acceptance_is_exact_and_bounded(request_bytes):
    r=request_bytes
    assert cycle.validated(r.raw,r.digest,r.controller,r.runtime,r.guard)==r.prepare
    for change in ('extra','derived','version','wrong_digest','runtime'):
        body=owner.parse(r.raw)
        if change=='extra':body['next_task']={}
        if change=='derived':body['prepare']['accepted_receipt_sha256']='f'*64
        if change=='version':body['prepare']['version']=1
        if change=='window':
            p=body['prepare'];p['agreement']['expires_at']=p['agreement']['not_before']
            p['accepted_agreement_sha256']=digest(p['agreement'])
        raw=owner.encoded(body)
        with pytest.raises(RuntimeError):
            cycle.validated(raw,'0'*64 if change=='wrong_digest' else owner.sha(raw),
                r.controller,b'other' if change=='runtime' else r.runtime,r.guard)


@pytest.fixture
def journal(tmp_path,monkeypatch,request_bytes):
    if os.geteuid()!=0:pytest.skip('Mandatory root workflow runs real journal metadata tests')
    r=request_bytes
    monkeypatch.setattr(owner,'ROOT',tmp_path/'owner')
    owner.ROOT.mkdir(mode=0o700)
    # pytest's temporary ancestor is not a production root parent. Leaf modes,
    # no-follow, exclusive creation, fsync/readback and locks are actual code.
    monkeypatch.setattr(owner.install,'root_parent',lambda path:None)
    monkeypatch.setattr(cycle.os,'uname',lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
    monkeypatch.setattr(owner.execution,'ROOT',tmp_path/'execution')
    owner.execution.ROOT.mkdir(mode=0o700)
    scope=owner.ROOT/r.prepare['accepted_plan_sha256']
    def containment_host(directory,value):
        if (directory/'execution.json').exists() or (directory/'test-feed-intent').exists():
            raise RuntimeError('LANE_OWNER_FEED_REQUIRES_INCIDENT')
    monkeypatch.setattr(owner,'containment_host',containment_host)
    calls=[]
    fault=SimpleNamespace(phase=None,after=False,bad=None,result_code='AUDIT_FINDINGS_REPORTED')
    def phase(wheels,credential,token,controller,runtime,raw,accepted,guard,*,_cycle=None):
        assert (wheels,credential,token)==(b'wheels','private-password','private-token')
        assert owner.sha(raw)==accepted
        value=owner.parse(raw);action=value['action']
        cycle.authorize_phase(value,_cycle)
        calls.append(action)
        if fault.phase==action and not fault.after:raise RuntimeError('private-password')
        output=dict(audit='LIGHT_LANE_OWNER',phase=action,dispatch_id='dispatch')
        if action=='prepare':
            scope.mkdir(mode=0o700)
            for name in ('before','baseline'):owner.retain(scope/(name+'.json'),b'{}')
            record=dict(plan_sha256=r.prepare['accepted_plan_sha256'],dispatch_id='dispatch',task_id='task')
            name='intake.json'
        elif action=='publish':record=dict(discovery='fresh');name='discovery.json'
        elif action=='permit':record=dict(permit='fresh');name='permit.json'
        elif action=='execute':
            owner.retain(scope/'execution.json',owner.encoded(dict(request_sha256='e'*64)))
            target=owner.execution.ROOT/('e'*64);target.mkdir(mode=0o700)
            owner.retain(target/'stopped-hold.json',owner.encoded(dict(state='STOPPED_HOLD')))
            output.update(state='STOPPED_HOLD',request_sha256='e'*64,terminal_verified=False)
        elif action=='terminal':
            record=dict(dispatch_id='dispatch',task_id='task',result=dict(result_code=fault.result_code))
            name='terminal.json';output.update(state='ACCEPTED',sequence=2)
        elif action=='restore':
            complete=dict(plan_sha256=r.prepare['accepted_plan_sha256'],controls_restored=True,
                terminal_sha256=owner.sha(owner.read(scope/'terminal.json')),native=dict(MainPID='123'))
            owner.retain(scope/'complete.json',owner.encoded(complete))
            output.update(state='HOLD',controls_restored=True,native_pid='123')
        elif action=='contain':
            output.update(state='CONTAINED_UNRESOLVED',plan_sha256=r.prepare['accepted_plan_sha256'],
                queue_retry_authorized=False)
            owner.retain(scope/'contained.json',owner.encoded(output))
        if action in ('prepare','publish','permit','terminal'):
            record=owner.encoded(record);owner.retain(scope/name,record)
            output['record_sha256']=owner.sha(record)
        if fault.phase==action and fault.after:raise RuntimeError('private-token')
        if fault.bad==action:output['record_sha256']='f'*64
        return output
    monkeypatch.setattr(owner,'phase',phase)
    def isolated(wheels,credential,token,controller,runtime,raw,accepted,guard,*,outer,mode):
        if mode=='validate':
            cycle.validate_scope(owner.parse(raw)['prepare'])
            return dict(audit='LIGHT_LANE_CYCLE_VALIDATED')
        value=owner.parse(raw)
        if mode=='containment_check':
            owner.containment_host(scope,value)
            return dict(audit='LIGHT_LANE_CYCLE_CONTAINMENT_CHECKED')
        return owner.phase(wheels,credential,token,controller,runtime,raw,accepted,guard,
            _cycle=cycle._Authority(r.prepare,outer))
    monkeypatch.setattr(cycle,'isolated',isolated)
    def run():return cycle.run(b'wheels','private-password','private-token',r.controller,r.runtime,r.raw,r.digest,r.guard)
    return SimpleNamespace(run=run,request=r,scope=scope,calls=calls,fault=fault,path=cycle.location(r.prepare))


@pytest.mark.parametrize('code',['AUDIT_PASSED','AUDIT_FINDINGS_REPORTED'])
def test_all_phases_advance_once_and_preserve_findings(journal,code):
    j=journal;j.fault.result_code=code
    result=j.run()
    assert j.calls==list(cycle.STEPS)
    assert result['state']=='COMPLETE_HOLD' and result['result_code']==code
    for action in cycle.STEPS:
        assert owner.read(j.path/(action+'-intent.json'))==owner.encoded(cycle.derive(j.request.prepare,action))
        assert owner.parse(owner.read(j.path/(action+'-done.json')))['phase']==action
    assert owner.parse(owner.read(j.path/'complete.json'))==result
    for path in owner.ROOT.rglob('*.json'):
        assert b'private-password' not in path.read_bytes() and b'private-token' not in path.read_bytes()
    with pytest.raises(RuntimeError,match='REPLAY'):j.run()
    assert j.calls==list(cycle.STEPS)


@pytest.mark.parametrize('action',cycle.STEPS)
@pytest.mark.parametrize('after',[False,True])
def test_each_unknown_phase_stops_without_replay(journal,action,after):
    j=journal;j.fault.phase=action;j.fault.after=after
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):j.run()
    attempted=list(cycle.STEPS[:cycle.STEPS.index(action)+1])
    if action in ('publish','permit') or action=='prepare' and after or action=='execute' and not after:
        attempted+=['contain']
    assert j.calls==attempted
    assert owner.parse(owner.read(j.path/'incident.json'))['phase']==action
    with pytest.raises(RuntimeError,match='REPLAY'):j.run()
    assert j.calls==attempted


def test_stdout_digest_cannot_authorize_next_effect(journal):
    j=journal;j.fault.bad='publish'
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):j.run()
    assert j.calls==['prepare','publish','contain']


def test_feed_intent_without_execution_record_prevents_containment(journal,monkeypatch):
    j=journal;original=owner.phase
    def phase(*args,**kwargs):
        if owner.parse(args[5])['action']=='execute':
            (j.scope/'test-feed-intent').write_bytes(b'uncertain feed')
            j.fault.phase='execute'
        return original(*args,**kwargs)
    monkeypatch.setattr(owner,'phase',phase)
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):j.run()
    assert j.calls==['prepare','publish','permit','execute']
    assert not (j.path/'contain-intent.json').exists()


def test_unproved_containment_is_not_reported_as_contained(journal,monkeypatch):
    j=journal;j.fault.phase='publish';original=owner.phase
    def phase(*args,**kwargs):
        result=original(*args,**kwargs)
        if owner.parse(args[5])['action']=='contain':result['state']='HOLD'
        return result
    monkeypatch.setattr(owner,'phase',phase)
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):j.run()
    assert owner.parse(owner.read(j.path/'incident.json'))['containment']=='RECONCILIATION_REQUIRED'
    assert not (j.path/'contain-done.json').exists()


def test_cancelled_workflow_never_advances_or_claims_containment(journal,monkeypatch):
    j=journal;original=owner.phase
    def cancelled():raise RuntimeError('cancelled')
    def phase(*args,**kwargs):
        result=original(*args,**kwargs)
        if owner.parse(args[5])['action']=='prepare':j.request.guard.assert_running=cancelled
        return result
    monkeypatch.setattr(owner,'phase',phase)
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):j.run()
    assert j.calls==['prepare']
    assert owner.parse(owner.read(j.path/'incident.json'))['containment']=='RECONCILIATION_REQUIRED'


def test_partial_cycle_directory_and_existing_scope_never_reused(journal):
    j=journal;j.scope.mkdir(mode=0o700)
    with pytest.raises(RuntimeError,match='REPLAY'):j.run()
    assert j.calls==[]


def test_cycle_lock_excludes_concurrent_cycle_without_driver_lock(journal):
    root=owner.ROOT/'cycles';root.mkdir(mode=0o700)
    with cycle.exclusive(root):
        with pytest.raises(BlockingIOError):journal.run()
    assert journal.calls==[] and not journal.path.exists()


def test_standalone_effects_refuse_cycle_but_reviewed_cleanup_remains(journal):
    j=journal;j.run()
    for action in cycle.STEPS[:4]:
        with pytest.raises(RuntimeError,match='REPLAY'):
            cycle.authorize_phase(dict(j.request.prepare,action=action),None)
    for action in ('terminal','restore','contain','recover'):
        cycle.authorize_phase(dict(j.request.prepare,action=action),None)
    authority=cycle._Authority(j.request.prepare,j.request.digest)
    value=cycle.derive(j.request.prepare,'execute');value['accepted_permit_sha256']='f'*64
    with pytest.raises(RuntimeError,match='SCOPE'):cycle.authorize_phase(value,authority)


def test_cleanup_not_blocked_by_outer_agreement_expiry(journal,monkeypatch):
    original=owner.phase
    def phase(*args,**kwargs):
        value=owner.parse(args[5])
        result=original(*args,**kwargs)
        if value['action']=='execute':monkeypatch.setattr(cycle.time,'time',lambda:9999999999)
        return result
    monkeypatch.setattr(owner,'phase',phase)
    assert journal.run()['controls_restored'] is True
    assert journal.calls[-2:]==['terminal','restore']


def test_new_packages_strict_but_retained_old_helpers_can_be_verified(request_bytes):
    r=request_bytes;value=owner.parse(r.controller)
    del value['helpers']['ops/light_native_lane_cycle.py']
    del value['helpers']['ops/light_native_lane_issuer.py']
    raw=owner.encoded(value)
    with pytest.raises(RuntimeError):owner.validate_package(raw,r.prepare['source'],owner.sha(raw))
    owner.validate_package(raw,r.prepare['source'],owner.sha(raw),allow_legacy=True)
    del value['helpers']['ops/light_native_lane_controller.py']
    raw=owner.encoded(value)
    with pytest.raises(RuntimeError):owner.validate_package(raw,r.prepare['source'],owner.sha(raw),allow_legacy=True)


def test_scope_window_checked_after_driver_loading(request_bytes):
    r=request_bytes
    cycle.validate_scope(r.prepare)
    value=deepcopy(r.prepare)
    value['agreement']['expires_at']=value['agreement']['not_before']
    value['accepted_agreement_sha256']=digest(value['agreement'])
    with pytest.raises(RuntimeError):cycle.validate_scope(value)


def test_parent_validation_is_stdlib_only(request_bytes):
    from pathlib import Path
    import subprocess
    r=request_bytes
    repo=Path(__file__).resolve().parents[1]
    script="""import sys,json,base64
sys.path.insert(0,REPO)
from ops import light_native_lane_cycle as cycle
from types import SimpleNamespace
r=json.loads(sys.stdin.buffer.read())
cycle.owner.release.staging.require_current_main=lambda source:None
cycle.validated(*(base64.b64decode(r[k]) if k!='digest' else r[k]
 for k in ('raw','digest','controller','runtime')),SimpleNamespace(assert_current=lambda:None))
assert not any(name in sys.modules for name in ('psycopg','psycopg_binary','database.light_native_pilot_intake'))
""".replace('REPO',repr(str(repo)))
    wire={k:base64.b64encode(getattr(r,k)).decode() for k in ('raw','controller','runtime')}
    wire['digest']=r.digest
    result=subprocess.run(['/usr/bin/python3','-I','-S','-B','-c',script],
        input=json.dumps(wire).encode(),capture_output=True)
    assert result.returncode==0,result.stderr.decode()


def test_actual_isolated_children_load_driver_once_and_do_not_inherit(journal):
    from pathlib import Path
    import psycopg,typing_extensions
    j=journal;r=j.request
    repo=Path(__file__).resolve().parents[1]
    helpers={name:('' if name.endswith('/__init__.py') else (repo/name).read_text())
        for name in dict.fromkeys((*owner.release.HELPERS,*owner.EXTRA))}
    # Replace external authentication/driver provisioning only. The real child
    # bootstrap, canonical wire, scope validation and one-phase call are used.
    helpers['ops/light_native_lane_cycle.py']+='\n'+"""
import sys
from contextlib import contextmanager
from types import SimpleNamespace
sys.path.append(REPO)
from ops import light_native_lane_run_guard as _guard
from ops import native_maintenance_owner_host as _host
_guard.authenticated=lambda *args:SimpleNamespace(assert_current=lambda:None,assert_running=lambda:None)
owner.release.staging.require_current_main=lambda source:None
@contextmanager
def _driver(wheels):
    assert 'psycopg' not in sys.modules
    sys.path[:0]=SITES
    import psycopg
    yield psycopg,{}
_host.loaded_runtime=_driver
_original_child=child_main
def child_main(wire,expected,context):
    assert sys.flags.isolated and sys.flags.no_site
    assert 'psycopg' not in sys.modules
    if wire['mode']=='phase':
        # Root intent/derived binding has its own real-journal tests.
        outer=owner.encoded(dict(version=1,action='cycle',prepare={}))
        owner.read=lambda *args:outer
        globals()['authorize_phase']=lambda *args:None
        def phase(*args,**kwargs):
            with _host.loaded_runtime(args[0]):
                assert 'psycopg' in sys.modules
                return dict(audit='CHILD_TEST',pid=os.getpid())
        owner.phase=phase
    return _original_child(wire,expected,context)
""".replace('REPO',repr(str(repo))).replace('SITES',repr(list(dict.fromkeys([str(Path(psycopg.__file__).parents[1]),str(Path(typing_extensions.__file__).parent)]))))
    package=owner.encoded(dict(version=1,kind='LIGHT_LANE_CONTROLLER',source=r.prepare['source'],helpers=helpers))
    prepare=dict(r.prepare,accepted_controller_sha256=owner.sha(package))
    raw=owner.encoded(dict(version=1,action='cycle',prepare=prepare))
    guard=SimpleNamespace(source=prepare['source'],run_id=1,attempt=1)
    # journal fixture replaces isolated: explicitly use the production function.
    invoke=REAL_ISOLATED
    result=invoke(b'wheels','private-password','private-token',package,r.runtime,raw,owner.sha(raw),guard,
        outer=owner.sha(raw),mode='validate')
    assert result==dict(audit='LIGHT_LANE_CYCLE_VALIDATED')
    pids=[]
    for action in ('prepare','publish'):
        payload=owner.encoded(dict(prepare,action=action))
        result=invoke(b'wheels','private-password','private-token',package,r.runtime,payload,owner.sha(payload),guard,
            outer=owner.sha(raw),mode='phase')
        pids.append(result['pid'])
    assert len(set(pids+[os.getpid()]))==3


REAL_ISOLATED=cycle.isolated
