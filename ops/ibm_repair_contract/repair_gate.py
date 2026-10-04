"""PREPARED ONLY: pure, bounded repair admission. No I/O or host actions.

The caller must independently authenticate the reviewed Policy and acquire real
typed snapshots. Passing fabricated inputs is not an authorization mechanism.
This gate permits no format, mount, cleanup, start, or permission relaxation.
"""
from dataclasses import dataclass
import math
import re

UV = 'universal-video-container.service'
CONFIG_PATH = '/etc/tmpfiles.d/bridge-school-universal-video.conf'
OPERATION = 'UV_TMPFILES_CONFIG_ONLY'
HEX = re.compile(r'[0-9a-f]{64}\Z')
INV = re.compile(r'[0-9a-f]{32}\Z')
UNIT_FIELDS = {'active','sub','main_pid','control_pid','invocation','restarts','job'}

class Refused(ValueError): pass

def require(value, reason):
    if not value: raise Refused(reason)

def number(value):
    return type(value) in (int,float) and math.isfinite(value)

@dataclass(frozen=True)
class Policy:
    # These are immutable reviewed expectations, never auto-learned at admission.
    host: str
    boot: str
    run: str
    artifact_sha256: str
    candidate_sha256: str
    scratch_uuid: str
    device_unit: str
    fsck_unit: str
    mount_unit: str
    config_hashes: tuple
    dependency_edges: tuple
    protected_units: tuple
    protected_containers: tuple
    t0_wall: float
    t0_mono: float
    admission_wall: float
    admission_mono: float
    # Must cover the separately reviewed operation plus safe-stop overhead.
    reserve_seconds: float

def check_policy(p):
    require(isinstance(p,Policy),'POLICY_TYPE')
    require(all(isinstance(v,str) and v for v in (p.host,p.boot,p.run,p.scratch_uuid)),'POLICY_IDENTITY')
    require(HEX.fullmatch(p.artifact_sha256) and HEX.fullmatch(p.candidate_sha256),'POLICY_PINS')
    require(len({p.device_unit,p.fsck_unit,p.mount_unit})==3,'POLICY_JOB_SCOPE')
    require(bool(p.config_hashes) and len(dict(p.config_hashes))==len(p.config_hashes),'POLICY_CONFIG_SCOPE')
    require(all(isinstance(path,str) and HEX.fullmatch(digest) for path,digest in p.config_hashes),'POLICY_CONFIG_HASH')
    require(bool(p.dependency_edges) and len(set(p.dependency_edges))==len(p.dependency_edges),'POLICY_DEPENDENCIES')
    require(bool(p.protected_units) and UV not in p.protected_units and len(set(p.protected_units))==len(p.protected_units),'POLICY_PROTECTED_UNITS')
    require(bool(p.protected_containers) and len(set(p.protected_containers))==len(p.protected_containers),'POLICY_CONTAINERS')
    require(all(number(x) for x in (p.t0_wall,p.t0_mono,p.admission_wall,p.admission_mono,p.reserve_seconds)),'POLICY_CLOCK')
    require(0<=p.admission_wall-p.t0_wall<120 and 0<=p.admission_mono-p.t0_mono<120,'ADMISSION_EXPIRED')
    require(30<=p.reserve_seconds<150,'OPERATION_RESERVE')

def unit(row):
    require(isinstance(row,dict) and set(row)==UNIT_FIELDS,'UNIT_SCHEMA')
    require(all(type(row[k]) is int and row[k]>=0 for k in ('main_pid','control_pid','restarts')),'UNIT_NUMERIC')
    require(row['job'] is None or type(row['job']) is int and row['job']>0,'UNIT_JOB')
    require(isinstance(row['invocation'],str) and (row['invocation']=='' or INV.fullmatch(row['invocation'])),'UNIT_INVOCATION')

