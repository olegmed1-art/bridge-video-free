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
import selectors
import subprocess
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
RECEIPT_SCHEMA = 'bridge.ibm.school-worlds.receipt.v2'
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
    'FILE_LIMIT','RESTORE_MISMATCH','METADATA_INVALID','SAFETY_GUARD_BLOCKED','INTERNAL_BLOCKED',
    'BOOT_SETTLE_TIMEOUT','BOOT_JOB_INVALID','BOOT_JOB_CHANGED','BOOT_EXECUTION_CHANGED','BOOT_CONDITION_UNPROVEN','COMMAND_FAILED','COMMAND_LIMIT','JOB_DISAPPEARED'))

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

JOB_PROPERTIES=('Id','Unit','JobType','State')

class MissingJob(safety.Blocked):
    def __init__(self,index,prefix):
        super().__init__('JOB_DISAPPEARED')
        self.index=index;self.prefix=prefix

def exact_job_read(argv):
    return (len(argv)==11 and argv[:5]==['/usr/bin/busctl','--system','--json=short','get-property','org.freedesktop.systemd1']
        and re.fullmatch(r'/org/freedesktop/systemd1/job/[1-9][0-9]{0,9}',argv[5]) is not None
        and int(argv[5].rsplit('/',1)[1])<=4294967295
        and argv[6]=='org.freedesktop.systemd1.Job' and tuple(argv[7:])==JOB_PROPERTIES)

