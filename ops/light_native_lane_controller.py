"""Fixed owner orchestration of one first-lane task, with retained phase evidence.

Runtime f82 and controller main are distinct accepted identities. Preparation,
publication and permit review precede execution; no previous pilot ledger is
reused. Imports needed by ExecStopPost remain stdlib-only.
"""
import base64
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import time

from ops import light_native_lane_execution as execution
from ops import light_native_lane_install as install
from ops import light_native_pilot_release as release
from ops import light_native_service_switch as switch

ROOT=Path('/var/lib/bridge-light-native-lane-owner')
FIRST_EXECUTION_SECONDS=420
EXTRA=('ops/light_native_lane_controller.py','ops/light_native_lane_execution.py',
       'ops/light_native_lane_feed.py','ops/light_native_lane_owner.py',
       'ops/light_native_lane_run_guard.py','database/__init__.py',
       'database/light_native_pilot_intake.py','database/native_cli_permission_engine.py')
require=release.require
encoded=execution.encoded
parse=execution.parse
sha=lambda raw:hashlib.sha256(raw).hexdigest()


def package(repo,source):
    names=tuple(dict.fromkeys((*release.HELPERS,*EXTRA)))
    helpers={name:('' if name.endswith('/__init__.py') else
        release.source.git(repo,'show',source+':'+name).decode()) for name in names}
    return encoded(dict(version=1,kind='LIGHT_LANE_CONTROLLER',source=source,helpers=helpers))


def validate_package(raw,source,accepted):
    require(type(raw) is bytes and len(raw)<3*1024*1024 and sha(raw)==accepted
            and release.source.identifier(source,40),'LANE_OWNER_PACKAGE')
    value=parse(raw)
    require(set(value)=={'version','kind','source','helpers'} and value['version']==1
            and value['kind']=='LIGHT_LANE_CONTROLLER' and value['source']==source
            and set(value['helpers'])==set((*release.HELPERS,*EXTRA))
            and all(type(v) is str for v in value['helpers'].values()),'LANE_OWNER_PACKAGE')
    return value


def read(path,accepted=None,limit=262144):
    install.root_parent(path.parent)
    require(path.lstat().st_nlink==1,'LANE_OWNER_RECORD_LINK')
    raw=install.hold.read(path,0o600,limit)
    require(accepted is None or sha(raw)==accepted,'LANE_OWNER_RECORD_NOT_ACCEPTED')
    return raw


def retain(path,raw):
    install.write_new(path,raw,0o600)


def helpers_root(source):
    require(release.source.identifier(source,40),'LANE_OWNER_SOURCE')
    return ROOT/'controllers'/source


def bootstrap_helpers(controller):
    """Create-only immutable helper tree; no service can modify or read it."""
    root=helpers_root(controller['source'])
    install.fresh_directory(root,0o700)
    for name in ('ops','database'):install.fresh_directory(root/name,0o700)
    for name,text in controller['helpers'].items():retain(root/name,text.encode())
    retain(root/'package.json',encoded(controller))
    return root


def verify_helpers(source,accepted):
    root=helpers_root(source)
    controller=validate_package(read(root/'package.json',accepted,3*1024*1024),source,accepted)
    for name,text in controller['helpers'].items():
        require(read(root/name,None,3*1024*1024)==text.encode(),'LANE_OWNER_HELPER_CHANGED')
    return root


def scope(payload):
    from database.light_native_pilot_intake import Plan
    raw=base64.b64decode(payload['plan_base64'],validate=True)
    plan=Plan(raw,payload['accepted_plan_sha256'])
    require(plan.value['source']==install.RETAINED_SOURCE,'LANE_OWNER_RUNTIME')
    return plan,ROOT/plan.digest


def guard(plan,prior,payload,run_guard,agreement=None):
    run_guard.assert_running()
    release.staging.require_current_main(payload['source'])
    require(asdict(install.hold.service_hold_identity())==asdict(prior),'LANE_OWNER_LEGACY_CHANGED')
    if agreement is not None:agreement.assert_held(plan.scope_digest)


