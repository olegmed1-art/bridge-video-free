"""Restore exact intake configuration after proven zero-submit and restored HOLD."""
import json
from pathlib import Path
from ops import light_native_service_controller as control
from ops import light_native_service_switch as switch

REQUEST='c12ef4667a3a620d6db2f876393545333fb772af36196970193fcb7d4a94c314'
INTAKE='92c4e1386cc88a5aa180356084520be1905a5fc1642f709b7b6a77c51ed517e3'
PLAN='3af45c61ece27fa57f6ebf3f26c304e46667d3585ce732392989143850ff06ab'
DISPATCH='9289ad56-f0aa-4683-be0c-101ec820d412'
TASK='d8595f4c-4c02-43d0-9de7-9f377a7aa0c4'
WORK='6bda2dca-d0a1-4783-bbb2-d4006ed3e536'
PAYLOAD='1c98d4830d058bb5f1d39815590453c2df2125d53f41394298b413bd6f09209d'


def check_rows(config,role,receipt,counts,task,outbox,goal_digest):
    switch.require(config==receipt['applied_config'] and role==receipt['applied_role'], 'ZERO_SUBMIT_CONFIG_DRIFT')
    switch.require(tuple(counts)==(0,1,1,0,1), 'ZERO_SUBMIT_COMPETITOR')
    switch.require(task['task_id']==TASK and task['status']=='WAITING_EXTERNAL'
        and goal_digest==receipt['goal_json_sha256'], 'ZERO_SUBMIT_TASK_DRIFT')
    switch.require(outbox['dispatch_id']==DISPATCH and outbox['task_id']==TASK
        and outbox['status']=='PUBLISHED' and outbox['mode']=='READ_ONLY'
        and outbox['claim_owner'] is None and outbox['delivery_contract_version']==3
        and outbox['expected_head_sha']==receipt['dispatch']['expected_head_sha']
        and outbox['target_pr']==receipt['dispatch']['target_pr'], 'ZERO_SUBMIT_OUTBOX_DRIFT')


