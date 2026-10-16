"""Bounded transient executor for the installed first native lane dispatch.

A reviewed owner controller retains the accepted request and invokes run_once
inside the armed root supervisor. ExecStopPost calls restore independently of
SSH, owner credentials, current-main freshness and permit expiry. No permanent
unit is edited. This module has no intake, credential input or dispatch CLI.
"""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import tempfile
import time

from ops import light_native_lane_install as install
from ops import light_native_service_plan as plan
from ops import light_native_service_switch as switch

ROOT = Path('/var/lib/bridge-light-native-lane-execution')
UNIT = 'bridge-light-native-lane-once.service'
SUPERVISOR = 'bridge-light-native-lane-supervisor.service'
require = install.require


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def parse(raw):
    value=json.loads(raw)
    require(type(value) is dict and encoded(value) == raw, 'LANE_EXEC_RECORD')
    return value


def argv(source):
    require(source == install.RETAINED_SOURCE, 'LANE_EXEC_SOURCE')
    return [plan.PYTHON,'-I','-B','-c',install.LAUNCH,str(plan.source_path(source))]


def command(source, prior, seconds, request_digest):
    argv(source)
    require(type(seconds) is int and 30 <= seconds <= 1800
            and install.release.source.identifier(request_digest,64), 'LANE_EXEC_BOUND')
    properties = plan.pilot_properties(source,prior,seconds,request_digest=request_digest)
    properties.update(Description='Bridge native lane '+request_digest,
        Environment=['PYTHONDONTWRITEBYTECODE=1','PYTHONUNBUFFERED=1','AUTOPILOT_ADMISSION_MODE=NATIVE'],
        PrivateNetwork='no')
    result=['/usr/bin/systemd-run','--quiet','--unit='+UNIT,'--service-type=exec']
    for key,value in properties.items():
        for item in value if isinstance(value,list) else [value]:
            result.append('--property='+key+'='+item)
    return result+['--']+argv(source)


def request(request_digest):
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic'
            and install.release.source.identifier(request_digest,64), 'LANE_EXEC_HOST')
    directory=ROOT/request_digest
    install.root_parent(directory)
    raw=install.hold.read(directory/'request.json',0o600,262144)
    require(hashlib.sha256(raw).hexdigest() == request_digest, 'LANE_EXEC_REQUEST')
    value=parse(raw)
    fields={'version','source','controller_source','seconds','expires_at','prior',
            'protected','protected_sha256','cursor_sha256','dispatch_id','original','supervisor_sha256','owner_context_sha256'}
    require((set(value)==fields and type(value['version']) is int and value['version']==1
             or set(value)==fields|{'sequence'} and type(value['version']) is int and value['version']==2
             and type(value['sequence']) is int and 1<=value['sequence']<10000)
            and install.release.source.identifier(value['controller_source'],40)
            and install.release.source.identifier(value['cursor_sha256'],64)
            and install.release.source.identifier(value['supervisor_sha256'],64)
            and install.release.source.identifier(value['owner_context_sha256'],64)
            and type(value['original']) is dict
            and set(value['original']) == {'ActiveState','SubState','MainPID','InvocationID','NRestarts'}
            and value['original']['ActiveState'] == 'active' and value['original']['SubState'] == 'running'
            and value['original']['NRestarts'] == '0'
            and type(value['expires_at']) is int, 'LANE_EXEC_REQUEST')
    prior=install.hold.HoldIdentity(**value['prior'])
    command(value['source'],prior,value['seconds'],request_digest)
    return value,prior,directory


