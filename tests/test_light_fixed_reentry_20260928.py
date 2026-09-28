from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import base64
import hashlib
import json
import os
import pytest
from ops.incident import light_fixed_reentry_20260928 as target
from ops.incident import light_fixed_reentry_runner_20260928 as runner

REPO=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('fault',[None,'changed','lost_guard'])
def test_archive_preserves_bytes_and_inode(tmp_path,fault):
    source=tmp_path/'original';source.mkdir();(source/'proof').write_bytes(b'original evidence')
    expected=target.inventory(source);destination=tmp_path/'archive'
    if fault=='changed':(source/'proof').write_bytes(b'changed')
    guard=Mock(side_effect=RuntimeError('lost guard')) if fault=='lost_guard' else Mock()
    if fault:
        with pytest.raises(RuntimeError):target.move_preserved(source,destination,expected,guard)
        assert source.exists() and not destination.exists()
    else:
        target.move_preserved(source,destination,expected,guard)
        assert not source.exists() and target.inventory(destination)==expected


def test_archive_refuses_links(tmp_path):
    source=tmp_path/'original';source.mkdir();(source/'link').symlink_to('/etc/passwd')
    with pytest.raises(RuntimeError,match='REENTRY_INVENTORY_TYPE'):target.inventory(source)


@pytest.mark.skipif(os.geteuid()!=0,reason='root metadata')
@pytest.mark.parametrize('fail_move',[None,2,3])
def test_reentry_namespace_faults_preserve_all_evidence(tmp_path,monkeypatch,fail_move):
    c=target.control;s=target.switch;h=target.hold
    root=tmp_path/'ledger';root.mkdir(mode=0o700);intake=root/'intake';intake.mkdir(mode=0o700)
    for name in target.COPIED:
        (intake/name).write_bytes(('historical '+name).encode());(intake/name).chmod(0o600)
    old_directory=root/target.OLD_REQUEST;old_directory.mkdir(mode=0o700)
    old_clean=dict(audit='LIGHT_ZERO_SUBMIT_CONTROLS_RESTORED',request_sha256=target.OLD_REQUEST,
        intake_sha256=target.INTAKE,snapshot_sha256=target.SNAPSHOT,native_enabled=False,can_repair=True,
        task_preserved=True,outbox_preserved=True,native_receipts=0,pilot_resubmitted=False)
    (old_directory/'zero-submit-controls-restored.json').write_bytes(c.canonical(old_clean));(old_directory/'zero-submit-controls-restored.json').chmod(0o600)
    # Valid JSON publication evidence is copied byte-for-byte by the reentry.
    for name in ('broker.json','discovery.json'):
        (intake/name).write_bytes(b'{}')
    ctl=tmp_path/'control';ctl.mkdir(mode=0o750)
    values={'permit.json':b'old permit','accepted-sha256':b'a'*64+b'\n','admission':b'HOLD\n'}
    for name,value in values.items():(ctl/name).write_bytes(value);(ctl/name).chmod(0o640)
    claim=tmp_path/'claim';claim.mkdir(mode=0o700)
    monkeypatch.setattr(c.plan,'ROOT',root);monkeypatch.setattr(c.plan,'LIGHT',tmp_path/'light')
    monkeypatch.setattr(c,'CLAIM',claim);monkeypatch.setattr(s,'CONTROL',ctl)
    monkeypatch.setattr(target.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=0,pw_gid=0))
    prior=h.HoldIdentity('autopilot-lite-vnic',123,'a'*32,'/prior','b'*64)
    old_request=SimpleNamespace(permit=b'old permit',value={'permit_sha256':'a'*64})
    monkeypatch.setattr(c,'ledger',lambda digest:(old_request,prior,{},'d'*64,old_directory))
    monkeypatch.setattr(c,'restored_receipt',lambda *a:{'restored':True})
    monkeypatch.setattr(c,'stage_observation',lambda *a:prior)
    monkeypatch.setattr(s,'protect_snapshot',lambda *a:{})
    monkeypatch.setattr(target,'baseline_guard',lambda *a,**kw:prior)
    monkeypatch.setattr(target,'observe',lambda *a:{})
    monkeypatch.setattr(target,'records',lambda *a:(intake,{},SimpleNamespace(),{'native_config':{},'autopilot_role':{}}))
    script=str(old_directory/'supervisor.py')
    supervisor=dict(LoadState='loaded',ActiveState='failed',SubState='failed',MainPID='0',ControlPID='0',
        InvocationID=target.OLD_INVOCATION,Restart='no')
    for key,action in [('ExecStart','run'),('ExecStopPost','restore')]:
        supervisor[key]='{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -I -S -B '+script+' '+action+' '+target.OLD_REQUEST+' ; ignore_errors=no ; status=2 }'
    monkeypatch.setattr(s,'show',lambda unit,fields:supervisor if 'ExecStart' in fields else {'LoadState':'not-found','ActiveState':'inactive','MainPID':'0'})
    command=Mock();monkeypatch.setattr(s,'command',command);monkeypatch.setattr(s,'unit_absent',lambda *a:None)
    owner=SimpleNamespace(observed_target=lambda *a:None,discovery=lambda *a:{})
    agreement=SimpleNamespace(accepted='c'*64);payload={'agreement':{'evidence':'test'}}
    original=target.move_preserved;calls=[]
    def move(*args):
        calls.append(args)
        if len(calls)==fail_move:raise RuntimeError('injected rename boundary')
        return original(*args)
    monkeypatch.setattr(target,'move_preserved',move)
    sources=[root,ctl,claim];expected=[target.inventory(p) for p in sources]
    if fail_move:
        with pytest.raises(RuntimeError,match='injected'):
            target.prepare_continuation({},payload,agreement,None,None,None,None,None,None,owner)
        command.assert_not_called()
    else:
        result=target.prepare_continuation({},payload,agreement,None,None,None,None,None,None,owner)
        assert result['db_writes'] is False and result['pilot_submitted'] is False
        command.assert_called_once_with('/usr/bin/systemctl','reset-failed',c.plan.SUPERVISOR_UNIT)
        assert (root/'continuation-ready.json').exists()
    for source,proof in zip(sources,expected):
        archive=source.with_name(source.name+target.ARCHIVE_SUFFIX)
        assert target.inventory(archive if archive.exists() else source)==proof