def reconcile(package,payload,accepted,wheels,credential,token,guard):
    switch.require(accepted==PAYLOAD and control.digest(payload)==PAYLOAD,'ZERO_SUBMIT_PAYLOAD')
    request,prior,protected,protected_digest,directory=control.ledger(REQUEST)
    root=control.plan.ROOT/'intake'
    raw=control.read(root/'intake.json')
    switch.require(control.digest(raw)==INTAKE,'ZERO_SUBMIT_INTAKE')
    receipt=control.strict_json(raw,262144)
    switch.require(receipt['plan_sha256']==PLAN and receipt['dispatch_id']==DISPATCH
        and receipt['task_id']==TASK and receipt['work_item_id']==WORK,'ZERO_SUBMIT_IDENTITY')
    def host_guard():
        guard.assert_running()
        request.agreement()
        switch.require(control.restored_receipt(request,prior,protected,protected_digest,directory) is not None,'ZERO_SUBMIT_HOLD')
        switch.unit_absent(control.plan.PILOT_UNIT)
        state=switch.show(control.plan.SUPERVISOR_UNIT,['MainPID','ControlPID','ActiveState'])
        switch.require(state=={'MainPID':'0','ControlPID':'0','ActiveState':'failed'},'ZERO_SUBMIT_SUPERVISOR')
        journal=control.plan.LIGHT/'runtime/codex-dispatch'/(DISPATCH+'.json')
        switch.require(not journal.exists() and not journal.is_symlink(),'ZERO_SUBMIT_PROVIDER_JOURNAL')
    host_guard()
    intent=directory/'zero-submit-controls-intent.json'
    switch.require(not intent.exists() and not intent.is_symlink(),'ZERO_SUBMIT_INTENT_EXISTS')
    from ops.native_maintenance_owner_host import loaded_runtime
    from ops.native_maintenance_owner_attest import parameters
    with loaded_runtime(wheels) as (psycopg,_):
        from database import light_native_pilot_intake as intake
        before=intake.engine.load_manifest(root/'before.json',receipt['snapshot_sha256'])
        switch.require(before['version']==1 and before['plan_sha256']==PLAN
            and before['target']==intake.EXPECTED_TARGET
            and before['native_config']['enabled'] is False,'ZERO_SUBMIT_SNAPSHOT')
        control.retained(intent,payload)
        with psycopg.connect(**parameters(credential),autocommit=True) as conn:
            conn.read_only=False
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
                conn.execute("SET LOCAL statement_timeout='5s'")
                conn.execute("SET LOCAL lock_timeout='5s'")
                intake.engine.identity(conn,intake.target())
                config=intake.one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton FOR UPDATE')
                role=intake.one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT' FOR UPDATE")
                task=intake.one(conn,'SELECT to_jsonb(t) FROM autopilot.task t WHERE task_id=%s::uuid FOR UPDATE',(TASK,))
                outbox=intake.one(conn,'SELECT to_jsonb(o) FROM autopilot.role_dispatch_outbox o WHERE dispatch_id=%s::uuid FOR UPDATE',(DISPATCH,))
                work=intake.one(conn,'SELECT to_jsonb(w) FROM autopilot.project_work_item w WHERE work_item_id=%s::uuid FOR UPDATE',(WORK,))
                switch.require(work['state']=='ACTIVE' and work['last_task_id']==TASK,'ZERO_SUBMIT_WORK_DRIFT')
                counts=conn.execute("SELECT (SELECT count(*) FROM autopilot.native_cli_receipt), (SELECT count(*) FROM autopilot.task WHERE status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')), (SELECT count(*) FROM autopilot.project_work_task WHERE work_item_id=%s::uuid), (SELECT count(*) FROM autopilot.task WHERE goal_json->>'origin_task_id'=%s), (SELECT count(*) FROM autopilot.role_dispatch_outbox WHERE task_id=%s::uuid)",(WORK,TASK,TASK)).fetchone()
                goal_digest=control.digest(json.dumps(task['goal_json'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
                check_rows(config,role,receipt,counts,task,outbox,goal_digest)
                host_guard()
                changed_config=conn.execute('UPDATE autopilot.native_cli_config SET enabled=%s,cutover_at=%s WHERE singleton',(before['native_config']['enabled'],before['native_config']['cutover_at']))
                changed_role=conn.execute("UPDATE autopilot.role_registry SET can_repair=%s WHERE role_id='AUTOPILOT'",(before['autopilot_role']['can_repair'],))
                switch.require(changed_config.rowcount==changed_role.rowcount==1,'ZERO_SUBMIT_UPDATE_COUNT')
                restored_config=intake.one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')
                restored_role=intake.one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT'")
                switch.require(restored_config==before['native_config'] and all(restored_role.get(k)==v for k,v in before['autopilot_role'].items() if k!='updated_at'),'ZERO_SUBMIT_TRANSACTION_READBACK')
                host_guard()
        with psycopg.connect(**parameters(credential),autocommit=True) as conn:
            conn.read_only=True
            intake.engine.identity(conn,intake.target())
            config=intake.one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')
            role=intake.one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT'")
            switch.require(config==before['native_config'] and all(role.get(k)==v for k,v in before['autopilot_role'].items() if k!='updated_at'),'ZERO_SUBMIT_COMMIT_READBACK')
            switch.require(intake.one(conn,'SELECT to_jsonb(t) FROM autopilot.task t WHERE task_id=%s::uuid',(TASK,))==task
                and intake.one(conn,'SELECT to_jsonb(o) FROM autopilot.role_dispatch_outbox o WHERE dispatch_id=%s::uuid',(DISPATCH,))==outbox
                and intake.one(conn,'SELECT to_jsonb(w) FROM autopilot.project_work_item w WHERE work_item_id=%s::uuid',(WORK,))==work
                and conn.execute('SELECT count(*) FROM autopilot.native_cli_receipt').fetchone()==(0,)
                and conn.execute("SELECT (SELECT count(*) FROM autopilot.project_work_task WHERE work_item_id=%s::uuid),(SELECT count(*) FROM autopilot.task WHERE goal_json->>'origin_task_id'=%s)",(WORK,TASK)).fetchone()==(1,0),'ZERO_SUBMIT_POSTCOMMIT_TASK')
        host_guard()
        result=dict(audit='LIGHT_ZERO_SUBMIT_CONTROLS_RESTORED',request_sha256=REQUEST,intake_sha256=INTAKE,
            snapshot_sha256=receipt['snapshot_sha256'],native_enabled=config['enabled'],can_repair=role['can_repair'],
            task_preserved=True,outbox_preserved=True,native_receipts=0,pilot_resubmitted=False)
        control.retained(directory/'zero-submit-controls-restored.json',control.canonical(result))
        return result
