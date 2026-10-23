"""Fixed immutable staging with one retained zero-submit task under HOLD."""
import base64
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
from ops import light_native_service_controller as control
from ops import light_native_service_switch as switch
from ops import light_native_pilot_release as release
from ops import oracle_light_active_hold_attest as hold
from ops.native_maintenance_agreement import Agreement

SOURCE='1269ad0f0f77efc65749bd194a0c0bcbda168ee4'
PACKAGE='092d6b1a52a0c554d188fce594e441a5303e34592b85b30c00a0613e8123171b'
OLD_PACKAGE='46acb2672ba58dc369c3ccf70f45bb4beff37cb4efad2bfe3b34ae4a170c0c94'
REQUEST='c12ef4667a3a620d6db2f876393545333fb772af36196970193fcb7d4a94c314'
INTAKE='92c4e1386cc88a5aa180356084520be1905a5fc1642f709b7b6a77c51ed517e3'
SNAPSHOT='3911c8aeee1264331055ccfe66865fa77551be32d887f10a4b5867c0de75ee78'
DISPATCH='9289ad56-f0aa-4683-be0c-101ec820d412'
TASK='d8595f4c-4c02-43d0-9de7-9f377a7aa0c4'
WORK='6bda2dca-d0a1-4783-bbb2-d4006ed3e536'
SWITCH_SHA='300aeaf2765f29a25ae9279001bd8d07bcf9daae0034c2606da45badc8800538'
SCOPE=dict(action='stage-fixed-owned-task',source=SOURCE,package_sha256=PACKAGE,
           prior_request_sha256=REQUEST,dispatch_id=DISPATCH)


def validate_packages(old_raw,new_raw):
    switch.require(control.digest(old_raw)==OLD_PACKAGE and control.digest(new_raw)==PACKAGE,'FIXED_STAGE_PACKAGE')
    old=control.strict_json(old_raw,16*1024*1024)
    new=control.strict_json(new_raw,16*1024*1024)
    switch.require(new['source']==SOURCE and new['version']==1
        and set(new)==set(old) and set(new['helpers'])==set(old['helpers']),'FIXED_STAGE_CLOSURE')
    changed=[name for name in new['helpers'] if new['helpers'][name]!=old['helpers'][name]]
    switch.require(changed==['ops/light_native_service_switch.py']
        and control.digest(new['helpers'][changed[0]].encode())==SWITCH_SHA,'FIXED_STAGE_HELPER_DIFF')
    old_files=old['runtime']['files'];new_files=new['runtime']['files']
    switch.require(set(old_files)==set(new_files)
        and [name for name in new_files if new_files[name]!=old_files[name]]==changed
        and all(package['runtime']['files'][changed[0]]==package['helpers'][changed[0]] for package in (old,new)),
        'FIXED_STAGE_RUNTIME_DIFF')
    release.validate(new['runtime'],SOURCE,new['runtime']['sha256'])
    return new


def stage_files(package,guard,provenance):
    before=guard()
    directory=release.ROOT/SOURCE
    switch.require(not directory.exists() and not directory.is_symlink(),'FIXED_STAGE_EXISTS')
    release.private_directory(release.ROOT)
    directory.mkdir(mode=0o700);release.staging.fsync_directory(release.ROOT)
    backup=dict(version=1,source=SOURCE,bundle_sha256=package['runtime']['sha256'],hold=asdict(before),
        unit=base64.b64encode(hold.read(hold.BASE,0o644,65536)).decode(),
        drop=base64.b64encode(hold.read(hold.DROP,0o644,4096)).decode())
    release.retain(directory/'before.json',release.encoded(backup))
    release.retain(directory/'owned-task-stage.json',release.encoded(provenance))
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        observed=json.loads(hold.read(directory/'before.json',0o600,262144))
        for name in ('unit','drop'):
            data=base64.b64decode(observed[name],validate=True)
            path=Path(temporary)/name;release.retain(path,data)
            switch.require(path.read_bytes()==base64.b64decode(backup[name]),'FIXED_STAGE_BACKUP_READBACK')
    switch.require(guard()==before,'FIXED_STAGE_HOLD_CHANGED')
    candidate=release.staging.stage(package['runtime'])
    release.probe(candidate)
    environment=release.probe_environment(candidate)
    release.retain(directory/'environment.json',release.encoded(environment))
    release.staging.verify_release(candidate,package['runtime'])
    switch.require(guard()==before,'FIXED_STAGE_HOLD_CHANGED')
    release.retain(directory/'staged.json',release.encoded(dict(version=1,source=SOURCE,
        bundle_sha256=package['runtime']['sha256'],hold=asdict(before),readonly_probe=True)))
    return dict(audit='LIGHT_FIXED_RELEASE_STAGED_OWNED_TASK',source=SOURCE,
        package_sha256=PACKAGE,bundle_sha256=package['runtime']['sha256'],
        queue_zero_reobserved=False,owned_dispatch_id=DISPATCH,hold_unchanged=True,
        service_restarted=False,production_sql_mutations=False,pilot_submitted=False)