def supervisor_source(source):
    return ("import sys\ntry:\n sys.path.insert(0,%r)\n sys.path.append(%r)\n"
            " from ops.light_native_lane_controller import supervisor\n supervisor(sys.argv[1],sys.argv[2])\n"
            "except BaseException:\n print('{\"audit\":\"LIGHT_LANE_SUPERVISOR_REFUSED\"}',flush=True)\n"
            " raise SystemExit(2) from None\n" %
            (str(helpers_root(source)),str(execution.plan.source_path(install.RETAINED_SOURCE)))).encode()


def phase(wheels,credential,token,controller_raw,retained_raw,payload_raw,accepted_payload,run_guard):
    """One authenticated phase; its accepted payload contains no credentials."""
    require(os.geteuid()==0 and os.uname().nodename=='autopilot-lite-vnic'
            and sha(payload_raw)==accepted_payload,'LANE_OWNER_AUTHORITY')
    value=parse(payload_raw)
    keys={'version','action','source','accepted_controller_sha256','accepted_runtime_sha256',
          'plan_base64','accepted_plan_sha256','agreement','accepted_agreement_sha256',
          'accepted_receipt_sha256','accepted_discovery_sha256','accepted_permit_sha256',
          'accepted_terminal_sha256'}
    require(set(value)==keys and value['version']==1 and value['action'] in
            ('prepare','publish','permit','execute','terminal','restore','contain'),'LANE_OWNER_PHASE')
    run_guard.assert_current()
    controller=validate_package(controller_raw,value['source'],value['accepted_controller_sha256'])
    require(sha(retained_raw)==value['accepted_runtime_sha256'],'LANE_OWNER_RUNTIME_PACKAGE')
    # Historical release packages use ASCII-escaped canonical JSON. Preserve
    # their exact accepted bytes; the lane record codec uses UTF-8 instead.
    retained=json.loads(retained_raw)
    require(type(retained) is dict and release.encoded(retained)==retained_raw,
            'LANE_OWNER_RUNTIME_PACKAGE_ENCODING')
    require(retained['source']==install.RETAINED_SOURCE,'LANE_OWNER_RUNTIME_PACKAGE')
    release.validate(retained['runtime'],retained['source'],retained['runtime']['sha256'])
    candidate=execution.plan.source_path(retained['source'])
    release.staging.verify_release(candidate,retained['runtime'])
    from ops.native_maintenance_owner_host import loaded_runtime
    from ops.native_maintenance_agreement import Agreement
    from ops import light_native_pilot_owner as owner
    from ops.native_maintenance_run_guard import API
    with loaded_runtime(wheels) as (psycopg,_):
        from ops.native_maintenance_owner_attest import parameters
        from database import light_native_pilot_intake as intake
        from oracle_autopilot import light_native_preflight as preflight
        plan,directory=scope(value)
        if value['action']=='prepare':
            agreement=Agreement(value['agreement'],value['accepted_agreement_sha256'],plan.scope)
            prior=install.hold.attest()
            install.verify_running(retained['source'],pwd.getpwnam('school-autopilot'))
            guard(plan,prior,value,run_guard,agreement)
            pr=owner.observed_target(API(token),plan)
            # These shared parents are fixed, root-only and not user data.
            for path in (ROOT,ROOT/'controllers'):
                if not path.exists():install.fresh_directory(path,0o700)
                install.root_parent(path)
            bootstrap_helpers(controller)
            install.fresh_directory(directory,0o700)
            protected=switch.protect_snapshot(prior)
            baseline=encoded(dict(version=1,source=retained['source'],
                package_sha256=value['accepted_runtime_sha256'],agreement_sha256=value['accepted_agreement_sha256'],
                scope_sha256=plan.scope_digest,prior=asdict(prior),protected=protected,
                protected_sha256=sha(release.encoded(protected)),observed_at=int(time.time())))
            for name,raw in (('plan.json',plan.raw),('baseline.json',baseline),('prepare.json',payload_raw),
                             ('wheels.tar',wheels),('runtime-package.json',retained_raw)):
                retain(directory/name,raw)
            def intake_guard():
                require(not (directory/'contain-intent.json').exists(),'LANE_OWNER_CONTAINMENT_PENDING')
                run_guard.assert_running()
            with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                conn.read_only=False
                intake.engine.privileges(conn,intake.target(),True)
                verify_terminal_policy(conn)
                receipt=intake.prepare(conn,plan,agreement,baseline,sha(baseline),directory/'before.json',
                    target_open=True,observed_head_sha=pr['head']['sha'],observed_branch=pr['head']['ref'],
                    effect_guard=intake_guard,
                    durable_receipt=lambda receipt:retain(directory/'intake.json',encoded(receipt)))
            remember(directory/'intake.json',encoded(receipt))
            return dict(audit='LIGHT_LANE_OWNER',phase='prepare',record_sha256=sha(encoded(receipt)),
                        dispatch_id=receipt['dispatch_id'],task_id=receipt['task_id'],work_item_id=receipt['work_item_id'])
        require(read(directory/'plan.json')==plan.raw,'LANE_OWNER_PLAN_CHANGED')
        original=parse(read(directory/'prepare.json'))
        verify_helpers(original['source'],original['accepted_controller_sha256'])
        stable=('accepted_runtime_sha256','plan_base64','accepted_plan_sha256','agreement','accepted_agreement_sha256')
        if value['action'] not in ('terminal','restore','contain'):
            stable+=('source','accepted_controller_sha256')
        # Cleanup may use a NEW authenticated workflow/current controller after
        # main advances; retained runtime, plan and original helper evidence stay bound.
        require(all(value[k]==original[k] for k in stable),'LANE_OWNER_SCOPE_CHANGED')
        baseline=parse(read(directory/'baseline.json'))
        prior=install.hold.HoldIdentity(**baseline['prior'])
        if value['action']=='contain':
            guard(plan,prior,value,run_guard)
            switch.unchanged_files(baseline['protected'],prior,baseline['protected_sha256'])
            return contain(psycopg,parameters(credential),plan,directory,value,run_guard)
        receipt_raw=read(directory/'intake.json',value['accepted_receipt_sha256'])
        receipt=parse(receipt_raw)
        require(receipt['plan_sha256']==plan.digest,'LANE_OWNER_RECEIPT')
        guard(plan,prior,value,run_guard)
        switch.unchanged_files(baseline['protected'],prior,baseline['protected_sha256'])
        if value['action'] in ('terminal','restore'):
            return finish(psycopg,parameters(credential),plan,receipt,directory,value,run_guard)
        require(not (directory/'contain-intent.json').exists(),'LANE_OWNER_CONTAINMENT_PENDING')
        agreement=Agreement(value['agreement'],value['accepted_agreement_sha256'],plan.scope)
        guard(plan,prior,value,run_guard,agreement)
        owner.observed_target(API(token),plan)
        if value['action']=='publish':
            with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                conn.read_only=True
                committed_intake(conn,plan,receipt)
            retain(directory/'publication-intent.json',payload_raw)
            run_guard.assert_running()
            published=owner.broker_publish(candidate,prior,receipt)
            retain(directory/'broker.json',encoded(published))
            discovered=owner.discovery(API(token),published,receipt)
            retain(directory/'discovery.json',encoded(discovered))
            return dict(audit='LIGHT_LANE_OWNER',phase='publish',record_sha256=sha(encoded(discovered)),
                        dispatch_id=receipt['dispatch_id'])
        discovered=parse(read(directory/'discovery.json',value['accepted_discovery_sha256']))
        published=parse(read(directory/'broker.json'))
        require(owner.discovery(API(token),published,receipt)==discovered,'LANE_OWNER_PUBLICATION_CHANGED')
        if value['action']=='permit':
            retain(directory/'permit-intent.json',payload_raw)
            with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                conn.read_only=False
                marked=intake.mark_reviewed_publication(conn,plan,agreement,receipt,encoded(discovered),
                    value['accepted_discovery_sha256'],effect_guard=run_guard.assert_running)
            retain(directory/'publication.json',encoded(marked))
            dispatch={k:receipt['dispatch'][k] for k in
                ('dispatch_id','expected_head_sha','mode','target_pr','task_fingerprint')}
            dispatch.update(branch=plan.value['branch'],assignment=receipt['assignment'])
            issued=int(time.time());expires=int(agreement.end)
            with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                conn.read_only=True
                evidence=preflight.observe(conn,dispatch,issued_at=issued,expires_at=expires,
                    agreement_sha256=value['accepted_agreement_sha256'])
            require(evidence['goal_json_sha256']==receipt['goal_json_sha256'],'LANE_OWNER_GOAL_CHANGED')
            cloud=read(release.ROOT/retained['source']/'environment.json',None,4096)
            permit=encoded(dict(version=1,source=retained['source'],dispatch=dispatch,
                environment_id=release.CLOUD_ENVIRONMENT_ID,environment_evidence_sha256=sha(cloud),
                issued_at=issued,expires_at=expires,owner_preflight=evidence))
            retain(directory/'permit.json',permit)
            return dict(audit='LIGHT_LANE_OWNER',phase='permit',record_sha256=sha(permit),
                        dispatch_id=receipt['dispatch_id'],expires_at=expires)
        permit=read(directory/'permit.json',value['accepted_permit_sha256'],65536)
        require(parse(permit)['expires_at']-time.time() >= 600,'LANE_OWNER_EXECUTION_WINDOW')
        from ops.light_native_lane_feed import publish_first
        guard(plan,prior,value,run_guard,agreement)
        with psycopg.connect(**parameters(credential),autocommit=True) as conn:
            conn.read_only=True
            result=publish_first(conn,retained_raw,value['accepted_runtime_sha256'],permit,
                value['accepted_permit_sha256'],value['source'],lambda n:API(token).get('/pulls/'+str(n)),
                plan.raw,plan.digest,receipt_raw,value['accepted_receipt_sha256'])
        launch_args=(wheels,credential,token,plan,directory,value,prior,baseline,result,run_guard)
    # Release the owner-driver flock before the PID1 supervisor acquires it.
    return launch(*launch_args)


