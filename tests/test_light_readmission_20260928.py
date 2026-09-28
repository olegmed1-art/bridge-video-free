from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import base64
import hashlib
import json
import os
import pytest
from ops.incident import light_readmission_20260928 as target
from ops.incident import light_readmission_runner_20260928 as runner

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


@pytest.mark.parametrize('action',['prepare-continuation','authorize','terminal','restore-controls','restore-zero-submit','inspect-zero-submit','restore-unreserved','inspect-provider','restore-submitted','recover-log-terminal'])
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
@pytest.mark.parametrize('fault',[None,'acl','lost_commit_ack','apply_drift','preflight','send_claim_false'])
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
        task={'unchanged':'task'},outbox={'unchanged':'outbox','published_at':'2026-09-28T10:36:36+00:00','delivery_deadline_at':'2026-09-28T11:06:36+00:00','updated_at':'old','codex_command_comment_id':None,'codex_ack_at':None,'sent_at':None,'delivered_at':None},work={'unchanged':'work'},counts=[0,1,1,0,1])
    state=copy.deepcopy(initial);writes=[];acl_calls=[];fence=[]
    from oracle_autopilot.github_codex_callback import COMMAND_FIELDS
    binding={k:'fixed' for k in COMMAND_FIELDS}
    monkeypatch.setattr(target,'UNSENT_FIELDS_SHA',c.digest(c.canonical(binding)))
    monkeypatch.setattr(target,'function_guards',lambda conn:None)
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        @contextmanager
        def transaction(self):
            old=copy.deepcopy(state);old_fence=copy.deepcopy(fence)
            try:yield
            except BaseException:
                state.clear();state.update(old);fence[:]=old_fence;raise
            if fault=='lost_commit_ack':raise RuntimeError('lost commit acknowledgement')
        def execute(self,sql,params=None):
            if sql.startswith('UPDATE autopilot.native_cli_config'):
                writes.append('config');state['config'].update(enabled=True,cutover_at=params[0])
                if fault=='apply_drift':state['config']['unchanged']='changed'
            if sql.startswith('UPDATE autopilot.role_registry'):
                writes.append('role');state['role'].update(can_repair=False,updated_at='after')
            if sql.startswith('UPDATE autopilot.role_dispatch_outbox'):
                writes.append('deadline');state['outbox'].update(delivery_deadline_at=params[0],updated_at='after')
            if 'claim_codex_command_send(' in sql:
                if fault=='send_claim_false':return SimpleNamespace(fetchone=lambda:(False,))
                fence.append(dict(dispatch_id=params[0],attempt_id=params[1],binding=json.loads(params[2]),command_sha256=params[3]))
                return SimpleNamespace(fetchone=lambda:(True,))
            if 'native_cli_authority_locked(' in sql:return SimpleNamespace(fetchone=lambda:(True,))
            if sql.startswith('SELECT count(*) FROM autopilot.codex_command_send_intent'):return SimpleNamespace(fetchone=lambda:(len(fence),))
            return SimpleNamespace(rowcount=1)
    psycopg=SimpleNamespace(connect=lambda **kw:Conn())
    def privileges(*a):
        acl_calls.append(True)
        if fault=='acl' and len(acl_calls)==2:raise RuntimeError('ACL drift')
    engine=SimpleNamespace(identity=lambda *a:None,privileges=privileges)
    def one(conn,sql,params):
        if 'codex_command_send_binding(' in sql:return copy.deepcopy(binding)
        if 'codex_command_send_intent' in sql:return copy.deepcopy(fence[0])
        raise AssertionError(sql)
    intake=SimpleNamespace(engine=engine,target=lambda:None,one=one)
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
    from datetime import datetime,timezone
    end=target.time.time()+1700
    agreement=SimpleNamespace(accepted='d'*64,end=int(end),record={'expires_at':datetime.fromtimestamp(int(end),timezone.utc).isoformat()})
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
            assert writes==['config','role','deadline']
        else:
            assert state==initial
            assert fence==[]
            if fault=='acl':assert writes==[]
    else:
        result=target.authorize(*args)
        assert writes==['config','role','deadline'] and len(acl_calls)==2 and len(fence)==1
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
        task={'id':'same'},outbox={'id':'same','delivery_deadline_at':'2026-09-28T11:06:36+00:00'},work={'id':'same'},counts=[0,1,1,0,1])
    applied=copy.deepcopy(before);applied['config'].update(enabled=True,cutover_at='temporary');applied['role'].update(can_repair=False,updated_at='new')
    applied['outbox']['delivery_deadline_at']='2026-09-28T14:30:00+00:00'
    fence={'retained':'no POST'}
    c.retained(root/'legacy-send-fence.json',c.canonical(fence))
    baseline=dict(source=target.SOURCE,package_sha256=target.PACKAGE,scope_sha256=c.digest(c.canonical(target.SCOPE)),prior=asdict(prior),protected={},protected_sha256='b'*64)
    braw=c.canonical(baseline);bsha=c.digest(braw)
    for name,data in [('baseline',baseline),('reapply-before',before),('reapply-applied',applied),('reapply-database-committed',dict(baseline_sha256=bsha,before_sha256=c.digest(c.canonical(before)),applied_sha256=c.digest(c.canonical(applied)),fence_sha256=c.digest(c.canonical(fence))))]:
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
            if sql.startswith('UPDATE autopilot.role_dispatch_outbox'):
                writes.append('deadline');state['outbox']['delivery_deadline_at']=params[0]
            return SimpleNamespace(rowcount=1)
    def rows(*a):
        result=copy.deepcopy(state)
        if fault=='readback' and len(connections)>1:result['task']['id']='unexpected'
        return result
    monkeypatch.setattr(target,'db_rows',rows)
    def zero(rows,receipt,config,role):
        assert rows['config']==config
        assert rows['role']['can_repair']==role['can_repair']
        assert all(rows[k]==before[k] for k in ('task','work','counts'))
    monkeypatch.setattr(target,'check_zero',zero)
    monkeypatch.setattr(target,'records',lambda *a:(root/'intake',{},None,dict(native_config=before['config'],autopilot_role=before['role'])))
    monkeypatch.setattr(h,'service_hold_identity',lambda:prior)
    for name in ('unchanged_files','no_processes','attest_hardening'):monkeypatch.setattr(s,name,lambda *a:None)
    monkeypatch.setattr(s,'show',lambda *a:dict(MainPID='0',ControlPID='0',ActiveState='inactive'))
    intake=SimpleNamespace(engine=SimpleNamespace(identity=lambda *a:None),target=lambda:None,one=lambda *a:fence)
    args=({'baseline_sha256':bsha,'request_sha256':''},b'accepted',SimpleNamespace(assert_running=lambda:None),SimpleNamespace(connect=lambda **kw:Conn()),lambda _: {},'credential',intake)
    if fault:
        with pytest.raises(RuntimeError):target.restore_zero_submit(*args)
        assert not (root/'zero-submit-controls-restored.json').exists()
        if fault in ('db_drift','claim'):assert writes==[]
    else:
        result=target.restore_zero_submit(*args)
        assert result['controls_restored'] and result['native_receipts']==0
        assert writes==['config','role','deadline'] and state['config']==before['config']
        assert state['role']['can_repair'] is True
    assert state['outbox']['id']==before['outbox']['id'] and state['work']==before['work']


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


