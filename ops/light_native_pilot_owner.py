"""Fixed owner steps for one accepted native pilot; no arbitrary SQL or queue loop.

Owner credentials and GitHub tokens arrive only through the supervised SSH pipe.
Every uncertain external or DB outcome leaves create-only private evidence.
"""
import base64
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
import time

from ops import light_native_pilot_release as release
from ops import light_native_service_controller as control
from ops import light_native_service_switch as switch
from ops import oracle_light_active_hold_attest as hold
from ops.native_maintenance_agreement import Agreement
from ops.native_maintenance_owner_host import loaded_runtime
from ops.native_maintenance_run_guard import API

require=release.require
ROOT=control.plan.ROOT/'intake'
REPOSITORY='olegmed1-art/bridge-video-free'


def observed_target(api, plan):
    pr=api.get('/pulls/'+str(plan.value['target_pr']))
    require(pr['number']==plan.value['target_pr'] and pr['state']=='open'
            and pr['merged'] is False
            and pr['head']['repo']['full_name']==pr['base']['repo']['full_name']==REPOSITORY
            and pr['head']['sha']==plan.value['expected_head_sha']
            and pr['head']['ref']==plan.value['branch'],'PILOT_OWNER_TARGET_CHANGED')
    return pr


def accepted_record(name, accepted):
    raw=control.read(ROOT/name)
    require(control.SHA256.fullmatch(accepted or '') and control.digest(raw)==accepted,
            'PILOT_OWNER_RECORD_NOT_ACCEPTED')
    return control.strict_json(raw,4*1024*1024)


def record_summary(raw):
    """Public audit projection; never emit private request/permit bytes."""
    value=control.strict_json(raw,4*1024*1024)
    result={'record_sha256':control.digest(raw)}
    for key in ('dispatch_id','task_id','work_item_id','plan_sha256','success','controls_restored'):
        if key in value:result[key]=value[key]
    return result


def guard(plan, agreement, baseline):
    agreement.assert_held(plan.scope_digest)
    release.staging.require_current_main(plan.value['source'])
    prior=hold.HoldIdentity(**baseline['prior'])
    require(asdict(hold.service_hold_identity())==asdict(prior),'PILOT_OWNER_HOLD_CHANGED')
    switch.unchanged_files(baseline['protected'],prior,baseline['protected_sha256'])
    return prior


def broker_publish(candidate, prior, receipt):
    """One call through the existing pinned, token-hiding broker as service uid."""
    environment=hold.env(hold.read(hold.ENV,0o600,262144))
    environment.update(hold.env(hold.read(Path(prior.release)/'ops/autopilot/broker-hold.env',0o444,4096)))
    child_env={k:v for k,v in environment.items() if k.startswith('AUTOPILOT_')}
    child_env.update(PATH='/usr/bin:/bin',PYTHONDONTWRITEBYTECODE='1')
    user=pwd.getpwnam('school-autopilot')
    def identity():
        os.setgroups([]);os.setgid(user.pw_gid);os.setuid(user.pw_uid)
    program='''import json,sys
sys.path.insert(0,sys.argv[1])
from oracle_autopilot.worker import _publish_role_dispatch
value=json.loads(sys.stdin.buffer.read(65537))
print(json.dumps(_publish_role_dispatch(value),sort_keys=True,separators=(',',':')))
'''
    result=subprocess.run([release.PYTHON,'-I','-B','-c',program,str(candidate)],
        cwd=candidate,env=child_env,preexec_fn=identity,input=release.encoded(receipt['dispatch']),
        capture_output=True,timeout=30)
    require(result.returncode==0 and len(result.stdout)<=65536,'PILOT_OWNER_PUBLICATION_UNKNOWN')
    # Worker validation already pins broker provenance and forbids exposed tokens.
    return json.loads(result.stdout)