def launch(wheels,credential,token,plan,directory,value,prior,baseline,feed_result,run_guard):
    remaining=FIRST_EXECUTION_SECONDS
    require(parse(read(directory/'permit.json'))['expires_at']-time.time() >= remaining+30,
            'LANE_OWNER_EXECUTION_WINDOW')
    script=supervisor_source(value['source'])
    context=encoded(dict(owner_plan_sha256=plan.digest,controller_sha256=value['accepted_controller_sha256'],
        wheels_sha256=sha(wheels),run_id=run_guard.run_id,attempt=run_guard.attempt))
    request=encoded(dict(version=1,source=install.RETAINED_SOURCE,controller_source=value['source'],
        seconds=remaining,expires_at=int(time.time())+remaining,prior=asdict(prior),
        protected=baseline['protected'],protected_sha256=baseline['protected_sha256'],
        cursor_sha256=feed_result['cursor_sha256'],dispatch_id=feed_result['dispatch_id'],
        original=install.verify_hold_process(install.RETAINED_SOURCE,pwd.getpwnam('school-autopilot')),
        supervisor_sha256=sha(script),owner_context_sha256=sha(context)))
    digest=sha(request)
    if not execution.ROOT.exists():install.fresh_directory(execution.ROOT,0o700)
    target=execution.ROOT/digest
    install.fresh_directory(target,0o700)
    for name,raw in (('request.json',request),('supervisor.py',script),('owner-context.json',context)):
        retain(target/name,raw)
    retain(directory/'execution.json',encoded(dict(request_sha256=digest)))
    run_guard.assert_running()
    command=execution.supervisor_command(digest,remaining)
    command[1:1]=['--wait','--pipe']
    wire=encoded(dict(credential=credential,token=token))
    result=subprocess.run(command,input=wire,capture_output=True,timeout=remaining+150,
                          env={'PATH':'/usr/bin:/bin'})
    # Lost exit status is not permission to retry. The root receipt decides what
    # the independent cleanup actually achieved, not stdout from the workload.
    stopped=parse(read(target/'stopped-hold.json'))
    require(stopped['state']=='STOPPED_HOLD' and result.returncode==0,'LANE_OWNER_EXECUTION_RECONCILE')
    return dict(audit='LIGHT_LANE_OWNER',phase='execute',state='STOPPED_HOLD',
                request_sha256=digest,terminal_verified=False)


