"""Private deterministic request assembly; never grants or workflow mutations.

The candidate digest must be independently accepted in a later stage dispatch.
Observed HOLD never supplies an Agreement, checkpoint head, or unit acceptance.
"""
import base64
import json
import os
from pathlib import Path

from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_recovery_assets as assets
from ops import native_maintenance_store as storage
from ops import oracle_light_active_hold_attest as hold
from ops.native_maintenance_workflow_pause import encoded, digest, unique, require, validate_plan
from ops.native_maintenance_stage_request import AcceptedRequest, submit_candidate, read_request
from ops.native_maintenance_agreement import Agreement

RELEASE_ROOT = Path('/var/lib/bridge-light-native-release')


def request_value(raw, accepted, source):
    require(type(raw) is bytes and 0 < len(raw) <= 262144 and bundle.digest(raw) == accepted,
            'CANDIDATE_INPUT_DIGEST')
    v = json.loads(raw, object_pairs_hook=unique)
    require(type(v) is dict and set(v) - {'operation'} == {'version','mode','source','runtime_digest','assets','plan',
        'stage','scope_digest','prior_units','accepted_head_digest','expected_outcome','agreement','request_id'}
        and v['version'] == 1 and type(v['version']) is int and v['mode'] == 'grant_request_candidate'
        and v['source'] == source and bundle.identifier(source,40)
        and bundle.identifier(v['runtime_digest'],64) and bundle.identifier(v['request_id'],32)
        and encoded(v) == raw, 'CANDIDATE_INPUT_SCHEMA')
    require(type(v.get('operation','apply')) is str
            and v.get('operation','apply') in ('apply','rollback'), 'CANDIDATE_OPERATION')
    validate_plan(v['plan'])
    require(v['plan']['source'] == source and v['stage'] in ('prepare','execute','restore'),
            'CANDIDATE_SOURCE_STAGE')
    require(type(v['assets']) is dict and set(v['assets']) ==
        {'source_digest','manifest_digest','baseline_digest','envelope_digest'}
        and all(bundle.identifier(x,64) for x in v['assets'].values()), 'CANDIDATE_ASSETS')
    require(type(v['prior_units']) is list and len(v['prior_units']) <= 16, 'CANDIDATE_PRIORS')
    keys = set()
    for ref in v['prior_units']:
        require(type(ref) is dict and set(ref) == {'stage','run','digest'}
            and ref['stage'] in ('prepare','execute','restore') and bundle.identifier(ref['digest'],64)
            and type(ref['run']) is dict and set(ref['run']) == {'run_id','attempt','job_id'}
            and all(type(n) is int and n > 0 for n in ref['run'].values()), 'CANDIDATE_PRIOR_REF')
        key = (ref['run']['run_id'],ref['run']['attempt'])
        require(key not in keys,'CANDIDATE_PRIOR_DUPLICATE'); keys.add(key)
    if v['stage'] == 'prepare':
        require(not v['prior_units'] and v['accepted_head_digest'] is None
                and v['expected_outcome'] is None,'CANDIDATE_PREPARE')
    else:
        require(v['prior_units'] and bundle.identifier(v['accepted_head_digest'],64)
                and sum(r['stage']=='prepare' for r in v['prior_units']) == 1,'CANDIDATE_RESUME')
        require(v['expected_outcome'] in ('BEFORE','AFTER') if v['stage']=='restore'
                else v['expected_outcome'] is None,'CANDIDATE_OUTCOME')
    require(bundle.identifier(v['scope_digest'],64) or
        (v['scope_digest'] is None and v['agreement'] is None and v['stage']=='prepare'),
        'CANDIDATE_SCOPE_ACCEPTANCE')
    require(v['agreement'] is not None or v['stage']=='prepare','CANDIDATE_OBSERVE_ONLY')
    return v


def staged_hold(source, runtime_digest):
    storage.trusted_parent(RELEASE_ROOT.parent)
    storage.private_directory(RELEASE_ROOT)
    directory = RELEASE_ROOT/source
    storage.private_directory(directory)
    before_raw = hold.read(directory/'before.json',0o600,262144)
    before = json.loads(before_raw,object_pairs_hook=unique)
    staged = json.loads(hold.read(directory/'staged.json',0o600,262144),object_pairs_hook=unique)
    require(type(before) is dict and set(before) == {'version','source','bundle_sha256','hold','unit','drop'}
        and type(staged) is dict and set(staged) == {'version','source','bundle_sha256','hold','readonly_probe'}
        and all(v['version']==1 and v['source']==source and v['bundle_sha256']==runtime_digest
                for v in (before,staged)) and staged['readonly_probe'] is True
        and staged['hold']==before['hold'],'CANDIDATE_STAGED_RELEASE')
    identity = hold.HoldIdentity(**before['hold'])
    require(hold.attest()==identity,'CANDIDATE_HOLD_CHANGED')
    return identity, bundle.digest(before_raw)