def snapshot(p,s):
    require(set(s)=={'host','boot','run','artifact_sha256','wall','mono','jobs','missing_uuid','uuid_present','config_hashes','dependency_edges','units','containers','uv','runtime_directory'},'SNAPSHOT_SCHEMA')
    require((s['host'],s['boot'],s['run'],s['artifact_sha256'])==(p.host,p.boot,p.run,p.artifact_sha256),'IDENTITY_OR_PIN')
    require(number(s['wall']) and number(s['mono']),'SNAPSHOT_CLOCK')
    require(s['missing_uuid']==p.scratch_uuid and s['uuid_present'] is False,'SCRATCH_UUID_CHANGED')
    expected={(p.device_unit,'start','running'),(p.fsck_unit,'start','waiting'),(p.mount_unit,'start','waiting')}
    jobs=s['jobs'];require(isinstance(jobs,list) and len(jobs)==3,'JOBS_COUNT')
    require(all(isinstance(j,dict) and set(j)=={'id','unit','type','state'} and type(j['id']) is int and j['id']>0 for j in jobs),'JOB_SCHEMA')
    require(len({j['id'] for j in jobs})==3 and {(j['unit'],j['type'],j['state']) for j in jobs}==expected,'UNEXPECTED_JOB')
    require(s['config_hashes']==dict(p.config_hashes),'CONFIG_DRIFT')
    edges=s['dependency_edges'];require(isinstance(edges,list) and len(edges)==len(p.dependency_edges) and {tuple(e) for e in edges}==set(p.dependency_edges),'DEPENDENCY_DRIFT')
    require(isinstance(s['units'],dict) and set(s['units'])==set(p.protected_units),'PROTECTED_UNIT_SCOPE')
    for row in s['units'].values():
        unit(row);require(row['active']=='active' and row['sub']=='running' and row['main_pid']>0 and row['control_pid']==0 and row['job'] is None and INV.fullmatch(row['invocation']),'PROTECTED_UNIT_NOT_RUNNING')
    require(isinstance(s['containers'],dict) and set(s['containers'])==set(p.protected_containers),'CONTAINER_SCOPE')
    for row in s['containers'].values():
        require(isinstance(row,dict) and set(row)=={'id','running','pid','restarts','oom'},'CONTAINER_SCHEMA')
        require(isinstance(row['id'],str) and HEX.fullmatch(row['id']) and row['running'] is True and row['oom'] is False and type(row['pid']) is int and row['pid']>0 and type(row['restarts']) is int and row['restarts']>=0,'CONTAINER_NOT_RUNNING')
    unit(s['uv'])
    directory=s['runtime_directory']
    require(isinstance(directory,dict) and set(directory)=={'state','ancestors_verified','empty','owner','group','mode'},'DIRECTORY_SCHEMA')
    require(directory['ancestors_verified'] is True,'DIRECTORY_ANCESTORS_UNKNOWN')
    # Refuse automatic repair/chown of existing content or an unexpected owner.
    require(directory['state']=='ABSENT' and all(directory[k] is None for k in ('empty','owner','group','mode')) or
            directory['state']=='DIRECTORY' and directory['empty'] is True and directory['owner']=='universal-video' and directory['group']=='universal-video' and type(directory['mode']) is int and directory['mode']==0o750,'DIRECTORY_PRESERVATION')