@pytest.mark.skipif(os.geteuid()!=0,reason='root metadata')
@pytest.mark.parametrize('fail_move',[None,2,4,5,6])
def test_reentry_namespace_faults_preserve_all_evidence(tmp_path,monkeypatch,fail_move):
    from dataclasses import asdict
    c=target.control;s=target.switch;h=target.hold
    root=tmp_path/'ledger';root.mkdir(mode=0o700);intake=root/'intake';intake.mkdir(mode=0o700)
    for name in target.COPIED:
        (intake/name).write_bytes(('historical '+name).encode());(intake/name).chmod(0o600)
    old_directory=root/target.OLD_REQUEST;old_directory.mkdir(mode=0o700)
    old_clean=dict(audit='LIGHT_FIXED_UNRESERVED_CONTROLS_RESTORED',controls_restored=True,native_enabled=False,can_repair=True,native_receipts=0,task_preserved=True,pilot_resubmitted=False)
    c.retained(root/'zero-submit-controls-restored.json',c.canonical(old_clean))
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
    monkeypatch.setattr(c,'restored_receipt',lambda *a:{'restored':asdict(prior)})
    monkeypatch.setattr(c,'stage_observation',lambda *a:prior)
    monkeypatch.setattr(h,'service_hold_identity',lambda:h.ServiceHoldIdentity(**asdict(prior)))
    monkeypatch.setattr(target,'verify_unreserved_claim',lambda *a:None)
    stage_root=tmp_path/'stages';stage_root.mkdir(mode=0o700);stage=stage_root/target.SOURCE;stage.mkdir(mode=0o700)
    (stage/'old-proof').write_bytes(b'preserve previous stage');(stage/'old-proof').chmod(0o600)
    monkeypatch.setattr(target.release,'ROOT',stage_root)
    monkeypatch.setattr(target.release,'probe',lambda *a:None)
    monkeypatch.setattr(target.release,'probe_environment',lambda *a:{'fresh':'environment'})
    for name in ('BASE','DROP'):
        path=tmp_path/name;path.write_bytes(b'original unit configuration');path.chmod(0o644);monkeypatch.setattr(h,name,path)
    def protect(value):
        assert type(value) is h.HoldIdentity
        assert asdict(value)==asdict(h.service_hold_identity())
        return {}
    monkeypatch.setattr(s,'protect_snapshot',protect)
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
    sources=[root,ctl,claim,stage];expected=[target.inventory(p) for p in sources]
    if fail_move:
        with pytest.raises(RuntimeError,match='injected'):
            target.prepare_continuation({'runtime':{'sha256':'b'*64}},payload,agreement,None,None,None,None,None,None,owner)
        command.assert_not_called()
    else:
        result=target.prepare_continuation({'runtime':{'sha256':'b'*64}},payload,agreement,None,None,None,None,None,None,owner)
        assert result['db_writes'] is False and result['pilot_submitted'] is False
        command.assert_not_called()
        assert (root/'continuation-ready.json').exists()
    for source,proof in zip(sources,expected):
        archive=source.with_name(source.name+target.ARCHIVE_SUFFIX)
        assert target.inventory(archive if archive.exists() else source)==proof




