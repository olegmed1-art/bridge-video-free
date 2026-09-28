"""Fixed zero-submit continuation; historical Plan/intake remain immutable."""
import base64
from dataclasses import asdict
import json
import math
import re
import os
from pathlib import Path
import pwd
import stat
import time
from ops import light_native_service_controller as control
from ops import light_native_service_switch as switch
from ops import light_native_pilot_release as release
from ops import oracle_light_active_hold_attest as hold
from ops.native_maintenance_agreement import Agreement

SOURCE='1269ad0f0f77efc65749bd194a0c0bcbda168ee4'
PACKAGE='092d6b1a52a0c554d188fce594e441a5303e34592b85b30c00a0613e8123171b'
OLD_SOURCE='8bbc1d61010ef70c3fca02b5151ac86fce02a144'
OLD_PACKAGE='46acb2672ba58dc369c3ccf70f45bb4beff37cb4efad2bfe3b34ae4a170c0c94'
OLD_REQUEST='c12ef4667a3a620d6db2f876393545333fb772af36196970193fcb7d4a94c314'
PLAN='3af45c61ece27fa57f6ebf3f26c304e46667d3585ce732392989143850ff06ab'
INTAKE='92c4e1386cc88a5aa180356084520be1905a5fc1642f709b7b6a77c51ed517e3'
SNAPSHOT='3911c8aeee1264331055ccfe66865fa77551be32d887f10a4b5867c0de75ee78'
DISPATCH='9289ad56-f0aa-4683-be0c-101ec820d412'
TASK='d8595f4c-4c02-43d0-9de7-9f377a7aa0c4'
WORK='6bda2dca-d0a1-4783-bbb2-d4006ed3e536'
OLD_INVOCATION='19ac65f40f8647cc9cda82d4fb29ff74'
SCOPE=dict(version=1,operation='native_same_task_fixed_continuation',source=SOURCE,
    package_sha256=PACKAGE,original_plan_sha256=PLAN,dispatch_id=DISPATCH,
    task_id=TASK,prior_request_sha256=OLD_REQUEST)
COPIED=('plan.json','intake.json','before.json','broker.json','discovery.json','publication.json')
ARCHIVE_SUFFIX='-archive-'+OLD_REQUEST[:12]
require=switch.require


def absent(path):
    require(not path.exists() and not path.is_symlink(),'REENTRY_PATH_EXISTS')


def inventory(path):
    """Preserve modes, ownership, inodes and bytes; no links or special files."""
    result={}
    for p in [path,*sorted(path.rglob('*'))]:
        row=p.lstat();name='.' if p==path else p.relative_to(path).as_posix()
        require(not stat.S_ISLNK(row.st_mode) and (stat.S_ISDIR(row.st_mode) or stat.S_ISREG(row.st_mode)), 'REENTRY_INVENTORY_TYPE')
        require(stat.S_ISDIR(row.st_mode) or row.st_nlink==1,'REENTRY_INVENTORY_LINK')
        result[name]=dict(inode=row.st_ino,uid=row.st_uid,gid=row.st_gid,mode=stat.S_IMODE(row.st_mode),
            kind='directory' if stat.S_ISDIR(row.st_mode) else 'file',
            sha256=None if stat.S_ISDIR(row.st_mode) else control.digest(p.read_bytes()))
    return result


def move_preserved(source,destination,expected,guard):
    guard();absent(destination)
    require(inventory(source)==expected,'REENTRY_ARCHIVE_CHANGED')
    require(source.parent.stat().st_dev==destination.parent.stat().st_dev,'REENTRY_ARCHIVE_DEVICE')
    os.rename(source,destination);release.staging.fsync_directory(source.parent)
    require(not source.exists() and inventory(destination)==expected,'REENTRY_ARCHIVE_READBACK')


def baseline_guard(baseline,agreement,run_guard,*,supervisor_absent=True):
    run_guard.assert_running();agreement.assert_held(control.digest(control.canonical(SCOPE)))
    release.staging.require_current_main(SOURCE)
    actual=hold.service_hold_identity()
    require(asdict(actual)==baseline['prior'],'REENTRY_SERVICE_CHANGED')
    prior=hold.HoldIdentity(**baseline['prior'])
    switch.unchanged_files(baseline['protected'],prior,baseline['protected_sha256'])
    switch.attest_hardening();switch.unit_absent(control.plan.PILOT_UNIT)
    if supervisor_absent:switch.unit_absent(control.plan.SUPERVISOR_UNIT)
    journal=control.plan.LIGHT/'runtime/codex-dispatch'/(DISPATCH+'.json')
    absent(journal)
    return actual


