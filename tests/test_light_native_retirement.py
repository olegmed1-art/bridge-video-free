"""Synthetic exact-journal fixtures for writer, issuer and consumer admission."""
import base64
from copy import deepcopy
import os
from types import SimpleNamespace

import pytest
from ops import light_native_retirement as r
from ops import light_native_retirement_live as live
from ops import light_native_lane_controller as owner
from ops import light_native_lane_issuer as issuer
from ops import light_native_lane_cycle as cycle
from test_light_native_lane_issuer import request_bytes
from test_light_native_retirement_faults import _record


def store(root,path,value):
    path=root/path
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    for item in (path.parent,*path.parent.parents):
        if item==root.parent:break
        item.chmod(0o700)
    path.write_bytes(value if type(value) is bytes else r.encoded(value));path.chmod(0o600)
    return path


@pytest.fixture
def retained(tmp_path,monkeypatch,request_bytes):
    t=request_bytes;root=tmp_path/'owner';root.mkdir(mode=0o700)
    monkeypatch.setattr(owner,'ROOT',root)
    policy=deepcopy(t.policy)
    prior_raw=base64.b64decode(policy['plans'][1]['plan_base64'])
    prior_plan=r.sha(prior_raw)
    terminal=dict(dispatch_id='synthetic-prior',task_id='synthetic-task',request={'bound':'request'},
                  provider_task_id='synthetic-provider',result={'provider_evidence_sha256':'a'*64})
    previous=dict(sequence=2,plan_sha256=prior_plan,terminal_sha256=r.sha(r.encoded(terminal)))
    policy['predecessor']=previous
    historical=owner.parse(t.controller)
    historical['helpers']={k:v for k,v in historical['helpers'].items() if k not in owner.RETIREMENT_HELPERS}
    historical['helpers']['ops/light_native_lane_controller.py']='# synthetic large helper\n'+'#'*280000
    package=owner.encoded(historical);policy['accepted_controller_sha256']=r.sha(package)
    digest=r.sha(r.encoded(policy));outer=issuer.derive(policy,digest,0,previous,t.now)
    prepare=outer['prepare'];plan=prepare['accepted_plan_sha256'];entry='issuers/'+digest+'/0000';cy='cycles/'+plan
    incident=dict(phase='prepare',containment='INTAKE_ROLLED_BACK',state='RECONCILIATION_REQUIRED')
    contained=dict(audit='LIGHT_LANE_OWNER',phase='contain',state='INTAKE_ROLLED_BACK',plan_sha256=plan,
                   controls_restored=False,task_success=False,queue_retry_authorized=False)
    rows={'issuers/cycle.lock':b'', 'cycles/cycle.lock':b'', 'issuers/'+digest+'/policy.json':policy,
          entry+'/intent.json':dict(start=t.now,cycle=outer),cy+'/intent.json':outer,
          cy+'/prepare-intent.json':prepare,cy+'/contain-intent.json':dict(prepare,action='contain'),
          cy+'/contain-done.json':contained,cy+'/incident.json':incident,
          plan+'/plan.json':base64.b64decode(prepare['plan_base64']),plan+'/prepare.json':prepare,
          plan+'/baseline.json':{'synthetic':'baseline'},plan+'/before.json':{'synthetic':'before'},
          plan+'/contained.json':contained,
          plan+'/contain-intent.json':dict(plan_sha256=plan,action='CONTAIN_PREEXECUTION'),
          plan+'/runtime-package.json':t.runtime,plan+'/wheels.tar':b'synthetic-driver',
          'controllers/'+policy['source']+'/package.json':package,
          prior_plan+'/plan.json':prior_raw,prior_plan+'/terminal.json':terminal,
          prior_plan+'/intake.json':dict(plan_sha256=prior_plan,dispatch_id='synthetic-prior'),
          prior_plan+'/complete.json':dict(controls_restored=True,native={'pid':'synthetic'}),
          prior_plan+'/controls-restored.json':dict(controls_restored=True),
          prior_plan+'/execution.json':dict(request_sha256='f'*64),
          prior_plan+'/prepare.json':dict(version=2,accepted_plan_sha256=prior_plan,
             accepted_runtime_sha256=policy['accepted_runtime_sha256'],
             predecessor=dict(sequence=1,plan_sha256='b'*64,terminal_sha256='c'*64))}
    binding=dict(plan_sha256=prior_plan,request_sha256='f'*64,
                 receipt_sha256=r.sha(r.encoded(rows[prior_plan+'/intake.json'])),terminal_sha256=previous['terminal_sha256'])
    rows[prior_plan+'/restore-intent.json']=rows[prior_plan+'/restart-intent.json']=binding
    for name,text in historical['helpers'].items():rows['controllers/'+policy['source']+'/'+name]=text.encode()
    for path,value in rows.items():store(root,path,value)
    value=r.parse(_record());value.update(policy_sha256=digest,plan_sha256=plan,predecessor=previous,
        repository=t.policy and r.parse(rows[plan+'/plan.json'])['repository'],
        failed_work_key=r.parse(rows[plan+'/plan.json'])['work_key'],failed_target_pr=r.parse(rows[plan+'/plan.json'])['target_pr'],
        incident_sha256=r.sha(r.encoded(incident)),historical_controller_source=policy['source'],
        historical_controller_sha256=r.sha(package),retained_runtime_source=owner.install.RETAINED_SOURCE,
        retained_runtime_sha256=r.sha(t.runtime))
    paths={x:plan+'/'+x for x in ('baseline.json','before.json','contained.json','prepare.json','runtime-package.json','wheels.tar')}
    paths.update({'cycle-contain-intent':cy+'/contain-intent.json','cycle-intent':cy+'/intent.json',
                  'driver-contain-intent':plan+'/contain-intent.json','incident.json':cy+'/incident.json',
                  'issuer-intent':entry+'/intent.json'})
    pins={k:r.sha((root/v).read_bytes()) for k,v in paths.items()}
    raw=r.encoded(value);ref=dict(policy_sha256=digest,index=0,record_sha256=r.sha(raw))
    return SimpleNamespace(root=root,policy=policy,record=value,raw=raw,ref=ref,pins=pins,paths=paths,
                           entry=entry,plan=plan,cycle=cy,previous=previous,request=t)


