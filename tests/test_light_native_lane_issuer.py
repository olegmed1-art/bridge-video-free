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
        terminal=owner.encoded(dict(result='AUDIT_PASSED',index=len(calls)));owner.retain(scope/'terminal.json',terminal)
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