def discovery(api, published, receipt):
    from database.light_native_pilot_intake import dispatch_body
    number=published['dispatch_pull_request']
    require(type(number) is int and 1<=number<=1000000,'PILOT_OWNER_DISCOVERY_ID')
    pr=api.get('/pulls/'+str(number))
    dispatch_id=receipt['dispatch_id']
    path='docs/evidence/autopilot/role-dispatch-'+dispatch_id+'.md'
    require(pr['number']==number and pr['state']=='open' and pr['merged'] is False
            and pr['draft'] is True and pr['head']['ref']=='autopilot/dispatch/'+dispatch_id
            and pr['head']['sha']==published['dispatch_commit_sha']
            and pr['head']['repo']['full_name']==pr['base']['repo']['full_name']==REPOSITORY,
            'PILOT_OWNER_DISCOVERY_CHANGED')
    file=api.get('/contents/'+path+'?ref='+pr['head']['sha'])
    require(file['type']=='file' and file['path']==path and file['encoding']=='base64',
            'PILOT_OWNER_DISCOVERY_FILE')
    body=base64.b64decode(file['content']).decode()
    require(body==dispatch_body(receipt['dispatch']) and pr['body']==body
            and pr['title']=='[Autopilot dispatch] AUTOPILOT '+dispatch_id,
            'PILOT_OWNER_DISCOVERY_BODY')
    expected=dispatch_body(receipt['dispatch'])
    return dict(repository=REPOSITORY,number=number,url=pr['html_url'],state=pr['state'],
        draft=pr['draft'],head_ref=pr['head']['ref'],head_sha=pr['head']['sha'],
        author_login=pr['user']['login'],author_id=pr['user']['id'],author_type=pr['user']['type'],
        dispatch_id=dispatch_id,dispatch_file=path,dispatch_body=expected,
        dispatch_body_sha256=hashlib.sha256(expected.encode()).hexdigest())


def fresh_provider_result(candidate, dispatch_id, environment_id, permit_raw, request):
    """Fresh Cloud status/diff read; no cached terminal, submission or DB RPC."""
    user=pwd.getpwnam('school-autopilot')
    def identity():
        os.setgroups([]);os.setgid(user.pw_gid);os.setuid(user.pw_uid)
    program='''import json,sys
sys.path.insert(0,sys.argv[1])
from oracle_autopilot import codex_cli_bridge as bridge
from oracle_autopilot.light_native_pilot import Claim
from oracle_autopilot.light_native_restart import proof
import base64
expected=json.loads(sys.stdin.buffer.read(131073))
binding={'profile':'light','environment_id':sys.argv[3],'repository':'olegmed1-art/bridge-video-free'}
def runner(args):return bridge.run_cli(args,timeout=20,profile='light')
journal=bridge.lookup(expected['request'],state_dir=bridge.LIGHT_ROOT/'runtime/codex-dispatch',binding=binding)
if (not journal or journal.get('state')!='SUBMITTED'
    or journal.get('prompt_sha256')!=bridge.digest(bridge.prompt_for(expected['request']))):
    raise RuntimeError('PILOT_OWNER_PROVIDER_JOURNAL')
value=bridge._collect(sys.argv[2],state_dir=bridge.LIGHT_ROOT/'runtime/codex-dispatch',binding=binding,runner=runner)
if value.get('provider_task_id')!=journal['provider_task_id']:raise RuntimeError('PILOT_OWNER_PROVIDER_JOURNAL')
with Claim(bridge.LIGHT_ROOT/'runtime/native-single-pilot',create_lock=False) as claim:
    restart=proof(claim,base64.b64decode(expected['permit_b64'],validate=True),
                  expected['request'],value['provider_task_id'])
print(bridge.canonical({'provider':value,'restart':restart}))
'''
    result=subprocess.run([release.PYTHON,'-I','-B','-c',program,str(candidate),dispatch_id,environment_id],
        cwd=candidate,env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},
        preexec_fn=identity,capture_output=True,timeout=45,
        input=release.encoded(dict(permit_b64=base64.b64encode(permit_raw).decode(),request=request)))
    require(result.returncode==0 and len(result.stdout)<=65536,'PILOT_OWNER_PROVIDER_READBACK')
    result_value=json.loads(result.stdout)
    require(type(result_value) is dict and set(result_value)=={'provider','restart'},
            'PILOT_OWNER_PROVIDER_READBACK')
    value=result_value['provider']
    require(value.get('state')=='RESULT_RETRIEVED' and type(value.get('changes')) is str and not value['changes'].strip(),
            'PILOT_OWNER_PROVIDER_NOT_VERIFIED')
    return value,result_value['restart']