@pytest.mark.skipif(os.geteuid()!=0,reason='root retention')
@pytest.mark.parametrize('fault',[None,'deadline_drift','fence_drift','terminal_invalid','postcommit_drift'])
def test_terminal_cleanup_restores_deadline_preserves_terminal_and_fence(tmp_path,monkeypatch,fault):
    import copy
    from contextlib import contextmanager
    c=target.control;root=tmp_path/'root';root.mkdir(mode=0o700)
    monkeypatch.setattr(c.plan,'ROOT',root)
    before=dict(config={'enabled':False,'cutover_at':'original'},role={'can_repair':True,'updated_at':'old'},outbox={'delivery_deadline_at':'original'})
    applied=dict(config={'enabled':True,'cutover_at':'accepted'},role={'can_repair':False,'updated_at':'new'},outbox={'delivery_deadline_at':'extended'})
    fence={'consumed':True,'post_attempted':False}
    for name,value in [('reapply-before',before),('reapply-applied',applied),('legacy-send-fence',fence),('terminal',{'valid':True}),('reapply-committed',dict(before_sha256=c.digest(c.canonical(before)),applied_sha256=c.digest(c.canonical(applied)),fence_sha256=c.digest(c.canonical(fence))))]:
        c.retained(root/(name+'.json'),c.canonical(value))
    request=SimpleNamespace(value={'source':target.SOURCE,'scope':target.SCOPE})
    monkeypatch.setattr(c,'ledger',lambda *a:(request,None,None,None,None))
    monkeypatch.setattr(c,'restored_receipt',lambda *a:{'restored':True})
    monkeypatch.setattr(target,'records',lambda *a:(root,{},None,None))
    state=copy.deepcopy(applied);state['outbox'].update(status='TERMINAL',ack='actual',updated_at='terminal')
    if fault=='deadline_drift':state['outbox']['delivery_deadline_at']='changed'
    initial=copy.deepcopy(state);connections=[];writes=[]
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
                writes.append('role');state['role']['can_repair']=params[0]
            if sql.startswith('UPDATE autopilot.role_dispatch_outbox'):
                writes.append('deadline');state['outbox'].update(delivery_deadline_at=params[0],updated_at='restored')
            return SimpleNamespace(rowcount=1)
    def one(conn,sql,params=None):
        if 'codex_command_send_intent' in sql:return {'changed':True} if fault=='fence_drift' else copy.deepcopy(fence)
        key='config' if 'native_cli_config' in sql else 'role' if 'role_registry' in sql else 'outbox'
        value=copy.deepcopy(state[key])
        if fault=='postcommit_drift' and len(connections)>1 and key=='outbox':value['ack']='changed'
        return value
    def terminal(*a):
        if fault=='terminal_invalid':raise RuntimeError('invalid terminal')
        assert state['outbox']['ack']=='actual'
        return True
    intake=SimpleNamespace(engine=SimpleNamespace(identity=lambda *a:None),target=lambda:None,one=one,terminal_evidence=lambda *a:{},_terminal_rows=terminal,observe_terminal=terminal)
    args=({'request_sha256':'accepted','terminal_sha256':'accepted'},b'accepted',SimpleNamespace(assert_running=lambda:None),SimpleNamespace(connect=lambda **kw:Conn()),lambda _: {},'credential',intake)
    if fault:
        with pytest.raises(RuntimeError):target.restore_controls(*args)
        assert not (root/'controls-restored.json').exists()
        if fault!='postcommit_drift':assert state==initial and writes==[]
    else:
        assert target.restore_controls(*args)['controls_restored']
        assert writes==['config','role','deadline']
        assert state['config']==before['config'] and state['role']['can_repair'] is True
        assert state['outbox']==dict(initial['outbox'],delivery_deadline_at='original',updated_at='restored')
    assert c.strict_json(c.read(root/'legacy-send-fence.json'),65536)==fence