def reconcile(old_raw,payload_raw,accepted,wheels,credential,token,run_guard,new_raw):
    package=validate_packages(old_raw,new_raw)
    switch.require(control.digest(payload_raw)==accepted,'FIXED_STAGE_PAYLOAD')
    payload=control.strict_json(payload_raw,4096)
    switch.require(set(payload)=={'scope','agreement','accepted_agreement_sha256'}
        and payload['scope']==SCOPE,'FIXED_STAGE_SCOPE')
    agreement=Agreement(payload['agreement'],payload['accepted_agreement_sha256'],SCOPE)
    request,prior,protected,protected_digest,old_directory=control.ledger(REQUEST)
    root=control.plan.ROOT/'intake'
    receipt_raw=control.read(root/'intake.json')
    switch.require(control.digest(receipt_raw)==INTAKE,'FIXED_STAGE_INTAKE')
    receipt=control.strict_json(receipt_raw,262144)
    switch.require(receipt['snapshot_sha256']==SNAPSHOT and receipt['dispatch_id']==DISPATCH
        and receipt['task_id']==TASK and receipt['work_item_id']==WORK,'FIXED_STAGE_IDENTITY')
    expected=dict(audit='LIGHT_ZERO_SUBMIT_CONTROLS_RESTORED',request_sha256=REQUEST,
        intake_sha256=INTAKE,snapshot_sha256=SNAPSHOT,native_enabled=False,can_repair=True,
        task_preserved=True,outbox_preserved=True,native_receipts=0,pilot_resubmitted=False)
    switch.require(control.strict_json(control.read(old_directory/'zero-submit-controls-restored.json'),4096)==expected,'FIXED_STAGE_RESTORE_PROOF')
    from ops.native_maintenance_owner_host import loaded_runtime
    with loaded_runtime(wheels) as (psycopg,_):
        from database import light_native_pilot_intake as intake
        from ops.native_maintenance_owner_attest import parameters
        before=intake.engine.load_manifest(root/'before.json',SNAPSHOT)
        def guard():
            run_guard.assert_running();agreement.assert_held(control.digest(control.canonical(SCOPE)))
            release.staging.require_current_main(SOURCE)
            switch.require(control.restored_receipt(request,prior,protected,protected_digest,old_directory) is not None,'FIXED_STAGE_HOLD')
            switch.unit_absent(control.plan.PILOT_UNIT)
            switch.require(switch.show(control.plan.SUPERVISOR_UNIT,['MainPID','ControlPID','ActiveState'])
                =={'MainPID':'0','ControlPID':'0','ActiveState':'failed'},'FIXED_STAGE_SUPERVISOR')
            journal=control.plan.LIGHT/'runtime/codex-dispatch'/(DISPATCH+'.json')
            switch.require(not journal.exists() and not journal.is_symlink(),'FIXED_STAGE_PROVIDER_JOURNAL')
            with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                conn.read_only=True
                with conn.transaction():
                    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    conn.execute("SET LOCAL statement_timeout='5s'")
                    intake.engine.identity(conn,intake.target())
                    config=intake.one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')
                    role=intake.one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT'")
                    task=intake.one(conn,'SELECT to_jsonb(t) FROM autopilot.task t WHERE task_id=%s::uuid',(TASK,))
                    outbox=intake.one(conn,'SELECT to_jsonb(o) FROM autopilot.role_dispatch_outbox o WHERE dispatch_id=%s::uuid',(DISPATCH,))
                    work=intake.one(conn,'SELECT to_jsonb(w) FROM autopilot.project_work_item w WHERE work_item_id=%s::uuid',(WORK,))
                    counts=conn.execute("SELECT (SELECT count(*) FROM autopilot.native_cli_receipt),(SELECT count(*) FROM autopilot.task WHERE status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')),(SELECT count(*) FROM autopilot.project_work_task WHERE work_item_id=%s::uuid),(SELECT count(*) FROM autopilot.task WHERE goal_json->>'origin_task_id'=%s),(SELECT count(*) FROM autopilot.role_dispatch_outbox WHERE task_id=%s::uuid)",(WORK,TASK,TASK)).fetchone()
                    switch.require(config==before['native_config'] and all(role.get(k)==v for k,v in before['autopilot_role'].items() if k!='updated_at') and counts==(0,1,1,0,1),'FIXED_STAGE_DB_CONFIG')
                    switch.require(task['status']=='WAITING_EXTERNAL' and task['task_id']==TASK
                        and control.digest(json.dumps(task['goal_json'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())==receipt['goal_json_sha256']
                        and work['state']=='ACTIVE' and work['last_task_id']==TASK,'FIXED_STAGE_TASK')
                    switch.require(outbox['task_id']==TASK and outbox['status']=='PUBLISHED'
                        and outbox['mode']=='READ_ONLY' and outbox['claim_owner'] is None
                        and outbox['delivery_contract_version']==3
                        and outbox['expected_head_sha']==receipt['dispatch']['expected_head_sha']
                        and outbox['target_pr']==receipt['dispatch']['target_pr'],'FIXED_STAGE_OUTBOX')
            agreement.assert_held(control.digest(control.canonical(SCOPE)))
            return hold.service_hold_identity()
        # Verify real restricted worker login/binding and existing Codex login before writes.
        guard();release.probe(control.plan.source_path(request.value['source']))
        return stage_files(package,guard,dict(version=1,kind='OWNED_TASK_STAGE_EXCEPTION',
            scope=SCOPE,agreement_sha256=agreement.accepted,intake_sha256=INTAKE,
            snapshot_sha256=SNAPSHOT,queue_zero_reobserved=False,
            identity_basis='service_hold_identity plus exact owned-task readonly DB proof'))