def admission(value):
    require(value in (b'HOLD\n',b'RUN\n'), 'LANE_EXEC_ADMISSION')
    install.root_parent(install.CONTROL)
    path=install.CONTROL/'admission'
    previous=install.hold.read(path,0o640,16)
    require(previous in (b'HOLD\n',b'RUN\n')
            and (value == b'HOLD\n' or previous == b'HOLD\n'), 'LANE_EXEC_ADMISSION')
    gid=pwd.getpwnam('school-autopilot').pw_gid
    require(path.lstat().st_gid == gid and path.lstat().st_nlink == 1, 'LANE_EXEC_ADMISSION')
    fd,name=tempfile.mkstemp(prefix='.lane-admission-',dir=install.CONTROL)
    temporary=Path(name)
    try:
        with os.fdopen(fd,'wb') as stream:
            os.fchmod(stream.fileno(),0o640);os.fchown(stream.fileno(),0,gid)
            stream.write(value);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        install.release.staging.fsync_directory(install.CONTROL)
    finally:
        if temporary.exists():temporary.unlink()


def feed(value):
    # Database driver dependencies are permitted only in the run path.
    from oracle_autopilot import light_native_lane as lane
    user=pwd.getpwnam('school-autopilot')
    def read(path,limit):
        install.root_parent(path.parent)
        row=path.lstat()
        require(row.st_gid == user.pw_gid and row.st_nlink == 1, 'LANE_EXEC_FEED_METADATA')
        return install.hold.read(path,0o640,limit)
    raw=read(install.CONTROL/'current.json',4096)
    cursor=lane.entry(raw,value['source'])
    number=value.get('sequence',0)
    intent='first-feed-intent.json' if number==0 else f'{number:08d}-feed-intent.json'
    require(lane.digest(raw) == value['cursor_sha256'] and cursor['sequence'] == number
            and cursor['dispatch_id'] == value['dispatch_id']
            and install.hold.read(install.LEDGER/intent,0o600,4096) == raw,
            'LANE_EXEC_FEED_CHANGED')
    path=install.CONTROL/'jobs'/cursor['dispatch_id']/'permit.json'
    permit=lane.Permit(read(path,65536),cursor['permit_sha256'],lambda:read(path,65536),value['source'])
    require(permit.value['expires_at'] >= value['expires_at'], 'LANE_EXEC_WINDOW')
    return permit


def owned(value,prior,digest):
    """Scope cleanup to this exact transient source, command and request."""
    existence=switch.show(UNIT,['LoadState','ActiveState','MainPID'])
    if existence['LoadState'] == 'not-found':
        require(existence == {'LoadState':'not-found','ActiveState':'inactive','MainPID':'0'},
                'LANE_EXEC_ABSENT_STATE')
        return False
    row=switch.show(UNIT,['LoadState','WorkingDirectory','Description'])
    require(row['WorkingDirectory'] == str(plan.source_path(value['source']))
            and row['Description'] == 'Bridge native lane '+digest, 'LANE_EXEC_FOREIGN_UNIT')
    expected='{ path='+plan.PYTHON+' ; argv[]='+' '.join(argv(value['source']))+' ; ignore_errors=no ;'
    execution=switch.show(UNIT,['ExecStart','Environment','EnvironmentFiles','RuntimeMaxUSec','PrivateNetwork'])
    require(execution['ExecStart'].startswith(expected) and execution['ExecStart'].endswith(' }')
            and execution['ExecStart'].count('{') == execution['ExecStart'].count('}') == 1
            and sorted(execution['Environment'].split()) == sorted([
                'PYTHONDONTWRITEBYTECODE=1','PYTHONUNBUFFERED=1','AUTOPILOT_ADMISSION_MODE=NATIVE'])
            and execution['EnvironmentFiles'] == str(install.hold.ENV)+' (ignore_errors=no) '+
                prior.release+'/ops/autopilot/broker-hold.env (ignore_errors=no)'
            and switch.duration_seconds(execution['RuntimeMaxUSec']) == value['seconds']
            and execution['PrivateNetwork'] == 'no', 'LANE_EXEC_UNIT_CHANGED')
    switch.attest_hardening(UNIT,pilot=True)
    return True


