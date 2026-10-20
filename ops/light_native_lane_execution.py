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
import re
import stat
import tempfile
import time

from ops import light_native_lane_install as install
from ops import light_native_service_plan as plan
from ops import light_native_service_switch as switch
from ops import light_native_service_unit as unit_readback

ROOT = Path('/var/lib/bridge-light-native-lane-execution')
OWNER_ROOT = Path('/var/lib/bridge-light-native-lane-owner')
UNIT = 'bridge-light-native-lane-once.service'
SUPERVISOR = 'bridge-light-native-lane-supervisor.service'
require = install.require


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def parse(raw):
    value=json.loads(raw)
    require(type(value) is dict and encoded(value) == raw, 'LANE_EXEC_RECORD')
    return value


def parse_plan(raw):
    """Decode retained Plans within explicit size/depth bounds.

    native_cli_permission_engine.encode uses ASCII escaping; lane records and
    issuer policies use UTF-8. Bytes and hashes are never rewritten. Full Plan
    scope validation stays in the driver; depth above 64 is refused here.
    """
    require(type(raw) is bytes and 0 < len(raw) <= 16384, 'LANE_EXEC_PLAN')
    # Bound decoder/encoder work independently of Python's recursion behavior.
    # Only structural brackets count; escaped quotes/backslashes stay strings.
    depth=0
    quoted=escaped=False
    for token in raw:
        if quoted:
            if escaped:escaped=False
            elif token == 92:escaped=True
            elif token == 34:quoted=False
        elif token == 34:quoted=True
        elif token in (91,123):
            depth+=1
            require(depth <= 64, 'LANE_EXEC_PLAN')
        elif token in (93,125):
            depth-=1
            require(depth >= 0, 'LANE_EXEC_PLAN')
    require(depth == 0 and not quoted, 'LANE_EXEC_PLAN')
    def unique(pairs):
        value={}
        for key,item in pairs:
            require(key not in value, 'LANE_EXEC_PLAN')
            value[key]=item
        return value
    try:
        value=json.loads(raw,object_pairs_hook=unique,
                         parse_constant=lambda _:require(False,'LANE_EXEC_PLAN'))
        require(type(value) is dict and json.dumps(value,sort_keys=True,separators=(',',':'),
                ensure_ascii=True,allow_nan=False).encode() == raw, 'LANE_EXEC_PLAN')
        return value
    except (ValueError,TypeError,UnicodeError,RecursionError):
        require(False, 'LANE_EXEC_PLAN')


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