@pytest.mark.parametrize('marker,state',[('[PENDING]','WAITING_PROVIDER'),('[APPLIED]','PROVIDER_STATUS_UNKNOWN'),('[READY]','RESULT_RETRIEVED')])
def test_provider_inspection_executes_only_bound_reads(monkeypatch,capsys,marker,state):
    import io,sys
    from oracle_autopilot import codex_cli_bridge as bridge
    expected={'request':{'dispatch_id':target.DISPATCH},'provider_task_id':'task_e_1234567890abcdef'}
    monkeypatch.setattr(sys,'argv',['probe',str(REPO),target.DISPATCH,'environment'])
    monkeypatch.setattr(sys,'stdin',SimpleNamespace(buffer=io.BytesIO(json.dumps(expected).encode())))
    monkeypatch.setattr(bridge,'prompt_for',lambda request:'prompt')
    monkeypatch.setattr(bridge,'lookup',lambda *a,**kw:dict(state='SUBMITTED',provider_task_id='task_e_1234567890abcdef',prompt_sha256=bridge.digest('prompt')))
    calls=[]
    def run(args,**kw):
        calls.append(args)
        return SimpleNamespace(returncode=0 if marker=='[READY]' else 1,stdout=marker+' safe title\nprivate body',stderr='private error')
    monkeypatch.setattr(bridge,'run_cli',run)
    def collect(dispatch_id,**kw):
        kw['runner'](['cloud','status','task_e_1234567890abcdef'])
        if marker=='[READY]':kw['runner'](['cloud','diff','task_e_1234567890abcdef','--attempt','1'])
        return {'state':state,'provider_task_id':'task_e_1234567890abcdef'}
    monkeypatch.setattr(bridge,'_collect',collect)
    exec(compile(target.INSPECT_PROVIDER_PROGRAM,'<provider-inspection>','exec'),{})
    raw=capsys.readouterr().out;value=json.loads(raw)
    assert value['state']==state and value['cli'][0]['marker']==marker
    assert 'private body' not in raw and 'private error' not in raw
    assert calls==[['cloud','status','task_e_1234567890abcdef']]+([['cloud','diff','task_e_1234567890abcdef','--attempt','1']] if marker=='[READY]' else [])