@pytest.mark.parametrize('action',['prepare-continuation','authorize','terminal','restore-controls','restore-zero-submit','inspect-zero-submit','restore-unreserved'])
def test_runner_bootstrap_selects_exact_old_or_new_namespace(monkeypatch,action):
    payload=json.dumps({'action':action}).encode()
    selected=runner.PACKAGE if action=='prepare-continuation' else target.PACKAGE
    values=dict(EXPECTED_MAIN=target.SOURCE,GITHUB_SHA=target.SOURCE,GITHUB_WORKFLOW_SHA=target.SOURCE,
        GITHUB_WORKFLOW_REF=runner.REPO+'/'+runner.WORKFLOW+'@refs/heads/'+runner.BRANCH,GITHUB_JOB='step',
        GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1',GITHUB_REF='refs/heads/'+runner.BRANCH,
        GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_ACTOR='olegmed1-art',GITHUB_TRIGGERING_ACTOR='olegmed1-art',
        GITHUB_REPOSITORY=runner.REPO,PILOT_ACCEPTED_PACKAGE=selected,
        PILOT_ACCEPTED_PAYLOAD=hashlib.sha256(payload).hexdigest(),NATIVE_OWNER_DATABASE_URL='test',GH_TOKEN='test',
        PILOT_PAYLOAD_BASE64=base64.b64encode(payload).decode())
    for k,v in values.items():monkeypatch.setenv(k,v)
    monkeypatch.setattr(runner,'connection_parameters',lambda *a:None);monkeypatch.setattr(runner.driver,'build',lambda *a:b'wheels')
    original=runner.subprocess.check_output
    def check(args,**kw):
        if args[:2]==['git','show']:return (REPO/args[2].split(':',1)[1]).read_bytes()
        return original(args,**kw)
    monkeypatch.setattr(runner.subprocess,'check_output',check)
    program=runner.program('/unused');compile(program,'<reentry-bootstrap>','exec')
    assert "assert hashlib.sha256(raw).hexdigest()=='"+selected+"'" in program


