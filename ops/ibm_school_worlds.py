#!/usr/bin/env python3
"""One fixed school compute job; retains every isolation gate and old queue.

The controller owns provider Start/Stop and pinned SSH. Only reviewed pure
stdlib WORLD_GENERATE code runs here. Existing snapshot configuration is verified
before and after computation. This module never calls a service mutation.
"""
from __future__ import annotations
import time
_STARTED = globals().get('__bundle_started_monotonic__', time.monotonic())
import base64
import ctypes
import hashlib
import json
import os
import platform
import re
import signal
import sys
import zlib
try:
    from . import ibm_unit_isolation as safety
    from . import ibm_worlds_validator as validator
except ImportError:
    import ibm_unit_isolation as safety
    import ibm_worlds_validator as validator

def generate_worlds(**kwargs):
    # Lazy import permits controller CLI imports without a repository PYTHONPATH.
    # The actual guest has the verified standalone generator preloaded.
    try:
        from ibm_worlds_generator import generate_worlds as generate
    except ImportError:
        from bridge_school_api.ai_worlds import generate_worlds as generate
    return generate(**kwargs)

REQUEST_SCHEMA = 'bridge.ibm.school-worlds.request.v1'
RECEIPT_SCHEMA = 'bridge.ibm.school-worlds.receipt.v1'
GENERATOR_SHA256 = 'c43b595ded92ee74da5ac884737585b0bfcc5b97a3c689afe126079ca04f884d'
SNAPSHOT_ID = '38123951485-e599b3b39325'
SNAPSHOT_SHA256 = '1bd1490b28c4b184729bc31b7650f83dd372af1fe545e434c4695971f0626c92'
ISOLATED_RUN = '38125459269'
ISOLATED_HEAD = '1c000350ffdecf1387d779d44472d4d219d02ad0'
SECONDS = 60
MAX_RECEIPT = 49152
MAX_ARTIFACT = 98304
REASONS = frozenset(('OK','REQUEST_INVALID','ROOT_REQUIRED','PARENT_LOST','DEADLINE','CANCELLED',
    'SNAPSHOT_INVALID','UNIT_BUSY','CGROUP_NOT_EMPTY','GATE_NOT_EFFECTIVE','FILE_DRIFT','COLLISION',
    'INVALID_RESULT','ARTIFACT_INVALID','READ_ONLY_COMMAND_REQUIRED','PATH_UNSAFE','ARCHIVE_FAILED',
    'FILE_LIMIT','RESTORE_MISMATCH','METADATA_INVALID','SAFETY_GUARD_BLOCKED','INTERNAL_BLOCKED'))

def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()

def require(ok, code):
    if not ok:
        raise safety.Blocked(code)

def validate_request(payload):
    keys={'schema','operation','run_id','attempt','head','identity','transport','snapshot_id','snapshot_sha256'}
    require(type(payload) is dict and set(payload)==keys,'REQUEST_INVALID')
    require(payload['schema']==REQUEST_SCHEMA and payload['operation']=='generate_worlds','REQUEST_INVALID')
    require(type(payload['run_id']) is str and re.fullmatch('[1-9][0-9]{0,19}',payload['run_id'])
        and payload['run_id'] not in {SNAPSHOT_ID.split('-')[0],ISOLATED_RUN},'REQUEST_INVALID')
    require(type(payload['attempt']) is int and payload['attempt']==1,'REQUEST_INVALID')
    require(type(payload['head']) is str and re.fullmatch('[0-9a-f]{40}',payload['head'])
        and payload['head'] not in {safety.RESUME_ORIGINAL_HEAD,ISOLATED_HEAD},'REQUEST_INVALID')
    require(type(payload['identity']) is dict and payload['identity']==safety.IDENTITY
        and type(payload['transport']) is dict and payload['transport']==safety.TRANSPORT,'REQUEST_INVALID')
    require(payload['snapshot_id']==SNAPSHOT_ID and payload['snapshot_sha256']==SNAPSHOT_SHA256,'REQUEST_INVALID')
    return payload

def readonly_runner(runner):
    """Even a future accidental call to reused maintenance cannot mutate units."""
    def call(argv, **kwargs):
        allowed=(len(argv)>2 and argv[:2]==['/usr/bin/systemctl','show']) or (
            len(argv)>5 and argv[:4]==['/usr/bin/busctl','--system','--json=short','get-property'])
        require(allowed,'READ_ONLY_COMMAND_REQUIRED')
        return runner(argv,**kwargs)
    return call