@pytest.mark.skipif(os.geteuid()!=0,reason='root retention')
@pytest.mark.parametrize('fault',[None,'snapshot','concurrent','update_drift','lost_commit_ack','readback'])
def test_submitted_cleanup_keeps_provider_and_task_evidence(tmp_path,monkeypatch,fault):
    import copy
    from contextlib import contextmanager
    from oracle_autopilot import codex_cli_bridge as bridge,light_native_restart
    c=target.control;root=tmp_path/'root';root.mkdir(mode=0o700);claim=tmp_path/'claim';claim.mkdir(mode=0o700)
    monkeypatch.setattr(c.plan,'ROOT',root);monkeypatch.setattr(c,'CLAIM',claim)
    monkeypatch.setattr(c.plan,'LIGHT',tmp_path/'light')
    dispatch={'dispatch_id':target.DISPATCH}
    request=SimpleNamespace(value=dict(source=target.SOURCE,scope=target.SCOPE,baseline_sha256='baseline'),permit_value={'dispatch':dispatch},permit=b'permit')
    monkeypatch.setattr(c,'ledger',lambda *a:(request,None,None,None,root))
    monkeypatch.setattr(c,'restored_receipt',lambda *a:{'restored':True})
    for name in ('image-start.json','image-restart.json','image-resumed.json'):(claim/name).write_bytes(b'{}')
    c.retained(root/'pilot-unit.json',b'{}')
    before=dict(config={'enabled':False,'cutover_at':'old'},role={'can_repair':True,'updated_at':'old'},outbox={'delivery_deadline_at':'old'})
    applied=dict(config={'enabled':True,'cutover_at':'accepted'},role={'can_repair':False,'updated_at':'applied'},outbox={'delivery_deadline_at':'window'})
    observed=dict(rows=dict(applied,task={'status':'WAITING_EXTERNAL'},work={'state':'ACTIVE'},counts=[1,1,1,0,1]),native=dict(state='SUBMITTED',terminal=None,completed_at=None,owner_name='autopilot_light_worker_login',submitted_at='submitted',submission_started_at='started',dispatch_id=target.DISPATCH,request=dict(dispatch,reservation_id='reservation'),provider_task_id='task_e_1234567890abcdef',prompt_sha256=bridge.digest('prompt')),fence={'consumed':True})
    observed['rows']=copy.deepcopy(observed['rows']);observed['rows']['outbox'].update(status='SENT',delivery_contract_version=4,provider='preserve')
    for name,value in [('reapply-before',before),('reapply-applied',applied),('reapply-committed',dict(before_sha256=c.digest(c.canonical(before)),applied_sha256=c.digest(c.canonical(applied)),baseline_sha256='baseline',fence_sha256=c.digest(c.canonical(observed['fence']))))]:c.retained(root/(name+'.json'),c.canonical(value))
    monkeypatch.setattr(target,'records',lambda *a:(root,{},None,dict(native_config=before['config'],autopilot_role=before['role'])))
    monkeypatch.setattr(target,'bounded_evidence_inventory',lambda *a:{'preserved':True})
    monkeypatch.setattr(target.release.staging,'require_current_main',lambda *a:None)
    monkeypatch.setattr(target.switch,'show',lambda *a:dict(MainPID='0',ControlPID='0',ActiveState='failed'))
    monkeypatch.setattr(bridge,'prompt_for',lambda *a:'prompt')
    monkeypatch.setattr(bridge,'lookup',lambda *a,**kw:dict(state='SUBMITTED',provider_task_id='task_e_1234567890abcdef',prompt_sha256=bridge.digest('prompt')))
    monkeypatch.setattr(light_native_restart,'validate_proof',lambda *a:{'verified':True})
    state=copy.deepcopy(observed);connections=[];writes=[];in_transaction=[False]
    class Conn:
        def __enter__(self):connections.append(self);return self
        def __exit__(self,*a):pass
        @contextmanager
        def transaction(self):
            old=copy.deepcopy(state);in_transaction[0]=True
            try:yield
            except BaseException:state.clear();state.update(old);raise
            finally:in_transaction[0]=False
            if fault=='lost_commit_ack':raise RuntimeError('unknown commit')
        def execute(self,sql,params=None):
            if sql.startswith('UPDATE autopilot.native_cli_config'):
                writes.append('config');state['rows']['config'].update(enabled=params[0],cutover_at=params[1])
            if sql.startswith('UPDATE autopilot.role_registry'):
                writes.append('role');state['rows']['role']['can_repair']=params[0]
            if sql.startswith('UPDATE autopilot.role_dispatch_outbox'):
                writes.append('deadline');state['rows']['outbox']['delivery_deadline_at']=params[0]
                if fault=='update_drift':state['native']['provider_task_id']='changed'
            return SimpleNamespace(rowcount=1)
    def snapshot(*a):
        result=copy.deepcopy(state)
        if (fault=='concurrent' and in_transaction[0]) or (fault=='readback' and len(connections)>2):result['native']['provider_task_id']='changed'
        return result
    monkeypatch.setattr(target,'submitted_snapshot',snapshot)
    intake=SimpleNamespace(engine=SimpleNamespace(identity=lambda *a:None),target=lambda:None)
    payload=dict(request_sha256='accepted',submitted_sha256='bad' if fault=='snapshot' else c.digest(c.canonical(observed)))
    args=(payload,b'accepted',SimpleNamespace(assert_running=lambda:None),SimpleNamespace(connect=lambda **kw:Conn()),lambda _: {},'credential',intake,SimpleNamespace(verify_restart_unit=lambda *a:None))
    if fault:
        with pytest.raises(RuntimeError):target.restore_submitted(*args)
        assert not (root/'submitted-controls-restored.json').exists()
        if fault not in ('lost_commit_ack','readback'):assert state==observed
        if fault in ('snapshot','concurrent'):assert writes==[]
    else:
        result=target.restore_submitted(*args)
        assert result['terminal_verified'] is False and result['normal_success_finish_requires_new_role_binding'] is True
        assert writes==['config','role','deadline']
        assert target.submitted_restoration_matches(observed,state,before)
    assert state['native']==observed['native'] and state['fence']==observed['fence']