@pytest.mark.skipif(os.geteuid()!=0,reason='root retention')
@pytest.mark.parametrize('fault',[None,'acl','lost_commit_ack','apply_drift','preflight'])
def test_authorize_transaction_and_unknown_ack(tmp_path,monkeypatch,fault):
    import copy
    from contextlib import contextmanager
    from oracle_autopilot import light_native_preflight
    c=target.control;root=tmp_path/'root';root.mkdir(mode=0o700);intake_root=root/'intake';intake_root.mkdir(mode=0o700)
    monkeypatch.setattr(c.plan,'ROOT',root)
    for name in ('broker.json','discovery.json'):
        (intake_root/name).write_bytes(b'{}');(intake_root/name).chmod(0o600)
    stage=tmp_path/'stage';(stage/target.SOURCE).mkdir(parents=True)
    (stage/target.SOURCE/'environment.json').write_bytes(b'{}');(stage/target.SOURCE/'environment.json').chmod(0o600)
    monkeypatch.setattr(target.release,'ROOT',stage)
    initial=dict(config={'enabled':False,'cutover_at':None,'unchanged':'yes'},role={'can_repair':True,'updated_at':'before'},
        task={'unchanged':'task'},outbox={'unchanged':'outbox','published_at':'2026-09-28T10:36:36+00:00','delivery_deadline_at':'2099-01-01T00:00:00+00:00'},work={'unchanged':'work'},counts=[0,1,1,0,1])
    state=copy.deepcopy(initial);writes=[];acl_calls=[]
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        @contextmanager
        def transaction(self):
            old=copy.deepcopy(state)
            try:yield
            except BaseException:
                state.clear();state.update(old);raise
            if fault=='lost_commit_ack':raise RuntimeError('lost commit acknowledgement')
        def execute(self,sql,params=None):
            if sql.startswith('UPDATE autopilot.native_cli_config'):
                writes.append('config');state['config'].update(enabled=True,cutover_at=params[0])
                if fault=='apply_drift':state['config']['unchanged']='changed'
            if sql.startswith('UPDATE autopilot.role_registry'):
                writes.append('role');state['role'].update(can_repair=False,updated_at='after')
            return SimpleNamespace(rowcount=1)
    psycopg=SimpleNamespace(connect=lambda **kw:Conn())
    def privileges(*a):
        acl_calls.append(True)
        if fault=='acl' and len(acl_calls)==2:raise RuntimeError('ACL drift')
    engine=SimpleNamespace(identity=lambda *a:None,privileges=privileges)
    intake=SimpleNamespace(engine=engine,target=lambda:None)
    receipt=dict(applied_config=dict(initial['config'],enabled=True,cutover_at='2026-09-28T10:30:00+00:00'),goal_json_sha256='goal',dispatch=dict(dispatch_id=target.DISPATCH,expected_head_sha='a'*40,
        mode='READ_ONLY',target_pr=2025,task_fingerprint='f'*64),assignment={'preserved':True})
    plan=SimpleNamespace(value={'branch':'fix/audit'})
    before={'native_config':initial['config'],'autopilot_role':initial['role']}
    monkeypatch.setattr(target,'records',lambda *a:(intake_root,receipt,plan,before))
    monkeypatch.setattr(target,'ready',lambda *a:{})
    monkeypatch.setattr(target,'baseline_guard',lambda *a:None)
    def observe(*a):
        target.require(state['config']==initial['config'],'already applied')
        return copy.deepcopy(state)
    monkeypatch.setattr(target,'observe',observe)
    monkeypatch.setattr(target,'db_rows',lambda *a:copy.deepcopy(state))
    def preflight(*a,**kw):
        if fault=='preflight':raise RuntimeError('preflight refusal')
        return {'goal_json_sha256':'goal'}
    monkeypatch.setattr(light_native_preflight,'observe',preflight)
    owner=SimpleNamespace(observed_target=lambda *a:None,discovery=lambda *a:{})
    agreement=SimpleNamespace(accepted='d'*64,end=target.time.time()+600)
    payload={'baseline_sha256':'b'*64}
    args=(payload,b'accepted payload',agreement,None,psycopg,lambda _: {},'credential',intake,None,owner)
    if fault:
        with pytest.raises(RuntimeError):target.authorize(*args)
        assert not (intake_root/'permit.json').exists()
        assert not (root/'reapply-committed.json').exists()
        assert (root/'reapply-intent.json').exists()
        if fault=='preflight':
            assert state['config']['enabled'] is True
            assert (root/'reapply-database-committed.json').exists()
        elif fault=='lost_commit_ack':
            assert state['config']['enabled'] is True
            assert (root/'reapply-applied.json').exists()
            with pytest.raises(RuntimeError):target.authorize(*args)
            assert writes==['config','role']
        else:
            assert state==initial
            if fault=='acl':assert writes==[]
    else:
        result=target.authorize(*args)
        assert writes==['config','role'] and len(acl_calls)==2
        assert state['config']['enabled'] is True and state['role']['can_repair'] is False
        assert (intake_root/'permit.json').exists() and (root/'reapply-committed.json').exists()
        assert result['pilot_submitted'] is False