def stopped():
    existence=switch.show(UNIT,['LoadState','ActiveState','MainPID'])
    if existence['LoadState'] == 'not-found':
        require(existence == {'LoadState':'not-found','ActiveState':'inactive','MainPID':'0'},
                'LANE_EXEC_ABSENT_STATE')
        return
    row=switch.show(UNIT,['MainPID','ControlPID','ActiveState','ControlGroup'])
    require(row['MainPID'] == row['ControlPID'] == '0'
            and row['ActiveState'] in ('inactive','failed')
            and row['ControlGroup'] in ('','/system.slice/'+UNIT), 'LANE_EXEC_PROCESS_REMAINS')
    path=Path('/sys/fs/cgroup/system.slice')/UNIT
    if path.exists():
        require(dict(line.split() for line in (path/'cgroup.events').read_text().splitlines()).get('populated') == '0',
                'LANE_EXEC_CGROUP_POPULATED')


def arm(digest,value,directory):
    script=directory/'supervisor.py'
    require(hashlib.sha256(install.hold.read(script,0o600,65536)).hexdigest() == value['supervisor_sha256'],
            'LANE_EXEC_SUPERVISOR_CHANGED')
    require(script.lstat().st_nlink == 1, 'LANE_EXEC_SUPERVISOR_FILE')
    row=switch.show(SUPERVISOR,['MainPID','ActiveState','User','Group','Restart','NoNewPrivileges',
                              'ExecStart','KillMode','ExecStopPost','RuntimeMaxUSec','TimeoutStopUSec'])
    expected='/usr/bin/python3 -I -S -B '+str(script)+' restore '+digest
    start='/usr/bin/python3 -I -S -B '+str(script)+' run '+digest
    launch=row['ExecStart']
    hook=row['ExecStopPost']
    require(row['MainPID'] == str(os.getpid()) and row['ActiveState'] in ('active','activating')
            and row['User'] == row['Group'] == 'root' and row['Restart'] == 'no'
            and row['NoNewPrivileges'] == 'yes' and row['KillMode'] == 'control-group'
            and launch.startswith('{ path=/usr/bin/python3 ; argv[]='+start+' ; ignore_errors=no ;')
            and launch.endswith(' }') and launch.count('{') == launch.count('}') == 1
            and hook.startswith('{ path=/usr/bin/python3 ; argv[]='+expected+' ; ignore_errors=no ;')
            and hook.endswith(' }') and hook.count('{') == hook.count('}') == 1
            and switch.duration_seconds(row['RuntimeMaxUSec']) == value['seconds']
            and switch.duration_seconds(row['TimeoutStopUSec']) == 120, 'LANE_EXEC_NOT_ARMED')


def process(value):
    row=switch.show(UNIT,['ActiveState','SubState','MainPID','InvocationID','NRestarts'])
    require(row['ActiveState'] == 'active' and row['SubState'] == 'running'
            and row['MainPID'].isdigit() and int(row['MainPID']) > 1 and row['NRestarts'] == '0'
            and install.release.source.identifier(row['InvocationID'],32), 'LANE_EXEC_NOT_RUNNING')
    path=Path('/proc')/row['MainPID']
    user=pwd.getpwnam('school-autopilot')
    require(path.stat().st_uid == user.pw_uid and (path/'cwd').resolve() == plan.source_path(value['source'])
            and (path/'cmdline').read_bytes().split(b'\0') == [x.encode() for x in argv(value['source'])+['']],
            'LANE_EXEC_PROCESS_CHANGED')
    env=dict(item.split(b'=',1) for item in (path/'environ').read_bytes().split(b'\0') if item)
    require(env.get(b'AUTOPILOT_ADMISSION_MODE') == b'NATIVE'
            and env.get(b'AUTOPILOT_DATABASE_URL') == install.hold.env(
                install.hold.read(install.hold.ENV,0o600,262144))['AUTOPILOT_DATABASE_URL'].encode(), 'LANE_EXEC_PROCESS_ENVIRONMENT')
    return row


def journal(invocation):
    return switch.command('/usr/bin/journalctl','--no-pager','-o','cat','-n','20',
                          '_SYSTEMD_INVOCATION_ID='+invocation).splitlines()