RECOVERED_LOG = b'transport report valid\n--- FINAL HEAD ---\naa286f693bc903fd2b93c555d9da898652fbdb26\n--- STATUS ---\n A slavik_result_9289ad56-f0aa-4683-be0c-101ec820d412.json\n--- DIFF STAT ---\n slavik_result_9289ad56-f0aa-4683-be0c-101ec820d412.json | 1 +\n 1 file changed, 1 insertion(+)\n--- DIFF ---\ndiff --git a/slavik_result_9289ad56-f0aa-4683-be0c-101ec820d412.json b/slavik_result_9289ad56-f0aa-4683-be0c-101ec820d412.json\nnew file mode 100644\nindex 0000000..d4fb944\n--- /dev/null\n+++ b/slavik_result_9289ad56-f0aa-4683-be0c-101ec820d412.json\n@@ -0,0 +1 @@\n+{"dispatch_id":"9289ad56-f0aa-4683-be0c-101ec820d412","expected_head_sha":"aa286f693bc903fd2b93c555d9da898652fbdb26","target_pr":2025,"task_fingerprint":"6f6fbcba356fb7353fc7aade0a508643716b3aaa9c314cb9831c05770747a6d0","status":"SUCCEEDED","result_code":"AUDIT_PASSED","summary":"Exact-head bounded audit passed all 87 selected tests and guard review; no repair is needed.","evidence":["git rev-parse HEAD = aa286f693bc903fd2b93c555d9da898652fbdb26.","Required pytest command passed: 87 passed in 0.74s.","oracle_autopilot/light_native_launch_gate.py fails closed on malformed admission and imports the loader only after PILOT.","oracle_autopilot/light_native_adapter.py checks admission before each RPC, PR read, and provider call.","Adapter validation binds immutable request, exact head, repository, target PR, Light profile, zero cost/repair attempts, and false effect flags.","Focused tests cover HOLD boundaries, replay safety, binding changes, unsafe flags, provider target aliases, and READ_ONLY/VERIFY-only execution."]}'
RECOVERED_NATIVE = {'request': {'assignment': {'can_repair': False, 'dispatch_id': '9289ad56-f0aa-4683-be0c-101ec820d412', 'execution_scope': 'REPOSITORY', 'executor_id': 'chat:6aa6a4c0-4858-83eb-872c-4bc3451edc83', 'objective': 'Perform a READ_ONLY AUTOPILOT audit of open PR #2025 at exact head aa286f693bc903fd2b93c555d9da898652fbdb26. Investigate only the bounded focus in task_spec_json, report evidence and the smallest repair scope if needed, and make no changes.', 'role': 'AUTOPILOT', 'target_chat_id': '6aa6a4c0-4858-83eb-872c-4bc3451edc83', 'target_chat_name': 'Autopilot role executor', 'target_chat_url': 'https://chatgpt.com/c/6aa6a4c0-4858-83eb-872c-4bc3451edc83', 'task_id': 'd8595f4c-4c02-43d0-9de7-9f377a7aa0c4', 'task_kind': 'REPOSITORY_AUDIT', 'task_spec_json': {'assignment_schema': 'SLAVIK_DISPATCH_ASSIGNMENT_V1', 'canon_mutation': False, 'cost_cap_microusd': 0, 'deploy': False, 'exact_head_binding': True, 'execution_mode': 'READ_ONLY', 'expected_changed_files': [], 'expected_head_sha': 'aa286f693bc903fd2b93c555d9da898652fbdb26', 'external_mutation': False, 'focus_paths': ['oracle_autopilot/light_native_launch_gate.py', 'oracle_autopilot/light_native_adapter.py', 'tests/test_oracle_autopilot_light_native_launch_gate.py', 'tests/test_oracle_autopilot_light_native_adapter.py'], 'mailbox_pr': 1703, 'max_repair_attempts': 0, 'media_execution': False, 'merge': False, 'neon_mutation': False, 'paid_action': False, 'preserve': 'Every original file; no installs, repairs, commits, pushes or production access. Only trusted disposable native transport report permitted.', 'production_mutation': False, 'repair_attempt': 0, 'repository': 'olegmed1-art/bridge-video-free', 'repository_mutation': False, 'required_checks': ['git rev-parse HEAD must equal aa286f693bc903fd2b93c555d9da898652fbdb26', 'PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_oracle_autopilot_light_native_launch_gate.py tests/test_oracle_autopilot_light_native_adapter.py'], 'role': 'AUTOPILOT', 'server_mutation': False, 'source_task_kind': 'REPOSITORY_AUDIT', 'target_pr': 2025, 'verification_kind': 'AUDIT_PASSED only if exact head, all 87 selected tests and guard review pass; otherwise truthful BLOCKED.', 'work_key': 'light-native-audit-20260928-8bbc-pilot'}}, 'branch': 'fix/light-native-acceptance-audit', 'dispatch_id': '9289ad56-f0aa-4683-be0c-101ec820d412', 'expected_head_sha': 'aa286f693bc903fd2b93c555d9da898652fbdb26', 'mode': 'READ_ONLY', 'reservation_id': '644442b3-ac84-4463-804d-93d4e690ed5b', 'target_pr': 2025, 'task_fingerprint': '6f6fbcba356fb7353fc7aade0a508643716b3aaa9c314cb9831c05770747a6d0'}, 'provider_task_id': 'task_e_6aba7c5b6d5c8323ad13a01a7499dc41'}

@pytest.mark.parametrize('fault',[None,'bytes','provider','head','dispatch'])
def test_actual_cloud_log_provenance_and_binding(fault):
    import copy
    native=copy.deepcopy(RECOVERED_NATIVE);raw=RECOVERED_LOG
    if fault=='bytes':raw+=b'\n'
    if fault=='provider':native['provider_task_id']='task_e_other'
    if fault=='head':native['request']['expected_head_sha']='0'*40
    if fault=='dispatch':native['request']['dispatch_id']='different'
    if fault:
        with pytest.raises(RuntimeError):target.recovered_log_evidence(native,raw)
    else:
        evidence=target.recovered_log_evidence(native,raw)
        assert evidence['kind']=='CLOUD_LOG_RECOVERY_V1' and evidence['cli_diff_verified'] is False
        assert evidence['report_sha256']=='2b427a1f6eda67879ec7ee75ba5fe328ae6195d418e45620ee16f51385e51cfa'
        assert evidence['patch_sha256']=='14c0316be5bdc007b6fffeb7c66450d4fa46ca1584b54efa08dfac1081998ec4'
        assert evidence['changes']=='' and evidence['report']['result_code']=='AUDIT_PASSED'