@pytest.mark.skipif(os.geteuid()!=0,reason='root retention')
@pytest.mark.parametrize('fault',[None,'db_drift','claim','readback'])
def test_zero_submit_fallback_preserves_task_and_restores_controls(tmp_path,monkeypatch,fault):
    import copy
    from contextlib import contextmanager
    from dataclasses import asdict
    c=target.control;s=target.switch;h=target.hold
    root=tmp_path/'root';root.mkdir(mode=0o700);(root/'intake').mkdir(mode=0o700)
    monkeypatch.setattr(c.plan,'ROOT',root);monkeypatch.setattr(c.plan,'LIGHT',tmp_path/'light')
    monkeypatch.setattr(c,'CLAIM',tmp_path/'claim');monkeypatch.setattr(s,'CONTROL',tmp_path/'control')
    if fault=='claim':c.CLAIM.mkdir();(c.CLAIM/'unexpected').write_text('preserve')
    prior=h.HoldIdentity('autopilot-lite-vnic',123,'a'*32,'/prior','b'*64)
    before=dict(config={'enabled':False,'cutover_at':None},role={'can_repair':True,'updated_at':'old'},
        task={'id':'same'},outbox={'id':'same'},work={'id':'same'},counts=[0,1,1,0,1])
    applied=copy.deepcopy(before);applied['config'].update(enabled=True,cutover_at='temporary');applied['role'].update(can_repair=False,updated_at='new')
    baseline=dict(source=target.SOURCE,package_sha256=target.PACKAGE,scope_sha256=c.digest(c.canonical(target.SCOPE)),prior=asdict(prior),protected={},protected_sha256='b'*64)
    braw=c.canonical(baseline);bsha=c.digest(braw)
    for name,data in [('baseline',baseline),('reapply-before',before),('reapply-applied',applied),('reapply-database-committed',dict(baseline_sha256=bsha,before_sha256=c.digest(c.canonical(before)),applied_sha256=c.digest(c.canonical(applied))))]:
        c.retained(root/(name+'.json'),c.canonical(data))
    # No permit/final authorization receipt is needed after a postcommit preflight failure.
    state=copy.deepcopy(applied);writes=[];connections=[]
    if fault=='db_drift':state['task']['id']='changed'
    class Conn:
        def __enter__(self):connections.append(self);return self
        def __exit__(self,*a):pass
        @contextmanager
        def transaction(self):
            old=copy.deepcopy(state)
            try:yield
            except BaseException:state.clear();state.update(old);raise
        def execute(self,sql,params=None):
            if sql.startswith('UPDATE autopilot.native_cli_config'):
                writes.append('config');state['config'].update(enabled=params[0],cutover_at=params[1])
            if sql.startswith('UPDATE autopilot.role_registry'):
                writes.append('role');state['role'].update(can_repair=params[0])
            return SimpleNamespace(rowcount=1)
    def rows(*a):
        result=copy.deepcopy(state)
        if fault=='readback' and len(connections)>1:result['task']['id']='unexpected'
        return result
    monkeypatch.setattr(target,'db_rows',rows)
    def zero(rows,receipt,config,role):
        assert rows['config']==config
        assert rows['role']['can_repair']==role['can_repair']
        assert all(rows[k]==before[k] for k in ('task','outbox','work','counts'))
    monkeypatch.setattr(target,'check_zero',zero)
    monkeypatch.setattr(target,'records',lambda *a:(root/'intake',{},None,dict(native_config=before['config'],autopilot_role=before['role'])))
    monkeypatch.setattr(h,'service_hold_identity',lambda:prior)
    for name in ('unchanged_files','no_processes','attest_hardening'):monkeypatch.setattr(s,name,lambda *a:None)
    monkeypatch.setattr(s,'show',lambda *a:dict(MainPID='0',ControlPID='0',ActiveState='inactive'))
    intake=SimpleNamespace(engine=SimpleNamespace(identity=lambda *a:None),target=lambda:None)
    args=({'baseline_sha256':bsha,'request_sha256':''},b'accepted',SimpleNamespace(assert_running=lambda:None),SimpleNamespace(connect=lambda **kw:Conn()),lambda _: {},'credential',intake)
    if fault:
        with pytest.raises(RuntimeError):target.restore_zero_submit(*args)
        assert not (root/'zero-submit-controls-restored.json').exists()
        if fault in ('db_drift','claim'):assert writes==[]
    else:
        result=target.restore_zero_submit(*args)
        assert result['controls_restored'] and result['native_receipts']==0
        assert writes==['config','role'] and state['config']==before['config']
        assert state['role']['can_repair'] is True
    assert state['outbox']==before['outbox'] and state['work']==before['work']


