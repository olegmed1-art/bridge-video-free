"""Observation of an ended stage; never approval, repair, resume or SQL.

Local and remote private bytes stay in the authenticated SSH/runner channel.
Only an explicit later review may accept any reported unit or checkpoint digest.
"""
import base64
import copy
from dataclasses import asdict
import fcntl
import json
import os
import re
import stat
from pathlib import Path

from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_checkpoint as checkpoint
from ops import native_maintenance_snapshot as snapshot
from ops import native_maintenance_store as storage
from ops import oracle_light_active_hold_attest as hold
from ops.native_maintenance_stage_request import AcceptedRequest, read_request, ROOT
from ops.native_maintenance_workflow_pause import Journal, encoded, digest, require, unique

LIMIT = 2*1024*1024
EVENTS = frozenset(('BOUND','PLAN','PREPARED','SESSION_BOUND','SESSION_INTENT','SESSION_RESULT',
                   'SESSION_ERROR','RESTORE_INTENT','RESTORED','PAUSE_INTENT','PAUSED',
                   'DISABLE_INTENT','DISABLED','ENABLE_INTENT','ENABLED'))


def input_value(raw, accepted, source):
    require(type(raw) is bytes and 0<len(raw)<=4096 and bundle.digest(raw)==accepted,
            'INSPECT_INPUT_DIGEST')
    v=json.loads(raw,object_pairs_hook=unique)
    require(type(v) is dict and set(v)=={'version','mode','source','failed_source',
        'failed_request_digest','failed_run'} and type(v['version']) is int and v['version']==1
        and v['mode']=='stage_inspect' and v['source']==source and bundle.identifier(source,40)
        and bundle.identifier(v['failed_source'],40) and bundle.identifier(v['failed_request_digest'],64)
        and type(v['failed_run']) is dict and set(v['failed_run'])=={'run_id','attempt','job_id'}
        and all(type(n) is int and n>0 for n in v['failed_run'].values()) and encoded(v)==raw,
        'INSPECT_INPUT_SCHEMA')
    return v


def failed_run(api,v):
    from ops.native_maintenance_run_guard import OWNER_ID,OWNER,REPOSITORY
    ref=v['failed_run'];run=api.get('/actions/runs/'+str(ref['run_id']))
    require(run['id']==ref['run_id'] and run['run_attempt']==ref['attempt']
        and run['head_sha']==v['failed_source'] and run['head_branch']=='main'
        and run['event']=='workflow_dispatch' and run['status']=='completed'
        and run['conclusion'] in ('failure','cancelled','timed_out')
        and run['repository']['full_name']==REPOSITORY
        and run['head_repository']['full_name']==REPOSITORY
        and all(run[n]['login']==OWNER and type(run[n]['id']) is int
                for n in ('actor','triggering_actor'))
        and run['actor']['id']==run['triggering_actor']['id']==OWNER_ID
        and run['path']=='.github/workflows/native-maintenance-stages.yml','INSPECT_FAILED_RUN')
    jobs=api.get('/actions/runs/'+str(ref['run_id'])+'/attempts/'+str(ref['attempt'])+'/jobs?per_page=100')
    require(type(jobs['total_count']) is int and jobs['total_count']==2
        and type(jobs['jobs']) is list and len(jobs['jobs'])==2
        and sorted(j['name'] for j in jobs['jobs'])==['contract','stage'],'INSPECT_JOB_SET')
    require(all(type(j['id']) is int and j['id']>0
        and type(j['run_id']) is int and j['run_id']==ref['run_id']
        and type(j['run_attempt']) is int and j['run_attempt']==ref['attempt']
        and j['head_sha']==v['failed_source'] for j in jobs['jobs'])
        and len({j['id'] for j in jobs['jobs']})==2,'INSPECT_JOB_IDENTITY')
    require(all(j['status']=='completed' and j['conclusion']=='success'
        for j in jobs['jobs'] if j['name']=='contract'),'INSPECT_CONTRACT_FAILED')
    selected=[j for j in jobs['jobs'] if j['id']==ref['job_id']]
    require(len(selected)==1 and selected[0]['name']=='stage'
        and selected[0]['status']=='completed' and selected[0]['run_id']==ref['run_id']
        and selected[0]['conclusion'] in ('failure','cancelled','timed_out'),'INSPECT_FAILED_JOB')


def present_nofollow(path):
    try: info=path.lstat()
    except FileNotFoundError: return False
    require(not stat.S_ISLNK(info.st_mode),'INSPECT_SYMLINK')
    return True


def private_read(path,limit):
    storage.trusted_parent(path.parent)
    storage.private_directory(path.parent)
    parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        fd=storage.open_file(parent,path.name,os.O_RDONLY)
        with os.fdopen(fd,'rb') as stream: raw=stream.read(limit+1)
        require(len(raw)<=limit,'INSPECT_FILE_SIZE')
        return raw
    finally: os.close(parent)