@pytest.mark.skipif(os.geteuid()!=0,reason='root retention')
@pytest.mark.parametrize('fault',[None,'snapshot','precommit','lost_commit_ack','child_failed','timeout','finish_lost_ack','role_drift','guard_expired'])
def test_log_recovery_restores_controls_and_never_resubmits(tmp_path,monkeypatch,fault):
    import copy, subprocess
    from contextlib import contextmanager
    from oracle_autopilot import codex_cli_bridge as bridge,light_native_restart
    c=target.control;root=tmp_path/'root';root.mkdir(mode=0o700);(root/'intake').mkdir(mode=0o700)
    claim=tmp_path/'claim';claim.mkdir(mode=0o700)
    monkeypatch.setattr(c.plan,'ROOT',root);monkeypatch.setattr(c,'CLAIM',claim);monkeypatch.setattr(c.plan,'LIGHT',tmp_path/'light')
    request=SimpleNamespace(value=dict(source=target.SOURCE,scope=target.SCOPE),permit=b'permit')
    monkeypatch.setattr(c,'ledger',lambda *a:(request,None,None,None,root))
    monkeypatch.setattr(c,'restored_receipt',lambda *a:{'restored':True})
    for name in ('image-start.json','image-restart.json','image-resumed.json'):(claim/name).write_bytes(b'{}')
    c.retained(root/'pilot-unit.json',b'{}')
    before=dict(config={'enabled':False,'cutover_at':'original'},role={'can_repair':True,'updated_at':'original','other':'preserved'},outbox={'delivery_deadline_at':'original','status':'SENT'})
    native=dict(copy.deepcopy(RECOVERED_NATIVE),state='SUBMITTED',terminal=None,owner_name='autopilot_light_worker_login',prompt_sha256=bridge.digest('prompt'))
    observed=dict(rows=before,native=native,fence={'consumed':True});state=copy.deepcopy(observed)
    sha=c.digest(c.canonical(observed));monkeypatch.setattr(target,'RECOVERY_SNAPSHOT',sha)
    c.retained(root/'submitted-controls-restored.json',c.canonical(dict(restored_sha256=sha,request_sha256=target.RECOVERY_REQUEST)))
    monkeypatch.setattr(target,'records',lambda *a:(root,{},None,dict(native_config=before['config'])))
    monkeypatch.setattr(target,'bounded_evidence_inventory',lambda *a:{'preserved':True})
    monkeypatch.setattr(target.release.staging,'require_current_main',lambda *a:None)
    monkeypatch.setattr(target.switch,'show',lambda *a:dict(MainPID='0',ControlPID='0',ActiveState='failed'))
    monkeypatch.setattr(bridge,'prompt_for',lambda *a:'prompt')
    monkeypatch.setattr(bridge,'lookup',lambda *a,**kw:dict(state='SUBMITTED',provider_task_id=target.RECOVERY_PROVIDER,prompt_sha256=bridge.digest('prompt')))
    monkeypatch.setattr(light_native_restart,'validate_proof',lambda *a:{'verified':True})
    monkeypatch.setattr(target,'Agreement',lambda *a:SimpleNamespace(assert_held=lambda *a:None,end=target.time.time()+600))
    monkeypatch.setattr(target.pwd,'getpwnam',lambda *a:SimpleNamespace(pw_uid=1000,pw_gid=1000))
    saved_read=target.hold.read
    monkeypatch.setattr(target.hold,'read',lambda path,*a:b'worker-env' if path==target.hold.ENV else saved_read(path,*a))
    monkeypatch.setattr(target.hold,'env',lambda *a:{'AUTOPILOT_DATABASE_URL':'worker-only'})
    writes=[];calls=[];transactions=[0];expired=[False]
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        @contextmanager
        def transaction(self):
            transactions[0]+=1;ordinal=transactions[0];old=copy.deepcopy(state)
            try:
                yield
                if fault=='precommit' and ordinal==1:raise RuntimeError('precommit')
            except BaseException:state.clear();state.update(old);raise
            if fault=='lost_commit_ack' and ordinal==1:raise RuntimeError('unknown commit')
        def execute(self,sql,params=None):
            if sql.startswith('UPDATE'):
                assert sql.startswith('UPDATE autopilot.role_registry')
                value=False if 'can_repair=false' in sql else params[0]
                writes.append(value);state['rows']['role'].update(can_repair=value,updated_at='applied' if not value else 'restored')
            return SimpleNamespace(rowcount=1,fetchone=lambda:(True,))
    monkeypatch.setattr(target,'submitted_snapshot',lambda *a:copy.deepcopy(state))
    def child(args,**kw):
        calls.append(kw)
        assert args[4]==target.RECOVERY_FINISH_PROGRAM
        assert kw['env']['AUTOPILOT_DATABASE_URL']=='worker-only' and len(kw['env'])==3
        assert state['rows']['role']['can_repair'] is False
        v=json.loads(kw['input']);assert v['provider_task_id']==target.RECOVERY_PROVIDER
        if fault=='role_drift':state['rows']['role']['other']='foreign';return SimpleNamespace(returncode=1)
        if fault=='child_failed':return SimpleNamespace(returncode=1)
        if fault=='timeout':raise subprocess.TimeoutExpired(args,45)
        if fault=='guard_expired':expired[0]=True;raise subprocess.TimeoutExpired(args,45)
        state['native'].update(state='TERMINAL',terminal=v['terminal'])
        state['rows']['outbox']['status']='CALLBACK_ACCEPTED'
        if fault=='finish_lost_ack':raise subprocess.TimeoutExpired(args,45)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess,'run',child)
    terminal_checks=[]
    def check_terminal(*a):
        assert state['rows']['role']['can_repair'] is True and state['native']['state']=='TERMINAL'
        terminal_checks.append(True)
    intake=SimpleNamespace(engine=SimpleNamespace(identity=lambda *a:None),target=lambda:None,
        one=lambda *a:copy.deepcopy(state['rows']['role']),observe_terminal=check_terminal)
    if fault=='snapshot':state['native']['provider_task_id']='changed'
    payload=dict(request_sha256=target.RECOVERY_REQUEST,recovered_log_b64=base64.b64encode(RECOVERED_LOG).decode(),agreement={},accepted_agreement_sha256='test')
    def run_check():
        if expired[0]:raise RuntimeError('runner expired')
    args=(payload,b'accepted',SimpleNamespace(assert_running=run_check),SimpleNamespace(connect=lambda **kw:Conn()),lambda _: {},'owner-credential',intake,SimpleNamespace(verify_restart_unit=lambda *a:None,observed_target=lambda *a:None),None)
    if fault not in (None,'finish_lost_ack'):
        with pytest.raises(RuntimeError):target.recover_log_terminal(*args)
        assert not (root/'log-recovery-completed.json').exists()
    else:
        result=target.recover_log_terminal(*args)
        assert result['task_done'] is True and result['native_transport_verified'] is False
        assert terminal_checks==[True]
    if fault in ('snapshot','precommit','lost_commit_ack'):assert not calls
    if fault=='role_drift':assert state['rows']['role']['other']=='foreign' and writes==[False]
    else:assert state['rows']['role']['can_repair'] is True
    assert state['rows']['config']==before['config'] and state['fence']==observed['fence']
    assert state['rows']['outbox']['delivery_deadline_at']=='original'
    assert len(calls)<=1