def supervisor(mode,digest):
    if mode=='restore':
        print(json.dumps(execution.restore(digest),sort_keys=True));return
    require(mode=='run','LANE_OWNER_SUPERVISOR_MODE')
    value,prior,target=execution.request(digest)
    context=parse(read(target/'owner-context.json',value['owner_context_sha256']))
    verify_helpers(value['controller_source'],context['controller_sha256'])
    require(set(context)=={'owner_plan_sha256','controller_sha256','wheels_sha256','run_id','attempt'}
            and all(release.source.identifier(context[k],64) for k in
                ('owner_plan_sha256','controller_sha256','wheels_sha256'))
            and type(context['run_id']) is int and context['run_id']>0
            and type(context['attempt']) is int and context['attempt']>0,'LANE_OWNER_CONTEXT')
    owner_directory=ROOT/context['owner_plan_sha256']
    wire=parse(sys.stdin.buffer.read(65537))
    require(set(wire)=={'credential','token'},'LANE_OWNER_SUPERVISOR_INPUT')
    wheels=read(owner_directory/'wheels.tar',context['wheels_sha256'],20*1024*1024)
    from ops.native_maintenance_owner_host import loaded_runtime
    from ops.light_native_lane_run_guard import authenticated
    from ops.light_native_pilot_owner import observed_target
    from ops.native_maintenance_run_guard import API
    with loaded_runtime(wheels) as (psycopg,_):
        from ops.native_maintenance_owner_attest import parameters
        from oracle_autopilot import light_native_preflight as preflight
        from database.light_native_pilot_intake import Plan
        plan=Plan(read(owner_directory/'plan.json'),context['owner_plan_sha256'])
        guard=authenticated(value['controller_source'],context['run_id'],context['attempt'],wire['token'])
        def fresh(permit):
            guard.assert_running()
            observed_target(API(wire['token']),plan)
            item=permit.value
            with psycopg.connect(**parameters(wire['credential']),autocommit=True) as conn:
                conn.read_only=True
                evidence=preflight.observe(conn,item['dispatch'],issued_at=item['issued_at'],
                    expires_at=item['expires_at'],agreement_sha256=item['owner_preflight']['agreement_sha256'])
            require(evidence==item['owner_preflight'],'LANE_OWNER_PREFLIGHT_CHANGED')
            return True
        result=execution.run_once(digest,fresh,live_guard=guard.assert_running)
        retain(target/'outcome.json',encoded(result))
        print(json.dumps(dict(audit='LIGHT_LANE_EXECUTED',**result),sort_keys=True))