def observe(t):
    def check(view,value):
        live.local(view,value,t.pins)
        return {'local_sha256':r.sha(r.encoded(view.rows))}
    return check


def retain_proposal(t):
    return r.write_proposal(t.root,t.raw,r.sha(t.raw),observe(t))


def test_create_exact_then_readonly_idempotence_large_historical_package(retained):
    t=retained;assert retain_proposal(t)['state']=='PROPOSAL_RETAINED_UNACCEPTED'
    p=t.root/t.entry/r.NAME;before=p.stat()
    assert p.read_bytes()==t.raw and before.st_mode&0o777==0o600
    assert retain_proposal(t)['state']=='PROPOSAL_PRESENT_UNACCEPTED'
    after=p.stat();assert (before.st_ino,before.st_mtime_ns,before.st_ctime_ns)==(after.st_ino,after.st_mtime_ns,after.st_ctime_ns)


@pytest.mark.parametrize('name',sorted(r.JOURNAL_KEYS))
def test_preexisting_original_content_drift_refused(retained,name):
    t=retained;(t.root/t.paths[name]).write_bytes(b'changed-before-invocation')
    with pytest.raises(RuntimeError):retain_proposal(t)
    assert not (t.root/t.entry/r.NAME).exists()


@pytest.mark.parametrize('fault',['partial','different','symlink','hardlink','intake','later-phase','later-issue','wrong-pin'])
def test_invalid_journal_never_repairs(retained,fault):
    t=retained;target=t.root/t.entry/r.NAME
    if fault in ('partial','different'):store(t.root,t.entry+'/'+r.NAME,b'{' if fault=='partial' else b'{}')
    if fault=='symlink':target.symlink_to(t.root/t.entry/'intent.json')
    if fault=='hardlink':os.link(t.root/t.entry/'intent.json',target)
    if fault=='intake':store(t.root,t.plan+'/intake.json',{})
    if fault=='later-phase':store(t.root,t.cycle+'/publish-intent.json',{})
    if fault=='later-issue':store(t.root,'issuers/'+t.ref['policy_sha256']+'/0001/intent.json',{})
    if fault=='wrong-pin':t.pins['baseline.json']='0'*64
    with pytest.raises((RuntimeError,OSError)):retain_proposal(t)
    if fault in ('partial','different'):assert target.read_bytes() in (b'{',b'{}')