def journal_state(op):
    entries=op.fs.listdir(op.archive,limit=128)
    return [(name,hashlib.sha256(op.fs.read(op.archive+'/'+name,safety.MAX_OUTPUT)).hexdigest())
        for name,directory,link in entries if re.fullmatch(r'journal-[0-9]{3}\.json',name)]


def verify_quiet(op):
    for unit in safety.UNITS:
        row=op.show(unit)
        require(row['ActiveState']=='inactive' and row['SubState']=='dead' and not row['Job'],'UNIT_BUSY')
        if unit.endswith('.service'):
            require(row['MainPID']=='0' and row['ControlPID']=='0','UNIT_BUSY')
        require(op.cgroup_empty(row['ControlGroup']),'CGROUP_NOT_EMPTY')
    op.drift()
    op.check_gates()


def execute(payload, *, root='/', runner=None, clock=time.monotonic, host_check=None, started=None,
            generator=generate_worlds):
    began=clock() if started is None else started
    receipt=dict(schema=RECEIPT_SCHEMA, operation='generate_worlds',run_id=None,attempt=None,head=None,
        snapshot_id=SNAPSHOT_ID,snapshot_sha256=SNAPSHOT_SHA256,result='BLOCKED',reason='REQUEST_INVALID',
        python_version=platform.python_version(),generator_sha256=GENERATOR_SHA256,input_sha256=validator.INPUT_SHA256,
        validation=None,artifact=None,gates_verified=[],output_scope=None,scope_created=False,
        restore_scope=None,restore_created=False,stage='REQUEST',
        elapsed_ms=0,changes=[])
    op=None
    try:
        validate_request(payload)
        receipt.update({key:payload[key] for key in ('run_id','attempt','head')})
        def tick():
            require(clock()-began<SECONDS-1,'DEADLINE')
        tick()
        op=safety.Isolation(payload,root=root,runner=readonly_runner(runner or safety.bounded_command),
            clock=clock,host_check=host_check,started=began)
        op.budget.end=min(op.budget.end,began+SECONDS-1)
        op.host_check(op.fs)
        # Existing snapshot restoration proof writes only a fresh private staging
        # directory. Neither the original snapshot nor its journal is rewritten.
        restore_scope=safety.RECOVERY_ROOT+'/'+SNAPSHOT_ID+'/resume-restore-'+payload['run_id']+'-'+payload['head'][:12]
        receipt['restore_scope']=restore_scope
        original_mkdir=op.fs.mkdir
        def tracked_mkdir(path,**kwargs):
            if path==restore_scope:receipt['restore_created']=None
            original_mkdir(path,**kwargs)
            if path==restore_scope:receipt['restore_created']=True
        op.fs.mkdir=tracked_mkdir
        safety.load_recovery_snapshot(op,payload,prefix='resume')
        require(op.original_request['head']==safety.RESUME_ORIGINAL_HEAD
            and op.original_request['run_id']==SNAPSHOT_ID.split('-')[0],'SNAPSHOT_INVALID')
        require(set(op.own_records)==set(safety.GATE_UNITS) and set(op.conditions)==set(safety.GATE_UNITS),
            'SNAPSHOT_INVALID')
        require(op.loaded_events and op.loaded_events[-1]['event']=='ISOLATED_TARGET_SCOPE','SNAPSHOT_INVALID')
        bound=[e for e in op.loaded_events if e['event']=='CONTINUATION_BOUND']
        require(len(bound)==1 and bound[0].get('run_id')==ISOLATED_RUN and bound[0].get('head')==ISOLATED_HEAD,
            'SNAPSHOT_INVALID')
        journal_before=[('journal-'+format(index,'03d')+'.json',hashlib.sha256(safety.canonical(event)).hexdigest())
            for index,event in enumerate(op.loaded_events)]
        require(journal_state(op)==journal_before,'SNAPSHOT_INVALID')
        receipt['stage']='SNAPSHOT_VERIFIED'
        verify_quiet(op);tick()
        receipt['gates_verified']=list(safety.GATE_UNITS)
        receipt['stage']='GATES_VERIFIED'
        scope=safety.RECOVERY_ROOT+'/worlds-'+payload['run_id']+'-'+payload['head'][:12]
        receipt['output_scope']=scope
        # Intent is exported before the exclusive creation; collision is never
        # retried or used as permission to overwrite an earlier result.
        receipt['scope_created']=None
        op.fs.mkdir(scope);receipt['scope_created']=True
        op.fs.write_new(scope+'/input.json',validator.INPUT_BYTES)
        first=generator(**json.loads(validator.INPUT_BYTES));tick()
        verification=validator.validate_result(first,expected_result_sha256=validator.EXPECTED_RESULT_SHA256)
        result=canonical(first)
        require(len(result)<=MAX_ARTIFACT,'ARTIFACT_INVALID')
        second=generator(**json.loads(validator.INPUT_BYTES));tick()
        require(canonical(second)==result,'INVALID_RESULT')
        receipt['validation']=validator.validate_repeated_results(first,second,
            generation_python_version=receipt['python_version'],expected_result_sha256=validator.EXPECTED_RESULT_SHA256)
        receipt['stage']='COMPUTED'
        verify_quiet(op);tick()
        require(journal_state(op)==journal_before and hashlib.sha256(op.fs.read(op.archive+'/snapshot.json',2*safety.MAX_BYTES)).hexdigest()==SNAPSHOT_SHA256,'SNAPSHOT_INVALID')
        op.fs.write_new(scope+'/worlds.json',result)
        require(op.fs.read(scope+'/worlds.json',MAX_ARTIFACT)==result,'ARTIFACT_INVALID')
        manifest=dict(schema='bridge.ibm.school-worlds.manifest.v1',run_id=payload['run_id'],source_commit=payload['head'],
            snapshot_id=SNAPSHOT_ID,snapshot_sha256=SNAPSHOT_SHA256,generator_sha256=GENERATOR_SHA256,
            input_sha256=validator.INPUT_SHA256,worlds_sha256=hashlib.sha256(result).hexdigest(),
            worlds_bytes=len(result),python_version=receipt['python_version'],identical_repeats=2,
            purpose='COMPUTE_VERIFICATION_AND_TRAINING_INPUT_ONLY',service_changes=[])
        op.fs.write_new(scope+'/manifest.json',canonical(manifest))
        receipt['stage']='PERSISTED'
        verify_quiet(op);tick()
        require(journal_state(op)==journal_before and hashlib.sha256(op.fs.read(op.archive+'/snapshot.json',2*safety.MAX_BYTES)).hexdigest()==SNAPSHOT_SHA256,'SNAPSHOT_INVALID')
        receipt['artifact']=dict(name='worlds.json',bytes=len(result),sha256=hashlib.sha256(result).hexdigest(),
            encoding='zlib+base64',data=base64.b64encode(zlib.compress(result,9)).decode())
        receipt.update(result='WORLD_GENERATED',reason='OK',stage='COMPLETE')
    except safety.Blocked as exc:
        receipt['reason']=str(exc) if str(exc) in REASONS else 'SAFETY_GUARD_BLOCKED'
    except (ValueError,TypeError,KeyError):
        receipt['reason']='INVALID_RESULT'
    except (Exception,KeyboardInterrupt):
        receipt['reason']='INTERNAL_BLOCKED'
    receipt['elapsed_ms']=max(0,int((clock()-began)*1000))
    if receipt['result']!='WORLD_GENERATED':
        receipt['artifact']=None
    return receipt