def finish(psycopg,parameters,plan,receipt,directory,value,run_guard):
    """Terminal verification is independent of service markers and permit time."""
    from ops.light_native_lane_owner import retain_acceptance
    from database import light_native_pilot_intake as intake
    request_digest=parse(read(directory/'execution.json'))['request_sha256']
    restarting=(directory/'restart-intent.json').exists()
    if not restarting:
        stopped=execution.restore(request_digest)
        require(stopped['state']=='STOPPED_HOLD','LANE_OWNER_NOT_STOPPED')
    else:
        # An acknowledged DB restore may precede a lost systemctl response.
        # Do not call the old-invocation cleanup on a new, credential-free HOLD.
        require(value['action']=='restore','LANE_OWNER_RESTART_RECONCILE')
        execution.admission(b'HOLD\n')
        execution.stopped()
    if value['action']=='terminal':
        with psycopg.connect(**parameters,autocommit=True) as conn:
            conn.read_only=True
            accepted=retain_acceptance(conn,plan,receipt,0)
            raw=install.STATE/'00000000-terminal.json'
            # retain_acceptance has just validated metadata and these exact
            # private service records against fresh Cloud and DB evidence.
            terminal=json.loads(raw.read_bytes())
            envelope=encoded(dict(version=1,plan_sha256=plan.digest,dispatch_id=receipt['dispatch_id'],
                task_id=receipt['task_id'],provider_task_id=terminal['result']['provider_task_id'],
                request=terminal['request'],result=terminal['result']['terminal']))
            intake.observe_terminal(conn,plan,receipt,envelope,sha(envelope))
        retain(directory/'terminal.json',envelope)
        return dict(audit='LIGHT_LANE_OWNER',phase='terminal',record_sha256=sha(envelope),**accepted)
    terminal=read(directory/'terminal.json',value['accepted_terminal_sha256'])
    binding=encoded(dict(plan_sha256=plan.digest,request_sha256=request_digest,
        receipt_sha256=sha(encoded(receipt)),terminal_sha256=sha(terminal)))
    if (directory/'restore-intent.json').exists():
        require(read(directory/'restore-intent.json')==binding,'LANE_OWNER_RESTORE_INTENT_CHANGED')
    else:retain(directory/'restore-intent.json',binding)
    # A missing acknowledgment cannot distinguish a committed transaction from
    # a rollback. Classify fresh exact rows before deciding whether SQL is needed.
    restored=reconcile_controls(psycopg,parameters,plan,receipt,terminal,directory,run_guard)
    remember(directory/'controls-restored.json',encoded(restored))
    run_guard.assert_running()
    install.verify_unit(install.RETAINED_SOURCE)
    require(install.hold.read(install.UNIT_FILE,0o644,8192)==install.render(install.RETAINED_SOURCE),
            'LANE_OWNER_ORIGINAL_UNIT_CHANGED')
    if restarting:
        require(read(directory/'restart-intent.json')==binding,'LANE_OWNER_RESTART_INTENT_CHANGED')
    else:
        install.stopped()
        retain(directory/'restart-intent.json',binding)
    state=install.show(install.UNIT,['ActiveState','MainPID'])
    if state['MainPID']=='0' and state['ActiveState'] in ('inactive','failed'):
        install.stopped()
        run_guard.assert_running()
        switch.command('/usr/bin/systemctl','start',install.UNIT)
    observed=None
    for _ in range(30):
        try:
            observed=install.verify_hold_process(install.RETAINED_SOURCE,pwd.getpwnam('school-autopilot'));break
        except (RuntimeError,FileNotFoundError):time.sleep(0.5)
    require(observed is not None,'LANE_OWNER_HOLD_RESTART_UNCONFIRMED')
    remember(directory/'complete.json',encoded(dict(**restored,native=observed)))
    return dict(audit='LIGHT_LANE_OWNER',phase='restore',state='HOLD',controls_restored=True,
                success=restored['success'],native_pid=observed['MainPID'])