def bounded_job_command(argv, *, timeout, limit=safety.MAX_OUTPUT):
    """Exact Job reads only; categorize bounded stderr, never export it.

    The Python exec shim installs PDEATHSIG before exec. start_new_session gives
    this command a distinct group; timeout cleanup cannot kill ssh/systemd/units.
    """
    require(timeout > 0 and timeout <= 3 and limit <= safety.MAX_OUTPUT, "DEADLINE")
    require(exact_job_read(argv), 'READ_ONLY_COMMAND_REQUIRED')
    limit=min(limit,4096)
    parent = os.getpid()
    boot = ("import ctypes,os,signal; p=" + str(parent) + ";"
            "assert ctypes.CDLL(None).prctl(1,signal.SIGKILL)==0 and os.getppid()==p;"
            "a=" + repr(list(argv)) + ";os.execv(a[0],a)")
    selector = selectors.DefaultSelector()
    try:
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", boot],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 start_new_session=True,
                                 env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                                      "SYSTEMD_PAGER": "", "SYSTEMD_COLORS": "0"})
    except BaseException:
        selector.close()
        raise
    output = bytearray(); error = bytearray()
    size = 0
    end = time.monotonic() + timeout
    try:
        for stream, label in ((child.stdout, "out"), (child.stderr, "err")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map():
            left = end - time.monotonic()
            require(left > 0, "DEADLINE")
            for key, _ in selector.select(min(left, .1)):
                part = os.read(key.fd, 4096)
                if not part:
                    selector.unregister(key.fileobj)
                else:
                    size += len(part)
                    require(size <= limit, "COMMAND_LIMIT")
                    if key.data == "out":
                        output.extend(part)
                    else:
                        error.extend(part)
        # Observe exit without reaping: the leader's zombie keeps its PID/PGID
        # unavailable for reuse until all owned descendants have been killed.
        while True:
            require(time.monotonic() < end, "DEADLINE")
            status = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
            if status is not None:
                break
            time.sleep(min(.01, max(0, end - time.monotonic())))
        if status.si_code == os.CLD_EXITED and status.si_status == 1:
            for index,name in enumerate(JOB_PROPERTIES):
                expected=("Failed to get property "+name+" on interface org.freedesktop.systemd1.Job: "
                          "Unknown object '"+argv[5]+"'.\n").encode()
                if bytes(error)==expected:raise MissingJob(index,bytes(output))
        require(status.si_code == os.CLD_EXITED and status.si_status == 0 and not error, "COMMAND_FAILED")
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise safety.Blocked("DEADLINE") from None
    finally:
        selector.close()
        # Always clean the anchored group, including a closed-stdio descendant
        # of a successfully exited leader. Never reap before this killpg.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=1)
        child.stdout.close()
        child.stderr.close()


def bounded_read_command(argv,*,timeout,limit):
    if exact_job_read(argv):return bounded_job_command(argv,timeout=timeout,limit=limit)
    return safety.bounded_command(argv,timeout=timeout,limit=limit)

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


BOOT_SETTLE_SECONDS = 25
GUARD_PHASES = ('REQUEST','SNAPSHOT','PREFLIGHT','SETTLING','GENERATE','BEFORE_PERSIST','PERSIST','FINAL_VERIFY','COMPLETE')
GUARD_PREDICATES = frozenset(('ACTIVE_STATE','SUB_STATE','JOB_PENDING','MAIN_PID_NONZERO','CONTROL_PID_NONZERO',
    'CGROUP_NOT_EMPTY','UNGATED_UNIT','BOOT_JOB_INVALID','BOOT_JOB_CHANGED','BOOT_EXECUTION_CHANGED',
    'BOOT_CONDITION_UNPROVEN','BOOT_SETTLE_TIMEOUT')) | REASONS

def new_guard():
    return dict(phase='REQUEST',unit=None,failed_predicates=[],gate_check='NOT_VERIFIED',
        states=[dict(unit=u,observed=False,active=None,sub=None,job_id=None,main_pid_nonzero=None,
                     control_pid_nonzero=None,cgroup_empty=None,inactive_exit_us=None) for u in safety.UNITS],
        settle=dict(status='NOT_NEEDED',polls=0,elapsed_ms=0,jobs=[]))

def guard_fail(g, unit, predicates, code='UNIT_BUSY'):
    g['unit']=unit;g['failed_predicates']=predicates
    raise safety.Blocked(code)

def marker(op,unit,name='InactiveExitTimestampMonotonic'):
    value=safety.strict_json(op.property(unit,'Unit',name))
    require(type(value) is dict and set(value)=={'type','data'} and value['type']=='t'
        and type(value['data']) is int and 0<=value['data']<=18446744073709551615,'METADATA_INVALID')
    return value['data']

def collect_states(op,g):
    g['gate_check']='NOT_VERIFIED'
    # Clear every old row before any new read, including unread later units.
    for item in g['states']:
        item.update(observed=False,active=None,sub=None,job_id=None,main_pid_nonzero=None,
                    control_pid_nonzero=None,cgroup_empty=None,inactive_exit_us=None)
    for item in g['states']:
        unit=item['unit'];g['unit']=unit
        row=op.show(unit)
        job=int(row['Job']) if row['Job'] else 0
        require(0<=job<=4294967295,'METADATA_INVALID')
        item.update(observed=True,active=row['ActiveState'],sub=row['SubState'],job_id=job,
            main_pid_nonzero=row.get('MainPID','0')!='0',control_pid_nonzero=row.get('ControlPID','0')!='0')
        item['cgroup_empty']=op.cgroup_empty(row['ControlGroup'])
        item['inactive_exit_us']=marker(op,unit)
    g['unit']=None

def verified_gates(op,g):
    g['gate_check']='NOT_VERIFIED';g['unit']=None
    try:
        op.drift()
        require(not op.fs.exists(op.allow),'GATE_NOT_EFFECTIVE')
        expected=['ConditionPathExists',False,False,op.allow]
        for unit in safety.GATE_UNITS:
            g['unit']=unit
            require(unit in op.own_records,'GATE_NOT_EFFECTIVE')
            if unit in safety.TARGETS:op.stop_handlers(unit)
            require(op.effective_conditions(unit)==sorted(op.conditions[unit]+[expected],key=safety.canonical),
                'GATE_NOT_EFFECTIVE')
            require(op.gate_paths[unit] in op.rows[unit]['DropInPaths'].split()
                and op.rows[unit]['NeedDaemonReload']=='no','GATE_NOT_EFFECTIVE')
            require(op.fs.record(op.gate_paths[unit])==op.own_records[unit],'FILE_DRIFT')
        g['gate_check']='VERIFIED';g['unit']=None
    except BaseException:
        g['gate_check']='FAILED'
        if op.drift_failure is not None:g['unit']=op.drift_failure['unit']
        raise

def check_state_predicates(g, *, pending_allowed):
    for item in g['states']:
        unit=item['unit'];bad=[]
        for name,failed in (('ACTIVE_STATE',item['active']!='inactive'),('SUB_STATE',item['sub']!='dead'),
                ('MAIN_PID_NONZERO',item['main_pid_nonzero'] is not False),
                ('CONTROL_PID_NONZERO',item['control_pid_nonzero'] is not False),
                ('CGROUP_NOT_EMPTY',item['cgroup_empty'] is not True)):
            if failed:bad.append(name)
        if item['job_id'] and (bad or not (pending_allowed and unit in safety.GATE_UNITS)):
            bad.append('JOB_PENDING')
            if unit not in safety.GATE_UNITS:bad.append('UNGATED_UNIT')
        if bad:guard_fail(g,unit,bad,'CGROUP_NOT_EMPTY' if bad==['CGROUP_NOT_EMPTY'] else 'UNIT_BUSY')


def read_waiting_job(op,g,unit,identifier):
    g['unit']=unit
    missing=None
    try:
        raw=op.command(['/usr/bin/busctl','--system','--json=short','get-property','org.freedesktop.systemd1',
            '/org/freedesktop/systemd1/job/'+str(identifier),'org.freedesktop.systemd1.Job',*JOB_PROPERTIES])
    except MissingJob as exc:
        missing=exc;raw=exc.prefix
    count=missing.index if missing is not None else 4
    lines=raw.splitlines();require(len(lines)==count,'BOOT_JOB_INVALID')
    expected=[identifier,[unit,safety.job_unit_path(unit)],'start','waiting']
    for index,(line,kind) in enumerate(zip(lines,('u','(so)','s','s'))):
        value=safety.strict_json(line)
        require(type(value) is dict and set(value)=={'type','data'} and value['type']==kind
            and type(value['data']) is type(expected[index]) and value['data']==expected[index],'BOOT_JOB_INVALID')
    if missing is not None:raise missing


def waiting_start_job(op,g,unit,identifier):
    g['unit']=unit
    condition_before=marker(op,unit,'ConditionTimestampMonotonic')
    read_waiting_job(op,g,unit,identifier)
    return dict(unit=unit,job_id=identifier,type='start',state='waiting',execution_unchanged=True,condition_skipped=False,
        condition_before_us=condition_before,condition_after_us=None)

def require_skipped(op,g,unit,job):
    g['unit']=unit
    raw=op.property(unit,'Unit','Conditions');safety.parse_conditions(raw)
    value=safety.strict_json(raw)
    require(['ConditionPathExists',False,False,op.allow,-1] in value['data'],'BOOT_CONDITION_UNPROVEN')
    job['condition_after_us']=marker(op,unit,'ConditionTimestampMonotonic')
    require(job['condition_after_us']>job['condition_before_us'],'BOOT_CONDITION_UNPROVEN')


def verify_quiet(op,g,*,phase,settle=False,sleep=time.sleep):
    g['phase']=phase;g['unit']=None;g['failed_predicates']=[];g['gate_check']='NOT_VERIFIED'
    collect_states(op,g)
    verified_gates(op,g)  # Prove current boot's exact gates before any waiting.
    check_state_predicates(g,pending_allowed=settle)
    current={item['unit']:item['inactive_exit_us'] for item in g['states']}
    initial=getattr(op,'worlds_execution_markers',None)
    if initial is not None:
        for unit in safety.UNITS:
            if current[unit]!=initial[unit]:guard_fail(g,unit,['BOOT_EXECUTION_CHANGED'],'BOOT_EXECUTION_CHANGED')
    else:op.worlds_execution_markers=current
    pending={item['unit']:item['job_id'] for item in g['states'] if item['job_id']}
    if not pending:
        g['unit']=None;return
    require(settle and op.private_tmp_version(),'BOOT_JOB_INVALID')
    started=op.budget.clock();end=min(started+BOOT_SETTLE_SECONDS,op.budget.end-15)
    require(end>started,'DEADLINE')
    previous_end=op.pending_end;op.pending_end=end
    g['phase']='SETTLING';g['settle']['status']='WAITING'
    try:
        jobs={};missing_job=None
        for unit,identifier in pending.items():
            jobs[unit]=waiting_start_job(op,g,unit,identifier)
            g['settle']['jobs'].append(jobs[unit])
        while True:
            g['settle']['polls']+=1
            require(g['settle']['polls']<=51 and op.budget.clock()<end,'BOOT_SETTLE_TIMEOUT')
            collect_states(op,g);verified_gates(op,g)
            # Do not retain a positive historical claim after observed revival,
            # even when the activity predicate blocks before marker comparison.
            for item in g['states']:
                if item['unit'] in jobs and (item['active']!='inactive' or item['sub']!='dead'
                        or item['main_pid_nonzero'] is not False or item['control_pid_nonzero'] is not False
                        or item['cgroup_empty'] is not True
                        or item['inactive_exit_us']!=op.worlds_execution_markers[item['unit']]):
                    jobs[item['unit']]['execution_unchanged']=False
            check_state_predicates(g,pending_allowed=True)
            if missing_job is not None:
                for item in g['states']:
                    if item['job_id']:
                        guard_fail(g,item['unit'],['JOB_PENDING','BOOT_JOB_CHANGED'],'BOOT_JOB_CHANGED')
            done=True
            for item in g['states']:
                unit=item['unit'];g['unit']=unit
                if item['inactive_exit_us']!=op.worlds_execution_markers[unit]:
                    if unit in jobs:jobs[unit]['execution_unchanged']=False
                    guard_fail(g,unit,['BOOT_EXECUTION_CHANGED'],'BOOT_EXECUTION_CHANGED')
                jid=item['job_id']
                if unit not in pending:
                    if jid:guard_fail(g,unit,['JOB_PENDING','BOOT_JOB_CHANGED'],'BOOT_JOB_CHANGED')
                    continue
                if jid:
                    require(jid==pending[unit],'BOOT_JOB_CHANGED')
                    try:read_waiting_job(op,g,unit,jid)
                    except MissingJob:
                        # One immediate fresh epoch; all six must now be quiet.
                        # Initial admission never enters this recovery branch.
                        missing_job=unit;done=False;break
                    done=False
                else:
                    require_skipped(op,g,unit,jobs[unit])
                    jobs[unit].update(state='removed',condition_skipped=True)
            if done:
                check_state_predicates(g,pending_allowed=False)
                g['settle']['status']='SETTLED';g['unit']=None;return
            if missing_job is not None:continue
            left=end-op.budget.clock();require(left>0,'BOOT_SETTLE_TIMEOUT')
            sleep(min(.5,left))
    except BaseException as exc:
        timeout=isinstance(exc,safety.Blocked) and str(exc) in {'DEADLINE','PENDING_GATE_TIMEOUT','BOOT_SETTLE_TIMEOUT'}
        g['settle']['status']='TIMEOUT' if timeout else 'BLOCKED'
        if timeout:
            g['unit']=next((u for u,j in jobs.items() if j['state']=='waiting'),g['unit'])
            guard_fail(g,g['unit'],['JOB_PENDING','BOOT_SETTLE_TIMEOUT'],'BOOT_SETTLE_TIMEOUT')
        raise
    finally:
        op.pending_end=previous_end
        g['settle']['elapsed_ms']=max(0,min(BOOT_SETTLE_SECONDS*1000,int((op.budget.clock()-started)*1000)))


def execute(payload, *, root='/', runner=None, clock=time.monotonic, host_check=None, started=None,
            generator=generate_worlds,sleep=time.sleep):
    began=clock() if started is None else started
    receipt=dict(schema=RECEIPT_SCHEMA, operation='generate_worlds',run_id=None,attempt=None,head=None,
        snapshot_id=SNAPSHOT_ID,snapshot_sha256=SNAPSHOT_SHA256,result='BLOCKED',reason='REQUEST_INVALID',
        python_version=platform.python_version(),generator_sha256=GENERATOR_SHA256,input_sha256=validator.INPUT_SHA256,
        validation=None,artifact=None,gates_verified=[],output_scope=None,scope_created=False,
        restore_scope=None,restore_created=False,stage='REQUEST',
        elapsed_ms=0,changes=[],guard_evidence=new_guard())
    op=None
    try:
        validate_request(payload)
        receipt.update({key:payload[key] for key in ('run_id','attempt','head')})
        def tick():
            require(clock()-began<SECONDS-1,'DEADLINE')
        tick()
        op=safety.Isolation(payload,root=root,runner=readonly_runner(runner or bounded_read_command),
            clock=clock,host_check=host_check,started=began)
        op.budget.end=min(op.budget.end,began+SECONDS-1)
        op.host_check(op.fs)
        receipt['guard_evidence']['phase']='SNAPSHOT'
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
        verify_quiet(op,receipt['guard_evidence'],phase='PREFLIGHT',settle=True,sleep=sleep);tick()
        receipt['gates_verified']=list(safety.GATE_UNITS)
        receipt['stage']='GATES_VERIFIED'
        scope=safety.RECOVERY_ROOT+'/worlds-'+payload['run_id']+'-'+payload['head'][:12]
        receipt['output_scope']=scope
        # Intent is exported before the exclusive creation; collision is never
        # retried or used as permission to overwrite an earlier result.
        receipt['scope_created']=None
        op.fs.mkdir(scope);receipt['scope_created']=True
        op.fs.write_new(scope+'/input.json',validator.INPUT_BYTES)
        receipt['guard_evidence'].update(phase='GENERATE',unit=None)
        first=generator(**json.loads(validator.INPUT_BYTES));tick()
        verification=validator.validate_result(first,expected_result_sha256=validator.EXPECTED_RESULT_SHA256)
        result=canonical(first)
        require(len(result)<=MAX_ARTIFACT,'ARTIFACT_INVALID')
        second=generator(**json.loads(validator.INPUT_BYTES));tick()
        require(canonical(second)==result,'INVALID_RESULT')
        receipt['validation']=validator.validate_repeated_results(first,second,
            generation_python_version=receipt['python_version'],expected_result_sha256=validator.EXPECTED_RESULT_SHA256)
        receipt['stage']='COMPUTED'
        verify_quiet(op,receipt['guard_evidence'],phase='BEFORE_PERSIST');tick()
        require(journal_state(op)==journal_before and hashlib.sha256(op.fs.read(op.archive+'/snapshot.json',2*safety.MAX_BYTES)).hexdigest()==SNAPSHOT_SHA256,'SNAPSHOT_INVALID')
        receipt['guard_evidence'].update(phase='PERSIST',unit=None)
        op.fs.write_new(scope+'/worlds.json',result)
        require(op.fs.read(scope+'/worlds.json',MAX_ARTIFACT)==result,'ARTIFACT_INVALID')
        manifest=dict(schema='bridge.ibm.school-worlds.manifest.v1',run_id=payload['run_id'],source_commit=payload['head'],
            snapshot_id=SNAPSHOT_ID,snapshot_sha256=SNAPSHOT_SHA256,generator_sha256=GENERATOR_SHA256,
            input_sha256=validator.INPUT_SHA256,worlds_sha256=hashlib.sha256(result).hexdigest(),
            worlds_bytes=len(result),python_version=receipt['python_version'],identical_repeats=2,
            purpose='COMPUTE_VERIFICATION_AND_TRAINING_INPUT_ONLY',service_changes=[])
        op.fs.write_new(scope+'/manifest.json',canonical(manifest))
        receipt['stage']='PERSISTED'
        verify_quiet(op,receipt['guard_evidence'],phase='FINAL_VERIFY');tick()
        require(journal_state(op)==journal_before and hashlib.sha256(op.fs.read(op.archive+'/snapshot.json',2*safety.MAX_BYTES)).hexdigest()==SNAPSHOT_SHA256,'SNAPSHOT_INVALID')
        receipt['artifact']=dict(name='worlds.json',bytes=len(result),sha256=hashlib.sha256(result).hexdigest(),
            encoding='zlib+base64',data=base64.b64encode(zlib.compress(result,9)).decode())
        receipt.update(result='WORLD_GENERATED',reason='OK',stage='COMPLETE')
        receipt['guard_evidence'].update(phase='COMPLETE',unit=None,failed_predicates=[])
    except safety.Blocked as exc:
        receipt['reason']=str(exc) if str(exc) in REASONS else 'SAFETY_GUARD_BLOCKED'
    except (ValueError,TypeError,KeyError):
        receipt['reason']='INVALID_RESULT'
    except (Exception,KeyboardInterrupt):
        receipt['reason']='INTERNAL_BLOCKED'
    receipt['elapsed_ms']=max(0,int((clock()-began)*1000))
    receipt['gates_verified']=list(safety.GATE_UNITS) if receipt['guard_evidence']['gate_check']=='VERIFIED' else []
    if receipt['result']!='WORLD_GENERATED':
        receipt['artifact']=None
        if receipt['reason'] not in receipt['guard_evidence']['failed_predicates']:
            receipt['guard_evidence']['failed_predicates'].append(receipt['reason'])
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
        if receipt['stage']=='COMPLETE':receipt['stage']='PERSISTED'
        if receipt['guard_evidence']['phase']=='COMPLETE':receipt['guard_evidence']['phase']='FINAL_VERIFY'
        receipt['guard_evidence']['unit']=None
        if receipt['reason'] not in receipt['guard_evidence']['failed_predicates']:
            receipt['guard_evidence']['failed_predicates'].append(receipt['reason'])
    finally:
        if timer:signal.setitimer(signal.ITIMER_REAL,0)
        for sig,handler in handlers.items():signal.signal(sig,handler)
    raw=canonical(receipt)
    require(len(raw)<=MAX_RECEIPT,'ARTIFACT_INVALID')
    sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()
    return 0 if receipt['result']=='WORLD_GENERATED' else 3

if __name__=='__main__':
    sys.exit(main())