def assemble(v, manifest, priors, guard):
    """The caller supplies source-authenticated, OCI-readback accepted unit bytes."""
    from ops.native_maintenance_runtime import unit_record
    from ops.native_maintenance_executor import operation_scope
    from database import native_cli_permission_engine as engine
    guard.assert_running()
    require(type(priors) is list and len(priors)==len(v['prior_units']), 'CANDIDATE_PRIOR_COUNT')
    assets.manifest(manifest,v['assets']['manifest_digest'],v['assets']['baseline_digest'])
    identity, before_digest = staged_hold(v['source'],v['runtime_digest'])
    target=engine.Target(**{**assets.EXPECTED_TARGET,'neon':engine.NeonBinding(**assets.EXPECTED_TARGET['neon'])})
    origin=None
    if v['stage']!='prepare':
        origin=next(r['run'] for r in v['prior_units'] if r['stage']=='prepare')
    scope=operation_scope(target=target,operation=v.get('operation','apply'),manifest_digest=v['assets']['manifest_digest'],
        plan_digest=digest(v['plan']),source=v['source'],expected_route=dict(version=1,backend='neon',database='autopilot',epoch=0),
        approved_hold=identity,origin_run=origin,staged=True,observed_admission=True)
    scope_digest=digest(scope)
    require(v['scope_digest'] is None or v['scope_digest']==scope_digest,'CANDIDATE_SCOPE_CHANGED')
    records=[]
    for ref, raw in zip(v['prior_units'],priors):
        require(bundle.digest(raw)==ref['digest'],'CANDIDATE_UNIT_DIGEST')
        row=unit_record(json.loads(raw,object_pairs_hook=unique))
        require(encoded(row)==raw and row['source']==v['source'] and row['scope_digest']==scope_digest
            and row['stage']==ref['stage'] and row['run']==ref['run'],'CANDIDATE_UNIT_BINDING')
        root=storage.PARENT/storage.NAME
        storage.private_directory(root); storage.persistent_mount(root)
        storage.private_directory(root/scope_digest); storage.private_directory(root/scope_digest/'units')
        path=root/scope_digest/'units'/(str(ref['run']['run_id'])+'-'+str(ref['run']['attempt'])+'.json')
        require(hold.read(path,0o600,4096)==raw,'CANDIDATE_LOCAL_UNIT_CHANGED')
        records.append(row)
    report=dict(audit='NATIVE_GRANT_REQUEST_CANDIDATE',source=v['source'],scope_digest=scope_digest,
        plan_digest=digest(v['plan']),before_digest=before_digest,assets=v['assets'],
        stage=v['stage'],request_digest=None,approved=False,production_mutations=False)
    if v['agreement'] is None:
        require(hold.attest()==identity,'CANDIDATE_FINAL_HOLD_CHANGED')
        guard.assert_running()
        return report, None
    agreement=Agreement(v['agreement'],digest(v['agreement']),scope)
    value=dict(version=1,request_id=v['request_id'],source=v['source'],assets=v['assets'],packet=dict(
        version=1,stage=v['stage'],scope=scope,plan=v['plan'],baseline_digest=v['assets']['baseline_digest'],
        agreement=v['agreement'],prior_units=records,accepted_head_digest=v['accepted_head_digest'],
        expected_outcome=v['expected_outcome']))
    raw=encoded(value); sha=bundle.digest(raw)
    AcceptedRequest(raw,sha,v['source'])
    agreement.assert_held(scope_digest)
    require(hold.attest()==identity,'CANDIDATE_FINAL_HOLD_CHANGED')
    guard.assert_running()
    # Create-only. An existing leaf is inspected separately; no overwrite/retry.
    require(submit_candidate(raw)==sha and read_request(sha)==raw,'CANDIDATE_RETAIN_FAILED')
    guard.assert_running(); agreement.assert_held(scope_digest)
    report['request_digest']=sha
    return report,raw


def main(source,run_id,attempt,accepted,wheel_digest,envelope):
    from ops.native_maintenance_run_guard import RehearsalRunBinding,API
    require(os.getuid()==0 and os.uname().nodename=='autopilot-lite-vnic','CANDIDATE_HOST')
    require(type(envelope) is dict and set(envelope)=={'request','manifest','units','token','job_id','driver'},
            'CANDIDATE_ENVELOPE')
    run=RehearsalRunBinding(source,run_id,attempt,API(envelope['token']))
    run.assert_running(); require(run.job_id==envelope['job_id'],'CANDIDATE_JOB')
    raw=base64.b64decode(envelope['request'],validate=True)
    v=request_value(raw,accepted,source)
    from ops.native_maintenance_owner_host import loaded_runtime
    wheels=base64.b64decode(envelope['driver'],validate=True)
    require(bundle.digest(wheels)==wheel_digest,'CANDIDATE_DRIVER_DIGEST')
    with loaded_runtime(wheels):
        report,result=assemble(v,base64.b64decode(envelope['manifest'],validate=True),
            [base64.b64decode(x,validate=True) for x in envelope['units']],run)
    print(json.dumps(dict(report=report,request=base64.b64encode(result).decode() if result else None),sort_keys=True))