def request(request_digest, *, for_restore=False):
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic'
            and install.release.source.identifier(request_digest,64), 'LANE_EXEC_HOST')
    directory=ROOT/request_digest
    install.root_parent(directory)
    raw=install.hold.read(directory/'request.json',0o600,262144)
    require(hashlib.sha256(raw).hexdigest() == request_digest, 'LANE_EXEC_REQUEST')
    value=parse(raw)
    fields={'version','source','controller_source','seconds','expires_at','prior',
            'protected','protected_sha256','cursor_sha256','dispatch_id','original','supervisor_sha256','owner_context_sha256'}
    require(type(for_restore) is bool and type(value.get('version')) is int
            and (for_restore and set(value)==fields and value['version']==1
             or for_restore and set(value)==fields|{'sequence'} and value['version']==2
             and type(value['sequence']) is int and 1<=value['sequence']<10000
             or set(value)==fields|{'sequence','clock','python_sha256'} and value['version']==3
             and type(value['sequence']) is int and 0<=value['sequence']<10000
             and install.release.source.identifier(value['python_sha256'],64))
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
    if value['version']==3:_clock_shape(value)
    return value,prior,directory


def _boot_id():
    fd=os.open('/proc/sys/kernel/random/boot_id',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:raw=unit_readback._read_fd(fd,128)
    finally:os.close(fd)
    value=raw.decode('ascii').strip()
    require(re.fullmatch('[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',value) is not None,
            'LANE_EXEC_SESSION_CLOCK')
    return value


def _session_read(path, expected=None, *, limit=262144, mode=0o600):
    """Root/no-follow immutable bytes, with exact ns metadata readback."""
    install.root_parent(path.parent)
    no_atime=getattr(os,'O_NOATIME',None)
    require(type(no_atime) is int and no_atime>0,'LANE_EXEC_SESSION_FILE')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|no_atime)
    try:
        row=os.fstat(fd);signature=unit_readback._metadata(row)
        require(stat.S_ISREG(row.st_mode) and row.st_uid==row.st_gid==0
                and (row.st_nlink==1 if mode is not None else row.st_nlink>0)
                and (stat.S_IMODE(row.st_mode)==mode if mode is not None else not row.st_mode&0o022)
                and 0<row.st_size<=limit,'LANE_EXEC_SESSION_FILE')
        raw=unit_readback._read_fd(fd,limit)
        require(unit_readback._metadata(os.fstat(fd))==signature
                and unit_readback._metadata(path.stat(follow_symlinks=False))==signature
                and (expected is None or hashlib.sha256(raw).hexdigest()==expected),
                'LANE_EXEC_SESSION_FILE_CHANGED')
        return raw
    finally:os.close(fd)


def capture_clock(seconds, expires_at, guard):
    """Only the existing authenticated GitHub branch may originate v3."""
    from ops.light_native_lane_run_guard import LaneRunBinding
    require(type(guard) is LaneRunBinding and type(seconds) is int and 30<=seconds<=1800
            and type(expires_at) is int,'LANE_EXEC_SESSION_ORIGIN')
    guard.assert_running()
    require(type(guard.deadline) is float and 0<guard.deadline<float('inf'),
            'LANE_EXEC_SESSION_ORIGIN')
    monotonic=time.monotonic_ns();wall=time.time_ns()
    ceiling=int(guard.deadline*10**9)
    clock=dict(boot_id=_boot_id(),wall_ns=wall,monotonic_start_ns=monotonic,
        monotonic_end_ns=min(monotonic+seconds*10**9,monotonic+expires_at*10**9-wall,ceiling),
        run_guard_deadline_ns=ceiling)
    _clock_shape(dict(seconds=seconds,expires_at=expires_at,clock=clock))
    return clock,hashlib.sha256(_session_read(Path('/usr/bin/python3').resolve(strict=True),
                                           limit=64*1024*1024,mode=None)).hexdigest()


def _clock_shape(value):
    clock=value['clock']
    require(type(clock) is dict and set(clock)=={'boot_id','wall_ns','monotonic_start_ns',
            'monotonic_end_ns','run_guard_deadline_ns'}
            and type(clock['boot_id']) is str and re.fullmatch(
                '[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',clock['boot_id'])
            and all(type(clock[k]) is int and clock[k]>0 for k in clock if k!='boot_id')
            and clock['monotonic_start_ns']<clock['monotonic_end_ns']
            <=min(clock['monotonic_start_ns']+value['seconds']*10**9,
                  clock['monotonic_start_ns']+value['expires_at']*10**9-clock['wall_ns'],
                  clock['run_guard_deadline_ns']), 'LANE_EXEC_SESSION_CLOCK')
    return clock


_SESSION_PROPERTIES=('LoadState','ActiveState','SubState','MainPID','InvocationID','NRestarts',
    'Transient','NeedDaemonReload','DropInPaths','User','Group','Restart','NoNewPrivileges',
    'KillMode','ExecStart','ExecStopPost','RuntimeMaxUSec','TimeoutStopUSec','ControlGroup','ControlPID',
    'Type','RuntimeRandomizedExtraUSec','ActiveEnterTimestampMonotonic')


def _session_show(deadline):
    raw=unit_readback._read_command(('/usr/bin/systemctl','--no-pager','show','--all',SUPERVISOR,
        '--property='+','.join(_SESSION_PROPERTIES)),deadline=deadline,limit=65536)
    require(type(raw) is bytes and raw.endswith(b'\n') and 0<len(raw)<=65536,
            'LANE_EXEC_SESSION_UNIT')
    result={}
    for line in raw.decode('ascii').splitlines():
        key,sep,value=line.partition('=')
        require(sep and key in _SESSION_PROPERTIES and key not in result,'LANE_EXEC_SESSION_UNIT')
        result[key]=value
    require(set(result)==set(_SESSION_PROPERTIES),'LANE_EXEC_SESSION_UNIT')
    return result


def _session_exec(raw, arguments, pid):
    require(raw.startswith('{ ') and raw.endswith(' }') and raw.count('{')==raw.count('}')==1,
            'LANE_EXEC_SESSION_UNIT')
    fields={}
    for part in raw[2:-2].split(' ; '):
        key,sep,value=part.partition('=')
        require(sep and key not in fields,'LANE_EXEC_SESSION_UNIT')
        fields[key]=value
    require(set(fields)=={'path','argv[]','ignore_errors','start_time','stop_time','pid','code','status'}
            and fields['path']==arguments[0] and fields['argv[]']==' '.join(arguments)
            and fields['ignore_errors']=='no' and fields['stop_time']=='[n/a]'
            and fields['code']=='(null)' and fields['status']=='0/0'
            and (pid==0 and arguments[-2]=='restore' and fields['pid']=='0' and fields['start_time']=='[n/a]' or
                 fields['pid']==str(pid) and pid>1 and re.fullmatch(
                    r'\[(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{4}-\d\d-\d\d \d\d:\d\d:\d\d [A-Za-z0-9_+:/.-]{1,32}\]',
                    fields['start_time'])), 'LANE_EXEC_SESSION_UNIT')


def _supervisor_timer(row,value):
    # systemd v255 service_running_timeout uses active_enter + RuntimeMaxUSec
    # plus randomized extra. Refuse extensions and pin this actual timer origin.
    runtime=switch.duration_seconds(row['RuntimeMaxUSec'])
    start=row['ActiveEnterTimestampMonotonic']
    require(row['Type']=='exec' and row['RuntimeRandomizedExtraUSec']=='0'
            and type(runtime) is int and 30<=runtime<=value['seconds']
            and re.fullmatch('[1-9][0-9]{0,18}',start), 'LANE_EXEC_SESSION_TIMER')
    start_ns=int(start)*1000
    require(value['clock']['monotonic_start_ns']<=start_ns<=time.monotonic_ns()
            and start_ns+runtime*10**9<=value['clock']['monotonic_end_ns'],
            'LANE_EXEC_SESSION_TIMER')


def _session_process(digest,value,directory,process):
    arguments=('/usr/bin/python3','-I','-S','-B',str(directory/'supervisor.py'),'run',digest)
    require(all(type(getattr(process,key)) is int for key in ('pid','uid','gid','no_new_privs','start_ticks'))
            and process.pid==os.getpid() and process.pid>1 and process.uid==process.gid==0
            and process.no_new_privs==1 and process.start_ticks>0
            and process.boot_id==value['clock']['boot_id'] and process.exe_sha256==value['python_sha256']
            and process.argv==arguments and process.cgroup=='0::/system.slice/'+SUPERVISOR
            and process.cwd=='/' and re.fullmatch('[0-9a-f]{32}',process.invocation_id),
            'LANE_EXEC_SESSION_PROCESS')
    return arguments


def _execution_identity(digest,value,directory,*,deadline=None):
    """Fixed transient PID1 readback; systemctl never supplies /proc identity."""
    deadline=min(time.monotonic()+10,value['clock']['monotonic_end_ns']/10**9,
                 float('inf') if deadline is None else deadline)
    before=_session_show(deadline)
    first=unit_readback._process(os.getpid())
    after=_session_show(deadline)
    last=unit_readback._process(os.getpid())
    require(time.monotonic()<deadline and before==after and first==last,
            'LANE_EXEC_SESSION_PROCESS_CHANGED')
    expected=dict(LoadState='loaded',ActiveState='active',SubState='running',MainPID=str(os.getpid()),
        InvocationID=first.invocation_id,NRestarts='0',Transient='yes',NeedDaemonReload='no',DropInPaths='',
        User='root',Group='root',Restart='no',NoNewPrivileges='yes',KillMode='control-group',
        ControlGroup='/system.slice/'+SUPERVISOR,ControlPID='0')
    arguments=_session_process(digest,value,directory,first)
    require(all(before[key]==item for key,item in expected.items())
            and switch.duration_seconds(before['TimeoutStopUSec'])==120,'LANE_EXEC_SESSION_UNIT')
    _supervisor_timer(before,value)
    _session_exec(before['ExecStart'],arguments,first.pid)
    _session_exec(before['ExecStopPost'],(*arguments[:-2],'restore',digest),0)
    return dict(process=asdict(first),loaded=before)


def _session_exists(path):
    try:path.lstat()
    except FileNotFoundError:return False
    return True  # Includes symlinks, partial bytes and an orphan first record.


def _await_supervisor(digest,value,directory,*,check_inputs):
    """Read-only Type=exec readiness, before consuming any first-entry record."""
    clock=value['clock']
    deadline_ns=min(time.monotonic_ns()+3*10**9,clock['monotonic_end_ns'])
    last_wall=clock['wall_ns'];last_monotonic=clock['monotonic_start_ns']
    while True:
        check_inputs()
        wall=time.time_ns();monotonic=time.monotonic_ns()
        require(_boot_id()==clock['boot_id'] and wall>=last_wall and wall<value['expires_at']*10**9
                and last_monotonic<=monotonic<deadline_ns,'LANE_EXEC_SESSION_READINESS')
        last_wall,last_monotonic=wall,monotonic
        row=_session_show(deadline_ns/10**9)
        if row['ActiveState']=='active' and row['SubState']=='running':
            result=_execution_identity(digest,value,directory,deadline=deadline_ns/10**9)
            check_inputs()
            require(time.time_ns()>=last_wall and last_monotonic<=time.monotonic_ns()<deadline_ns,
                    'LANE_EXEC_SESSION_READINESS')
            return result
        require(row['ActiveState']=='activating' and row['SubState'] in ('start','start-post'),
                'LANE_EXEC_SESSION_READINESS')
        process=unit_readback._process(os.getpid())
        arguments=_session_process(digest,value,directory,process)
        expected=dict(LoadState='loaded',MainPID=str(os.getpid()),InvocationID=process.invocation_id,
            NRestarts='0',Transient='yes',NeedDaemonReload='no',DropInPaths='',User='root',Group='root',
            Restart='no',NoNewPrivileges='yes',KillMode='control-group',Type='exec',
            RuntimeRandomizedExtraUSec='0',ControlGroup='/system.slice/'+SUPERVISOR)
        require(all(row[key]==item for key,item in expected.items())
                and 30<=switch.duration_seconds(row['RuntimeMaxUSec'])<=value['seconds']
                and switch.duration_seconds(row['TimeoutStopUSec'])==120,
                'LANE_EXEC_SESSION_READINESS')
        _session_exec(row['ExecStart'],arguments,process.pid)
        _session_exec(row['ExecStopPost'],(*arguments[:-2],'restore',digest),0)
        check_inputs()
        remaining=deadline_ns-time.monotonic_ns()
        require(remaining>0,'LANE_EXEC_SESSION_READINESS')
        time.sleep(min(0.05,remaining/10**9))


class ExecutionSession:
    """One create-only first invocation, never reconstructed or renewed on restart."""
    @classmethod
    def enter(cls, digest):
        self=cls()
        self.failed=False
        self.digest=digest
        self.value,self.prior,self.directory=request(digest)
        require(self.value['version']==3,'LANE_EXEC_SESSION_REQUEST')
        self.clock=_clock_shape(self.value)
        self.monotonic_end_ns=self.clock['monotonic_end_ns']
        self.last_wall_ns=self.clock['wall_ns']
        self.last_monotonic_ns=self.clock['monotonic_start_ns']
        self.inputs=self._inputs()
        self._time()
        paths=(self.directory/'session-intent.json',self.directory/'session-first.json')
        require(not any(_session_exists(path) for path in paths),'LANE_EXEC_SESSION_REENTRY')
        def check_inputs():
            self._time()
            require(self._inputs()==self.inputs,'LANE_EXEC_SESSION_INPUT_CHANGED')
            self._time()
        ready=_await_supervisor(digest,self.value,self.directory,check_inputs=check_inputs)
        self._time()
        require(self._inputs()==self.inputs,'LANE_EXEC_SESSION_INPUT_CHANGED')
        require(not any(_session_exists(path) for path in paths),'LANE_EXEC_SESSION_REENTRY')
        intent=encoded(dict(version=1,kind='LIGHT_LANE_EXECUTION_SESSION_INTENT',request_sha256=digest,
                            inputs_sha256=hashlib.sha256(encoded(self.inputs)).hexdigest()))
        try:install.write_new(self.directory/'session-intent.json',intent,0o600)
        except FileExistsError:raise RuntimeError('LANE_EXEC_SESSION_REENTRY') from None
        self.intent=intent
        self.identity=_execution_identity(digest,self.value,self.directory)
        require(encoded(self.identity)==encoded(ready) and self._inputs()==self.inputs,
                'LANE_EXEC_SESSION_INPUT_CHANGED')
        self._time()
        self.first=encoded(dict(version=1,kind='LIGHT_LANE_EXECUTION_SESSION_FIRST',request_sha256=digest,
            intent_sha256=hashlib.sha256(intent).hexdigest(),identity=self.identity))
        install.write_new(self.directory/'session-first.json',self.first,0o600)
        self.assert_current()
        return self

    def _inputs(self):
        raw=_session_read(self.directory/'request.json',self.digest)
        require(parse(raw)==self.value,'LANE_EXEC_SESSION_REQUEST')
        context_raw=_session_read(self.directory/'owner-context.json',self.value['owner_context_sha256'])
        context=parse(context_raw)
        require(set(context)=={'owner_plan_sha256','controller_sha256','wheels_sha256','run_id','attempt'}
                and all(install.release.source.identifier(context[k],64) for k in
                        ('owner_plan_sha256','controller_sha256','wheels_sha256'))
                and all(type(context[k]) is int and context[k]>0 for k in ('run_id','attempt')),
                'LANE_EXEC_SESSION_CONTEXT')
        self.context=context
        script=_session_read(self.directory/'supervisor.py',self.value['supervisor_sha256'],limit=65536)
        pointer=_session_read(OWNER_ROOT/context['owner_plan_sha256']/'execution.json')
        require(parse(pointer)=={'request_sha256':self.digest},'LANE_EXEC_SESSION_POINTER')
        return dict(request=hashlib.sha256(raw).hexdigest(),context=hashlib.sha256(context_raw).hexdigest(),
                    script=hashlib.sha256(script).hexdigest(),pointer=hashlib.sha256(pointer).hexdigest())

    def _time(self):
        wall=time.time_ns();monotonic=time.monotonic_ns()
        require(_boot_id()==self.clock['boot_id'] and wall>=self.last_wall_ns
                and wall<self.value['expires_at']*10**9
                and self.last_monotonic_ns<=monotonic<self.monotonic_end_ns,
                'LANE_EXEC_SESSION_EXPIRED')
        self.last_wall_ns=wall
        self.last_monotonic_ns=monotonic

    def assert_current(self):
        require(not self.failed,'LANE_EXEC_SESSION_CLOSED')
        try:
            self._time()
            require(self._inputs()==self.inputs
                    and _session_read(self.directory/'session-intent.json')==self.intent
                    and _session_read(self.directory/'session-first.json')==self.first,
                    'LANE_EXEC_SESSION_INPUT_CHANGED')
            require(encoded(_execution_identity(self.digest,self.value,self.directory))==encoded(self.identity),
                    'LANE_EXEC_SESSION_PROCESS_CHANGED')
            require(self._inputs()==self.inputs,'LANE_EXEC_SESSION_INPUT_CHANGED')
            self._time()
        except BaseException:
            self.failed=True
            raise RuntimeError('LANE_EXEC_SESSION_CLOSED') from None


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
        'ExecStart','KillMode','ExecStopPost','RuntimeMaxUSec','TimeoutStopUSec','Type',
        'RuntimeRandomizedExtraUSec','ActiveEnterTimestampMonotonic'])
    _supervisor_timer(row,value)
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