def remember(path,raw):
    """Idempotent evidence requires identical root-owned bytes, including metadata."""
    if path.exists():require(read(path)==raw,'LANE_OWNER_RECORD_CHANGED')
    else:retain(path,raw)


def reconcile_controls(psycopg,parameters,plan,receipt,terminal,directory,run_guard):
    from database import light_native_pilot_intake as intake
    accepted=sha(terminal)
    before=intake.engine.load_manifest(directory/'before.json',receipt['snapshot_sha256'])
    require(before['version']==1 and before['plan_sha256']==plan.digest
            and before['target']==intake.EXPECTED_TARGET,'LANE_OWNER_SNAPSHOT_CHANGED')
    def classify():
        with psycopg.connect(**parameters,autocommit=True) as conn:
            conn.read_only=True
            evidence=intake.terminal_evidence(terminal,accepted,plan,receipt)
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                conn.execute("SET LOCAL statement_timeout='5s'")
                intake.engine.identity(conn,intake.target())
                success=intake._terminal_rows(conn,plan,receipt,evidence)
                config=intake.one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')
                role=intake.one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT'")
                applied=config==receipt['applied_config'] and role==receipt['applied_role']
                restored=config==before['native_config'] and set(role)==set(before['autopilot_role']) and all(
                    role[k]==v for k,v in before['autopilot_role'].items() if k!='updated_at')
                require(applied != restored,'LANE_OWNER_DB_RESTORE_CHANGED')
                return restored,success
    done,success=classify()
    if not done:
        require(not (directory/'restart-intent.json').exists(),'LANE_OWNER_RESTART_WITH_APPLIED_DB')
        with psycopg.connect(**parameters,autocommit=True) as conn:
            conn.read_only=False
            intake.restore_controls_after_terminal(conn,plan,receipt,terminal,accepted,
                directory/'before.json',effect_guard=run_guard.assert_running)
        done,success=classify()
        require(done,'LANE_OWNER_DB_RESTORE_CHANGED')
    return dict(plan_sha256=plan.digest,dispatch_id=receipt['dispatch_id'],
        terminal_sha256=accepted,success=success,controls_restored=True)