def db_rows(conn,intake):
    config=intake.one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')
    role=intake.one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT'")
    task=intake.one(conn,'SELECT to_jsonb(t) FROM autopilot.task t WHERE task_id=%s::uuid',(TASK,))
    outbox=intake.one(conn,'SELECT to_jsonb(o) FROM autopilot.role_dispatch_outbox o WHERE dispatch_id=%s::uuid',(DISPATCH,))
    work=intake.one(conn,'SELECT to_jsonb(w) FROM autopilot.project_work_item w WHERE work_item_id=%s::uuid',(WORK,))
    counts=conn.execute("SELECT (SELECT count(*) FROM autopilot.native_cli_receipt),(SELECT count(*) FROM autopilot.task WHERE status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')),(SELECT count(*) FROM autopilot.project_work_task WHERE work_item_id=%s::uuid),(SELECT count(*) FROM autopilot.task WHERE goal_json->>'origin_task_id'=%s),(SELECT count(*) FROM autopilot.role_dispatch_outbox WHERE task_id=%s::uuid)",(WORK,TASK,TASK)).fetchone()
    return dict(config=config,role=role,task=task,outbox=outbox,work=work,counts=list(counts))


def check_zero(rows,receipt,config,role):
    require(rows['config']==config and all(rows['role'].get(k)==v for k,v in role.items() if k!='updated_at'),'REENTRY_CONFIG_DRIFT')
    require(rows['counts']==[0,1,1,0,1],'REENTRY_QUEUE_DRIFT')
    task=rows['task'];outbox=rows['outbox'];work=rows['work']
    require(task['task_id']==TASK and task['status']=='WAITING_EXTERNAL'
        and control.digest(json.dumps(task['goal_json'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())==receipt['goal_json_sha256']
        and work['state']=='ACTIVE' and work['last_task_id']==TASK,'REENTRY_TASK_DRIFT')
    require(outbox['task_id']==TASK and outbox['status']=='PUBLISHED' and outbox['mode']=='READ_ONLY'
        and outbox['claim_owner'] is None and outbox['delivery_contract_version']==3
        and all(outbox[k]==receipt['dispatch'][k] for k in ('dispatch_id','target_pr','expected_head_sha','task_fingerprint')),'REENTRY_OUTBOX_DRIFT')


def observe(psycopg,parameters,credential,intake,receipt,config,role):
    with psycopg.connect(**parameters(credential),autocommit=True) as conn:
        conn.read_only=True
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.execute("SET LOCAL statement_timeout='5s'")
            intake.engine.identity(conn,intake.target())
            rows=db_rows(conn,intake);check_zero(rows,receipt,config,role)
            return rows


def records(intake):
    root=control.plan.ROOT/'intake'
    raw=control.read(root/'intake.json');require(control.digest(raw)==INTAKE,'REENTRY_INTAKE')
    receipt=control.strict_json(raw,262144)
    plan_raw=control.read(root/'plan.json');plan=intake.Plan(plan_raw,PLAN)
    require(receipt['plan_sha256']==PLAN and receipt['snapshot_sha256']==SNAPSHOT
        and receipt['dispatch_id']==DISPATCH and receipt['task_id']==TASK and receipt['work_item_id']==WORK,'REENTRY_IDENTITY')
    before=intake.engine.load_manifest(root/'before.json',SNAPSHOT)
    return root,receipt,plan,before


def prepare_continuation(package,payload,agreement,run_guard,psycopg,parameters,credential,intake,api,owner):
    root,receipt,plan,before=records(intake)
    old_request,old_prior,old_protected,old_digest,old_directory=control.ledger(OLD_REQUEST)
    require(control.restored_receipt(old_request,old_prior,old_protected,old_digest,old_directory) is not None,'REENTRY_OLD_RESTORE')
    old_clean=control.strict_json(control.read(old_directory/'zero-submit-controls-restored.json'),4096)
    require(old_clean==dict(audit='LIGHT_ZERO_SUBMIT_CONTROLS_RESTORED',request_sha256=OLD_REQUEST,
        intake_sha256=INTAKE,snapshot_sha256=SNAPSHOT,native_enabled=False,can_repair=True,
        task_preserved=True,outbox_preserved=True,native_receipts=0,pilot_resubmitted=False),'REENTRY_OLD_CONTROLS')
    prior=control.stage_observation(SOURCE,package)
    protected=switch.protect_snapshot(prior)
    baseline=dict(version=1,source=SOURCE,package_sha256=PACKAGE,agreement_sha256=agreement.accepted,
        scope_sha256=control.digest(control.canonical(SCOPE)),prior=asdict(prior),protected=protected,
        protected_sha256=control.digest(control.canonical(protected)),observed_at=int(time.time()))
    def guard():return baseline_guard(baseline,agreement,run_guard,supervisor_absent=False)
    guard()
    owner.observed_target(api,plan)
    broker=control.strict_json(control.read(root/'broker.json'),65536)
    discovery=control.strict_json(control.read(root/'discovery.json'),65536)
    require(owner.discovery(api,broker,receipt)==discovery,'REENTRY_DISCOVERY')
    observe(psycopg,parameters,credential,intake,receipt,before['native_config'],before['autopilot_role'])
    service=pwd.getpwnam('school-autopilot')
    control.directory(control.CLAIM,service.pw_uid,service.pw_gid,0o700)
    require(list(control.CLAIM.iterdir())==[],'REENTRY_CLAIM_NOT_EMPTY')
    control.directory(switch.CONTROL,0,service.pw_gid,0o750)
    require(set(p.name for p in switch.CONTROL.iterdir())=={'permit.json','accepted-sha256','admission'},'REENTRY_CONTROL_INVENTORY')
    require(hold.read(switch.CONTROL/'permit.json',0o640,65536)==old_request.permit
        and hold.read(switch.CONTROL/'accepted-sha256',0o640,128)==old_request.value['permit_sha256'].encode()+b'\n'
        and hold.read(switch.CONTROL/'admission',0o640,16)==b'HOLD\n','REENTRY_CONTROL_DRIFT')
    fields=['LoadState','ActiveState','SubState','MainPID','ControlPID','InvocationID','Restart','ExecStart','ExecStopPost']
    supervisor=switch.show(control.plan.SUPERVISOR_UNIT,fields)
    require(supervisor['LoadState']=='loaded' and supervisor['ActiveState']==supervisor['SubState']=='failed'
        and supervisor['MainPID']==supervisor['ControlPID']=='0' and supervisor['InvocationID']==OLD_INVOCATION
        and supervisor['Restart']=='no','REENTRY_SUPERVISOR_IDENTITY')
    script=str(old_directory/'supervisor.py')
    for key,action in [('ExecStart','run'),('ExecStopPost','restore')]:
        expected='{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -I -S -B '+script+' '+action+' '+OLD_REQUEST+' ; ignore_errors=no ;'
        require(supervisor[key].startswith(expected) and supervisor[key].endswith(' }')
            and supervisor[key].count('{')==supervisor[key].count('}')==1,'REENTRY_SUPERVISOR_COMMAND')
    cgroup=Path('/sys/fs/cgroup/system.slice')/control.plan.SUPERVISOR_UNIT
    require(not cgroup.exists() or dict(line.split() for line in (cgroup/'cgroup.events').read_text().splitlines()).get('populated')=='0','REENTRY_SUPERVISOR_PROCESS')
    sources=[control.plan.ROOT,switch.CONTROL,control.CLAIM]
    archives=[p.with_name(p.name+ARCHIVE_SUFFIX) for p in sources]
    for p in archives:absent(p)
    inventories=[inventory(p) for p in sources]
    staged=control.plan.ROOT.with_name(control.plan.ROOT.name+'-next-'+SOURCE[:12])
    control.new_directory(staged,0,0,0o700);control.new_directory(staged/'intake',0,0,0o700)
    for name in COPIED:control.retained(staged/'intake'/name,control.read(root/name))
    control.retained(staged/'baseline.json',control.canonical(baseline))
    provenance=dict(version=1,kind='FIXED_SOURCE_ZERO_SUBMIT_CONTINUATION',scope=SCOPE,
        original_plan_sha256=PLAN,intake_sha256=INTAKE,old_request_sha256=OLD_REQUEST,
        queue_zero_reobserved=False,agreement=payload['agreement'],accepted_agreement_sha256=agreement.accepted,
        archive_paths=[str(p) for p in archives],inventories=inventories,supervisor=supervisor,
        historical_files={name:control.digest(control.read(root/name)) for name in COPIED})
    control.retained(staged/'continuation.json',control.canonical(provenance))
    staged_inventory=inventory(staged)
    guard();observe(psycopg,parameters,credential,intake,receipt,before['native_config'],before['autopilot_role'])
    require(inventory(staged)==staged_inventory,'REENTRY_STAGED_CHANGED')
    # All three originals remain intact; a partial rename is never replayed.
    for source,archive,expected in zip(sources,archives,inventories):
        move_preserved(source,archive,expected,guard)
    absent(control.plan.ROOT);guard()
    os.rename(staged,control.plan.ROOT);release.staging.fsync_directory(control.plan.ROOT.parent)
    for archive,expected in zip(archives,inventories):require(inventory(archive)==expected,'REENTRY_ARCHIVE_FINAL')
    require(inventory(control.plan.ROOT)==staged_inventory,'REENTRY_INSTALL_READBACK')
    require(switch.show(control.plan.SUPERVISOR_UNIT,fields)==supervisor,'REENTRY_SUPERVISOR_CHANGED')
    guard()
    switch.command('/usr/bin/systemctl','reset-failed',control.plan.SUPERVISOR_UNIT)
    for attempt in range(51):
        state=switch.show(control.plan.SUPERVISOR_UNIT,['LoadState','ActiveState','MainPID'])
        if state=={'LoadState':'not-found','ActiveState':'inactive','MainPID':'0'}:break
        require(attempt<50,'REENTRY_SUPERVISOR_NOT_UNLOADED');time.sleep(0.1)
    switch.unit_absent(control.plan.SUPERVISOR_UNIT)
    baseline_guard(baseline,agreement,run_guard)
    require(inventory(control.plan.ROOT)==staged_inventory,'REENTRY_INSTALL_FINAL')
    result=dict(audit='LIGHT_FIXED_CONTINUATION_PREPARED',baseline_sha256=control.digest(control.canonical(baseline)),
        continuation_sha256=control.digest(control.canonical(provenance)),original_plan_sha256=PLAN,
        intake_sha256=INTAKE,dispatch_id=DISPATCH,pilot_submitted=False,db_writes=False)
    control.retained(control.plan.ROOT/'continuation-ready.json',control.canonical(result))
    return result


def ready(payload,agreement,run_guard):
    baseline_raw=control.read(control.plan.ROOT/'baseline.json')
    require(control.digest(baseline_raw)==payload['baseline_sha256'],'REENTRY_BASELINE')
    baseline=control.strict_json(baseline_raw,262144)
    require(baseline['source']==SOURCE and baseline['package_sha256']==PACKAGE
        and baseline['scope_sha256']==control.digest(control.canonical(SCOPE))
        and baseline['agreement_sha256']==agreement.accepted,'REENTRY_BASELINE_SCOPE')
    proof=control.strict_json(control.read(control.plan.ROOT/'continuation-ready.json'),4096)
    require(proof['baseline_sha256']==payload['baseline_sha256'] and proof['intake_sha256']==INTAKE,'REENTRY_READY')
    provenance=control.read(control.plan.ROOT/'continuation.json')
    require(control.digest(provenance)==proof['continuation_sha256'],'REENTRY_PROVENANCE')
    return baseline


def authorize(payload,payload_raw,agreement,run_guard,psycopg,parameters,credential,intake,api,owner):
    root,receipt,plan,before=records(intake)
    baseline=ready(payload,agreement,run_guard)
    guard=lambda:baseline_guard(baseline,agreement,run_guard)
    guard();owner.observed_target(api,plan)
    require(owner.discovery(api,control.strict_json(control.read(root/'broker.json'),65536),receipt)
        ==control.strict_json(control.read(root/'discovery.json'),65536),'REENTRY_DISCOVERY')
    observed=observe(psycopg,parameters,credential,intake,receipt,before['native_config'],before['autopilot_role'])
    with psycopg.connect(**parameters(credential),autocommit=True) as conn:
        conn.read_only=True;intake.engine.identity(conn,intake.target())
        intake.engine.privileges(conn,intake.target(),True)
    absent(root/'permit.json');absent(control.plan.ROOT/'reapply-intent.json')
    control.retained(control.plan.ROOT/'reapply-intent.json',payload_raw)
    control.retained(control.plan.ROOT/'reapply-before.json',control.canonical(observed))
    with psycopg.connect(**parameters(credential),autocommit=True) as conn:
        conn.read_only=False
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
            conn.execute("SET LOCAL statement_timeout='5s'");conn.execute("SET LOCAL lock_timeout='5s'")
            intake.engine.identity(conn,intake.target())
            conn.execute('SELECT singleton FROM autopilot.native_cli_config WHERE singleton FOR UPDATE')
            conn.execute("SELECT role_id FROM autopilot.role_registry WHERE role_id='AUTOPILOT' FOR UPDATE")
            conn.execute('SELECT task_id FROM autopilot.task WHERE task_id=%s::uuid FOR UPDATE',(TASK,))
            conn.execute('SELECT dispatch_id FROM autopilot.role_dispatch_outbox WHERE dispatch_id=%s::uuid FOR UPDATE',(DISPATCH,))
            locked=db_rows(conn,intake);require(locked==observed,'REENTRY_CONCURRENT_DB')
            intake.engine.privileges(conn,intake.target(),True)
            guard()
            a=conn.execute('UPDATE autopilot.native_cli_config SET enabled=true,cutover_at=transaction_timestamp() WHERE singleton')
            b=conn.execute("UPDATE autopilot.role_registry SET can_repair=false WHERE role_id='AUTOPILOT'")
            require(a.rowcount==b.rowcount==1,'REENTRY_UPDATE_COUNT')
            applied=db_rows(conn,intake)
            require(applied['config']['enabled'] is True and applied['role']['can_repair'] is False
                and all(applied['config'].get(k)==v for k,v in observed['config'].items() if k not in ('enabled','cutover_at'))
                and all(applied['role'].get(k)==v for k,v in observed['role'].items() if k not in ('can_repair','updated_at'))
                and all(applied[k]==observed[k] for k in ('task','outbox','work','counts')),'REENTRY_APPLY_DRIFT')
            control.retained(control.plan.ROOT/'reapply-applied.json',control.canonical(applied));guard()
    with psycopg.connect(**parameters(credential),autocommit=True) as conn:
        conn.read_only=True
        require(db_rows(conn,intake)==applied,'REENTRY_COMMIT_READBACK')
        database_commit=dict(baseline_sha256=payload['baseline_sha256'],
            before_sha256=control.digest(control.canonical(observed)),
            applied_sha256=control.digest(control.canonical(applied)))
        control.retained(control.plan.ROOT/'reapply-database-committed.json',control.canonical(database_commit))
        from oracle_autopilot import light_native_preflight as preflight
        dispatch={k:receipt['dispatch'][k] for k in ('dispatch_id','expected_head_sha','mode','target_pr','task_fingerprint')}
        dispatch.update(branch=plan.value['branch'],assignment=receipt['assignment'])
        issued=int(time.time());expires=int(agreement.end)
        evidence=preflight.observe(conn,dispatch,issued_at=issued,expires_at=expires,agreement_sha256=agreement.accepted)
        require(evidence['goal_json_sha256']==receipt['goal_json_sha256'],'REENTRY_GOAL')
    guard()
    permit=dict(version=1,source=SOURCE,dispatch=dispatch,environment_id=release.CLOUD_ENVIRONMENT_ID,
        environment_evidence_sha256=control.digest(control.read(release.ROOT/SOURCE/'environment.json',4096)),
        issued_at=issued,expires_at=expires,owner_preflight=evidence)
    raw=control.canonical(permit);control.retained(root/'permit.json',raw)
    result=dict(audit='LIGHT_FIXED_CONTINUATION_AUTHORIZED',permit_sha256=control.digest(raw),
        baseline_sha256=payload['baseline_sha256'],applied_sha256=control.digest(control.canonical(applied)),
        before_sha256=control.digest(control.canonical(observed)),
        original_plan_sha256=PLAN,intake_sha256=INTAKE,pilot_submitted=False)
    control.retained(control.plan.ROOT/'reapply-committed.json',control.canonical(result))
    return result


def terminal(payload,run_guard,psycopg,parameters,credential,intake,owner,api):
    root,receipt,plan,before=records(intake)
    request,prior,protected,protected_digest,directory=control.ledger(payload['request_sha256'])
    require(request.value['source']==SOURCE and request.value['scope']==SCOPE,'REENTRY_TERMINAL_SCOPE')
    require(control.restored_receipt(request,prior,protected,protected_digest,directory) is not None,'REENTRY_SERVICE_NOT_RESTORED')
    run_guard.assert_running();owner.observed_target(api,plan)
    with psycopg.connect(**parameters(credential),autocommit=True) as conn:
        conn.read_only=True;intake.engine.identity(conn,intake.target())
        native=intake.one(conn,'SELECT to_jsonb(n) FROM autopilot.native_cli_receipt n WHERE dispatch_id=%s::uuid',(DISPATCH,))
        provider,restart=owner.fresh_provider_result(control.plan.source_path(SOURCE),DISPATCH,
            release.CLOUD_ENVIRONMENT_ID,request.permit,native['request'])
        pilot_unit=control.strict_json(control.read(directory/'pilot-unit.json',4096),4096)
        owner.verify_restart_unit(restart,pilot_unit,DISPATCH,native['provider_task_id'])
        from oracle_autopilot import codex_cli_bridge as bridge
        require(native['state']=='TERMINAL' and native['provider_task_id']==provider['provider_task_id']
            and native['terminal']['provider_evidence_sha256']==bridge.digest(bridge.canonical(provider))
            and all(native['terminal'][k]==provider['report'][k] for k in ('status','result_code','summary')),'REENTRY_PROVIDER_MISMATCH')
        record=dict(version=1,plan_sha256=PLAN,dispatch_id=DISPATCH,task_id=TASK,
            provider_task_id=native['provider_task_id'],request=native['request'],result=native['terminal'])
        raw=control.canonical(record);observed=intake.observe_terminal(conn,plan,receipt,raw,control.digest(raw))
    control.retained(root/'terminal.json',raw);control.retained(root/'restart.json',control.canonical(restart))
    return dict(audit='LIGHT_FIXED_TERMINAL_CANDIDATE',terminal_sha256=control.digest(raw),success=observed['success'],controlled_restart_verified=True)


def restore_controls(payload,payload_raw,run_guard,psycopg,parameters,credential,intake):
    root,receipt,plan,_=records(intake)
    request,prior,protected,protected_digest,directory=control.ledger(payload['request_sha256'])
    require(request.value['source']==SOURCE and request.value['scope']==SCOPE,'REENTRY_RESTORE_SCOPE')
    def guard():
        run_guard.assert_running()
        require(control.restored_receipt(request,prior,protected,protected_digest,directory) is not None,'REENTRY_SERVICE_NOT_RESTORED')
    guard()
    raw=control.read(root/'terminal.json');evidence=intake.terminal_evidence(raw,payload['terminal_sha256'],plan,receipt)
    before_raw=control.read(control.plan.ROOT/'reapply-before.json')
    before=control.strict_json(before_raw,262144)
    applied_raw=control.read(control.plan.ROOT/'reapply-applied.json')
    committed=control.strict_json(control.read(control.plan.ROOT/'reapply-committed.json'),4096)
    require(control.digest(applied_raw)==committed['applied_sha256'] and control.digest(before_raw)==committed['before_sha256'],'REENTRY_APPLIED_PROOF')
    applied=control.strict_json(applied_raw,262144)
    control.retained(control.plan.ROOT/'restore-controls-intent.json',payload_raw)
    with psycopg.connect(**parameters(credential),autocommit=True) as conn:
        conn.read_only=False
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE');conn.execute("SET LOCAL statement_timeout='5s'")
            intake.engine.identity(conn,intake.target())
            config=intake.one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton FOR UPDATE')
            role=intake.one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT' FOR UPDATE")
            require(config==applied['config'] and role==applied['role'],'REENTRY_RESTORE_CONCURRENT')
            success=intake._terminal_rows(conn,plan,receipt,evidence);guard()
            a=conn.execute('UPDATE autopilot.native_cli_config SET enabled=%s,cutover_at=%s WHERE singleton',(before['config']['enabled'],before['config']['cutover_at']))
            b=conn.execute("UPDATE autopilot.role_registry SET can_repair=%s WHERE role_id='AUTOPILOT'",(before['role']['can_repair'],))
            require(a.rowcount==b.rowcount==1,'REENTRY_RESTORE_COUNT')
            config=intake.one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')
            role=intake.one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT'")
            require(config==before['config'] and all(role.get(k)==v for k,v in before['role'].items() if k!='updated_at'),'REENTRY_RESTORE_TRANSACTION_READBACK');guard()
    with psycopg.connect(**parameters(credential),autocommit=True) as conn:
        conn.read_only=True;intake.engine.identity(conn,intake.target())
        config=intake.one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')
        role=intake.one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT'")
        require(config==before['config'] and all(role.get(k)==v for k,v in before['role'].items() if k!='updated_at'),'REENTRY_RESTORE_READBACK')
        intake.observe_terminal(conn,plan,receipt,raw,payload['terminal_sha256'])
    guard();result=dict(audit='LIGHT_FIXED_CONTROLS_RESTORED',terminal_sha256=payload['terminal_sha256'],success=success,controls_restored=True)
    control.retained(control.plan.ROOT/'controls-restored.json',control.canonical(result));return result



def verify_unreserved_claim(request,directory,accepted):
    path=control.CLAIM;service=pwd.getpwnam('school-autopilot')
    require(set(p.name for p in path.iterdir())=={'pilot.lock','image-start.json','permit.json'},'REENTRY_UNRESERVED_FILES')
    require(all(p.stat().st_size<=65536 for p in path.iterdir()),'REENTRY_UNRESERVED_SIZE')
    proof=inventory(path)
    require(control.digest(control.canonical(proof))==accepted,'REENTRY_UNRESERVED_INVENTORY')
    require(proof['.']['uid']==service.pw_uid and proof['.']['gid']==service.pw_gid and proof['.']['mode']==0o700
        and all(v['uid']==service.pw_uid and v['gid']==service.pw_gid and v['mode']==0o600 and v['kind']=='file'
            for k,v in proof.items() if k!='.'),'REENTRY_UNRESERVED_OWNERSHIP')
    require((path/'pilot.lock').read_bytes()==b'' and (path/'permit.json').read_bytes()==request.permit,'REENTRY_UNRESERVED_PERMIT')
    start=control.strict_json((path/'image-start.json').read_bytes(),8192)
    unit=control.strict_json(control.read(directory/'pilot-unit.json',4096),4096)
    permit=control.strict_json(request.permit,65536)
    require(set(start)=={'version','source','permit_sha256','pid','invocation_id','image_id','deadline','last_wall'}
        and type(start['version']) is int and start['version']==1 and start['source']==SOURCE
        and start['permit_sha256']==control.digest(request.permit)
        and type(start['pid']) is int and start['pid']>0 and str(start['pid'])==unit['MainPID']
        and start['invocation_id']==unit['InvocationID'] and re.fullmatch('[0-9a-f]{32}',start['invocation_id'])
        and re.fullmatch('[0-9a-f]{64}',start['image_id'])
        and all(type(start[k]) in (int,float) and math.isfinite(start[k]) and start[k]>0 for k in ('deadline','last_wall'))
        and permit['issued_at']<=start['last_wall']<permit['expires_at'],'REENTRY_UNRESERVED_IMAGE')
    require(inventory(path)==proof,'REENTRY_UNRESERVED_CHANGED')


def restore_zero_submit(payload,payload_raw,run_guard,psycopg,parameters,credential,intake):
    root,receipt,plan,original=records(intake)
    baseline_raw=control.read(control.plan.ROOT/'baseline.json')
    require(control.digest(baseline_raw)==payload['baseline_sha256'],'REENTRY_ZERO_BASELINE')
    baseline=control.strict_json(baseline_raw,262144)
    require(baseline['source']==SOURCE and baseline['package_sha256']==PACKAGE
        and baseline['scope_sha256']==control.digest(control.canonical(SCOPE)),'REENTRY_ZERO_SCOPE')
    committed=control.strict_json(control.read(control.plan.ROOT/'reapply-database-committed.json'),4096)
    before_raw=control.read(control.plan.ROOT/'reapply-before.json')
    applied_raw=control.read(control.plan.ROOT/'reapply-applied.json')
    require(control.digest(before_raw)==committed['before_sha256'] and control.digest(applied_raw)==committed['applied_sha256']
        and committed['baseline_sha256']==payload['baseline_sha256'],'REENTRY_ZERO_COMMIT')
    before=control.strict_json(before_raw,262144);applied=control.strict_json(applied_raw,262144)
    require(before['config']==original['native_config']
        and all(before['role'].get(k)==v for k,v in original['autopilot_role'].items() if k!='updated_at'),'REENTRY_ZERO_ORIGINAL')
    request_digest=payload['request_sha256']
    def guard():
        run_guard.assert_running()
        if request_digest:
            request,prior,protected,protected_digest,directory=control.ledger(request_digest)
            require(request.value['source']==SOURCE and request.value['scope']==SCOPE
                and request.value['baseline_sha256']==payload['baseline_sha256'],'REENTRY_ZERO_REQUEST')
            if (directory/'supervisor-intent.json').exists():
                require(control.restored_receipt(request,prior,protected,protected_digest,directory) is not None,'REENTRY_ZERO_RESTORE')
            else:
                absent(directory/'run-started.json');absent(directory/'pilot-unit.json')
                require(asdict(hold.service_hold_identity())==baseline['prior'],'REENTRY_ZERO_HOLD')
                switch.unchanged_files(protected,prior,protected_digest)
        else:
            require(not any(p.is_dir() and p.name!='intake' for p in control.plan.ROOT.iterdir()),'REENTRY_ZERO_UNBOUND_REQUEST')
            require(asdict(hold.service_hold_identity())==baseline['prior'],'REENTRY_ZERO_HOLD')
            prior=hold.HoldIdentity(**baseline['prior'])
            switch.unchanged_files(baseline['protected'],prior,baseline['protected_sha256'])
        switch.no_processes(control.plan.PILOT_UNIT);switch.attest_hardening()
        state=switch.show(control.plan.SUPERVISOR_UNIT,['MainPID','ControlPID','ActiveState'])
        require(state['MainPID']==state['ControlPID']=='0' and state['ActiveState'] in ('inactive','failed'),'REENTRY_ZERO_SUPERVISOR')
        absent(control.plan.LIGHT/'runtime/codex-dispatch'/(DISPATCH+'.json'))
        if switch.CONTROL.exists():require(hold.read(switch.CONTROL/'admission',0o640,16)==b'HOLD\n','REENTRY_ZERO_ADMISSION')
        if payload.get('action')=='restore-unreserved':
            require(bool(request_digest),'REENTRY_UNRESERVED_REQUEST')
            verify_unreserved_claim(request,directory,payload['claim_inventory_sha256'])
        elif control.CLAIM.exists():require(list(control.CLAIM.iterdir())==[],'REENTRY_ZERO_CLAIM')
    guard()
    control.retained(control.plan.ROOT/'zero-submit-restore-intent.json',payload_raw)
    with psycopg.connect(**parameters(credential),autocommit=True) as conn:
        conn.read_only=False
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE');conn.execute("SET LOCAL statement_timeout='5s'")
            intake.engine.identity(conn,intake.target())
            conn.execute('SELECT singleton FROM autopilot.native_cli_config WHERE singleton FOR UPDATE')
            conn.execute("SELECT role_id FROM autopilot.role_registry WHERE role_id='AUTOPILOT' FOR UPDATE")
            conn.execute('SELECT task_id FROM autopilot.task WHERE task_id=%s::uuid FOR UPDATE',(TASK,))
            conn.execute('SELECT dispatch_id FROM autopilot.role_dispatch_outbox WHERE dispatch_id=%s::uuid FOR UPDATE',(DISPATCH,))
            rows=db_rows(conn,intake);require(rows==applied,'REENTRY_ZERO_APPLIED_DRIFT')
            check_zero(rows,receipt,applied['config'],applied['role']);guard()
            a=conn.execute('UPDATE autopilot.native_cli_config SET enabled=%s,cutover_at=%s WHERE singleton',(before['config']['enabled'],before['config']['cutover_at']))
            b=conn.execute("UPDATE autopilot.role_registry SET can_repair=%s WHERE role_id='AUTOPILOT'",(before['role']['can_repair'],))
            require(a.rowcount==b.rowcount==1,'REENTRY_ZERO_UPDATE_COUNT')
            restored=db_rows(conn,intake);check_zero(restored,receipt,before['config'],before['role']);guard()
    with psycopg.connect(**parameters(credential),autocommit=True) as conn:
        conn.read_only=True;intake.engine.identity(conn,intake.target())
        rows=db_rows(conn,intake);require(rows==restored,'REENTRY_ZERO_READBACK')
        check_zero(rows,receipt,before['config'],before['role'])
    guard();result=dict(audit='LIGHT_FIXED_UNRESERVED_CONTROLS_RESTORED' if payload.get('action')=='restore-unreserved' else 'LIGHT_FIXED_ZERO_SUBMIT_CONTROLS_RESTORED',controls_restored=True,
        native_enabled=rows['config']['enabled'],can_repair=rows['role']['can_repair'],native_receipts=0,
        task_preserved=True,pilot_resubmitted=False)
    control.retained(control.plan.ROOT/'zero-submit-controls-restored.json',control.canonical(result));return result


def inspect_zero_submit(payload,run_guard,psycopg,parameters,credential,intake):
    root,receipt,plan,original=records(intake)
    request,prior,protected,protected_digest,directory=control.ledger(payload['request_sha256'])
    require(request.value['source']==SOURCE and request.value['scope']==SCOPE
        and request.value['baseline_sha256']==payload['baseline_sha256'],'REENTRY_INSPECT_SCOPE')
    run_guard.assert_running()
    require(control.restored_receipt(request,prior,protected,protected_digest,directory) is not None,'REENTRY_INSPECT_HOLD')
    state=switch.show(control.plan.SUPERVISOR_UNIT,['MainPID','ControlPID','ActiveState'])
    require(state['MainPID']==state['ControlPID']=='0' and state['ActiveState'] in ('inactive','failed'),'REENTRY_INSPECT_SUPERVISOR')
    entries=list(control.CLAIM.iterdir())
    require(len(entries)<=8 and all(p.is_file() and not p.is_symlink() and p.stat().st_size<=65536 for p in entries),'REENTRY_INSPECT_CLAIM_SIZE')
    claim=inventory(control.CLAIM)
    applied=control.strict_json(control.read(control.plan.ROOT/'reapply-applied.json'),262144)
    rows=observe(psycopg,parameters,credential,intake,receipt,applied['config'],applied['role'])
    journal=control.plan.LIGHT/'runtime/codex-dispatch'/(DISPATCH+'.json')
    return dict(audit='LIGHT_FIXED_ZERO_SUBMIT_INSPECTION',request_sha256=payload['request_sha256'],
        claim_inventory=claim,claim_inventory_sha256=control.digest(control.canonical(claim)),
        provider_journal_exists=journal.exists() or journal.is_symlink(),database_matches_applied=rows==applied,
        native_receipts=rows['counts'][0],claim_request_exists=(control.CLAIM/'request.json').exists(),
        permit_matches=(control.CLAIM/'permit.json').exists() and (control.CLAIM/'permit.json').read_bytes()==request.permit,
        production_mutations=False)


def reconcile(old_raw,payload_raw,accepted,wheels,credential,token,run_guard,new_raw):
    require(control.digest(old_raw)==OLD_PACKAGE and control.digest(new_raw)==PACKAGE,'REENTRY_PACKAGES')
    require(control.digest(payload_raw)==accepted,'REENTRY_PAYLOAD')
    payload=control.strict_json(payload_raw,4096)
    action=payload.get('action')
    shapes={'prepare-continuation':{'action','agreement','accepted_agreement_sha256'},
        'authorize':{'action','agreement','accepted_agreement_sha256','baseline_sha256'},
        'terminal':{'action','request_sha256'},'inspect-zero-submit':{'action','baseline_sha256','request_sha256'},'restore-unreserved':{'action','baseline_sha256','request_sha256','claim_inventory_sha256'},'restore-zero-submit':{'action','baseline_sha256','request_sha256'},'restore-controls':{'action','request_sha256','terminal_sha256'}}
    require(action in shapes and set(payload)==shapes[action],'REENTRY_ACTION')
    package=control.verified_package(old_raw,OLD_SOURCE,OLD_PACKAGE) if action=='prepare-continuation' else control.verified_package(new_raw,SOURCE,PACKAGE)
    if action=='prepare-continuation':
        package=control.strict_json(new_raw,16*1024*1024);release.validate(package['runtime'],SOURCE,package['runtime']['sha256'])
    agreement=Agreement(payload['agreement'],payload['accepted_agreement_sha256'],SCOPE) if action in ('prepare-continuation','authorize') else None
    from ops.native_maintenance_owner_host import loaded_runtime
    with loaded_runtime(wheels) as (psycopg,_):
        from ops.native_maintenance_owner_attest import parameters
        from database import light_native_pilot_intake as intake
        from ops import light_native_pilot_owner as owner
        from ops.native_maintenance_run_guard import API
        api=API(token)
        if action=='prepare-continuation':return prepare_continuation(package,payload,agreement,run_guard,psycopg,parameters,credential,intake,api,owner)
        if action=='authorize':return authorize(payload,payload_raw,agreement,run_guard,psycopg,parameters,credential,intake,api,owner)
        if action=='inspect-zero-submit':return inspect_zero_submit(payload,run_guard,psycopg,parameters,credential,intake)
        if action in ('restore-zero-submit','restore-unreserved'):return restore_zero_submit(payload,payload_raw,run_guard,psycopg,parameters,credential,intake)
        if action=='terminal':return terminal(payload,run_guard,psycopg,parameters,credential,intake,owner,api)
        return restore_controls(payload,payload_raw,run_guard,psycopg,parameters,credential,intake)