def verify_restart_unit(restart, pilot_unit, dispatch_id, provider_id):
    require(type(restart) is dict and set(restart)=={'version','kind','dispatch_id',
            'provider_task_id','pid','invocation_id','start_sha256','intent_sha256','resumed_sha256'}
            and type(restart['version']) is int and restart['version']==1
            and restart['kind']=='CONTROLLED_IMAGE_RESTART'
            and type(restart['pid']) is int and restart['pid']==int(pilot_unit['MainPID'])
            and restart['invocation_id']==pilot_unit['InvocationID']
            and restart['dispatch_id']==dispatch_id
            and restart['provider_task_id']==provider_id
            and all(type(restart[k]) is str and control.SHA256.fullmatch(restart[k])
                    for k in ('start_sha256','intent_sha256','resumed_sha256')),
            'PILOT_OWNER_RESTART_UNIT_MISMATCH')


def step(wheels, credential, token, package_raw, payload_raw, accepted_payload, run_guard):
    require(os.geteuid()==0 and os.uname().nodename=='autopilot-lite-vnic','PILOT_OWNER_HOST')
    require(control.digest(payload_raw)==accepted_payload,'PILOT_OWNER_PAYLOAD_NOT_ACCEPTED')
    value=control.strict_json(payload_raw,262144)
    require(set(value)=={'version','action','source','package_sha256','plan_base64',
        'accepted_plan_sha256','agreement','accepted_agreement_sha256','baseline_sha256',
        'accepted_receipt_sha256','accepted_discovery_sha256','accepted_request_sha256',
        'accepted_terminal_sha256'}
        and value['version']==1 and type(value['version']) is int
        and value['action'] in ('intake','publish','mark','permit','inspect','terminal','restore-controls'),
        'PILOT_OWNER_PAYLOAD_SCHEMA')
    run_guard.assert_current()
    package=control.verified_package(package_raw,value['source'],value['package_sha256'])
    candidate=control.plan.source_path(value['source'])
    release.staging.verify_release(candidate,package['runtime'])
    # Driver is byte-verified before any DB-dependent pilot imports.
    with loaded_runtime(wheels) as (psycopg,_):
        from database import light_native_pilot_intake as intake
        from ops.native_maintenance_owner_attest import parameters
        from oracle_autopilot import light_native_preflight as preflight
        plan_raw=base64.b64decode(value['plan_base64'],validate=True)
        plan=intake.Plan(plan_raw,value['accepted_plan_sha256'])
        require(plan.value['source']==value['source'],'PILOT_OWNER_PLAN_SOURCE')
        baseline_raw=control.read(control.plan.ROOT/'baseline.json',262144)
        require(control.digest(baseline_raw)==value['baseline_sha256'],'PILOT_OWNER_BASELINE')
        baseline=control.strict_json(baseline_raw,262144)
        require(baseline['source']==value['source'] and baseline['package_sha256']==value['package_sha256']
                and baseline['scope_sha256']==plan.scope_digest
                and baseline['agreement_sha256']==value['accepted_agreement_sha256'],
                'PILOT_OWNER_BASELINE_SCOPE')
        if value['action']=='inspect':
            control.directory(ROOT,0,0,0o700)
            return {name:control.digest(control.read(ROOT/name)) for name in
                ('intake.json','broker.json','discovery.json','publication.json','permit.json',
                 'terminal.json','restart.json','controls-restored.json')
                if (ROOT/name).exists()}
        if value['action'] in ('terminal','restore-controls'):
            # Cleanup/readback can outlive the accepted execution window, but
            # must independently prove the owned pilot stopped and HOLD returned.
            request,prior,protected,protected_digest,directory=control.ledger(value['accepted_request_sha256'])
            require(request.value['scope']==plan.scope
                    and request.value['baseline_sha256']==value['baseline_sha256'],
                    'PILOT_OWNER_TERMINAL_SCOPE')
            require(control.restored_receipt(request,prior,protected,protected_digest,directory) is not None,
                    'PILOT_OWNER_SERVICE_NOT_RESTORED')
            receipt=accepted_record('intake.json',value['accepted_receipt_sha256'])
            require(receipt['plan_sha256']==plan.digest,'PILOT_OWNER_RECEIPT_SCOPE')
            if value['action']=='terminal':
                observed_target(API(token),plan)
                with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                    conn.read_only=True
                    intake.engine.identity(conn,intake.target())
                    native=intake.one(conn,'SELECT to_jsonb(n) FROM autopilot.native_cli_receipt n '
                        'WHERE dispatch_id=%s::uuid',(receipt['dispatch_id'],))
                    provider,restart=fresh_provider_result(candidate,receipt['dispatch_id'],
                        release.CLOUD_ENVIRONMENT_ID,base64.b64decode(request.value['permit_b64'],validate=True),
                        native['request'])
                    pilot_unit=control.strict_json(control.read(directory/'pilot-unit.json',4096),4096)
                    verify_restart_unit(restart,pilot_unit,receipt['dispatch_id'],native['provider_task_id'])
                    from oracle_autopilot import codex_cli_bridge as bridge
                    require(native['state']=='TERMINAL'
                            and native['provider_task_id']==provider['provider_task_id']
                            and native['terminal']['provider_evidence_sha256']==bridge.digest(bridge.canonical(provider))
                            and all(native['terminal'][k]==provider['report'][k]
                                    for k in ('status','result_code','summary')),
                            'PILOT_OWNER_PROVIDER_DB_MISMATCH')
                    terminal=dict(version=1,plan_sha256=plan.digest,dispatch_id=receipt['dispatch_id'],
                        task_id=receipt['task_id'],provider_task_id=native['provider_task_id'],
                        request=native['request'],result=native['terminal'])
                    raw=release.encoded(terminal)
                    intake.observe_terminal(conn,plan,receipt,raw,control.digest(raw))
                recovery_raw=release.encoded(dict(terminal_sha256=control.digest(raw),**restart))
                control.retained(ROOT/'restart.json',recovery_raw)
                control.retained(ROOT/'terminal.json',raw)
                return {'audit':'LIGHT_NATIVE_TERMINAL_CANDIDATE','record_sha256':control.digest(raw),
                        'independently_accepted':False,'controlled_restart_verified':True,
                        'restart_sha256':control.digest(recovery_raw)}
            terminal=accepted_record('terminal.json',value['accepted_terminal_sha256'])
            control.retained(ROOT/'control-restore-intent.json',payload_raw)
            with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                conn.read_only=False
                result=intake.restore_controls_after_terminal(conn,plan,receipt,release.encoded(terminal),
                    value['accepted_terminal_sha256'],ROOT/'before.json',effect_guard=run_guard.assert_running)
            with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                conn.read_only=True
                intake.observe_terminal(conn,plan,receipt,release.encoded(terminal),
                    value['accepted_terminal_sha256'])
                before=intake.engine.load_manifest(ROOT/'before.json',receipt['snapshot_sha256'])
                with conn.transaction():
                    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    config=intake.one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')
                    role=intake.one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT'")
                    require(config==before['native_config']
                            and all(role.get(k)==v for k,v in before['autopilot_role'].items() if k!='updated_at'),
                            'PILOT_OWNER_RESTORE_COMMIT_READBACK')
            control.retained(ROOT/'controls-restored.json',release.encoded(result))
            return {'audit':'LIGHT_NATIVE_CONTROLS_RESTORED',**result}
        agreement=Agreement(value['agreement'],value['accepted_agreement_sha256'],plan.scope)
        api=API(token)
        prior=guard(plan,agreement,baseline)
        pr=observed_target(api,plan)
        if value['action']=='intake':
            control.new_directory(ROOT,0,0,0o700)
            control.retained(ROOT/'plan.json',plan_raw)
            control.retained(ROOT/'intake-intent.json',payload_raw)
            with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                conn.read_only=False
                intake.engine.privileges(conn,intake.target(),True)
                result=intake.prepare(conn,plan,agreement,baseline_raw,value['baseline_sha256'],
                    ROOT/'before.json',target_open=pr['state']=='open',observed_head_sha=pr['head']['sha'],
                    observed_branch=pr['head']['ref'],effect_guard=run_guard.assert_running)
            control.retained(ROOT/'intake.json',release.encoded(result))
            name='intake.json'
        else:
            control.directory(ROOT,0,0,0o700)
            require(control.read(ROOT/'plan.json')==plan_raw,'PILOT_OWNER_PLAN_CHANGED')
            receipt=accepted_record('intake.json',value['accepted_receipt_sha256'])
            require(receipt['plan_sha256']==plan.digest,'PILOT_OWNER_RECEIPT_SCOPE')
            if value['action']=='publish':
                control.retained(ROOT/'publication-intent.json',payload_raw)
                run_guard.assert_running()
                result=broker_publish(candidate,prior,receipt)
                control.retained(ROOT/'broker.json',release.encoded(result))
                observed=discovery(api,result,receipt)
                control.retained(ROOT/'discovery.json',release.encoded(observed))
                name='discovery.json'
            elif value['action']=='mark':
                observed=accepted_record('discovery.json',value['accepted_discovery_sha256'])
                published=control.strict_json(control.read(ROOT/'broker.json'),65536)
                require(discovery(api,published,receipt)==observed,'PILOT_OWNER_DISCOVERY_DRIFT')
                with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                    conn.read_only=False
                    result=intake.mark_reviewed_publication(conn,plan,agreement,receipt,
                        release.encoded(observed),value['accepted_discovery_sha256'],effect_guard=run_guard.assert_running)
                control.retained(ROOT/'publication.json',release.encoded(result))
                name='publication.json'
            else:
                publication=control.strict_json(control.read(ROOT/'publication.json'),65536)
                require(publication['dispatch_id']==receipt['dispatch_id'] and publication['published'] is True,
                        'PILOT_OWNER_NOT_PUBLISHED')
                dispatch={k:receipt['dispatch'][k] for k in
                    ('dispatch_id','expected_head_sha','mode','target_pr','task_fingerprint')}
                dispatch.update(branch=plan.value['branch'],assignment=receipt['assignment'])
                issued=int(time.time());expires=int(agreement.end)
                with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                    conn.read_only=True
                    evidence=preflight.observe(conn,dispatch,issued_at=issued,expires_at=expires,
                        agreement_sha256=value['accepted_agreement_sha256'])
                require(evidence['goal_json_sha256']==receipt['goal_json_sha256'],
                        'PILOT_OWNER_GOAL_CHANGED')
                cloud=control.read(release.ROOT/value['source']/'environment.json',4096)
                result=dict(version=1,source=value['source'],dispatch=dispatch,
                    environment_id=release.CLOUD_ENVIRONMENT_ID,
                    environment_evidence_sha256=control.digest(cloud),issued_at=issued,expires_at=expires,
                    owner_preflight=evidence)
                control.retained(ROOT/'permit.json',release.encoded(result))
                name='permit.json'
        guard(plan,agreement,baseline)
        raw=control.read(ROOT/name)
        return {'audit':'LIGHT_NATIVE_OWNER_STEP_COMPLETE','action':value['action'],
                **record_summary(raw),'pilot_submitted':False}