def contain(psycopg,parameters,plan,directory,value,run_guard):
    """Freeze a pre-execution intake; queue evidence stays unresolved and intact.

    This is containment, not cancellation, rollback or permission to retry.
    External publication uncertainty is deliberately preserved.
    """
    from database import light_native_pilot_intake as intake
    binding=encoded(dict(plan_sha256=plan.digest,action='CONTAIN_PREEXECUTION'))
    remember(directory/'contain-intent.json',binding)
    def host_guard():
        run_guard.assert_running()
        require(not (directory/'execution.json').exists()
                and not (install.LEDGER/'first-feed-intent.json').exists()
                and not (install.CONTROL/'current.json').exists(),'LANE_OWNER_FEED_REQUIRES_INCIDENT')
        execution.stopped()
        install.verify_running(install.RETAINED_SOURCE,pwd.getpwnam('school-autopilot'))
    host_guard()
    raw=read(directory/'before.json')
    before=intake.engine.load_manifest(directory/'before.json',sha(raw))
    require(before['plan_sha256']==plan.digest and before['target']==intake.EXPECTED_TARGET,
            'LANE_OWNER_SNAPSHOT_CHANGED')
    receipt=None
    if (directory/'intake.json').exists():
        receipt=parse(read(directory/'intake.json'))
        require(receipt['plan_sha256']==plan.digest and receipt['snapshot_sha256']==sha(raw),
                'LANE_OWNER_RECEIPT')
    with psycopg.connect(**parameters,autocommit=True) as conn:
        conn.read_only=False
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            conn.execute("SET LOCAL statement_timeout='10s'")
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('light-lane:'+plan.digest,))
            intake.engine.identity(conn,intake.target())
            config=intake.one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton FOR UPDATE')
            role=intake.one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT' FOR UPDATE")
            rows=conn.execute('SELECT to_jsonb(w) FROM autopilot.project_work_item w WHERE work_key=%s FOR UPDATE',
                              (plan.value['work_key'],)).fetchall()
            if not rows:
                require(config==before['native_config'] and role==before['autopilot_role'],
                        'LANE_OWNER_INTAKE_UNKNOWN')
                state='INTAKE_ROLLED_BACK'
            else:
                require(receipt is not None and len(rows)==1,'LANE_OWNER_INTAKE_UNKNOWN')
                work=rows[0][0]
                require(work['work_item_id']==receipt['work_item_id'] and work['last_task_id']==receipt['task_id']
                        and work['state']=='ACTIVE','LANE_OWNER_INTAKE_GRAPH_CHANGED')
                task=intake.one(conn,'SELECT to_jsonb(t) FROM autopilot.task t WHERE task_id=%s::uuid FOR UPDATE',
                                (receipt['task_id'],))
                outbox=intake.one(conn,'SELECT to_jsonb(o) FROM autopilot.role_dispatch_outbox o WHERE dispatch_id=%s::uuid FOR UPDATE',
                                  (receipt['dispatch_id'],))
                step=intake.one(conn,'SELECT to_jsonb(s) FROM autopilot.step_attempt s WHERE step_attempt_id=%s::uuid FOR UPDATE',
                                (outbox['step_attempt_id'],))
                require(task['status']=='WAITING_EXTERNAL' and step['status']=='WAITING_EXTERNAL'
                        and step['task_id']==receipt['task_id'] and outbox['task_id']==receipt['task_id']
                        and outbox['status'] in ('CLAIMED','PUBLISHED') and outbox['delivery_contract_version']==3
                        and outbox['expected_head_sha']==plan.value['expected_head_sha']
                        and outbox['task_fingerprint']==receipt['dispatch']['task_fingerprint']
                        and sha(json.dumps(task['goal_json'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
                            ==receipt['goal_json_sha256'],'LANE_OWNER_INTAKE_GRAPH_CHANGED')
                # No receipt may appear between the observation and containment.
                conn.execute('LOCK TABLE autopilot.native_cli_receipt IN SHARE MODE')
                require(conn.execute('SELECT count(*) FROM autopilot.native_cli_receipt WHERE dispatch_id=%s::uuid',
                                     (receipt['dispatch_id'],)).fetchone()==(0,),'LANE_OWNER_NATIVE_ACTIVITY')
                disabled=dict(receipt['applied_config'],enabled=False)
                require(role==receipt['applied_role'] and config in (receipt['applied_config'],disabled),
                        'LANE_OWNER_CONTAIN_CONFIG_CHANGED')
                host_guard()
                if config!=disabled:
                    conn.execute('UPDATE autopilot.native_cli_config SET enabled=false WHERE singleton')
                require(intake.one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')==disabled,
                        'LANE_OWNER_CONTAIN_READBACK')
                state='CONTAINED_UNRESOLVED'
            host_guard()
    result=dict(audit='LIGHT_LANE_OWNER',phase='contain',state=state,plan_sha256=plan.digest,
        controls_restored=False,task_success=False,queue_retry_authorized=False)
    remember(directory/'contained.json',encoded(result))
    return result


def committed_intake(conn,plan,receipt):
    """A prospective durable receipt is never a COMMIT acknowledgment."""
    from database import light_native_pilot_intake as intake
    require(conn.autocommit is True and conn.read_only is True,'LANE_OWNER_COMMIT_AUTHORITY')
    with conn.transaction():
        # READ COMMITTED observes after the prior transaction releases the lock.
        conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY')
        conn.execute("SET LOCAL statement_timeout='10s'")
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('light-lane:'+plan.digest,))
        intake.engine.identity(conn,intake.target())
        config=intake.one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')
        role=intake.one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT'")
        work=intake.one(conn,'SELECT to_jsonb(w) FROM autopilot.project_work_item w WHERE work_key=%s',
                        (plan.value['work_key'],))
        task=intake.one(conn,'SELECT to_jsonb(t) FROM autopilot.task t WHERE task_id=%s::uuid',(receipt['task_id'],))
        outbox=intake.one(conn,'SELECT to_jsonb(o) FROM autopilot.role_dispatch_outbox o WHERE dispatch_id=%s::uuid',
                          (receipt['dispatch_id'],))
        step=intake.one(conn,'SELECT to_jsonb(s) FROM autopilot.step_attempt s WHERE step_attempt_id=%s::uuid',
                        (outbox['step_attempt_id'],))
        assignment=intake.one(conn,'SELECT to_jsonb(x) FROM autopilot.get_dispatch_assignment(%s::uuid) x',
                             (receipt['dispatch_id'],))
        require(config==receipt['applied_config'] and role==receipt['applied_role']
                and work['work_item_id']==receipt['work_item_id'] and work['last_task_id']==receipt['task_id']
                and work['state']=='ACTIVE' and task['status']=='WAITING_EXTERNAL'
                and outbox['task_id']==receipt['task_id'] and outbox['status']=='CLAIMED'
                and outbox['claim_owner']==plan.worker and outbox['claim_epoch']==receipt['claim_epoch']
                and outbox['delivery_contract_version']==3 and outbox['mode']=='READ_ONLY'
                and outbox['expected_head_sha']==plan.value['expected_head_sha']
                and outbox['task_fingerprint']==receipt['dispatch']['task_fingerprint']
                and step['task_id']==receipt['task_id'] and step['status']=='WAITING_EXTERNAL'
                and assignment==receipt['assignment']
                and sha(json.dumps(task['goal_json'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
                    ==receipt['goal_json_sha256'],'LANE_OWNER_INTAKE_NOT_COMMITTED')
        require(conn.execute('SELECT count(*) FROM autopilot.native_cli_receipt WHERE dispatch_id=%s::uuid',
                             (receipt['dispatch_id'],)).fetchone()==(0,),'LANE_OWNER_NATIVE_ACTIVITY')


def verify_terminal_policy(conn):
    """Read-only deployed trigger check before admitting a substantive audit."""
    require(conn.execute("""SELECT EXISTS(
      SELECT FROM public.schema_migration WHERE migration_key='0372_autopilot_audit_pass_alias')
      AND EXISTS(SELECT FROM autopilot.migration_0372_function_backup
        WHERE function_key='autopilot.on_project_work_task_terminal()'
          AND patched_definition=pg_get_functiondef('autopilot.on_project_work_task_terminal()'::regprocedure)
          AND position('AUDIT_FINDINGS_REPORTED' in patched_definition)>0
          AND position('NEXT_STEP_TERMINAL_FENCE' in patched_definition)>0)""").fetchone()==(True,),
      'LANE_OWNER_TERMINAL_POLICY_CHANGED')