@pytest.mark.parametrize('fault',[None,'function_drift','receipt_drift'])
def test_finish_child_uses_only_original_finish_rpc(monkeypatch,capsys,fault):
    import io,sys
    from oracle_autopilot import light_native_loader
    calls=[];value={'request':RECOVERED_NATIVE['request'],'provider_task_id':target.RECOVERY_PROVIDER,'terminal':{'status':'SUCCEEDED'}}
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,sql,args=None):
            calls.append(sql)
            if sql.startswith('SELECT pg_get_functiondef'):return SimpleNamespace(fetchone=lambda:('reviewed definition',))
            if sql.startswith('SELECT autopilot.native_cli_finish'):
                assert json.loads(args[0])==value['request'] and args[1]==value['provider_task_id'] and json.loads(args[2])==value['terminal']
                return SimpleNamespace(fetchone=lambda:({**value,'state':'SUBMITTED' if fault=='receipt_drift' else 'TERMINAL'},))
    monkeypatch.setattr(sys,'stdin',SimpleNamespace(buffer=io.BytesIO(json.dumps(value).encode())))
    monkeypatch.setattr(sys,'argv',['program','/reviewed/candidate']);monkeypatch.setenv('AUTOPILOT_DATABASE_URL','worker-only')
    monkeypatch.setattr(sys,'path',list(sys.path))
    monkeypatch.setitem(sys.modules,'psycopg',SimpleNamespace(connect=lambda **kw:Conn()))
    monkeypatch.setattr(light_native_loader,'runtime_parameters',lambda raw: {'dsn':raw} if raw=='worker-only' else pytest.fail('wrong credential'))
    monkeypatch.setattr(light_native_loader,'runtime_identity',lambda conn:None)
    monkeypatch.setattr(hashlib,'sha256',lambda *a:SimpleNamespace(hexdigest=lambda:'bad' if fault=='function_drift' else '8b3882bf0955a7aab1c0f76cc9c5a8269452b7d1aa0f6f99f8d7feaf2b7deb26'))
    if fault:
        with pytest.raises(AssertionError):exec(target.RECOVERY_FINISH_PROGRAM,{})
        assert capsys.readouterr().out==''
    else:
        exec(target.RECOVERY_FINISH_PROGRAM,{})
        assert capsys.readouterr().out=='LIGHT_EXISTING_NATIVE_FINISH_CONFIRMED\n'
    assert sum(s.startswith('SELECT autopilot.native_cli_finish') for s in calls)==(0 if fault=='function_drift' else 1)
    assert len(calls)==(2 if fault=='function_drift' else 3)