def run_once(request_digest,fresh_gate,*,live_guard,session=None):
    """fresh_gate is the reviewed controller's live PR/DB/authority check."""
    session=ExecutionSession.enter(request_digest) if session is None else session
    require(type(session) is ExecutionSession and session.digest==request_digest,'LANE_EXEC_SESSION_REQUEST')
    session.assert_current()
    value,prior,directory=session.value,session.prior,session.directory
    def guard():
        session.assert_current()
        live_guard()
        arm(request_digest,value,directory)
        install.release.staging.require_current_main(value['controller_source'])
        require(asdict(install.hold.service_hold_identity()) == asdict(prior), 'LANE_EXEC_LEGACY_CHANGED')
        switch.unchanged_files(value['protected'],prior,value['protected_sha256'])
        permit=feed(value);permit.check()
        require(fresh_gate(permit) is True, 'LANE_EXEC_OWNER_GATE')
        session.assert_current()
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
    session.assert_current()
    switch.command('/usr/bin/systemctl','stop',install.UNIT)
    install.stopped()
    guard()
    switch.command(*command(value['source'],prior,value['seconds'],request_digest))
    require(owned(value,prior,request_digest), 'LANE_EXEC_START_UNKNOWN')
    row=None
    for _ in range(30):
        session.assert_current()
        arm(request_digest,value,directory)
        row=process(value)
        if journal(row['InvocationID']) == ['{"audit":"LIGHT_NATIVE_LANE","state":"HOLD"}']:break
        time.sleep(0.5)
    else:raise RuntimeError('LANE_EXEC_HOLD_NOT_OBSERVED')
    install.write_new(directory/'running.json',encoded(row),0o600)
    guard()
    require(process(value) == row, 'LANE_EXEC_PROCESS_CHANGED')
    session.assert_current()
    admission(b'RUN\n')
    next_remote_check=time.monotonic()+15
    while True:
        session.assert_current()
        # Authenticated guard includes exact main, workflow, actor and live job.
        # Local process/admission/deadline checks continue every two seconds.
        if time.monotonic() >= next_remote_check:
            live_guard()
            next_remote_check=time.monotonic()+15
        # Provider effects remain bounded independently by Permit and PID1.
        arm(request_digest,value,directory)
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
    value,prior,directory=request(request_digest,for_restore=True)
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