def evaluate(p,phase,samples,queue,authorization,stop_receipt,now_wall,now_mono):
    """Return narrow permission description, or Refused. Performs NO action."""
    check_policy(p)
    require(phase in ('PRE_QUIESCE','CONFIG_WRITE'),'PHASE')
    require(number(now_wall) and number(now_mono),'CLOCK_SCHEMA')
    wall_elapsed=now_wall-p.t0_wall;mono_elapsed=now_mono-p.t0_mono
    require(0<=wall_elapsed<270-p.reserve_seconds and 0<=mono_elapsed<270-p.reserve_seconds and abs(wall_elapsed-mono_elapsed)<=2,'DEADLINE_OR_CLOCK_DRIFT')
    require(p.admission_wall<=now_wall and p.admission_mono<=now_mono,'ADMISSION_IN_FUTURE')
    require(isinstance(samples,list) and len(samples)==2,'TWO_SAMPLES_REQUIRED')
    for s in samples:
        snapshot(p,s)
        require(p.admission_wall<=s['wall'] and p.admission_mono<=s['mono'],'SAMPLE_BEFORE_ADMISSION')
        require(0<=now_wall-s['wall']<=10 and 0<=now_mono-s['mono']<=10,'SNAPSHOT_STALE')
    a,b=samples
    require(0<b['mono']-a['mono']<=10 and 0<b['wall']-a['wall']<=10,'SAMPLE_ORDER')
    for key in ('jobs','units','containers','runtime_directory'):
        if key=='jobs':require(sorted(a[key],key=lambda x:x['id'])==sorted(b[key],key=lambda x:x['id']),'JOB_REPLACED')
        else:require(a[key]==b[key],'PROTECTED_STATE_CHANGED')
    require(set(queue)=={'host','boot','run','wall','mono','queued','running','query_complete','read_only'},'QUEUE_SCHEMA')
    require((queue['host'],queue['boot'],queue['run'])==(p.host,p.boot,p.run),'QUEUE_BINDING')
    require(number(queue['wall']) and number(queue['mono']) and 0<=now_wall-queue['wall']<=10 and 0<=now_mono-queue['mono']<=10,'QUEUE_STALE')
    require(queue['query_complete'] is True and queue['read_only'] is True and type(queue['queued']) is int and type(queue['running']) is int and queue['queued']==queue['running']==0,'QUEUES_NOT_IDLE')
    require(set(authorization)=={'host','boot','run','operation','candidate_sha256','uv_stop','config_write'},'AUTH_SCHEMA')
    require((authorization['host'],authorization['boot'],authorization['run'],authorization['operation'],authorization['candidate_sha256'])==(p.host,p.boot,p.run,OPERATION,p.candidate_sha256),'AUTH_SCOPE')
    require(authorization['uv_stop'] is True,'STOP_NOT_AUTHORIZED')
    if phase=='PRE_QUIESCE':
        require(stop_receipt is None,'UNEXPECTED_STOP_RECEIPT')
        for s in samples:
            uv=s['uv'];require(uv['active']=='activating' and uv['sub']=='auto-restart' and uv['main_pid']==uv['control_pid']==0 and uv['job'] is None and INV.fullmatch(uv['invocation']),'UV_DEFECT_SCOPE')
        write=False
    else:
        require(authorization['config_write'] is True,'CONFIG_NOT_AUTHORIZED')
        require(isinstance(stop_receipt,dict) and set(stop_receipt)=={'host','boot','run','unit','completed','wall','mono'},'STOP_RECEIPT_SCHEMA')
        require((stop_receipt['host'],stop_receipt['boot'],stop_receipt['run'],stop_receipt['unit'])==(p.host,p.boot,p.run,UV) and stop_receipt['completed'] is True,'STOP_RECEIPT_BINDING')
        require(number(stop_receipt['wall']) and number(stop_receipt['mono']) and p.admission_wall<=stop_receipt['wall']<=a['wall'] and p.admission_mono<=stop_receipt['mono']<=a['mono'],'STOP_RECEIPT_TIME')
        for s in samples:
            uv=s['uv'];require(uv['active']=='inactive' and uv['sub']=='dead' and uv['main_pid']==uv['control_pid']==0 and uv['job'] is None,'UV_NOT_QUIESCENT')
        require(a['uv']==b['uv'],'UV_CHANGED_AFTER_STOP')
        write=True
    return {'phase':phase,'uv_stop_admitted':not write,'config_write_admitted':write,'config_path':CONFIG_PATH if write else None,
            'known_pending_scratch_jobs':3,'global_settle_asserted':False,'format_admitted':False,'mount_admitted':False,
            'start_admitted':False,'cleanup_admitted':False,'permission_relaxation_admitted':False,'lease_or_lock_asserted':False}