def inspection_packet(request, failed_run):
    """Derive observation scope from retained accepted bytes, never a new claim."""
    from ops.native_maintenance_runtime import unit_record
    packet=copy.deepcopy(request.value['packet'])
    require(packet['stage'] in ('prepare','execute','restore'),'INSPECT_STAGE')
    if packet['stage']=='prepare':
        require(not packet['prior_units'],'INSPECT_PREPARE_PRIORS')
        packet['scope']['origin_run']=failed_run
    else:
        require(packet['scope'].get('origin_run')!=failed_run,'INSPECT_ORIGIN_REUSED')
    scope_digest=digest(packet['scope'])
    priors=[unit_record(row) for row in packet['prior_units']]
    require(all(row['source']==request.source and row['scope_digest']==scope_digest
                and row['run']!=failed_run for row in priors)
            and len({(r['run']['run_id'],r['run']['attempt']) for r in priors})==len(priors),
            'INSPECT_PRIOR_BINDING')
    if packet['stage']!='prepare':
        require(any(r['stage']=='prepare' and r['run']==packet['scope']['origin_run']
                    for r in priors),'INSPECT_ORIGIN_MISSING')
    return packet


def local(v,run):
    from ops.native_maintenance_runtime import unit_record
    from ops.native_maintenance_supervisor import SelfSupervisor,PriorSupervisors
    run.assert_running();failed_run(run.api,v)
    supervisor=SelfSupervisor(run.source,run);supervisor.assert_exclusive()
    raw=read_request(v['failed_request_digest'])
    request=AcceptedRequest(raw,v['failed_request_digest'],v['failed_source'])
    packet=inspection_packet(request,v['failed_run'])
    scope=packet['scope'];sha=digest(scope)
    prior=hold.HoldIdentity(**scope['hold'])
    require(asdict(hold.service_hold_identity())==asdict(prior),'INSPECT_HOLD_CHANGED')
    claim_path=ROOT/'claims'/(v['failed_request_digest']+'.json')
    claimed=present_nofollow(claim_path)
    if claimed:
        require(private_read(claim_path,4096)==encoded(dict(
            request_digest=v['failed_request_digest'],packet_digest=digest(packet),run=v['failed_run'])),
            'INSPECT_CLAIM_CHANGED')
    root=storage.PARENT/storage.NAME
    storage.trusted_parent(root.parent);storage.private_directory(root);storage.persistent_mount(root)
    parent=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    lock=None
    try:
        lock=storage.open_file(parent,'lock',os.O_RDWR)
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(private_read(root/'VERSION',256)==storage.VERSION,'INSPECT_STORE_VERSION')
        path=root/sha;present=present_nofollow(path)
        units=[];journals=None;manifest_digest=None
        if present:
            require(claimed,'INSPECT_UNCLAIMED_SCOPE')
            storage.private_directory(path)
            require(set(os.listdir(path))=={'operation','pause','units','manifest.json'},'INSPECT_PARTIAL_SCOPE')
            manifest=private_read(path/'manifest.json',4*1024*1024)
            manifest_digest=bundle.digest(manifest)
            require(manifest_digest==request.value['assets']['manifest_digest'],'INSPECT_MANIFEST_CHANGED')
            storage.private_directory(path/'units')
            names=os.listdir(path/'units');require(len(names)<=16,'INSPECT_UNIT_COUNT')
            for name in sorted(names):
                require(re.fullmatch(r'[1-9][0-9]{0,19}-[1-9][0-9]{0,5}\.json',name),'INSPECT_UNIT_NAME')
                data=private_read(path/'units'/name,4096)
                unit=unit_record(json.loads(data,object_pairs_hook=unique))
                require(encoded(unit)==data and unit['source']==v['failed_source']
                    and unit['scope_digest']==sha and name==str(unit['run']['run_id'])+'-'+str(unit['run']['attempt'])+'.json',
                    'INSPECT_UNIT_BINDING')
                require((unit['run']==v['failed_run'] and unit['stage']==packet['stage'])
                        or unit in packet['prior_units'],'INSPECT_UNKNOWN_STAGE')
                units.append(unit)
            require(all(row in units for row in packet['prior_units']),'INSPECT_MISSING_PRIOR')
            PriorSupervisors([u['supervisor'] for u in units],digest([u['supervisor'] for u in units])).assert_drained()
            with Journal(path/'operation',create_lock=False,max_bytes=LIMIT//4) as op, \
                 Journal(path/'pause',create_lock=False,max_bytes=LIMIT//4) as pause:
                journals={'operation':snapshot._read(op),'pause':snapshot._read(pause)}
                if op.records and pause.records:
                    require(op.records[0]['event']==dict(kind='BOUND',scope=scope),'INSPECT_SCOPE_CHANGED')
                    snapshot._parse(encoded(dict(version=1,scope_digest=sha,journals=journals)))
        result=dict(scope_digest=sha,claimed=claimed,scope_present=present,
                    manifest_digest=manifest_digest,units=units,journals=journals,
                    failed_stage=packet['stage'])
        require(len(encoded(result))<=LIMIT,'INSPECT_SIZE')
        require(read_request(v['failed_request_digest'])==raw and asdict(hold.service_hold_identity())==asdict(prior),'INSPECT_FINAL_CHANGED')
        supervisor.assert_exclusive();failed_run(run.api,v);run.assert_running()
        return result
    finally:
        if lock is not None:os.close(lock)
        os.close(parent)


def compare(observed,store,v):
    """Classify observed private bytes; never invokes accepted_latest or a PUT."""
    require(type(observed) is dict and set(observed)-{'failed_stage'}=={'scope_digest','claimed','scope_present',
        'manifest_digest','units','journals'} and bundle.identifier(observed['scope_digest'],64),
        'INSPECT_LOCAL_SCHEMA')
    scope=observed['scope_digest'];store.assert_private()
    from ops.native_maintenance_stage_unit import decode, path
    stage=observed.get('failed_stage','prepare')
    require(stage in ('prepare','execute','restore'),'INSPECT_STAGE')
    require(type(observed['units']) is list and len(observed['units'])<=16,'INSPECT_UNIT_COUNT')
    refs=[dict(source=v['failed_source'],scope_digest=scope,stage=stage,run=v['failed_run'])]
    by_run={}
    for unit in observed['units']:
        expected={k:unit[k] for k in ('source','scope_digest','stage','run')}
        require(expected['source']==v['failed_source'] and expected['scope_digest']==scope,
                'INSPECT_UNIT_BINDING')
        decode(encoded(unit),expected)
        key=(unit['run']['run_id'],unit['run']['attempt'])
        require(key not in by_run,'INSPECT_DUPLICATE_UNIT');by_run[key]=unit
        if unit['run']==v['failed_run']:
            require(unit['stage']==stage,'INSPECT_UNKNOWN_STAGE')
        else:refs.append(expected)
    unit_reports=[]
    for expected in refs:
        local_unit=by_run.get((expected['run']['run_id'],expected['run']['attempt']))
        if local_unit is not None:decode(encoded(local_unit),expected)
        remote=store._read(path(expected),4096)
        remote_unit=None if remote is None else decode(remote[0],expected)
        require(local_unit is None or remote_unit is None or local_unit==remote_unit,
                'INSPECT_REMOTE_UNIT_CONFLICT')
        unit_reports.append(dict(stage=expected['stage'],run=expected['run'],
            local_digest=None if local_unit is None else digest(local_unit),
            remote_digest=None if remote_unit is None else digest(remote_unit),
            local_present=local_unit is not None,remote_present=remote_unit is not None))
    head=checkpoint.read_head(store,scope);archive=None;local_raw=None
    rows=observed['journals']
    if rows is not None and all(rows[n] for n in snapshot.NAMES):
        local_raw=encoded(dict(version=1,scope_digest=scope,journals=rows));snapshot._parse(local_raw)
    relation='no_head' if head is None else 'mismatch_or_unknown'
    if head is not None:
        meta=checkpoint.parse_head(head[0],scope)
        archive=checkpoint.read_archive(store,scope,meta['archive_digest'])
        if local_raw==archive:relation='exact_pair'
        elif local_raw is not None:
            before=snapshot._parse(archive)['journals']
            if all(rows[n][:len(before[n])]==before[n] for n in snapshot.NAMES):
                relation='local_uncheckpointed_suffix'
        require(checkpoint.read_head(store,scope)==head,'INSPECT_REMOTE_HEAD_CHANGED')
    else:require(checkpoint.read_head(store,scope) is None,'INSPECT_REMOTE_HEAD_CHANGED')
    store.assert_private()
    events={}
    if rows is not None:
        for name in snapshot.NAMES:
            events[name]=[]
            for raw in rows[name]:
                kind=json.loads(raw,object_pairs_hook=unique)['event'].get('kind')
                events[name].append(kind if kind in EVENTS else 'OTHER')
    return dict(audit='NATIVE_STAGE_READBACK',scope_digest=scope,claimed=observed['claimed'],
        scope_present=observed['scope_present'],manifest_digest=observed['manifest_digest'],
        units=unit_reports,head_digest=None if head is None else checkpoint.sha(head[0]),
        archive_digest=None if archive is None else checkpoint.sha(archive),
        local_pair_digest=None if local_raw is None else checkpoint.sha(local_raw),
        relation=relation,events=events,production_mutations=False,resume_authorized=False)


def main(source,run_id,attempt,accepted,wheel_digest,envelope):
    from ops.native_maintenance_run_guard import RehearsalRunBinding,API
    from ops.native_maintenance_owner_host import loaded_runtime
    require(os.getuid()==0 and os.uname().nodename=='autopilot-lite-vnic','INSPECT_HOST')
    require(type(envelope) is dict and set(envelope)=={'request','token','job_id','driver'},'INSPECT_ENVELOPE')
    run=RehearsalRunBinding(source,run_id,attempt,API(envelope['token']))
    run.assert_running();require(run.job_id==envelope['job_id'],'INSPECT_JOB')
    v=input_value(base64.b64decode(envelope['request'],validate=True),accepted,source)
    wheels=base64.b64decode(envelope['driver'],validate=True)
    require(bundle.digest(wheels)==wheel_digest,'INSPECT_DRIVER')
    with loaded_runtime(wheels):result=local(v,run)
    print(encoded(result).decode(),flush=True)
