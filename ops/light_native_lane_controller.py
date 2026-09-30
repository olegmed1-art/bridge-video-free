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
EXTRA=('ops/light_native_lane_issuer.py','ops/light_native_lane_controller.py','ops/light_native_lane_cycle.py','ops/light_native_lane_execution.py',
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


def validate_package(raw,source,accepted,*,allow_legacy=False):
    require(type(raw) is bytes and len(raw)<3*1024*1024 and sha(raw)==accepted
            and release.source.identifier(source,40),'LANE_OWNER_PACKAGE')
    value=parse(raw)
    expected=set((*release.HELPERS,*EXTRA))
    names=set(value.get('helpers',{}))
    require(set(value)=={'version','kind','source','helpers'} and value['version']==1
            and value['kind']=='LIGHT_LANE_CONTROLLER' and value['source']==source
            and (names==expected or allow_legacy and names in
                 (expected-{'ops/light_native_lane_issuer.py'},
                  expected-{'ops/light_native_lane_cycle.py','ops/light_native_lane_issuer.py'}))
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
    controller=validate_package(read(root/'package.json',accepted,3*1024*1024),source,accepted,allow_legacy=True)
    for name,text in controller['helpers'].items():
        require(read(root/name,None,3*1024*1024)==text.encode(),'LANE_OWNER_HELPER_CHANGED')
    return root


def scope(payload):
    from database.light_native_pilot_intake import Plan
    raw=base64.b64decode(payload['plan_base64'],validate=True)
    plan=Plan(raw,payload['accepted_plan_sha256'])
    require(plan.value['source']==install.RETAINED_SOURCE,'LANE_OWNER_RUNTIME')
    return plan,ROOT/plan.digest


def sequence(value):
    predecessor=value.get('predecessor')
    if predecessor is None:
        require(value['version']==1,'LANE_OWNER_PREDECESSOR')
        return 0
    require(value['version']==2 and type(predecessor) is dict
            and set(predecessor)=={'plan_sha256','terminal_sha256','sequence'}
            and type(predecessor['sequence']) is int and 0<=predecessor['sequence']<9999
            and all(release.source.identifier(predecessor[k],64) for k in ('plan_sha256','terminal_sha256')),
            'LANE_OWNER_PREDECESSOR')
    require(predecessor['plan_sha256']!=value['accepted_plan_sha256'],'LANE_OWNER_REUSED_PLAN')
    return predecessor['sequence']+1


def verify_previous(conn,value):
    """Freshly reconcile the exact retained predecessor; never resubmit it."""
    from database.light_native_pilot_intake import Plan
    from ops.light_native_lane_owner import retain_acceptance
    from ops.light_native_lane_feed import root_record,verify_serial_hold
    number=sequence(value)
    require(number>0,'LANE_OWNER_PREDECESSOR')
    previous=value['predecessor']
    directory=ROOT/previous['plan_sha256']
    plan=Plan(read(directory/'plan.json',previous['plan_sha256']),previous['plan_sha256'])
    receipt=parse(read(directory/'intake.json'))
    terminal=read(directory/'terminal.json',previous['terminal_sha256'])
    prepared=parse(read(directory/'prepare.json'))
    require(sequence(prepared)==number-1 and plan.value['source']==install.RETAINED_SOURCE
            and prepared['accepted_runtime_sha256']==value['accepted_runtime_sha256'], 'LANE_OWNER_PREDECESSOR')
    complete=parse(read(directory/'complete.json'))
    restored=parse(read(directory/'controls-restored.json'))
    request_digest=parse(read(directory/'execution.json'))['request_sha256']
    binding=encoded(dict(plan_sha256=plan.digest,request_sha256=request_digest,
        receipt_sha256=sha(encoded(receipt)),terminal_sha256=sha(terminal)))
    require(read(directory/'restore-intent.json')==binding and read(directory/'restart-intent.json')==binding
            and complete.get('controls_restored') is True
            and {k:v for k,v in complete.items() if k!='native'}==restored,
            'LANE_OWNER_PREDECESSOR_NOT_RESTORED')
    cursor=root_record(install.CONTROL/'current.json',4096)
    before,history=verify_serial_hold(install.RETAINED_SOURCE,cursor)
    require(len(history)==number and before==complete['native']
            and parse(cursor)['dispatch_id']==receipt['dispatch_id'], 'LANE_OWNER_PREDECESSOR_CHANGED')
    # Existing acceptance is required by verify_serial_hold. The helper now
    # rechecks Cloud/DB and the exact original goal, without creating a task.
    retain_acceptance(conn,plan,receipt,number-1)
    from database import light_native_pilot_intake as intake
    intake.observe_terminal(conn,plan,receipt,terminal,previous['terminal_sha256'])
    require(verify_serial_hold(install.RETAINED_SOURCE,cursor)==(before,history),
            'LANE_OWNER_PREDECESSOR_CHANGED')
    return cursor


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


def phase(wheels,credential,token,controller_raw,retained_raw,payload_raw,accepted_payload,run_guard,*,_cycle=None):
    """One authenticated phase; its accepted payload contains no credentials."""
    require(os.geteuid()==0 and os.uname().nodename=='autopilot-lite-vnic'
            and sha(payload_raw)==accepted_payload,'LANE_OWNER_AUTHORITY')
    value=parse(payload_raw)
    if value.get('action')=='issue':
        require(_cycle is None,'LANE_ISSUER_SCOPE')
        from ops.light_native_lane_issuer import run
        return run(wheels,credential,token,controller_raw,retained_raw,payload_raw,accepted_payload,run_guard)
    if value.get('action')=='cycle':
        require(_cycle is None,'LANE_CYCLE_SCOPE')
        from ops.light_native_lane_cycle import run
        return run(wheels,credential,token,controller_raw,retained_raw,payload_raw,accepted_payload,run_guard)
    keys={'version','action','source','accepted_controller_sha256','accepted_runtime_sha256',
          'plan_base64','accepted_plan_sha256','agreement','accepted_agreement_sha256',
          'accepted_receipt_sha256','accepted_discovery_sha256','accepted_permit_sha256',
          'accepted_terminal_sha256'}
    require((set(value)==keys and value['version']==1
             or set(value)==keys|{'predecessor'} and value['version']==2) and value['action'] in
            ('prepare','publish','permit','execute','terminal','restore','contain','recover'),'LANE_OWNER_PHASE')
    number=sequence(value)
    from ops.light_native_lane_cycle import authorize_phase
    authorize_phase(value,_cycle)
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
            if number==0:
                install.verify_running(retained['source'],pwd.getpwnam('school-autopilot'))
            guard(plan,prior,value,run_guard,agreement)
            pr=owner.observed_target(API(token),plan)
            # These shared parents are fixed, root-only and not user data.
            # Fail read-only prerequisites before retaining any preparation state.
            with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                conn.read_only=True
                intake.engine.privileges(conn,intake.target(),True)
                verify_terminal_policy(conn)
                if number:
                    verify_previous(conn,value)
            for path in (ROOT,ROOT/'controllers'):
                if not path.exists():install.fresh_directory(path,0o700)
                install.root_parent(path)
            if helpers_root(controller['source']).exists():
                verify_helpers(controller['source'],value['accepted_controller_sha256'])
            else:
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
                if number: containment_host(directory,value)
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
        if value['action'] not in ('terminal','restore','contain','recover'):
            stable+=('source','accepted_controller_sha256')
        # Cleanup may use a NEW authenticated workflow/current controller after
        # main advances; retained runtime, plan and original helper evidence stay bound.
        require(all(value[k]==original[k] for k in stable),'LANE_OWNER_SCOPE_CHANGED')
        require(value.get('predecessor')==original.get('predecessor')
                and sequence(value)==sequence(original),'LANE_OWNER_PREDECESSOR_CHANGED')
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
        if value['action']=='recover':
            return recover(psycopg,parameters(credential),plan,receipt,directory,value,run_guard,API(token))
        if value['action'] in ('terminal','restore'):
            return finish(psycopg,parameters(credential),plan,receipt,directory,value,run_guard)
        require(not (directory/'contain-intent.json').exists(),'LANE_OWNER_CONTAINMENT_PENDING')
        agreement=Agreement(value['agreement'],value['accepted_agreement_sha256'],plan.scope)
        guard(plan,prior,value,run_guard,agreement)
        owner.observed_target(API(token),plan)
        if value['action']=='publish':
            require(not (directory/'publication-intent.json').exists(),'LANE_OWNER_PUBLICATION_UNCERTAIN')
            with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                conn.read_only=True
                if number: verify_previous(conn,value)
                committed_intake(conn,plan,receipt)
            lease=refresh_owner_claim(psycopg,parameters(credential),plan,agreement,receipt,directory,value,run_guard)
            retain(directory/'publication-intent.json',payload_raw)
            run_guard.assert_running()
            if number: containment_host(directory,value)
            with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                conn.read_only=True
                intake.assert_publication_claim(conn,plan,agreement,receipt,lease,minimum_seconds=60)
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
            require(not (directory/'permit-intent.json').exists(),'LANE_OWNER_PERMIT_UNCERTAIN')
            if number:
                with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                    conn.read_only=True
                    verify_previous(conn,value)
            refresh_owner_claim(psycopg,parameters(credential),plan,agreement,receipt,directory,value,run_guard)
            def permit_guard():
                run_guard.assert_running()
                if number: containment_host(directory,value)
            retain(directory/'permit-intent.json',payload_raw)
            with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                conn.read_only=False
                marked=intake.mark_reviewed_publication(conn,plan,agreement,receipt,encoded(discovered),
                    value['accepted_discovery_sha256'],effect_guard=permit_guard)
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
            predecessor=verify_previous(conn,value) if number else None
            options={'previous_cursor':predecessor} if number else {}
            result=publish_first(conn,retained_raw,value['accepted_runtime_sha256'],permit,
                value['accepted_permit_sha256'],value['source'],lambda n:API(token).get('/pulls/'+str(n)),
                plan.raw,plan.digest,receipt_raw,value['accepted_receipt_sha256'],**options)
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
    if value.get('version')==2:
        body=parse(request);body.update(version=2,sequence=sequence(value))
        request=encoded(body)
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
            number=sequence(value)
            accepted=retain_acceptance(conn,plan,receipt,number)
            raw=install.STATE/f'{number:08d}-terminal.json'
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


def containment_host(directory,value):
    """Contain only before this sequence's publication, preserving old evidence."""
    from ops.light_native_lane_feed import root_record,verify_serial_hold
    number=sequence(value)
    intent='first-feed-intent.json' if number==0 else f'{number:08d}-feed-intent.json'
    require(not (directory/'execution.json').exists()
            and not (install.LEDGER/intent).exists(),'LANE_OWNER_FEED_REQUIRES_INCIDENT')
    execution.stopped()
    if number==0:
        require(not (install.CONTROL/'current.json').exists(),'LANE_OWNER_FEED_REQUIRES_INCIDENT')
        install.verify_running(install.RETAINED_SOURCE,pwd.getpwnam('school-autopilot'))
        return
    previous=value['predecessor']
    prior=ROOT/previous['plan_sha256']
    read(prior/'terminal.json',previous['terminal_sha256'])
    receipt=parse(read(prior/'intake.json'))
    complete=parse(read(prior/'complete.json'))
    cursor=root_record(install.CONTROL/'current.json',4096)
    before,history=verify_serial_hold(install.RETAINED_SOURCE,cursor)
    require(len(history)==number and complete.get('controls_restored') is True
            and before==complete['native'] and parse(cursor)['dispatch_id']==receipt['dispatch_id']
            and set(os.listdir(install.CONTROL/'jobs'))=={parse(item[0])['dispatch_id'] for item in history},
            'LANE_OWNER_FEED_REQUIRES_INCIDENT')


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
        containment_host(directory,value)
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


RECOVERY_CODE='NATIVE_PREEXECUTION_PUBLICATION_EXPIRED'
RECOVERY_PLAN='3748d3ae6288b650182d851d8f161871ca5ff302ca12d8de6b2ace214b8315a3'


def recovery_scope(plan,receipt,value):
    # One reviewed incident, not a general cancellation capability.
    require(plan.digest==RECOVERY_PLAN and value['version']==2 and sequence(value)==1
        and value['predecessor']==dict(plan_sha256='71706f2ccb9054a0fa955f51cd8289810775253b5ca87b981726b7a7583d6af4',
            terminal_sha256='517273e66872c271c7eea70e89c7d792419d5df4474c3e63c71fc52a5ac2fa91',sequence=0)
        and receipt['task_id']=='036bda80-b063-4578-a87b-61e9d556d5b2'
        and receipt['dispatch_id']=='6ac74f4e-d3fa-4140-a700-904cc6d408c7'
        and receipt['work_item_id']=='0ede25b2-02e4-437b-85e0-7eb4882bc3d0'
        and value['accepted_receipt_sha256']=='c328bdb8660885f588c5ea07c10803726608cc1f3ea1017db420789db351a2e9'
        and value['accepted_discovery_sha256']=='5d2133d05579483409993d18bd831a474d95511fc4fb3dd0d04a452200b996fb',
        'LANE_RECOVERY_SCOPE')


def recovery_rows(conn,receipt,*,locked=True):
    """Read under the recovery table locks, including empty activity evidence."""
    from database.light_native_pilot_intake import one
    result={}
    lock=' FOR UPDATE' if locked else ''
    for name,table,key,identifier in (
        ('task','task','task_id',receipt['task_id']),
        ('outbox','role_dispatch_outbox','dispatch_id',receipt['dispatch_id']),
        ('work','project_work_item','work_item_id',receipt['work_item_id'])):
        result[name]=one(conn,f'SELECT to_jsonb(x) FROM autopilot.{table} x WHERE {key}=%s::uuid'+lock,
                         (identifier,))
    result['step']=one(conn,'SELECT to_jsonb(x) FROM autopilot.step_attempt x WHERE step_attempt_id=%s::uuid'+lock,
                       (result['outbox']['step_attempt_id'],))
    result['config']=one(conn,'SELECT to_jsonb(x) FROM autopilot.native_cli_config x WHERE singleton'+lock)
    result['role']=one(conn,"SELECT to_jsonb(x) FROM autopilot.role_registry x WHERE role_id='AUTOPILOT'"+lock)
    result['planner']=one(conn,'SELECT to_jsonb(x) FROM autopilot.project_planner_state x WHERE singleton'+lock)
    result['mapping']=[row[0] for row in conn.execute(
        'SELECT to_jsonb(x) FROM autopilot.project_work_task x WHERE work_item_id=%s::uuid ORDER BY task_id',
        (receipt['work_item_id'],)).fetchall()]
    result['receipts']=conn.execute('SELECT count(*) FROM autopilot.native_cli_receipt WHERE dispatch_id=%s::uuid',
                                   (receipt['dispatch_id'],)).fetchone()[0]
    result['successors']=conn.execute('SELECT count(*) FROM autopilot.role_dispatch_outbox WHERE prior_task_id=%s::uuid OR origin_task_id=%s::uuid',
                                     (receipt['task_id'],receipt['task_id'])).fetchone()[0]
    # No unrelated task/dispatch/receipt may be created by a terminal trigger.
    result['counts']=[conn.execute('SELECT count(*) FROM autopilot.'+table).fetchone()[0]
                      for table in ('task','role_dispatch_outbox','native_cli_receipt','project_work_task')]
    result['event']=[row[0] for row in conn.execute('SELECT to_jsonb(x) FROM autopilot.task_event x WHERE idempotency_key=%s',
                        ('native-preexecution-recovery:'+receipt['dispatch_id'],)).fetchall()]
    result['active_tasks']=[str(row[0]) for row in conn.execute("SELECT task_id FROM autopilot.task WHERE status IN "
        "('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING') ORDER BY task_id").fetchall()]
    return result


def validate_recovery_rows(rows,plan,receipt):
    t,o,w,s=(rows[k] for k in ('task','outbox','work','step'))
    require(rows['config']==dict(receipt['applied_config'],enabled=False)
            and rows['role']==receipt['applied_role'] and rows['role']['can_repair'] is False,
            'LANE_RECOVERY_CONTROLS_CHANGED')
    require(w['work_key']==plan.value['work_key'] and w['state']=='ACTIVE'
            and w['last_task_id']==receipt['task_id'] and w['generation']==1
            and w['task_spec_json']==plan.value['task_spec_json']
            and w['hold_reason'] is None and w['probe_lease_owner'] is None
            and len(rows['mapping'])==1 and rows['mapping'][0]['task_id']==receipt['task_id']
            and rows['mapping'][0]['run_kind']=='AUDIT', 'LANE_RECOVERY_WORK_CHANGED')
    require(t['status']==s['status']=='WAITING_EXTERNAL' and t['attempts']==1
            and t['lease_owner'] is None and t['lease_until'] is None
            and t['completed_at'] is None and t['terminal_reason_code'] is None
            and t['safe_summary_json']=={} and s['task_id']==receipt['task_id']
            and s['completed_at'] is None
            and all(t[k]==0 for k in ('cost_actual_microusd','cost_reserved_microusd','cost_cap_microusd'))
            and sha(json.dumps(t['goal_json'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
                ==receipt['goal_json_sha256'], 'LANE_RECOVERY_TASK_CHANGED')
    require(o['status']=='CLAIMED' and o['claim_owner']==plan.worker
            and o['claim_epoch']==receipt['claim_epoch']==1 and o['attempts']==1
            and o['task_id']==receipt['task_id'] and o['mode']=='READ_ONLY'
            and o['delivery_contract_version']==3 and o['target_pr']==plan.value['target_pr']
            and o['expected_head_sha']==plan.value['expected_head_sha']
            and o['task_fingerprint']==receipt['dispatch']['task_fingerprint']
            and all(o[k] is None for k in ('published_at','github_dispatch_comment_id','dispatch_body_sha256',
                'executor_id','codex_ack_at','delivered_at','completed_at','prior_task_id','origin_task_id',
                'codex_command_pr','codex_command_comment_id','sent_at'))
            and rows['receipts']==0 and rows['successors']==0 and rows['event']==[]
            and rows['active_tasks']==[receipt['task_id']], 'LANE_RECOVERY_DISPATCH_CHANGED')


def validate_recovery_delta(before,after,snapshot,receipt):
    """Every unlisted field, attempt counter and graph edge must stay identical."""
    permitted={
        'outbox':{'status','claim_owner','claim_until','last_error_code','completed_at','updated_at'},
        'step':{'status','error_code','result_summary_json','completed_at'},
        'task':{'status','terminal_reason_code','safe_summary_json','completed_at','updated_at'},
        'planner':{'decision_count','last_decision_code','last_work_item_id','last_decision_at'},
        'work':{'state','hold_reason','hold_until','result_code','result_summary','completed_at','updated_at','not_before'}}
    for name,fields in permitted.items():
        require(set(before[name])==set(after[name]) and all(after[name][k]==v for k,v in before[name].items() if k not in fields),
                'LANE_RECOVERY_UNEXPECTED_DELTA')
    require(all(after[k]==before[k] for k in ('counts','mapping','receipts','successors'))
        and after['receipts']==after['successors']==0
        and after['config']==snapshot['native_config']
        and {k:v for k,v in after['role'].items() if k!='updated_at'}==
            {k:v for k,v in snapshot['autopilot_role'].items() if k!='updated_at'}
        and after['task']['status']==after['step']['status']==after['outbox']['status']=='FAILED_CLOSED'
        and after['outbox']['claim_owner'] is None and after['outbox']['claim_until'] is None
        and after['outbox']['last_error_code']==after['step']['error_code']==after['task']['terminal_reason_code']==RECOVERY_CODE
        and all(after[k]['completed_at'] is not None for k in ('task','step','outbox'))
        and 'status' not in after['task']['safe_summary_json']
        and after['task']['safe_summary_json']['provider_started'] is False
        and after['task']['safe_summary_json']['queue_retry_authorized'] is False
        and after['task']['safe_summary_json']['result_code']==RECOVERY_CODE
        and after['step']['result_summary_json']==after['task']['safe_summary_json']
        and after['work']['result_code']==RECOVERY_CODE
        and after['active_tasks']==[]
        and after['planner']['decision_count']==before['planner']['decision_count']+1
        and after['planner']['last_decision_code']=='WORK_ITEM_BLOCKED_CONTINUE'
        and after['planner']['last_work_item_id']==receipt['work_item_id']
        and after['planner']['last_decision_at'] is not None
        and len(after['event'])==1 and after['event'][0]['task_id']==receipt['task_id']
        and after['event'][0]['event_type']=='TASK_FAILED_CLOSED'
        and after['work']['state']=='PAUSED' and after['work']['hold_reason']=='OWNER_HOLD'
        and after['work']['hold_until'] is None and after['work']['completed_at'] is None
        and after['work']['last_task_id']==receipt['task_id'], 'LANE_RECOVERY_READBACK')


def recover(psycopg,parameters,plan,receipt,directory,value,run_guard,api):
    """Retire one contained, expired, never-executed intake without replay.

    Root intent and before/expected-after rows survive lost COMMIT responses.
    An existing intent permits exact read-only reconciliation, never new SQL.
    Neither a provider result nor a native receipt is fabricated.
    """
    from database import light_native_pilot_intake as intake
    from ops import light_native_pilot_owner as owner
    recovery_scope(plan,receipt,value)
    contained=parse(read(directory/'contained.json'))
    require(contained==dict(audit='LIGHT_LANE_OWNER',phase='contain',state='CONTAINED_UNRESOLVED',
        plan_sha256=plan.digest,controls_restored=False,task_success=False,queue_retry_authorized=False),
        'LANE_RECOVERY_NOT_CONTAINED')
    require(read(directory/'contain-intent.json')==encoded(dict(plan_sha256=plan.digest,action='CONTAIN_PREEXECUTION'))
            and not (directory/'publication.json').exists() and not (directory/'permit.json').exists(),
            'LANE_RECOVERY_PUBLICATION_MARKED')
    discovery=read(directory/'discovery.json',value['accepted_discovery_sha256'])
    published=parse(read(directory/'broker.json'))
    require(owner.discovery(api,published,receipt)==parse(discovery),'LANE_OWNER_PUBLICATION_CHANGED')
    before=intake.engine.load_manifest(directory/'before.json',receipt['snapshot_sha256'])
    require(before['plan_sha256']==plan.digest and before['target']==intake.EXPECTED_TARGET
            and before['native_config']['enabled'] is False, 'LANE_RECOVERY_SNAPSHOT_CHANGED')
    binding=encoded(dict(plan_sha256=plan.digest,receipt_sha256=sha(encoded(receipt)),
        discovery_sha256=sha(discovery),contained_sha256=sha(encoded(contained)),action='RECOVER_PREEXECUTION'))
    pending=(directory/'recovery-intent.json').exists()
    if pending:require(read(directory/'recovery-intent.json')==binding,'LANE_RECOVERY_INTENT_CHANGED')
    def host_guard():
        run_guard.assert_running()
        containment_host(directory,value)
    host_guard()
    with psycopg.connect(**parameters,autocommit=True) as conn:
        conn.read_only=False
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            conn.execute("SET LOCAL statement_timeout='10s'")
            conn.execute("SET LOCAL lock_timeout='5s'")
            intake.engine.identity(conn,intake.target())
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('autopilot.role-worker-capacity-v1',))
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('light-lane:'+plan.digest,))
            # Prevent a receipt, successor or unrelated config edit during CAS/readback.
            for table in ('native_cli_config','native_cli_receipt','project_planner_state','project_work_item','project_work_task',
                          'role_dispatch_outbox','role_registry','step_attempt','task'):
                conn.execute('LOCK TABLE autopilot.'+table+' IN SHARE ROW EXCLUSIVE MODE')
            rows=recovery_rows(conn,receipt)
            if pending:
                expected=parse(read(directory/'recovery-after.json'))
                require(rows==expected,'LANE_RECOVERY_OUTCOME_UNKNOWN')
            else:
                verify_terminal_policy(conn)
                validate_recovery_rows(rows,plan,receipt)
                require(conn.execute('SELECT claim_until<clock_timestamp() FROM autopilot.role_dispatch_outbox WHERE dispatch_id=%s::uuid',
                                     (receipt['dispatch_id'],)).fetchone()==(True,), 'LANE_RECOVERY_CLAIM_NOT_EXPIRED')
                host_guard()
                retain(directory/'recovery-before.json',encoded(rows))
                retain(directory/'recovery-intent.json',binding)
                summary=dict(result_code=RECOVERY_CODE,provider_started=False,queue_retry_authorized=False,
                             summary='Publication lease expired before native execution; owner retired intake without retry.')
                def update(sql,args):
                    require(conn.execute(sql,args).rowcount==1,'LANE_RECOVERY_ROWCOUNT')
                update("UPDATE autopilot.role_dispatch_outbox SET status='FAILED_CLOSED',claim_owner=NULL,claim_until=NULL,"
                    'last_error_code=%s,completed_at=now(),updated_at=now() WHERE dispatch_id=%s::uuid',
                    (RECOVERY_CODE,receipt['dispatch_id']))
                update("UPDATE autopilot.step_attempt SET status='FAILED_CLOSED',error_code=%s,result_summary_json=%s::jsonb,"
                    'completed_at=now() WHERE step_attempt_id=%s::uuid',(RECOVERY_CODE,encoded(summary).decode(),rows['step']['step_attempt_id']))
                # No status=BLOCKED in safe_summary: it is not a provider terminal and must not authorize repair.
                update("UPDATE autopilot.task SET status='FAILED_CLOSED',terminal_reason_code=%s,safe_summary_json=%s::jsonb,"
                    'completed_at=now(),updated_at=now() WHERE task_id=%s::uuid',
                    (RECOVERY_CODE,encoded(summary).decode(),receipt['task_id']))
                update("UPDATE autopilot.project_work_item SET state='PAUSED',hold_reason='OWNER_HOLD',hold_until=NULL,"
                    'result_code=%s,result_summary=%s,completed_at=NULL,updated_at=now() WHERE work_item_id=%s::uuid',
                    (RECOVERY_CODE,summary['summary'],receipt['work_item_id']))
                update('UPDATE autopilot.native_cli_config SET enabled=%s,cutover_at=%s WHERE singleton',
                    (before['native_config']['enabled'],before['native_config']['cutover_at']))
                update("UPDATE autopilot.role_registry SET can_repair=%s WHERE role_id='AUTOPILOT'",
                    (before['autopilot_role']['can_repair'],))
                conn.execute("SELECT autopilot.record_event(%s::uuid,'TASK_FAILED_CLOSED','WAITING_EXTERNAL','FAILED_CLOSED',"
                    "%s::jsonb,'SYSTEM','NATIVE_OWNER_RECOVERY',%s)",
                    (receipt['task_id'],encoded(dict(summary,dispatch_id=receipt['dispatch_id'],
                        plan_sha256=plan.digest,recovery_intent_sha256=sha(binding))).decode(),
                     'native-preexecution-recovery:'+receipt['dispatch_id']))
                expected=recovery_rows(conn,receipt)
                validate_recovery_delta(rows,expected,before,receipt)
                retain(directory/'recovery-after.json',encoded(expected))
            host_guard()
    # Independent post-commit readback; never treat durable prospective data as ACK.
    with psycopg.connect(**parameters,autocommit=True) as conn:
        conn.read_only=True
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            require(recovery_rows(conn,receipt,locked=False)==expected,'LANE_RECOVERY_OUTCOME_UNKNOWN')
    host_guard()
    result=dict(audit='LIGHT_LANE_OWNER',phase='recover',state='RETIRED_PREEXECUTION',plan_sha256=plan.digest,
        task_id=receipt['task_id'],dispatch_id=receipt['dispatch_id'],controls_restored=True,
        task_success=False,provider_started=False,queue_retry_authorized=False)
    remember(directory/'recovered.json',encoded(result))
    return result


def refresh_owner_claim(psycopg,parameters,plan,agreement,receipt,directory,value,run_guard):
    """One bounded refresh per accepted phase, with no replay on lost ACK."""
    from database import light_native_pilot_intake as intake
    phase=value['action']
    require(phase in ('publish','permit'),'LANE_OWNER_CLAIM_PHASE')
    intent=directory/(phase+'-claim-refresh-intent.json')
    before=directory/(phase+'-claim-refresh-before.json')
    after=directory/(phase+'-claim-refresh-after.json')
    binding=encoded(dict(plan_sha256=plan.digest,receipt_sha256=value['accepted_receipt_sha256'],
        agreement_sha256=value['accepted_agreement_sha256'],action=phase,
        discovery_sha256=value['accepted_discovery_sha256'] if phase=='permit' else None))
    def effect_guard():
        run_guard.assert_running()
        agreement.assert_held(plan.scope_digest)
        containment_host(directory,value)
        require(not (directory/'contain-intent.json').exists(),'LANE_OWNER_CONTAINMENT_PENDING')
    effect_guard()
    if intent.exists():
        require(read(intent)==binding,'LANE_OWNER_CLAIM_INTENT_CHANGED')
        expected=parse(read(after))
    else:
        # A preexisting orphan snapshot is uncertainty, not permission to overwrite.
        require(not before.exists() and not after.exists(),'LANE_OWNER_CLAIM_OUTCOME_UNKNOWN')
        retain(intent,binding)
        with psycopg.connect(**parameters,autocommit=True) as conn:
            conn.read_only=False
            expected=intake.refresh_publication_claim(conn,plan,agreement,receipt,effect_guard=effect_guard,
                durable_before=lambda row:retain(before,encoded(row)),durable_after=lambda row:retain(after,encoded(row)))
    effect_guard()
    with psycopg.connect(**parameters,autocommit=True) as conn:
        conn.read_only=True
        intake.assert_publication_claim(conn,plan,agreement,receipt,expected,minimum_seconds=60)
    return expected


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
    require(conn.execute("SELECT to_regclass('autopilot.migration_0372_function_backup') IS NOT NULL").fetchone()==(True,),
            'LANE_OWNER_TERMINAL_POLICY_MISSING')
    require(conn.execute("""SELECT EXISTS(
      SELECT FROM public.schema_migration WHERE migration_key='0372_autopilot_audit_pass_alias')
      AND EXISTS(SELECT FROM autopilot.migration_0372_function_backup
        WHERE function_key='autopilot.on_project_work_task_terminal()'
          AND patched_definition=pg_get_functiondef('autopilot.on_project_work_task_terminal()'::regprocedure)
          AND position('AUDIT_FINDINGS_REPORTED' in patched_definition)>0
          AND position('NEXT_STEP_TERMINAL_FENCE' in patched_definition)>0)""").fetchone()==(True,),
      'LANE_OWNER_TERMINAL_POLICY_CHANGED')