def supervisor_command(request_digest,seconds,*,clock=None):
    require(install.release.source.identifier(request_digest,64)
            and type(seconds) is int and 30 <= seconds <= 1800, 'LANE_EXEC_BOUND')
    if clock is not None:
        require(_boot_id()==clock['boot_id'] and time.monotonic_ns()>=clock['monotonic_start_ns'],
                'LANE_EXEC_SESSION_CLOCK')
        seconds=min(seconds,(clock['monotonic_end_ns']-time.monotonic_ns())//10**9-5)
        require(30<=seconds<=1800,'LANE_EXEC_SESSION_TIMER')
    script=ROOT/request_digest/'supervisor.py'
    restore_command='/usr/bin/python3 -I -S -B '+str(script)+' restore '+request_digest
    return ['/usr/bin/systemd-run','--quiet','--unit='+SUPERVISOR,'--service-type=exec',
            '--property=User=root','--property=Group=root','--property=UMask=0077',
            '--property=NoNewPrivileges=yes','--property=Restart=no','--property=KillMode=control-group',
            '--property=RuntimeRandomizedExtraSec=0',
            '--property=TimeoutStopSec=120','--property=RuntimeMaxSec='+str(seconds),
            '--property=ExecStopPost='+restore_command,'--','/usr/bin/python3','-I','-S','-B',
            str(script),'run',request_digest]