def run_once(request_digest,fresh_gate,*,live_guard):
    """fresh_gate is the reviewed controller's live PR/DB/authority check."""
    value,prior,directory=request(request_digest)
    deadline=time.monotonic()+max(0,value['expires_at']-time.time())
    def guard():
        live_guard()
        require(time.time() < value['expires_at'] and time.monotonic() < deadline, 'LANE_EXEC_EXPIRED')
        arm(request_digest,value,directory)
        install.release.staging.require_current_main(value['controller_source'])
        require(asdict(install.hold.service_hold_identity()) == asdict(prior), 'LANE_EXEC_LEGACY_CHANGED')
        switch.unchanged_files(value['protected'],prior,value['protected_sha256'])
        permit=feed(value);permit.check()
        require(fresh_gate(permit) is True, 'LANE_EXEC_OWNER_GATE')
    guard()
    switch.unit_absent(UNIT)
    install.verify_unit(value['source'])
    require(install.show(install.UNIT,list(value['original'])) == value['original']
            and install.hold.read(install.UNIT_FILE,0o644,8192) == install.render(value['source'])
            and install.hold.read(install.CONTROL/'admission',0o640,16) == b'HOLD\n', 'LANE_EXEC_ORIGINAL_CHANGED')
    state=install.STATE.lstat()
    number=value.get('sequence',0)
    if number:
        from ops.light_native_lane_feed import completed_history,root_record
        from oracle_autopilot import light_native_lane as lane
        history=completed_history(value['source'])
        cursor=lane.entry(root_record(install.CONTROL/'current.json',4096),value['source'])
        require(len(history)==number and cursor['sequence']==number
                and cursor['previous_terminal_sha256']==lane.digest(history[-1][1]), 'LANE_EXEC_HISTORY_CHANGED')
    require(stat.S_ISDIR(state.st_mode) and state.st_uid == pwd.getpwnam('school-autopilot').pw_uid
            and stat.S_IMODE(state.st_mode) == 0o700
            and (number>0 or set(os.listdir(install.STATE)) == {'pilot.lock'}),
            'LANE_EXEC_NOT_PRISTINE')
    require(install.verify_hold_process(value['source'],pwd.getpwnam('school-autopilot')) == value['original'],
            'LANE_EXEC_ORIGINAL_CHANGED')
    install.write_new(directory/'launch-intent.json',encoded(dict(request_sha256=request_digest)),0o600)
    switch.command('/usr/bin/systemctl','stop',install.UNIT)
    install.stopped()
    guard()
    switch.command(*command(value['source'],prior,value['seconds'],request_digest))
    require(owned(value,prior,request_digest), 'LANE_EXEC_START_UNKNOWN')
    row=None
    for _ in range(30):
        arm(request_digest,value,directory)
        require(time.time() < value['expires_at'] and time.monotonic() < deadline, 'LANE_EXEC_EXPIRED')
        row=process(value)
        if journal(row['InvocationID']) == ['{"audit":"LIGHT_NATIVE_LANE","state":"HOLD"}']:break
        time.sleep(0.5)
    else:raise RuntimeError('LANE_EXEC_HOLD_NOT_OBSERVED')
    install.write_new(directory/'running.json',encoded(row),0o600)
    guard()
    require(process(value) == row, 'LANE_EXEC_PROCESS_CHANGED')
    admission(b'RUN\n')
    next_remote_check=time.monotonic()+15
    while True:
        # Authenticated guard includes exact main, workflow, actor and live job.
        # Local process/admission/deadline checks continue every two seconds.
        if time.monotonic() >= next_remote_check:
            live_guard()
            next_remote_check=time.monotonic()+15
        # Provider effects remain bounded independently by Permit and PID1.
        arm(request_digest,value,directory)
        require(time.time() < value['expires_at'] and time.monotonic() < deadline, 'LANE_EXEC_EXPIRED')
        require(process(value) == row and owned(value,prior,request_digest), 'LANE_EXEC_PROCESS_CHANGED')
        require(asdict(install.hold.service_hold_identity()) == asdict(prior), 'LANE_EXEC_LEGACY_CHANGED')
        switch.unchanged_files(value['protected'],prior,value['protected_sha256'])
        require(install.hold.read(install.CONTROL/'admission',0o640,16) == b'RUN\n', 'LANE_EXEC_ADMISSION')
        lines=journal(row['InvocationID'])
        records=[json.loads(line) for line in lines]
        if any(item.get('audit') == 'LIGHT_NATIVE_LANE_QUARANTINED' for item in records):
            return {'state':'READBACK_REQUIRED'}
        terminal=[item for item in records if item.get('audit') == 'LIGHT_NATIVE_LANE'
                  and item.get('state') == 'AWAITING_OWNER']
        if terminal:
            require(len(terminal) == 1 and install.release.source.identifier(terminal[0].get('terminal_sha256'),64),
                    'LANE_EXEC_TERMINAL_MARKER')
            return {'state':'READBACK_REQUIRED','terminal_sha256':terminal[0]['terminal_sha256']}
        time.sleep(2)