def main(payload=None):
    handlers={};timer=False;receipt=None
    def interrupted(sig,_frame):
        raise safety.Blocked('DEADLINE' if sig==signal.SIGALRM else 'CANCELLED')
    try:
        require(platform.system()=='Linux' and os.geteuid()==0,'ROOT_REQUIRED')
        require(signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0),'INTERNAL_BLOCKED')
        for sig in (signal.SIGALRM,signal.SIGTERM,signal.SIGINT,signal.SIGHUP):
            handlers[sig]=signal.signal(sig,interrupted)
        signal.setitimer(signal.ITIMER_REAL,max(.001,SECONDS-1-(time.monotonic()-_STARTED)));timer=True
        parent=os.getppid()
        require(parent>1 and ctypes.CDLL(None).prctl(1,signal.SIGTERM)==0 and os.getppid()==parent,'PARENT_LOST')
        if payload is None:
            raw=sys.stdin.buffer.read(8193);require(len(raw)<=8192,'REQUEST_INVALID')
            payload=safety.strict_json(raw)
        receipt=execute(payload,started=_STARTED)
    except (Exception,KeyboardInterrupt) as exc:
        if receipt is None:receipt=execute(None)
        receipt.update(result='BLOCKED',artifact=None)
        receipt['reason']=str(exc) if str(exc) in REASONS else 'INTERNAL_BLOCKED'
    finally:
        if timer:signal.setitimer(signal.ITIMER_REAL,0)
        for sig,handler in handlers.items():signal.signal(sig,handler)
    raw=canonical(receipt)
    require(len(raw)<=MAX_RECEIPT,'ARTIFACT_INVALID')
    sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()
    return 0 if receipt['result']=='WORLD_GENERATED' else 3

if __name__=='__main__':
    sys.exit(main())
