"""Finite authority and durable issuance fault tests."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import pytest
from ops import light_native_lane_issuer as issuer
from ops import light_native_lane_controller as owner
from ops import light_native_lane_cycle as cycle
from ops.native_maintenance_agreement import COVERAGE
from oracle_autopilot.light_native_adapter import FALSE_FLAGS

@pytest.fixture
def request_bytes(monkeypatch):
    plans=[]
    for n in range(2):
        spec=dict(assignment_schema='SLAVIK_DISPATCH_ASSIGNMENT_V1',repository='olegmed1-art/bridge-video-free',target_pr=1900+n,expected_head_sha=str(n+1)*40,execution_mode='READ_ONLY',exact_head_binding=True,cost_cap_microusd=0,max_repair_attempts=0,**dict.fromkeys(FALSE_FLAGS,False))
        raw=owner.encoded(dict(version=1,source=owner.install.RETAINED_SOURCE,repository=spec['repository'],target_pr=spec['target_pr'],expected_head_sha=spec['expected_head_sha'],branch=f'fix/audit-{n}',work_key=f'issuer-{n}',objective='Read source only.',priority=0,task_spec_json=spec))
        plans.append(dict(plan_base64=base64.b64encode(raw).decode(),accepted_plan_sha256=owner.sha(raw)))
    controller=owner.encoded(dict(version=1,kind='LIGHT_LANE_CONTROLLER',source='a'*40,helpers=dict.fromkeys((*owner.release.HELPERS,*owner.EXTRA),'# fixture')))
    now=datetime.now(timezone.utc).replace(microsecond=0);stamp=lambda dt:dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    policy=dict(version=1,action='issue',source='a'*40,accepted_controller_sha256=owner.sha(controller),accepted_runtime_sha256=owner.sha(b'retained'),not_before=stamp(now-timedelta(seconds=1)),expires_at=stamp(now+timedelta(hours=2)),predecessor=dict(plan_sha256='b'*64,terminal_sha256='c'*64,sequence=2),plans=plans,authority=dict(owner='olegmed1-art',coverage=COVERAGE,delegation='FINITE_ISSUER_AGREEMENTS',evidence='Independent acceptance of catalogue and exclusive write lanes.'))
    guard=SimpleNamespace(assert_current=lambda:None,assert_running=lambda:None)
    monkeypatch.setattr(owner.release.staging,'require_current_main',lambda source:None)
    raw=owner.encoded(policy)
    return SimpleNamespace(policy=policy,raw=raw,accepted=owner.sha(raw),controller=controller,runtime=b'retained',guard=guard,now=int(now.timestamp()))

def test_acceptance_and_delegation(request_bytes):
    r=request_bytes
    assert issuer.validate(r.raw,r.accepted,r.controller,r.runtime,r.guard)==r.policy
    with pytest.raises(RuntimeError,match='NOT_ACCEPTED'):issuer.validate(r.raw,'0'*64,r.controller,r.runtime,r.guard)
    for change in ('authority','extra','version','empty','duplicate','expired','long','source','runtime'):
        p=deepcopy(r.policy)
        if change=='authority':p['authority']['delegation']='AUTO'
        if change=='extra':p['renew']=True
        if change=='version':p['version']=True
        if change=='empty':p['plans']=[]
        if change=='duplicate':p['plans'][1]=p['plans'][0]
        if change=='expired':p['expires_at']=p['not_before']
        if change=='long':p['expires_at']='2099-01-01T00:00:00Z'
        if change=='source':p['source']='f'*40
        if change=='runtime':p['accepted_runtime_sha256']='f'*64
        raw=owner.encoded(p)
        with pytest.raises(RuntimeError):issuer.validate(raw,owner.sha(raw),r.controller,r.runtime,r.guard)

def test_exact_derived_scope_and_finite_time(request_bytes):
    r=request_bytes;p=issuer.derive(r.policy,r.accepted,0,r.policy['predecessor'],r.now)['prepare']
    from database.light_native_pilot_intake import Plan
    from ops.native_maintenance_agreement import Agreement
    plan=Plan(base64.b64decode(p['plan_base64']),p['accepted_plan_sha256'])
    a=Agreement(p['agreement'],p['accepted_agreement_sha256'],plan.scope)
    assert a.end-a.start==1800 and owner.sequence(p)==3
    p=deepcopy(r.policy);p['expires_at']=datetime.fromtimestamp(r.now+1000,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    assert issuer.derive(p,r.accepted,0,p['predecessor'],r.now)['prepare']['agreement']['expires_at']==p['expires_at']
    with pytest.raises(RuntimeError,match='EXPIRED'):issuer.derive(p,r.accepted,0,p['predecessor'],r.now+101)

@pytest.fixture
def journal(request_bytes,tmp_path,monkeypatch):
    if os.geteuid()!=0:pytest.skip('Mandatory root CI exercises durable metadata')
    r=request_bytes
    monkeypatch.setattr(owner,'ROOT',tmp_path/'owner');owner.ROOT.mkdir(mode=0o700)
    monkeypatch.setattr(owner.install,'root_parent',lambda path:None)
    monkeypatch.setattr(owner.execution,'ROOT',tmp_path/'execution');owner.execution.ROOT.mkdir(mode=0o700)
    monkeypatch.setattr(issuer.os,'uname',lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
    calls=[];admitted=[];fault=SimpleNamespace(unknown=False,preflight=False,after=False)
    def isolated(wheels,credential,token,controller,runtime,raw,accepted,guard,*,outer,mode):
        assert mode=='issuer_validate' and raw==r.raw and accepted==r.accepted
        admitted.append(outer)
        if fault.preflight:raise RuntimeError('preflight rejected')
        value=issuer.derive(r.policy,r.accepted,**outer)
        return dict(audit='LIGHT_LANE_ISSUER_ADMITTED',cycle_sha256=owner.sha(owner.encoded(value)))
    def run(wheels,credential,token,controller,runtime,raw,accepted,guard):
        calls.append(owner.parse(raw));assert owner.sha(raw)==accepted
        if fault.unknown:raise RuntimeError('lost acknowledgement private-secret')
        outer=owner.parse(raw);p=outer['prepare'];target=cycle.location(p)
        target.parent.mkdir(mode=0o700,exist_ok=True);target.mkdir(mode=0o700)
        owner.retain(target/'intent.json',raw)
        scope=owner.ROOT/p['accepted_plan_sha256'];scope.mkdir(mode=0o700)
        dispatch=f'dispatch-{len(calls)}';task=f'task-{len(calls)}'
        terminal=owner.encoded(dict(dispatch_id=dispatch,task_id=task,result=dict(result_code='AUDIT_PASSED')))
        owner.retain(scope/'terminal.json',terminal)
        for name,body in [('intake',dict(plan_sha256=p['accepted_plan_sha256'],dispatch_id=dispatch,task_id=task)),('discovery',dict(published=True)),('permit',dict(accepted=True))]:
            owner.retain(scope/(name+'.json'),owner.encoded(body))
        request_sha=str(len(calls))*64
        owner.retain(scope/'execution.json',owner.encoded(dict(request_sha256=request_sha)))
        execution=owner.execution.ROOT/request_sha;execution.mkdir(mode=0o700)
        owner.retain(execution/'stopped-hold.json',owner.encoded(dict(state='STOPPED_HOLD')))
        owner.retain(scope/'complete.json',owner.encoded(dict(plan_sha256=p['accepted_plan_sha256'],terminal_sha256=owner.sha(terminal),controls_restored=True,native=dict(MainPID='123'))))
        for action in cycle.STEPS:
            owner.retain(target/(action+'-intent.json'),owner.encoded(cycle.derive(p,action)))
            output=dict(audit='LIGHT_LANE_OWNER',phase=action,dispatch_id=dispatch)
            if action in ('prepare','publish','permit','terminal'):
                name={'prepare':'intake','publish':'discovery','permit':'permit','terminal':'terminal'}[action]
                output['record_sha256']=owner.sha(owner.read(scope/(name+'.json')))
            if action=='terminal':output.update(state='ACCEPTED',sequence=owner.sequence(p))
            if action=='execute':output.update(state='STOPPED_HOLD',request_sha256=request_sha)
            if action=='restore':output.update(state='HOLD',controls_restored=True,native_pid='123')
            owner.retain(target/(action+'-done.json'),owner.encoded(output))
        result=dict(audit='LIGHT_LANE_CYCLE',state='COMPLETE_HOLD',controls_restored=True,plan_sha256=p['accepted_plan_sha256'],sequence=owner.sequence(p),terminal_sha256=owner.sha(terminal),dispatch_id=f'dispatch-{len(calls)}',task_id=f'task-{len(calls)}',result_code='AUDIT_PASSED')
        owner.retain(target/'complete.json',owner.encoded(result))
        if fault.after:raise RuntimeError('lost acknowledgement after completion')
        return result
    monkeypatch.setattr(cycle,'isolated',isolated);monkeypatch.setattr(cycle,'run',run)
    def issue():return issuer.run(b'wheels','private-secret','private-token',r.controller,r.runtime,r.raw,r.accepted,r.guard)
    return SimpleNamespace(run=issue,request=r,calls=calls,admitted=admitted,fault=fault,path=owner.ROOT/'issuers'/r.accepted)

def test_two_new_tasks_once_then_exhausted(journal):
    j=journal;assert j.run()['issued']==1;first=j.calls[0]['prepare']
    assert j.run()['issued']==2;second=j.calls[1]['prepare']
    assert second['predecessor']['plan_sha256']==first['accepted_plan_sha256']
    assert owner.sequence(second)==owner.sequence(first)+1
    assert j.run()['state']=='EXHAUSTED' and len(j.calls)==2 and len(j.admitted)==2
    for path in owner.ROOT.rglob('*.json'):assert b'private-secret' not in path.read_bytes() and b'private-token' not in path.read_bytes()

@pytest.mark.parametrize('after',[False,True])
def test_unknown_never_advances_even_if_cycle_completed(journal,after):
    j=journal;j.fault.after=after;j.fault.unknown=not after
    with pytest.raises(RuntimeError):j.run()
    j.fault.after=False;j.fault.unknown=False
    with pytest.raises(RuntimeError,match='RECONCILIATION'):j.run()
    assert len(j.calls)==1

def test_preflight_failure_no_issue(journal):
    j=journal;j.fault.preflight=True
    with pytest.raises(RuntimeError):j.run()
    assert not (j.path/'0000').exists() and not j.calls

@pytest.mark.parametrize('point',['intent.json','done.json'])
def test_partial_write_refuses_retry(journal,monkeypatch,point):
    j=journal;original=owner.retain
    def retain(path,raw):
        if path.name==point and path.parent.name=='0000':
            original(path,raw[:12]);raise OSError('uncertain fsync')
        original(path,raw)
    monkeypatch.setattr(owner,'retain',retain)
    with pytest.raises(OSError):j.run()
    count=len(j.calls);monkeypatch.setattr(owner,'retain',original)
    with pytest.raises((RuntimeError,ValueError)):j.run()
    assert len(j.calls)==count

def test_new_policy_cannot_skip_unknown(journal):
    j=journal;j.fault.unknown=True
    with pytest.raises(RuntimeError):j.run()
    r=j.request;p=deepcopy(r.policy);p['authority']['evidence']='Different accepted policy.';raw=owner.encoded(p)
    with pytest.raises(RuntimeError,match='RECONCILIATION'):issuer.run(b'wheels','','',r.controller,r.runtime,raw,owner.sha(raw),r.guard)
    assert len(j.calls)==1

@pytest.mark.parametrize('damage',['hole','extra','done','terminal'])
def test_corrupt_history_refuses(journal,damage):
    j=journal;j.run()
    if damage=='hole':(j.path/'0000').rename(j.path/'0001')
    if damage=='extra':(j.path/'unexpected').mkdir()
    if damage=='done':(j.path/'0000'/'done.json').write_bytes(b'{}')
    if damage=='terminal':(owner.ROOT/j.calls[0]['prepare']['accepted_plan_sha256']/'terminal.json').write_bytes(b'{}')
    with pytest.raises(RuntimeError):j.run()
    assert len(j.calls)==1

@pytest.mark.parametrize('fault',['none','queue','duplicate','controls','head','predecessor','scope','outbox'])
def test_driver_preflight_uses_live_readonly_sources(request_bytes,monkeypatch,fault):
    r=request_bytes
    from database import light_native_pilot_intake as intake
    from ops import native_maintenance_owner_attest as attest
    from ops import native_maintenance_run_guard as run_guard
    from ops import light_native_pilot_owner as pilot
    class Connection:
        read_only=False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql,args=()):
            assert self.read_only is True and sql.strip().startswith('SELECT')
            assert args==('olegmed1-art/bridge-video-free',1900,'issuer-0','olegmed1-art/bridge-video-free',1900)
            return SimpleNamespace(fetchone=lambda:(1,0,0,0) if fault=='queue' else (0,0,1,0) if fault=='duplicate' else (0,0,0,1) if fault=='outbox' else (0,0,0,0))
    conn=Connection();seen=[]
    monkeypatch.setattr(attest,'parameters',lambda credential:{})
    monkeypatch.setattr(intake.engine,'identity',lambda c,t:seen.append('identity'))
    def one(c,sql):
        assert c.read_only and sql.startswith('SELECT')
        if 'native_cli_config' in sql:return dict(enabled=fault=='controls')
        return dict(enabled=True,can_repair=True)
    monkeypatch.setattr(intake,'one',one)
    monkeypatch.setattr(run_guard,'API',lambda token:object())
    def observed(api,plan):
        seen.append('head')
        if fault=='head':raise RuntimeError('head changed')
    monkeypatch.setattr(pilot,'observed_target',observed)
    def previous(c,p):
        assert c.read_only and p['predecessor']==r.policy['predecessor']
        seen.append('predecessor')
        if fault=='predecessor':raise RuntimeError('predecessor changed')
    monkeypatch.setattr(owner,'verify_previous',previous)
    if fault=='scope':
        # Even an unselected later entry cannot grant repair authority.
        entry=r.policy['plans'][1];p=owner.parse(base64.b64decode(entry['plan_base64']))
        p['task_spec_json']['max_repair_attempts']=1;raw=owner.encoded(p)
        entry.update(plan_base64=base64.b64encode(raw).decode(),accepted_plan_sha256=owner.sha(raw))
    def invoke():return issuer.preflight(r.policy,r.accepted,dict(index=0,start=r.now,predecessor=r.policy['predecessor']),'secret','token',SimpleNamespace(connect=lambda **kw:conn))
    if fault=='none':
        assert invoke()['audit']=='LIGHT_LANE_ISSUER_ADMITTED'
        assert seen==['head','identity','predecessor']
    else:
        with pytest.raises(RuntimeError):invoke()


def test_parent_stays_stdlib_only():
    import subprocess,sys
    from pathlib import Path
    source=str(Path(__file__).resolve().parents[1])
    code=f"import sys;sys.path.insert(0,{source!r});from ops import light_native_lane_issuer;assert not any(n=='psycopg' or n.startswith('psycopg.') for n in sys.modules)"
    result=subprocess.run([sys.executable,'-I','-S','-c',code],capture_output=True)
    assert result.returncode==0,result.stderr.decode()


def test_near_expiry_does_not_retain_issue_intent(journal,monkeypatch):
    j=journal
    r=j.request
    now=r.now
    values=iter([now,now,now+850])
    monkeypatch.setattr(issuer.time,'time',lambda:next(values))
    with pytest.raises(RuntimeError,match='EXPIRED'):j.run()
    assert not (j.path/'0000').exists() and not j.calls

@pytest.fixture
def monitor(journal,monkeypatch):
    j=journal;r=j.request
    j.fault.after=True
    with pytest.raises(RuntimeError):j.run()
    value=dict(version=1,action='observe-issue',source=r.policy['source'],accepted_controller_sha256=owner.sha(r.controller),accepted_runtime_sha256=owner.sha(r.runtime),policy_sha256=r.accepted,index=0,expected_cycle_sha256=None)
    complete=owner.read(cycle.location(j.calls[0]['prepare'])/'complete.json')
    checks=[];fault=SimpleNamespace(refuse=False,drift=False)
    def isolated(wheels,credential,token,controller,runtime,raw,accepted,guard,*,outer,mode):
        assert mode=='issuer_monitor' and owner.sha(raw)==accepted
        v=owner.parse(raw);record=issuer.monitor_records(v);checks.append(v)
        if fault.refuse:raise RuntimeError('primary source unavailable')
        proof=dict(audit='LIGHT_LANE_ISSUER_OBSERVED',state=record['state'],cycle_sha256=owner.sha(record['complete_raw']),intent_sha256=record['intent_sha256'],policy_sha256=v['policy_sha256'],index=v['index'],sequence=record['predecessor']['sequence'],result_code=record['result']['result_code'])
        if fault.drift:(record['path']/'intent.json').write_bytes(b'{}')
        return proof
    monkeypatch.setattr(cycle,'isolated',isolated)
    def call(action='observe-issue'):
        v=dict(value,action=action,expected_cycle_sha256=owner.sha(complete) if action=='reconcile-issue' else value['expected_cycle_sha256'])
        raw=owner.encoded(v)
        return issuer.monitor(b'wheels','private-secret','private-token',r.controller,r.runtime,raw,owner.sha(raw),r.guard)
    return SimpleNamespace(j=j,call=call,value=value,checks=checks,fault=fault,complete=complete)


def test_observe_is_readonly_and_reconcile_only_missing_ack(monitor):
    m=monitor
    before={str(p):p.read_bytes() for p in owner.ROOT.rglob('*') if p.is_file()}
    assert m.call()['state']=='COMPLETE_ACK_MISSING'
    assert before=={str(p):p.read_bytes() for p in owner.ROOT.rglob('*') if p.is_file()}
    assert m.call('reconcile-issue')['execution_replayed'] is False
    assert owner.read(m.j.path/'0000'/'done.json')==m.complete
    assert m.call('reconcile-issue')['state']=='COMPLETE_ACKNOWLEDGED'
    assert m.call()['state']=='COMPLETE_ACKNOWLEDGED'
    assert len(m.j.calls)==1


@pytest.mark.parametrize('damage',['complete','phase_intent','phase_done','terminal','incident','partial_ack','extra_result'])
def test_monitor_refuses_incomplete_or_inconsistent_journals(monitor,damage):
    m=monitor;root=cycle.location(m.j.calls[0]['prepare'])
    if damage=='complete':(root/'complete.json').unlink()
    if damage=='phase_intent':(root/'publish-intent.json').write_bytes(b'{}')
    if damage=='phase_done':(root/'terminal-done.json').write_bytes(b'{}')
    if damage=='terminal':(owner.ROOT/m.j.calls[0]['prepare']['accepted_plan_sha256']/'terminal.json').write_bytes(b'{}')
    if damage=='incident':owner.retain(root/'incident.json',b'{}')
    if damage=='partial_ack':owner.retain(m.j.path/'0000'/'done.json',b'{')
    if damage=='extra_result':
        result=owner.parse(owner.read(root/'complete.json'));result['dispatch_id']='other'
        (root/'complete.json').write_bytes(owner.encoded(result))
    with pytest.raises((RuntimeError,ValueError)):m.call('reconcile-issue')
    assert len(m.j.calls)==1
    if damage=='complete':
        assert m.call()['live_verified'] is False and not m.checks


@pytest.mark.parametrize('fault',['refuse','drift'])
def test_live_failure_or_midcheck_drift_never_writes_ack(monitor,fault):
    m=monitor;setattr(m.fault,fault,True)
    with pytest.raises(RuntimeError):m.call('reconcile-issue')
    assert not (m.j.path/'0000'/'done.json').exists() and len(m.j.calls)==1


def test_expired_policy_can_be_observed_and_acknowledged(monitor,monkeypatch):
    m=monitor
    monkeypatch.setattr(issuer.time,'time',lambda:4102444800)
    assert m.call()['state']=='COMPLETE_ACK_MISSING'
    assert m.call('reconcile-issue')['state']=='COMPLETE_ACKNOWLEDGED'
    assert len(m.j.calls)==1


def test_acknowledgement_loss_rechecks_without_execution(monitor,monkeypatch):
    m=monitor;original=owner.remember
    def remember(path,raw):
        original(path,raw)
        if path.name=='done.json':raise OSError('lost fsync response')
    monkeypatch.setattr(owner,'remember',remember)
    with pytest.raises(OSError):m.call('reconcile-issue')
    monkeypatch.setattr(owner,'remember',original)
    assert m.call('reconcile-issue')['state']=='COMPLETE_ACKNOWLEDGED'
    assert len(m.checks)==3 and len(m.j.calls)==1


def test_missing_lock_is_not_recreated(monitor):
    m=monitor;lock=owner.ROOT/'issuers'/'cycle.lock';lock.unlink()
    with pytest.raises(RuntimeError):m.call()
    assert not lock.exists()

@pytest.mark.parametrize('fault',['none','queue','controls','provider'])
def test_monitor_live_requires_original_sources_and_exact_restored_controls(monitor,monkeypatch,fault):
    m=monitor;record=issuer.monitor_records(m.value)
    from database import light_native_pilot_intake as intake
    from ops import native_maintenance_owner_attest as attest
    scope=owner.ROOT/record['predecessor']['plan_sha256']
    receipt=owner.parse(owner.read(scope/'intake.json'));receipt['snapshot_sha256']='d'*64
    (scope/'intake.json').write_bytes(owner.encoded(receipt))
    baseline=dict(version=1,plan_sha256=record['predecessor']['plan_sha256'],target=intake.EXPECTED_TARGET,
                  native_config=dict(enabled=False,cutover_at='original'),autopilot_role=dict(can_repair=True,enabled=True,updated_at='prior'))
    monkeypatch.setattr(intake.engine,'load_manifest',lambda p,d:baseline)
    monkeypatch.setattr(intake.engine,'identity',lambda c,t:None)
    monkeypatch.setattr(attest,'parameters',lambda credential:{})
    monkeypatch.setattr(owner.release,'validate',lambda *args:None)
    monkeypatch.setattr(owner.release.staging,'verify_release',lambda *args:None)
    seen=[]
    class Connection:
        read_only=False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql):
            assert self.read_only is True and sql.strip().startswith('SELECT')
            return SimpleNamespace(fetchone=lambda:(1,0) if fault=='queue' else (0,0))
    conn=Connection()
    def one(c,sql):
        assert c.read_only and sql.startswith('SELECT')
        if 'native_cli_config' in sql:return dict(enabled=False,cutover_at='changed' if fault=='controls' else 'original')
        return dict(can_repair=True,enabled=True,updated_at='current')
    monkeypatch.setattr(intake,'one',one)
    def previous(c,v,*,readonly):
        assert readonly is True and c.read_only and v['predecessor']==record['predecessor']
        seen.append('fresh original Cloud/DB/HOLD')
        if fault=='provider':raise RuntimeError('original provider mismatch')
    monkeypatch.setattr(owner,'verify_previous',previous)
    runtime=owner.release.encoded(dict(source=owner.install.RETAINED_SOURCE,runtime=dict(sha256='f'*64)))
    def invoke():return issuer.monitor_live(m.value,record,'private',SimpleNamespace(connect=lambda **kw:conn),runtime)
    if fault=='none':
        assert invoke()['state']=='COMPLETE_ACK_MISSING'
        assert seen==['fresh original Cloud/DB/HOLD']
    else:
        with pytest.raises(RuntimeError):invoke()
    assert not (m.j.path/'0000'/'done.json').exists()


def test_reconcile_existing_ack_needs_its_exact_prior_request(monitor):
    m=monitor
    owner.retain(m.j.path/'0000'/'done.json',m.complete)
    with pytest.raises((RuntimeError,OSError)):m.call('reconcile-issue')
    assert not (owner.ROOT/'issuer-reconciliations').exists()


def test_final_primary_failure_after_intent_does_not_write_ack(monitor,monkeypatch):
    m=monitor;original=cycle.isolated;calls=[]
    def failing(*args,**kwargs):
        calls.append(True)
        if len(calls)==2:raise RuntimeError('primary evidence changed before ack')
        return original(*args,**kwargs)
    monkeypatch.setattr(cycle,'isolated',failing)
    with pytest.raises(RuntimeError):m.call('reconcile-issue')
    assert not (m.j.path/'0000'/'done.json').exists() and len(m.j.calls)==1


def test_incomplete_diagnostic_reads_only_bounded_metadata(monitor, monkeypatch):
    m = monitor
    root = cycle.location(m.j.calls[0]['prepare'])
    (root/'complete.json').unlink()
    incident = dict(phase='prepare', containment='NOT_ATTEMPTED', state='RECONCILIATION_REQUIRED')
    owner.retain(root/'incident.json', owner.encoded(incident))
    scope = owner.ROOT/m.j.calls[0]['prepare']['accepted_plan_sha256']
    owner.retain(scope/'baseline.json', b'private-secret-do-not-return')
    before = {str(p): p.read_bytes() for p in owner.ROOT.rglob('*') if p.is_file()}
    def forbidden(*args, **kwargs):
        raise AssertionError('diagnostic attempted effects or live verification')
    monkeypatch.setattr(owner, 'retain', forbidden)
    monkeypatch.setattr(owner, 'remember', forbidden)
    monkeypatch.setattr(cycle, 'isolated', forbidden)
    result = m.call()
    assert result['live_verified'] is False
    assert result['diagnostic']['incident'] == incident
    assert result['diagnostic']['records']['baseline.json'] is True
    assert 'private-secret' not in owner.encoded(result).decode()
    assert before == {str(p): p.read_bytes() for p in owner.ROOT.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage', ['incident_extra', 'incident_code', 'scope_link',
                                  'record_link', 'record_mode', 'cycle_binding', 'issuer_binding'])
def test_incomplete_diagnostic_refuses_untrusted_metadata(monitor, damage):
    m = monitor
    root = cycle.location(m.j.calls[0]['prepare'])
    (root/'complete.json').unlink()
    scope = owner.ROOT/m.j.calls[0]['prepare']['accepted_plan_sha256']
    if damage.startswith('incident'):
        value = dict(phase='prepare', containment='NOT_ATTEMPTED', state='RECONCILIATION_REQUIRED')
        if damage == 'incident_extra': value['secret'] = 'must-not-emit'
        else: value['phase'] = 'untrusted-error-message'
        owner.retain(root/'incident.json', owner.encoded(value))
    if damage == 'scope_link':
        moved = scope.with_name('relocated'); scope.rename(moved); scope.symlink_to(moved)
    if damage == 'record_link':
        (scope/'intake.json').unlink(); (scope/'intake.json').symlink_to(root/'intent.json')
    if damage == 'record_mode': (scope/'intake.json').chmod(0o644)
    if damage == 'cycle_binding': (root/'intent.json').write_bytes(b'{}')
    if damage == 'issuer_binding':
        path = m.j.path/'0000'/'intent.json'
        value = owner.parse(path.read_bytes()); value['start'] += 1
        path.write_bytes(owner.encoded(value))
    with pytest.raises((RuntimeError, OSError)): m.call()
    assert not m.checks and len(m.j.calls) == 1