@pytest.mark.skipif(os.geteuid()!=0,reason='root metadata')
@pytest.mark.parametrize('fault',[None,'request','wrong_invocation','changed_inode','wrong_permit'])
def test_unreserved_claim_is_preserved_and_exact(tmp_path,monkeypatch,fault):
    c=target.control;claim=tmp_path/'claim';claim.mkdir(mode=0o700)
    directory=tmp_path/'request';directory.mkdir(mode=0o700)
    monkeypatch.setattr(c,'CLAIM',claim)
    monkeypatch.setattr(target.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=0,pw_gid=0))
    permit=c.canonical(dict(issued_at=100,expires_at=200))
    start=dict(version=1,source=target.SOURCE,permit_sha256=c.digest(permit),pid=123,
        invocation_id='a'*32,image_id='b'*64,deadline=1000,last_wall=150)
    for name,raw in [('pilot.lock',b''),('permit.json',permit),('image-start.json',c.canonical(start))]:
        (claim/name).write_bytes(raw);(claim/name).chmod(0o600)
    unit=dict(MainPID='123',InvocationID='a'*32)
    if fault=='wrong_invocation':unit['InvocationID']='c'*32
    c.retained(directory/'pilot-unit.json',c.canonical(unit))
    proof=target.inventory(claim);accepted=c.digest(c.canonical(proof))
    if fault=='request':(claim/'request.json').write_bytes(b'{}')
    if fault=='changed_inode':
        replacement=tmp_path/'replacement';replacement.write_bytes(b'');replacement.chmod(0o600);replacement.replace(claim/'pilot.lock')
    if fault=='wrong_permit':permit=b'{}'
    request=SimpleNamespace(permit=permit)
    if fault:
        with pytest.raises(RuntimeError):target.verify_unreserved_claim(request,directory,accepted)
    else:
        target.verify_unreserved_claim(request,directory,accepted)
        assert target.inventory(claim)==proof


@pytest.mark.parametrize('fault',[None,'late_cutover','expired','short_deadline','config_drift'])
def test_continuation_preflight_enforces_actual_reserve_dates(monkeypatch,fault):
    from datetime import datetime
    now=datetime.fromisoformat('2026-09-28T13:41:57+00:00').timestamp()
    monkeypatch.setattr(target.time,'time',lambda:now)
    original={'enabled':True,'cutover_at':'2026-09-28T10:30:00+00:00','singleton':True}
    rows={'config':{'enabled':False,'cutover_at':'2026-09-17T08:06:55+00:00','singleton':True},
        'outbox':{'published_at':'2026-09-28T10:36:36+00:00','delivery_deadline_at':'2026-09-28T14:30:00+00:00'}}
    if fault=='late_cutover':original['cutover_at']='2026-09-28T13:41:57+00:00'
    if fault=='expired':rows['outbox']['delivery_deadline_at']='2026-09-28T11:06:36+00:00'
    if fault=='short_deadline':rows['outbox']['delivery_deadline_at']='2026-09-28T13:42:00+00:00'
    if fault=='config_drift':original['singleton']=False
    if fault:
        with pytest.raises(RuntimeError):target.continuation_admission_config({'applied_config':original},rows,now+1200)
    else:
        result=target.continuation_admission_config({'applied_config':original},rows,now+1200)
        assert result==original  # Preserve the intake boundary rather than assigning now.
