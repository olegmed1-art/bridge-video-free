"""Disposable Linux/PID1 rehearsal only; never a production transport.

Only private-network dummy workers in a unique /run fixture namespace are used.
No database/provider client, secret, installation, account or access change.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

PREFIX = 'bridge-native-transition-fixture-'
SYSTEM_UNITS = Path('/run/systemd/system')


def need(ok, code):
    if not ok:
        raise RuntimeError('TRANSITION_FIXTURE_'+code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def command(*argv, timeout=10, allow_failure=False):
    result = subprocess.run(list(argv), capture_output=True, timeout=timeout,
                            env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
    need(len(result.stdout) <= 65536 and len(result.stderr) <= 65536, 'OUTPUT_BOUND')
    need(allow_failure or result.returncode == 0, 'COMMAND_'+Path(argv[0]).name)
    return result


def show(unit, keys):
    need(re.fullmatch(PREFIX+r'[a-z0-9_-]+\.service', unit), 'UNIT_SCOPE')
    output = command('/usr/bin/systemctl', 'show', unit, '--no-pager',
                     *['--property='+key for key in keys]).stdout.decode()
    result = {}
    for line in output.splitlines():
        key, sep, value = line.partition('=')
        need(sep and key in keys and key not in result, 'UNIT_FIELDS')
        result[key] = value
    need(set(result) == set(keys), 'UNIT_FIELDS')
    return result


def new_file(path, raw, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def root_file(path, mode, limit=65536):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
             and stat.S_IMODE(before.st_mode) == mode and 0 <= before.st_size <= limit, 'FILE')
        raw = os.read(fd, limit+1)
        after = os.fstat(fd); linked = path.stat(follow_symlinks=False)
        signature = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        need(len(raw) <= limit and signature(before) == signature(after) == signature(linked), 'FILE_CHANGED')
        return raw
    finally: os.close(fd)


def proc_start(pid):
    raw = Path('/proc', str(pid), 'stat').read_bytes()
    return int(raw[raw.rfind(b')')+2:].split()[19])


def same_process(pid, ticks):
    try: return proc_start(pid) == ticks
    except FileNotFoundError: return False


def await_descendant(unit, parent):
    expected_group = '0::/system.slice/'+unit
    for _ in range(80):
        children = Path('/proc', str(parent), 'task', str(parent), 'children').read_text().split()
        if len(children) == 1:
            pid = int(children[0])
            status = dict(line.split(':', 1) for line in Path('/proc', str(pid), 'status').read_text().splitlines() if ':' in line)
            if int(status['PPid']) == parent and int(status['SigIgn'].strip(), 16) & (1 << (signal.SIGTERM-1)):
                need(Path('/proc', str(parent), 'cgroup').read_text().strip() == expected_group and
                     Path('/proc', str(pid), 'cgroup').read_text().strip() == expected_group, 'DESCENDANT_CGROUP')
                return dict(pid=pid, start_ticks=proc_start(pid))
        time.sleep(0.025)
    raise RuntimeError('TRANSITION_FIXTURE_DESCENDANT_NOT_READY')


def cgroup_empty(unit):
    row = show(unit, ['MainPID', 'ControlPID', 'ActiveState', 'ControlGroup'])
    need(row['ControlGroup'] in ('', '/system.slice/'+unit), 'CGROUP_SCOPE')
    path = Path('/sys/fs/cgroup/system.slice')/unit/'cgroup.events'
    populated = dict(line.split() for line in path.read_text().splitlines()).get('populated') if path.exists() else '0'
    return row['MainPID'] == row['ControlPID'] == '0' and populated == '0'


def await_running(unit, admission):
    for _ in range(80):
        row = show(unit, ['ActiveState', 'SubState', 'MainPID', 'InvocationID', 'Environment'])
        modes = [item for item in row['Environment'].split() if item.startswith('AUTOPILOT_ADMISSION_MODE=')]
        if row['ActiveState'] == 'active' and row['SubState'] == 'running' and int(row['MainPID']) > 0:
            need(modes == ['AUTOPILOT_ADMISSION_MODE='+admission], 'ADMISSION')
            need(re.fullmatch('[0-9a-f]{32}', row['InvocationID']), 'INVOCATION')
            proc_start(int(row['MainPID']))
            if unit.endswith('-legacy.service'):
                await_descendant(unit, int(row['MainPID']))
            return row
        time.sleep(0.025)
    raise RuntimeError('TRANSITION_FIXTURE_START_TIMEOUT')


def exact_drop(release, admission):
    return ('[Service]\nWorkingDirectory='+str(release)+'\nEnvironment=AUTOPILOT_ADMISSION_MODE='+
            admission+'\nEnvironmentFile='+str(release/'ops/autopilot/broker-hold.env')+'\n').encode()


WORKER = b'''import os,signal,subprocess,sys,time
if os.environ.get('FIXTURE_DESCENDANT')=='1':
 subprocess.Popen([sys.executable,'-I','-B','-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'])
while True:
 time.sleep(0.05)
'''


class Layout:
    def __init__(self, root):
        self.root = Path(root)
        need(self.root.parent == Path('/run') and re.fullmatch(PREFIX+r'[a-z0-9_]{8,32}', self.root.name)
             and not self.root.is_symlink(), 'ROOT_SCOPE')
        self.name = self.root.name
        self.legacy = self.name+'-legacy.service'
        self.native = self.name+'-native.service'
        self.supervisor = self.name+'-supervisor.service'
        self.release = self.root/'releases'/('a'*40)
        self.drop_parent = SYSTEM_UNITS/(self.legacy+'.d')
        self.drop = self.drop_parent/'40-admission.conf'
        self.context = self.root/'context.json'
        self.stage = self.root/'source'
        self.journal = self.root/'journal'

    def unit(self, native=False):
        unit = self.native if native else self.legacy
        admission = 'HOLD' if native else 'ACTIVE'
        text = ('[Unit]\nDescription=Disposable Native transition fixture '+self.name+'\n'
            '[Service]\nType=exec\nUser=nobody\nGroup=nogroup\nUMask=0077\n'
            'ExecStart=/usr/bin/python3 -I -B '+str(self.root/'worker.py')+'\n'
            'WorkingDirectory='+str(self.release)+'\n'
            'Environment=AUTOPILOT_ADMISSION_MODE='+admission+'\n'
            'Environment=FIXTURE_DESCENDANT='+('0' if native else '1')+'\n'
            'Restart=no\nKillMode=control-group\nTimeoutStopSec=1\nRuntimeMaxSec=90\n'
            'NoNewPrivileges=yes\nPrivateNetwork=yes\nPrivateTmp=yes\n'
            'ProtectSystem=strict\nProtectHome=yes\n')
        return SYSTEM_UNITS/unit, text.encode()

    def prepare(self, repo, *, case):
        need(os.geteuid() == 0, 'ROOT')
        self.root.chmod(0o755)
        self.release.mkdir(parents=True, mode=0o755)
        for parent in self.release.parents:
            if parent == self.root.parent: break
            parent.chmod(0o755)
        (self.release/'ops/autopilot').mkdir(parents=True, mode=0o755)
        new_file(self.release/'ops/autopilot/broker-hold.env', b'FIXTURE_PUBLIC_PIN=synthetic\n', 0o444)
        new_file(self.root/'worker.py', WORKER, 0o444)
        self.stage.mkdir(mode=0o700); (self.stage/'ops').mkdir(mode=0o700)
        new_file(self.stage/'ops/__init__.py', b'')
        for name in ('light_native_legacy_transition.py', 'light_native_service_authority.py'):
            new_file(self.stage/'ops'/name, (repo/'ops'/name).read_bytes())
        new_file(self.stage/'fixture.py', Path(__file__).read_bytes())
        for native in (False, True):
            path, raw = self.unit(native); new_file(path, raw, 0o644)
        self.drop_parent.mkdir(mode=0o755)
        new_file(self.drop, exact_drop(self.release, 'ACTIVE'), 0o644)
        new_file(self.root/'native-admission', b'HOLD\n', 0o600)
        new_file(self.root/'provider-state.json', encoded(dict(database=[], provider=[], leases=[])))
        command('/usr/bin/systemctl', 'daemon-reload')
        command('/usr/bin/systemctl', 'start', self.legacy, self.native)
        legacy = await_running(self.legacy, 'ACTIVE'); native = await_running(self.native, 'HOLD')
        context = dict(version=1, purpose='CI_ONLY_NO_PRODUCTION_AUTHORITY', case=case, fixture=self.name, legacy=legacy, native=native,
            helper_sha256=sha(root_file(self.stage/'fixture.py', 0o600)),
            original_baseline_sha256='b'*64, original_inventory_sha256='c'*64)
        return context

    def teardown(self):
        # Only this checked disposable namespace is ever stopped or removed.
        need(self.root.parent == Path('/run') and self.root.name.startswith(PREFIX)
             and self.root.resolve() == self.root and not self.root.is_symlink(), 'CLEANUP_SCOPE')
        uncleared = []
        for unit in (self.supervisor, self.legacy, self.native):
            try:
                try:
                    command('/usr/bin/systemctl', 'stop', unit, timeout=20, allow_failure=True)
                except subprocess.TimeoutExpired:
                    pass
                # Attempt every owned unit. Never unlink a live worker's source.
                if not cgroup_empty(unit):
                    command('/usr/bin/systemctl', 'kill', '--kill-whom=all', '--signal=KILL', unit,
                            timeout=5, allow_failure=True)
                    command('/usr/bin/systemctl', 'stop', unit, timeout=20, allow_failure=True)
                if not cgroup_empty(unit): uncleared.append(unit)
                command('/usr/bin/systemctl', 'reset-failed', unit, allow_failure=True)
            except Exception:
                uncleared.append(unit)
        need(not uncleared, 'CLEANUP_NOT_EMPTY')
        for native in (False, True):
            path, _ = self.unit(native)
            if path.exists(): path.unlink()
        if self.drop.exists(): self.drop.unlink()
        if self.drop_parent.exists(): self.drop_parent.rmdir()
        command('/usr/bin/systemctl', 'daemon-reload')
        shutil.rmtree(self.root)


class SystemdPorts:
    """Actual fixture I/O; database/provider rows are explicitly CI evidence."""
    def __init__(self, layout, kernel, context):
        self.layout, self.kernel, self.context = layout, kernel, context
        self.case = context['case']
        self.faulted = False
        import fcntl
        self.lock = os.open(layout.root/'exclusive.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def close(self): os.close(self.lock)

    def check(self, request):
        need(request.digest == self.context['request_sha256'], 'REQUEST_BINDING')
        for name, pin in self.context['staged_sha256'].items():
            need(sha(root_file(self.layout.stage/name, 0o600)) == pin, 'SOURCE_DRIFT')

    def guard_forward(self, request):
        self.check(request)
        need(not (self.layout.root/'STOP').exists(), 'STOP')

    def guard_recovery(self, request): self.check(request)

    def lifecycle_clock(self, request):
        self.check(request)
        return self.kernel.LifecycleClock(observed_at=int(time.time()),
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            boottime_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME))

    def effects(self, name):
        fd = os.open(self.layout.root/'effects.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'ab') as stream:
            stream.write(encoded(dict(effect=name, pid=os.getpid(), at=time.monotonic_ns()))+b'\n')
            stream.flush(); os.fsync(stream.fileno())

    def after(self, name):
        if self.case == 'lost-'+name+'-ack' and not self.faulted:
            self.faulted = True
            raise RuntimeError('FIXTURE_LOST_ACK')
        if name == 'stop' and self.case in ('sigkill-after-stop', 'timeout-after-stop'):
            new_file(self.layout.root/'stopped-before-death', b'1')
            if self.case == 'sigkill-after-stop': os.kill(os.getpid(), signal.SIGKILL)
            time.sleep(60)  # PID1 RuntimeMax, never the CI caller, ends this case.

    def snapshot(self, request):
        k, l = self.kernel, self.layout
        fields = ['ActiveState', 'SubState', 'MainPID', 'ControlPID', 'InvocationID',
                  'WorkingDirectory', 'FragmentPath', 'DropInPaths', 'Environment', 'NeedDaemonReload']
        row = show(l.legacy, fields)
        need(row['FragmentPath'] == str(SYSTEM_UNITS/l.legacy)
             and row['DropInPaths'] == str(l.drop) and row['WorkingDirectory'] == str(l.release), 'LOADED_LAYOUT')
        modes = [item.split('=', 1)[1] for item in row['Environment'].split() if item.startswith('AUTOPILOT_ADMISSION_MODE=')]
        need(len(modes) == 1 and modes[0] in ('ACTIVE', 'HOLD'), 'MODE')
        pid = int(row['MainPID'])
        running = row['ActiveState'] == 'active' and row['SubState'] == 'running' and pid > 0
        need(running or pid == 0 and row['ActiveState'] in ('inactive', 'failed'), 'STATE')
        hardening = show(l.legacy, ['User', 'Group', 'NoNewPrivileges', 'PrivateNetwork', 'PrivateTmp',
                                    'ProtectSystem', 'ProtectHome', 'KillMode', 'Restart', 'TimeoutStopUSec', 'RuntimeMaxUSec'])
        need(hardening == dict(User='nobody', Group='nogroup', NoNewPrivileges='yes', PrivateNetwork='yes',
             PrivateTmp='yes', ProtectSystem='strict', ProtectHome='yes', KillMode='control-group',
             Restart='no', TimeoutStopUSec='1s', RuntimeMaxUSec='1min 30s'), 'HARDENING')
        immutable = {str(path): sha(root_file(path, mode)) for path, mode in (
            (SYSTEM_UNITS/l.legacy, 0o644), (l.root/'worker.py', 0o444),
            (l.release/'ops/autopilot/broker-hold.env', 0o444))}
        return k.Snapshot(observed_at=int(time.time()), hostname=os.uname().nodename, unit=l.legacy,
            legacy_source='a'*40, legacy_bundle_sha256=sha(encoded(immutable)), release=str(l.release),
            state='RUNNING' if running else 'STOPPED', admission=modes[0], pid=pid,
            invocation_id=row['InvocationID'], start_ticks=proc_start(pid) if pid else 0,
            immutable_config_sha256=sha(encoded(immutable)), hardening_sha256=sha(encoded(hardening)),
            drop_bytes=root_file(l.drop, 0o644), control_pid=int(row['ControlPID']),
            cgroup_empty=cgroup_empty(l.legacy) if not running else False,
            needs_reload=row['NeedDaemonReload'] == 'yes')

    def native_hold(self, request):
        l = self.layout
        row = await_running(l.native, 'HOLD')
        need(root_file(l.root/'native-admission', 0o600) == b'HOLD\n', 'NATIVE_ADMISSION')
        config = sha(root_file(SYSTEM_UNITS/l.native, 0o644)+root_file(l.root/'native-admission', 0o600))
        return self.kernel.NativeHold(observed_at=int(time.time()), unit=l.native, state='RUNNING', admission='HOLD',
            pid=int(row['MainPID']), invocation_id=row['InvocationID'], start_ticks=proc_start(int(row['MainPID'])),
            configuration_sha256=config)

    @staticmethod
    def seconds(value):
        terms = re.findall(r'(\d+)(min|ms|us|s)', value)
        need(terms and ' '.join(n+unit for n, unit in terms) == value, 'DURATION')
        return sum(int(n)*{'min': 60, 's': 1, 'ms': .001, 'us': .000001}[unit] for n, unit in terms)

    def armed(self, request):
        l = self.layout
        keys = ['MainPID', 'ControlPID', 'InvocationID', 'ExecStart', 'ExecStopPost', 'RuntimeMaxUSec',
                'TimeoutStopUSec', 'User', 'Group', 'NoNewPrivileges', 'KillMode', 'Restart']
        row = show(l.supervisor, keys)
        phase = 'MAIN' if row['MainPID'] == str(os.getpid()) else 'CLEANUP'
        need(row['MainPID' if phase == 'MAIN' else 'ControlPID'] == str(os.getpid()), 'PID1_OWNER')
        hook = '/usr/bin/python3 -I -S -B '+str(l.stage/'fixture.py')+' recover '+str(l.root)+' '+request.digest
        need(row['ExecStopPost'].startswith('{ path=/usr/bin/python3 ; argv[]='+hook+' ; ignore_errors=no ;')
             and row['ExecStopPost'].endswith(' }') and row['ExecStopPost'].count('{') == 1, 'CLEANUP_HOOK')
        forward = hook.replace(' recover ', ' forward ')
        need(row['ExecStart'].startswith('{ path=/usr/bin/python3 ; argv[]='+forward+' ; ignore_errors=no ;')
             and row['ExecStart'].endswith(' }') and row['ExecStart'].count('{') == 1, 'FORWARD_COMMAND')
        runtime = self.seconds(row['RuntimeMaxUSec'])
        stop_timeout = self.seconds(row['TimeoutStopUSec'])
        need(runtime == int(runtime) and stop_timeout == int(stop_timeout), 'FRACTIONAL_DURATION')
        return self.kernel.ArmedCleanup(observed_at=int(time.time()), request_sha256=request.digest,
            unit=l.supervisor, pid=os.getpid(), invocation_id=row['InvocationID'], start_ticks=proc_start(os.getpid()),
            script_sha256=sha(root_file(l.stage/'fixture.py', 0o600)), exec_stop_post_request_sha256=request.digest,
            runtime_max_seconds=int(runtime), timeout_stop_seconds=int(stop_timeout), user=row['User'], group=row['Group'],
            no_new_privileges=row['NoNewPrivileges'], kill_mode=row['KillMode'], restart=row['Restart'], phase=phase)

    def arm_cleanup(self, request): return self.armed(request)
    def verify_cleanup(self, request, receipt): return self.armed(request)

    def drain(self, request, snapshot):
        self.check(request)
        need(cgroup_empty(self.layout.legacy), 'DRAIN_LEGACY')
        rows = json.loads(root_file(self.layout.root/'provider-state.json', 0o600))
        snapshot_pin = sha(self.kernel.encoded(snapshot.binding()))
        evidence = sha(encoded(rows))
        return self.kernel.DrainProof(observed_at=int(time.time()), request_sha256=request.digest,
            snapshot_sha256=snapshot_pin,
            database=dict(identity_sha256=sha(b'CI_ONLY_NO_DATABASE'), snapshot_sha256=snapshot_pin,
                native_enabled=False, legacy_backends=[], live_tasks=rows['database'], active_outbox=[],
                worker_leases=[], probe_leases=[]),
            provider=dict(evidence_sha256=evidence, inflight=rows['provider'], unknown=[]),
            leases=dict(evidence_sha256=evidence, owner_pid=os.getpid(), owner_start_ticks=proc_start(os.getpid()),
                exclusive=True, active_consumers=rows['leases']))

    def stop_expected(self, request, snapshot):
        need(self.snapshot(request).binding() == snapshot.binding() and snapshot.pid > 0, 'STOP_OWNERSHIP')
        child_process = await_descendant(self.layout.legacy, snapshot.pid)
        self.effects('stop')
        command('/usr/bin/systemctl', 'stop', self.layout.legacy, timeout=5)
        need(not same_process(child_process['pid'], child_process['start_ticks']) and
             cgroup_empty(self.layout.legacy), 'DESCENDANT_SURVIVED')
        fd = os.open(self.layout.root/'descendants.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'ab') as stream:
            stream.write(encoded(child_process)+b'\n'); stream.flush(); os.fsync(stream.fileno())
        self.after('stop')

    def replace_drop_cas(self, request, old, new):
        l = self.layout
        need(cgroup_empty(l.legacy) and root_file(l.drop, 0o644) == old, 'DROP_CAS')
        need(new in (exact_drop(l.release, 'HOLD'), exact_drop(l.release, 'ACTIVE')), 'DROP_TARGET')
        path = l.drop_parent/'.admission-new'
        new_file(path, new, 0o644)
        try:
            need(root_file(l.drop, 0o644) == old, 'DROP_CAS')
            os.replace(path, l.drop); sync(l.drop_parent)
        finally:
            if path.exists(): path.unlink()
        self.effects('replace'); self.after('replace')

    def reload_expected(self, request):
        self.effects('reload')
        command('/usr/bin/systemctl', 'daemon-reload')
        self.after('reload')

    def start_once(self, request, admission):
        need(cgroup_empty(self.layout.legacy) and root_file(self.layout.drop, 0o644) ==
             exact_drop(self.layout.release, admission), 'START_STATE')
        self.effects('start-'+admission.lower())
        command('/usr/bin/systemctl', 'start', self.layout.legacy)
        await_running(self.layout.legacy, admission)
        self.after('start-'+admission.lower())


def load_kernel(stage):
    sys.path.insert(0, str(stage))
    from ops import light_native_legacy_transition
    return light_native_legacy_transition


def request_for(layout, context, kernel, source, case):
    ports = SystemdPorts(layout, kernel, context)
    try:
        before = ports.snapshot(None); native = ports.native_hold(None)
    finally:
        ports.close()
    now = int(time.time())
    recovery = case in ('active-recovery', 'inflight-recovery', 'lost-start-active-ack')
    value = dict(version=1, kind='LEGACY_HOLD_TRANSITION', source=source,
        controller_sha256=sha(root_file(layout.stage/'ops/light_native_legacy_transition.py', 0o600)),
        legacy_source=before.legacy_source, legacy_bundle_sha256=before.legacy_bundle_sha256,
        original_baseline_sha256=context['original_baseline_sha256'],
        original_inventory_sha256=context['original_inventory_sha256'],
        before=before.binding(), native_before=native.binding(),
        start_hold=case in ('running-hold', 'active-recovery', 'lost-start-hold-ack'), max_seconds=30,
        cleanup_script_sha256=context['helper_sha256'], forward_agreement=None,
        active_recovery_agreement=None)
    # These are synthetic fixture acceptances, never production Agreement.
    end = now + (20 if case == 'timeout-after-stop' else 90)
    value['forward_agreement'] = dict(version=1, kind='LEGACY_TRANSITION_FORWARD',
        operation_sha256=kernel.scope_sha256(value, 'FORWARD'), not_before=now-1,
        expires_at=end, evidence_sha256=sha(b'CI_ONLY_FORWARD_ACCEPTANCE'))
    if recovery:
        value['active_recovery_agreement'] = dict(version=1, kind='LEGACY_TRANSITION_ACTIVE_RECOVERY',
            operation_sha256=kernel.scope_sha256(value, 'RECOVERY'), not_before=now-1,
            expires_at=end+60, evidence_sha256=sha(b'CI_ONLY_ACTIVE_RECOVERY_ACCEPTANCE'))
    raw = kernel.encoded(value)
    context.update(request_sha256=sha(raw), source=source,
        accepted_forward_agreement_sha256=sha(kernel.encoded(value['forward_agreement'])),
        accepted_active_recovery_agreement_sha256=(sha(kernel.encoded(value['active_recovery_agreement'])) if recovery else None),
        staged_sha256={name: sha(root_file(layout.stage/name, 0o600)) for name in
            ('fixture.py', 'ops/__init__.py', 'ops/light_native_legacy_transition.py', 'ops/light_native_service_authority.py')})
    new_file(layout.root/'request.json', raw)
    new_file(layout.context, encoded(context))
    kernel.Journal(layout.journal)
    return kernel.Request.load(raw, context['request_sha256'],
        accepted_forward_agreement_sha256=context['accepted_forward_agreement_sha256'],
        accepted_active_recovery_agreement_sha256=context['accepted_active_recovery_agreement_sha256'])


def child(mode, root, request_pin):
    layout = Layout(root)
    context_raw = root_file(layout.context, 0o600)
    context = json.loads(context_raw)
    need(encoded(context) == context_raw and context['purpose'] == 'CI_ONLY_NO_PRODUCTION_AUTHORITY' and context['fixture'] == layout.name and
         context['request_sha256'] == request_pin, 'CHILD_BINDING')
    for name, pin in context['staged_sha256'].items():
        need(name in ('fixture.py', 'ops/__init__.py', 'ops/light_native_legacy_transition.py',
                      'ops/light_native_service_authority.py') and
             sha(root_file(layout.stage/name, 0o600)) == pin, 'CHILD_SOURCE')
    kernel = load_kernel(layout.stage)
    request = kernel.Request.load(root_file(layout.root/'request.json', 0o600), request_pin,
        accepted_forward_agreement_sha256=context['accepted_forward_agreement_sha256'],
        accepted_active_recovery_agreement_sha256=context['accepted_active_recovery_agreement_sha256'])
    journal = kernel.Journal.open(layout.journal)
    ports = SystemdPorts(layout, kernel, context)
    try:
        if mode == 'forward':
            result = kernel.run_transition(request, ports=ports, journal=journal)
        else:
            operation = kernel.rollback_transition if request.value['active_recovery_agreement'] else kernel.contain_transition
            result = operation(request, ports=ports, journal=journal)
        new_file(layout.root/(mode+'-result.json'), kernel.encoded(result.public()))
        if mode == 'recover' and context['case'] == 'lost-start-active-ack':
            readback = kernel.rollback_transition(request, ports=ports, journal=journal)
            new_file(layout.root/'recover-readback-result.json', kernel.encoded(readback.public()))
    finally:
        ports.close()


def launch(layout, request):
    script = str(layout.stage/'fixture.py')
    hook = '/usr/bin/python3 -I -S -B '+script+' recover '+str(layout.root)+' '+request.digest
    command('/usr/bin/systemd-run', '--quiet', '--unit='+layout.supervisor,
        '--property=Type=exec', '--property=User=root', '--property=Group=root',
        '--property=NoNewPrivileges=yes', '--property=PrivateNetwork=yes',
        '--property=Restart=no', '--property=KillMode=control-group',
        '--property=RuntimeMaxSec=30', '--property=TimeoutStopSec=15',
        '--property=ExecStopPost='+hook,
        '/usr/bin/python3', '-I', '-S', '-B', script, 'forward', str(layout.root), request.digest)


def await_finished(layout, case):
    end = time.monotonic() + (55 if case == 'timeout-after-stop' else 15)
    while time.monotonic() < end:
        row = show(layout.supervisor, ['ActiveState', 'MainPID', 'ControlPID'])
        if row['ActiveState'] in ('inactive', 'failed') and row['MainPID'] == row['ControlPID'] == '0':
            return
        time.sleep(0.05)
    raise RuntimeError('TRANSITION_FIXTURE_SUPERVISOR_TIMEOUT')


def outcome(layout, mode, kernel):
    path = layout.root/(mode+'-result.json')
    return kernel.parse(root_file(path, 0o600)) if path.exists() else None


def verify_case(layout, context, request, kernel, case):
    ports = SystemdPorts(layout, kernel, context)
    try:
        current = ports.snapshot(request)
        need(ports.native_hold(request).binding() == request.value['native_before'], 'NATIVE_CHANGED')
    finally:
        ports.close()
    forward, recovery = outcome(layout, 'forward', kernel), outcome(layout, 'recover', kernel)
    need(recovery is not None, 'INDEPENDENT_CLEANUP_MISSING')
    for result in (forward, recovery):
        if result is None: continue
        need(result['request_sha256'] == request.digest and all(result[name] is False for name in
            ('historical_chain_verified', 'original_baseline_matched', 'incident_closed',
             'replay_authorized', 'native_admission_authorized', 'new_task_authorized')), 'AUTHORITY_ESCALATION')
    active = case in ('active-recovery', 'foreign-invocation', 'lost-start-active-ack')
    running = active or case in ('running-hold', 'lost-start-hold-ack')
    need(current.state == ('RUNNING' if running else 'STOPPED') and current.admission ==
         ('ACTIVE' if active else 'HOLD') and not current.needs_reload and current.drop_bytes ==
         exact_drop(layout.release, current.admission), 'FINAL_STATE')
    effects_path = layout.root/'effects.jsonl'
    effects = [json.loads(line)['effect'] for line in root_file(effects_path, 0o600).splitlines()] if effects_path.exists() else []
    need(effects.count('start-active') <= 1 and effects.count('start-hold') <= 1, 'START_REPLAY')
    if effects.count('stop'):
        descendants = [json.loads(line) for line in root_file(layout.root/'descendants.jsonl', 0o600).splitlines()]
        need(len(descendants) == effects.count('stop') and all(not same_process(row['pid'], row['start_ticks'])
             for row in descendants), 'DESCENDANT_EVIDENCE')
    if case == 'foreign-invocation':
        need(forward['state'] == 'REFUSED_BEFORE_EFFECT' and effects == [] and
             current.invocation_id != request.before.invocation_id, 'FOREIGN_PROCESS_MUTATED')
    elif case == 'active-recovery':
        need(forward['state'] == 'COMPLETED_RUNNING_HOLD' and recovery['state'] == 'ACTIVE_RESTORED'
             and effects.count('start-active') == 1 and effects.count('stop') == 2, 'ACTIVE_RECOVERY')
    elif case == 'inflight-recovery':
        need(forward['state'] == recovery['state'] == 'UNKNOWN' and recovery['safe_state'] == 'STOPPED_HOLD'
             and 'start-active' not in effects, 'INFLIGHT_ACTIVE_FORBIDDEN')
    elif case in ('sigkill-after-stop', 'timeout-after-stop'):
        need(forward is None and (layout.root/'stopped-before-death').exists() and
             recovery['state'] == 'SAFE_STOPPED_HOLD', 'PID1_CLEANUP')
        if case == 'timeout-after-stop':
            need(time.time() >= request.value['forward_agreement']['expires_at'], 'EXPIRY_NOT_EXERCISED')
    elif case == 'lost-start-active-ack':
        readback = outcome(layout, 'recover-readback', kernel)
        need(forward['state'] == 'COMPLETED_STOPPED_HOLD' and recovery['state'] == 'UNKNOWN' and
             readback['state'] == 'ACTIVE_OBSERVED' and readback['unknown'] is True and
             effects.count('start-active') == 1, 'ACTIVE_START_ACK_REPLAY')
    elif case.startswith('lost-'):
        need(forward['state'] == 'UNKNOWN' and effects.count(case[5:-4]) == 1 and
             recovery['state'] == ('SAFE_RUNNING_HOLD' if case == 'lost-start-hold-ack' else 'SAFE_STOPPED_HOLD'), 'ACK_REPLAY')
    else:
        need(forward['state'] == ('COMPLETED_RUNNING_HOLD' if running else 'COMPLETED_STOPPED_HOLD'), 'FORWARD_RESULT')
    need(request.value['original_baseline_sha256'] == context['original_baseline_sha256'] and
         request.value['original_inventory_sha256'] == context['original_inventory_sha256'], 'BASELINE_REPLACED')
    print(json.dumps(dict(case=case, result='PASS', real_systemd=True,
        real_database=False, real_provider=False), sort_keys=True), flush=True)


def run_fixture(repo, source):
    need(sys.platform == 'linux' and os.geteuid() == 0 and Path('/proc/1/comm').read_text().strip() == 'systemd', 'REAL_PID1_REQUIRED')
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_REPOSITORY') ==
         'olegmed1-art/bridge-video-free', 'CI_ONLY')
    need(re.fullmatch('[0-9a-f]{40}', source) and repo.is_absolute() and repo.is_dir(), 'SOURCE')
    cases = ('stopped-hold', 'running-hold', 'active-recovery', 'lost-stop-ack',
             'lost-replace-ack', 'lost-reload-ack', 'foreign-invocation',
             'inflight-recovery', 'lost-start-hold-ack', 'lost-start-active-ack',
             'sigkill-after-stop', 'timeout-after-stop')
    started = time.monotonic()
    for case in cases:
        layout = Layout(tempfile.mkdtemp(prefix=PREFIX, dir='/run'))
        try:
            context = layout.prepare(repo, case=case)
            kernel = load_kernel(layout.stage)
            request = request_for(layout, context, kernel, source, case)
            if case == 'foreign-invocation':
                command('/usr/bin/systemctl', 'restart', layout.legacy)
                await_running(layout.legacy, 'ACTIVE')
            if case == 'inflight-recovery':
                path = layout.root/'provider-state.json'
                path.unlink()
                new_file(path, encoded(dict(database=[], provider=['CI_SYNTHETIC_INFLIGHT'], leases=[])))
            launch(layout, request)
            await_finished(layout, case)
            verify_case(layout, context, request, kernel, case)
        except Exception:
            # Only this secretless fixture's unit journal is diagnostic output.
            diagnostic = command('/usr/bin/journalctl', '--unit='+layout.supervisor,
                '--no-pager', '--output=cat', '--lines=25', allow_failure=True).stdout
            print(json.dumps(dict(case=case, result='FAIL', fixture_journal=diagnostic.decode(errors='replace'))), flush=True)
            raise
        finally:
            layout.teardown()
    print(json.dumps(dict(audit='LEGACY_TRANSITION_SYSTEMD_REHEARSAL', passed=len(cases),
        elapsed_seconds=round(time.monotonic()-started, 1), production_accepted=False,
        database_connected=False, provider_connected=False), sort_keys=True), flush=True)


if __name__ == '__main__':
    need(os.geteuid() == 0, 'ROOT')
    if len(sys.argv) == 4 and sys.argv[1] in ('forward', 'recover'):
        need(re.fullmatch('[0-9a-f]{64}', sys.argv[3]), 'REQUEST_PIN')
        child(sys.argv[1], sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 4 and sys.argv[1] == 'ci':
        run_fixture(Path(sys.argv[2]).resolve(), sys.argv[3])
    else:
        raise RuntimeError('TRANSITION_FIXTURE_ARGUMENTS')