def test_postwrite_guard_failure_preserves_complete_unknown(retained):
    t=retained;calls=0
    def check(view,value):
        nonlocal calls
        calls+=1;observe(t)(view,value)
        if calls==3:raise RuntimeError('synthetic late primary drift')
        return True
    with pytest.raises(RuntimeError,match='UNKNOWN'):r.write_proposal(t.root,t.raw,r.sha(t.raw),check)
    assert (t.root/t.entry/r.NAME).read_bytes()==t.raw


def new_policy(t):
    p=deepcopy(t.request.policy);p.update(version=2,retirements=[t.ref],retirement_evidence={t.ref['policy_sha256']:t.pins},predecessor=t.previous)
    plan=r.parse(base64.b64decode(p['plans'][0]['plan_base64']));plan.update(work_key='synthetic-new-work',target_pr=1909)
    plan['task_spec_json']['target_pr']=1909;raw=r.encoded(plan)
    p['plans']=[dict(plan_base64=base64.b64encode(raw).decode(),accepted_plan_sha256=r.sha(raw))]
    return p


def test_presence_alone_old_policy_and_direct_routes_stay_blocked(retained):
    t=retained;retain_proposal(t)
    with pytest.raises(RuntimeError):issuer.progress(t.root/'issuers'/t.ref['policy_sha256'],t.policy,t.ref['policy_sha256'])
    with pytest.raises(RuntimeError):live.history_gate(t.policy,t.ref['policy_sha256'])
    p=new_policy(t);p['version']=1;p.pop('retirements');p.pop('retirement_evidence')
    with pytest.raises(RuntimeError):live.history_gate(p,r.sha(r.encoded(p)))
    direct=issuer.derive(p,r.sha(r.encoded(p)),0,t.previous,t.request.now)['prepare']
    with pytest.raises(RuntimeError):live.entry_guard(direct)


@pytest.mark.parametrize('fault',['missing','hash','identity','evidence','self'])
def test_new_policy_reference_binding_refusals(retained,fault):
    t=retained;retain_proposal(t);p=new_policy(t);accepted=r.sha(r.encoded(p))
    if fault=='missing':p['retirements']=[]
    if fault=='hash':p['retirements'][0]=dict(t.ref,record_sha256='0'*64)
    if fault=='identity':p['plans']=deepcopy(t.policy['plans'])
    if fault=='evidence':p['retirement_evidence'][t.ref['policy_sha256']]=dict(t.pins,**{'before.json':'0'*64})
    if fault=='self':accepted=t.ref['policy_sha256']
    with pytest.raises(RuntimeError):live.history_gate(p,accepted)


def test_explicit_policy_gate_and_private_issuer_capability(retained):
    t=retained;retain_proposal(t);p=new_policy(t);raw=r.encoded(p);accepted=r.sha(raw)
    live.history_gate(p,accepted)
    assert issuer.validate(raw,accepted,t.request.controller,t.request.runtime,t.request.guard)==p
    outer=issuer.derive(p,accepted,0,t.previous,t.request.now);prepare=outer['prepare']
    assert prepare['version']==3 and owner.sequence(prepare)==3
    store(t.root,'issuers/'+accepted+'/policy.json',raw)
    store(t.root,'issuers/'+accepted+'/0000/intent.json',dict(start=t.request.now,cycle=outer))
    with pytest.raises(RuntimeError):live.entry_guard(prepare)
    live.entry_guard(prepare,live.IssueAuthority(accepted,0,prepare))


def test_strict_record_reference_and_large_parser():
    raw=_record();assert r.record(raw,r.sha(raw))['incident_closed'] is False
    for candidate in (raw+b'\n',raw.replace(b'false',b'true',1),b'{"a":1,"a":2}',b'NaN'):
        with pytest.raises((RuntimeError,ValueError)):r.record(candidate)
    big=r.encoded({'data':'x'*280000})
    with pytest.raises(RuntimeError):r.parse(big)
    assert len(r.parse(big,limit=3*1024*1024)['data'])==280000
    with pytest.raises(RuntimeError):r.references([{'policy_sha256':'a'*64,'index':False,'record_sha256':'b'*64}])