def restore(request_digest):
    """Deny and stop on every outcome; owner acceptance controls later restart.

    Deliberately leaves the permanent native service stopped. A separate owner
    terminal/DB readback must prove clean history before starting it in HOLD.
    """
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic'
            and install.release.source.identifier(request_digest,64), 'LANE_EXEC_HOST')
    admission(b'HOLD\n')
    value,prior,directory=request(request_digest)
    if owned(value,prior,request_digest):
        switch.command('/usr/bin/systemctl','stop',UNIT)
    stopped()
    # Never terminate a replacement or unrelated permanent process on cleanup.
    permanent=install.show(install.UNIT,list(value['original']))
    unchanged=permanent == value['original']
    require(unchanged or (permanent['MainPID'] == '0' and permanent['ActiveState'] in ('inactive','failed')),
            'LANE_EXEC_PERMANENT_PROCESS_CHANGED')
    require(install.hold.read(install.UNIT_FILE,0o644,8192) == install.render(value['source']),
            'LANE_EXEC_ORIGINAL_CHANGED')
    require(asdict(install.hold.service_hold_identity()) == asdict(prior), 'LANE_EXEC_LEGACY_CHANGED')
    switch.unchanged_files(value['protected'],prior,value['protected_sha256'])
    install.verify_unit(value['source'])
    if unchanged:
        require(install.verify_hold_process(value['source'],pwd.getpwnam('school-autopilot')) == value['original'],
                'LANE_EXEC_ORIGINAL_CHANGED')
    else:
        install.stopped()
    result=dict(state='ORIGINAL_HOLD' if unchanged else 'STOPPED_HOLD',request_sha256=request_digest,legacy_unchanged=True,
                terminal_verified=False,db_controls_restored=False)
    # Idempotent evidence only after rechecking primary state; no workload retry.
    install.release.retain(directory/'stopped-hold.json',encoded(result))
    return result


def supervisor_command(request_digest,seconds):
    require(install.release.source.identifier(request_digest,64)
            and type(seconds) is int and 30 <= seconds <= 1800, 'LANE_EXEC_BOUND')
    script=ROOT/request_digest/'supervisor.py'
    restore_command='/usr/bin/python3 -I -S -B '+str(script)+' restore '+request_digest
    return ['/usr/bin/systemd-run','--quiet','--unit='+SUPERVISOR,'--service-type=exec',
            '--property=User=root','--property=Group=root','--property=UMask=0077',
            '--property=NoNewPrivileges=yes','--property=Restart=no','--property=KillMode=control-group',
            '--property=TimeoutStopSec=120','--property=RuntimeMaxSec='+str(seconds),
            '--property=ExecStopPost='+restore_command,'--','/usr/bin/python3','-I','-S','-B',
            str(script),'run',request_digest]