def completed_new(t,monkeypatch):
    p=new_policy(t);digest=r.sha(r.encoded(p));outer=issuer.derive(p,digest,0,t.previous,t.request.now)
    prepare=outer['prepare'];plan=prepare['accepted_plan_sha256'];cy='cycles/'+plan
    terminal=dict(dispatch_id='synthetic-descendant',task_id='synthetic-descendant-task',
                  request={'bound':'new-request'},provider_task_id='synthetic-new-provider',
                  result=dict(result_code='AUDIT_PASSED',provider_evidence_sha256='f'*64))
    result=dict(audit='LIGHT_LANE_CYCLE',state='COMPLETE_HOLD',controls_restored=True,plan_sha256=plan,
                sequence=3,terminal_sha256=r.sha(r.encoded(terminal)),dispatch_id=terminal['dispatch_id'],
                task_id=terminal['task_id'],result_code='AUDIT_PASSED')
    rows={'issuers/'+digest+'/policy.json':p,'issuers/'+digest+'/0000/intent.json':dict(start=t.request.now,cycle=outer),
          'issuers/'+digest+'/0000/done.json':result,cy+'/intent.json':outer,cy+'/complete.json':result,
          plan+'/plan.json':base64.b64decode(prepare['plan_base64']),plan+'/terminal.json':terminal,
          plan+'/intake.json':dict(plan_sha256=plan,dispatch_id=terminal['dispatch_id'],task_id=terminal['task_id']),
          plan+'/discovery.json':{'synthetic':True},plan+'/permit.json':{'synthetic':True},
          plan+'/execution.json':{'request_sha256':'e'*64},
          plan+'/complete.json':dict(controls_restored=True,plan_sha256=plan,terminal_sha256=result['terminal_sha256'],native={'MainPID':'5'})}
    for path,body in rows.items():store(t.root,path,body)
    execution=t.root.parent/'execution';execution.mkdir(mode=0o700)
    monkeypatch.setattr(owner.execution,'ROOT',execution)
    store(execution,'e'*64+'/stopped-hold.json',{'state':'STOPPED_HOLD'})
    for action in cycle.STEPS:
        store(t.root,cy+'/'+action+'-intent.json',cycle.derive(prepare,action))
        output=dict(audit='LIGHT_LANE_OWNER',phase=action,dispatch_id=terminal['dispatch_id'])
        if action in ('prepare','publish','permit','terminal'):
            name={'prepare':'intake','publish':'discovery','permit':'permit','terminal':'terminal'}[action]
            output['record_sha256']=r.sha((t.root/plan/(name+'.json')).read_bytes())
        if action=='terminal':output.update(state='ACCEPTED',sequence=3)
        if action=='execute':output.update(state='STOPPED_HOLD',request_sha256='e'*64)
        if action=='restore':output.update(state='HOLD',controls_restored=True,native_pid='5')
        store(t.root,cy+'/'+action+'-done.json',output)
    return p,digest,result


def test_completed_sequence_three_remains_valid_on_later_catalogue_scan(retained,monkeypatch):
    t=retained;retain_proposal(t);p,digest,result=completed_new(t,monkeypatch)
    for _ in range(2):
        with r.Snapshot(t.root,'unused') as view:
            records,descendants=live.catalogue(view,[t.ref],evidence=p['retirement_evidence'])
            assert len(records)==len(descendants)==1
            assert descendants[0]['sequence']==3 and descendants[0]['predecessor']==t.previous
            assert descendants[0]['terminal_sha256']==result['terminal_sha256']
            view.check()
    later=new_policy(t);later['predecessor']={k:result[k] for k in ('sequence','plan_sha256','terminal_sha256')}
    live.history_gate(later,r.sha(r.encoded(later)))


@pytest.mark.parametrize('fault',['missing-phase','altered-terminal','unapproved-reference'])
def test_descendant_completion_cannot_be_asserted_by_caller(retained,monkeypatch,fault):
    t=retained;retain_proposal(t);p,digest,result=completed_new(t,monkeypatch)
    if fault=='missing-phase':(t.root/'cycles'/result['plan_sha256']/'execute-done.json').unlink()
    elif fault=='altered-terminal':store(t.root,result['plan_sha256']+'/terminal.json',{})
    else:p['retirement_evidence'][t.ref['policy_sha256']]['baseline.json']='0'*64
    with pytest.raises((RuntimeError,OSError)):
        with r.Snapshot(t.root,'unused') as view:live.catalogue(view,[t.ref],evidence=p['retirement_evidence'])
