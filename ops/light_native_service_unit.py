"""Read-only permanent-supervisor unit/readback boundary.

collect_loaded obtains protected unit bytes, two real systemctl readbacks and
independent /proc snapshots. A successful comparison grants no issue, execution,
installation or historical-continuity authority. Production installation still
requires independent acceptance; the fixed profile remains prospective.

Only the service's own context/journal root is writable in this profile. Issuer
and transient-execution paths, credentials and bootstrap wiring are not chosen
here. The retained finite authority still needs its own guards after restart.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import time


PROFILE = 'PROSPECTIVE_READ_ONLY_SUPERVISOR_V1'
ROOT = Path('/var/lib/bridge-light-native-service')
UNIT = 'bridge-light-native-supervisor.service'
UNIT_FILE = Path('/etc/systemd/system') / UNIT
ARGV = ('/usr/bin/python3', '-I', '-S', '-B', ROOT.as_posix()+'/launcher.py')
_ENVIRONMENT = ('PATH=/usr/bin:/bin', 'LC_ALL=C', 'PYTHONDONTWRITEBYTECODE=1', 'PYTHONUNBUFFERED=1')

_SERVICE = (
    ('Type', 'exec'), ('User', 'root'), ('Group', 'root'), ('UMask', '0077'),
    ('WorkingDirectory', ROOT.as_posix()), ('ExecStart', ' '.join(ARGV)),
    ('Environment', ' '.join(_ENVIRONMENT)), ('PassEnvironment', ''),
    ('UnsetEnvironment', 'PYTHONHOME PYTHONPATH'),
    ('ExecCondition', ''), ('ExecStartPre', ''), ('ExecStartPost', ''),
    ('ExecReload', ''), ('ExecStop', ''), ('ExecStopPost', ''),
    ('Restart', 'on-failure'), ('RestartSec', '5s'), ('RuntimeMaxSec', 'infinity'),
    ('KillMode', 'control-group'), ('TimeoutStopSec', '30s'),
    ('Nice', '10'), ('CPUQuota', '100%'), ('MemoryHigh', '512M'),
    ('MemoryMax', '768M'), ('TasksMax', '64'), ('NoNewPrivileges', 'yes'),
    ('PrivateTmp', 'yes'), ('PrivateDevices', 'yes'), ('PrivateNetwork', 'no'),
    ('ProtectHome', 'yes'), ('ProtectSystem', 'strict'),
    ('ProtectKernelTunables', 'yes'), ('ProtectKernelModules', 'yes'),
    ('ProtectControlGroups', 'yes'), ('RestrictSUIDSGID', 'yes'),
    ('ReadWritePaths', ROOT.as_posix()),
)

# Values below are normalized systemctl-show values, not unit-file syntax.
_EXPECTED = dict(
    LoadState='loaded', ActiveState='active', SubState='running', Type='exec',
    FragmentPath=UNIT_FILE.as_posix(), SourcePath='', DropInPaths='',
    NeedDaemonReload=False, Transient=False, UnitFileState='enabled',
    User='root', Group='root', UMask=0o77, WorkingDirectory=ROOT.as_posix(),
    Environment=tuple(sorted(_ENVIRONMENT)), EnvironmentFiles='', PassEnvironment='',
    UnsetEnvironment=('PYTHONHOME', 'PYTHONPATH'), RootDirectory='', RootImage='',
    PAMName='', SupplementaryGroups='', DynamicUser=False,
    ExecCondition='', ExecStartPre='', ExecStartPost='', ExecReload='', ExecStop='', ExecStopPost='',
    Restart='on-failure', RestartUSec=5_000_000, RuntimeMaxUSec='infinity',
    KillMode='control-group', TimeoutStopUSec=30_000_000,
    StartLimitIntervalUSec=60_000_000, StartLimitBurst=3,
    Nice=10, CPUQuotaPerSecUSec=1_000_000, MemoryHigh=536870912, MemoryMax=805306368,
    TasksMax=64, NoNewPrivileges=True, PrivateTmp=True, PrivateDevices=True,
    PrivateNetwork=False, ProtectHome=True, ProtectSystem='strict',
    ProtectKernelTunables=True, ProtectKernelModules=True, ProtectControlGroups=True,
    RestrictSUIDSGID=True, ReadWritePaths=ROOT.as_posix(),
    ControlGroup='/system.slice/'+UNIT, ControlPID=0,
)
SHOW_PROPERTIES = tuple(sorted((*_EXPECTED, 'ExecStart', 'MainPID', 'InvocationID', 'NRestarts')))
_BOOLS = frozenset(key for key, value in _EXPECTED.items() if type(value) is bool)
_INTEGERS = frozenset(key for key, value in _EXPECTED.items() if type(value) is int) - {
    'UMask', 'RestartUSec', 'TimeoutStopUSec', 'StartLimitIntervalUSec', 'CPUQuotaPerSecUSec'}
_DURATIONS = frozenset(('RestartUSec', 'TimeoutStopUSec', 'StartLimitIntervalUSec', 'CPUQuotaPerSecUSec'))
_PROC = Path('/proc')
_SHOW_LIMIT = 65536
_SHOW_SECONDS = 10
_EMPTY_ARRAYS = (
    ('EnvironmentFiles', 'a(sb)'),
    ('ExecCondition', 'a(sasbttttuii)'), ('ExecStartPre', 'a(sasbttttuii)'),
    ('ExecStartPost', 'a(sasbttttuii)'), ('ExecReload', 'a(sasbttttuii)'),
    ('ExecStop', 'a(sasbttttuii)'), ('ExecStopPost', 'a(sasbttttuii)'),
)
_SYSTEMCTL = ('/usr/bin/systemctl', '--no-pager', 'show', '--all', UNIT,
              '--property='+','.join(SHOW_PROPERTIES))
_BUSCTL = ('/usr/bin/busctl', '--system', '--json=short', 'get-property',
           'org.freedesktop.systemd1',
           '/org/freedesktop/systemd1/unit/bridge_2dlight_2dnative_2dsupervisor_2eservice',
           'org.freedesktop.systemd1.Service', *(name for name, _ in _EMPTY_ARRAYS))


class Refusal(RuntimeError):
    """Fixed public reason only; raw readback/process material is never included."""


def require(ok, code):
    if not ok:
        raise Refusal(code)


def render():
    """Reviewable future unit bytes; this function performs no installation."""
    lines = ['[Unit]', 'Description=Bridge Light Native prospective read-only supervisor',
             'After=network.target', 'StartLimitIntervalSec=60s', 'StartLimitBurst=3', '', '[Service]']
    lines.extend(key+'='+value for key, value in _SERVICE)
    lines.extend(('', '[Install]', 'WantedBy=multi-user.target', ''))
    return '\n'.join(lines).encode('ascii')


def _integer(value):
    require(re.fullmatch(r'0|[1-9][0-9]{0,18}', value) is not None, 'NATIVE_UNIT_VALUE')
    result = int(value)
    require(result <= 2**63-1, 'NATIVE_UNIT_VALUE')
    return result


def _duration(value):
    # Closed finite forms for this profile; no floats, rounding or compound units.
    match = re.fullmatch(r'(0|[1-9][0-9]{0,18})(us|ms|s|min|h)', value)
    require(match is not None, 'NATIVE_UNIT_VALUE')
    result = _integer(match[1])*{'us': 1, 'ms': 1000, 's': 10**6, 'min': 60*10**6, 'h': 3600*10**6}[match[2]]
    require(result <= 2**63-1, 'NATIVE_UNIT_VALUE')
    return result


def _words(value):
    words = value.split(' ')
    require(bool(value) and all(words) and len(words) == len(set(words))
            and all(re.fullmatch(r'[A-Za-z0-9_=/.:+-]+', word) for word in words), 'NATIVE_UNIT_VALUE')
    return tuple(sorted(words))


def _exec_start(value):
    require(value.startswith('{ ') and value.endswith(' }')
            and value.count('{') == value.count('}') == 1, 'NATIVE_UNIT_EXEC')
    fields = {}
    for item in value[2:-2].split(' ; '):
        key, sep, content = item.partition('=')
        require(sep and key not in fields, 'NATIVE_UNIT_EXEC')
        fields[key] = content
    require(set(fields) == {'path', 'argv[]', 'ignore_errors', 'start_time', 'stop_time', 'pid', 'code', 'status'}
            and fields['path'] == ARGV[0] and fields['argv[]'] == ' '.join(ARGV)
            and fields['ignore_errors'] == 'no' and fields['stop_time'] == '[n/a]'
            and fields['code'] == '(null)' and fields['status'] == '0/0', 'NATIVE_UNIT_EXEC')
    pid = _integer(fields['pid'])
    if pid == 0:
        # daemon-reload can reset command accounting while the real process
        # remains alive. Retain this explicit form; it is not process identity.
        require(fields['start_time'] == '[n/a]', 'NATIVE_UNIT_EXEC')
        return (ARGV, 0, '[n/a]')
    require(pid > 1, 'NATIVE_UNIT_EXEC')
    match = re.fullmatch(r'\[(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ([A-Za-z0-9_+:/.-]{1,32})\]',
                         fields['start_time'])
    require(match is not None, 'NATIVE_UNIT_EXEC')
    try:
        moment = datetime.fromisoformat(match[2])
    except ValueError:
        raise Refusal('NATIVE_UNIT_EXEC') from None
    require(('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[moment.weekday()] == match[1], 'NATIVE_UNIT_EXEC')
    return (ARGV, pid, fields['start_time'])


def parse_show(raw):
    """Parse only an exact SHOW_PROPERTIES query, with no optional/unknown keys."""
    require(type(raw) is bytes and 0 < len(raw) <= 65536 and raw.endswith(b'\n')
            and all(byte == 10 or 32 <= byte <= 126 for byte in raw), 'NATIVE_UNIT_READBACK')
    try:
        lines = raw.decode('ascii').splitlines()
    except UnicodeError:
        raise Refusal('NATIVE_UNIT_READBACK') from None
    result = {}
    for line in lines:
        key, sep, value = line.partition('=')
        require(sep and key in SHOW_PROPERTIES and key not in result, 'NATIVE_UNIT_READBACK')
        if key in _BOOLS:
            require(value in ('yes', 'no'), 'NATIVE_UNIT_VALUE')
            value = value == 'yes'
        elif key in _INTEGERS or key in ('MainPID', 'NRestarts'):
            value = _integer(value)
        elif key in _DURATIONS:
            value = _duration(value)
        elif key == 'UMask':
            require(re.fullmatch('[0-7]{4}', value) is not None, 'NATIVE_UNIT_VALUE')
            value = int(value, 8)
        elif key in ('Environment', 'UnsetEnvironment'):
            value = _words(value)
        elif key == 'ExecStart':
            value = _exec_start(value)
        result[key] = value
    require(set(result) == set(SHOW_PROPERTIES), 'NATIVE_UNIT_READBACK')
    return result


@dataclass(frozen=True)
class ProcessSnapshot:
    """Trusted collector inputs from the actual process, not loaded properties.

    no_new_privs is the observed NoNewPrivs integer from /proc/<pid>/status;
    invocation_id is INVOCATION_ID from that process. Copying these or UID/cwd
    from systemctl is not independent /proc evidence. Loaded NoNewPrivileges=yes
    alone cannot establish the effective protection of an already running PID.
    collect_loaded uses the fixed /proc collector, with no configurable port.
    """
    pid: int
    uid: int
    gid: int
    no_new_privs: int
    start_ticks: int
    boot_id: str
    exe_sha256: str
    argv: tuple
    cgroup: str
    cwd: str
    invocation_id: str


@dataclass(frozen=True)
class LoadedBinding:
    """Internal comparison result, not an authority or installation receipt."""
    profile: str
    unit_sha256: str
    loaded_sha256: str
    process: ProcessSnapshot
    restarts: int


def validate_loaded(unit_raw, before_raw, after_raw, process_before, process_after, *, expected_python_sha256):
    """Compare two loaded readbacks and /proc snapshots against the fixed profile.

    Caller collection must bracket the loaded queries with the real process
    snapshots, use protected reads and repeat at the actual future action point.
    Manifest acceptance, source, STOP, clocks, child ancestry and PID1 execution
    delegation remain separate mandatory authority checks.
    """
    require(type(unit_raw) is bytes and unit_raw == render(), 'NATIVE_UNIT_BYTES')
    before, after = parse_show(before_raw), parse_show(after_raw)
    require(before == after, 'NATIVE_UNIT_CHANGED')
    require(all(before[key] == expected for key, expected in _EXPECTED.items()), 'NATIVE_UNIT_PROFILE')
    require(type(process_before) is ProcessSnapshot and type(process_after) is ProcessSnapshot,
            'NATIVE_UNIT_PROCESS')
    for process in (process_before, process_after):
        require(all(type(getattr(process, key)) is int for key in ('pid', 'uid', 'gid', 'no_new_privs', 'start_ticks'))
                and process.pid > 1 and process.uid == process.gid == 0
                and process.no_new_privs == 1 and process.start_ticks > 0
                and type(process.boot_id) is str and re.fullmatch(
                    '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', process.boot_id)
                and type(process.invocation_id) is str and re.fullmatch('[0-9a-f]{32}', process.invocation_id)
                and type(expected_python_sha256) is str and re.fullmatch('[0-9a-f]{64}', expected_python_sha256)
                and type(process.exe_sha256) is str and process.exe_sha256 == expected_python_sha256
                and type(process.argv) is tuple and all(type(arg) is str for arg in process.argv)
                and process.argv == ARGV and type(process.cwd) is str and process.cwd == ROOT.as_posix()
                and type(process.cgroup) is str and process.cgroup == '0::/system.slice/'+UNIT,
                'NATIVE_UNIT_PROCESS')
    require(process_before == process_after and before['MainPID'] == process_before.pid
            and before['InvocationID'] == process_before.invocation_id
            and before['ExecStart'][0] == ARGV
            and (before['ExecStart'][1] == process_before.pid
                 or before['ExecStart'][1:] == (0, '[n/a]')), 'NATIVE_UNIT_PROCESS_CHANGED')
    canonical = json.dumps(before, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')
    return LoadedBinding(PROFILE, hashlib.sha256(unit_raw).hexdigest(), hashlib.sha256(canonical).hexdigest(),
                         process_before, before['NRestarts'])


def _metadata(row):
    names = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size',
             'st_atime_ns', 'st_mtime_ns', 'st_ctime_ns')
    return tuple(getattr(row, key) for key in names) + (getattr(row, 'st_rdev', None),)


def _read_fd(fd, limit):
    result = bytearray()
    while len(result) <= limit:
        chunk = os.read(fd, min(65536, limit+1-len(result)))
        if not chunk:
            return bytes(result)
        result.extend(chunk)
    raise Refusal('NATIVE_UNIT_READ_LIMIT')


def _protected_unit(expected):
    require(type(expected) is str and re.fullmatch('[0-9a-f]{64}', expected), 'NATIVE_UNIT_PIN')
    for parent in (UNIT_FILE.parent, *UNIT_FILE.parent.parents):
        row = parent.lstat()
        require(stat.S_ISDIR(row.st_mode) and row.st_uid == 0 and not row.st_mode & 0o022,
                'NATIVE_UNIT_FILE')
    no_atime = getattr(os, 'O_NOATIME', None)
    require(type(no_atime) is int and no_atime > 0, 'NATIVE_UNIT_NOATIME')
    fd = os.open(UNIT_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | no_atime)
    try:
        row = os.fstat(fd)
        signature = _metadata(row)
        require(stat.S_ISREG(row.st_mode) and row.st_uid == row.st_gid == 0 and row.st_nlink == 1
                and stat.S_IMODE(row.st_mode) == 0o644 and 0 < row.st_size <= 65536, 'NATIVE_UNIT_FILE')
        raw = _read_fd(fd, 65536)
        require(_metadata(os.fstat(fd)) == signature
                and _metadata(UNIT_FILE.stat(follow_symlinks=False)) == signature, 'NATIVE_UNIT_FILE_CHANGED')
        require(hashlib.sha256(raw).hexdigest() == expected, 'NATIVE_UNIT_PIN')
        return raw
    finally:
        os.close(fd)


def _read_command(command, *, deadline, limit):
    """Bound one of the two fixed read commands, including its arriving bytes."""
    require(time.monotonic() < deadline, 'NATIVE_UNIT_SHOW_TIMEOUT')
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'TZ': 'UTC', 'SYSTEMD_COLORS': '0'})
    try:
        result = bytearray()
        os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline-time.monotonic()
                require(remaining > 0, 'NATIVE_UNIT_SHOW_TIMEOUT')
                if not selector.select(remaining):
                    continue
                try:
                    chunk = os.read(process.stdout.fileno(), limit+1-len(result))
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                result.extend(chunk)
                require(len(result) <= limit, 'NATIVE_UNIT_SHOW_LIMIT')
        remaining = deadline-time.monotonic()
        require(remaining > 0, 'NATIVE_UNIT_SHOW_TIMEOUT')
        require(process.wait(timeout=remaining) == 0, 'NATIVE_UNIT_SHOW')
        return bytes(result)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        process.stdout.close()


def _attest_empty_arrays(raw):
    """Typed D-Bus evidence only; absence in systemctl output is not emptiness."""
    require(type(raw) is bytes and 0 < len(raw) <= 4096 and raw.endswith(b'\n')
            and all(byte == 10 or 32 <= byte <= 126 for byte in raw), 'NATIVE_UNIT_ARRAY_ATTESTATION')
    rows = raw.splitlines()
    require(len(rows) == len(_EMPTY_ARRAYS), 'NATIVE_UNIT_ARRAY_ATTESTATION')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'NATIVE_UNIT_ARRAY_ATTESTATION')
            result[key] = value
        return result
    try:
        for row, (_, signature) in zip(rows, _EMPTY_ARRAYS):
            value = json.loads(row, object_pairs_hook=unique,
                parse_constant=lambda _: require(False, 'NATIVE_UNIT_ARRAY_ATTESTATION'))
            require(type(value) is dict and set(value) == {'type', 'data'}
                    and type(value['type']) is str and value['type'] == signature
                    and type(value['data']) is list and value['data'] == [], 'NATIVE_UNIT_ARRAY_ATTESTATION')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Refusal('NATIVE_UNIT_ARRAY_ATTESTATION') from None


def _show():
    """Attest seven known empty arrays, then complete only those missing lines."""
    deadline = time.monotonic()+_SHOW_SECONDS
    raw = _read_command(_SYSTEMCTL, deadline=deadline, limit=_SHOW_LIMIT)
    _attest_empty_arrays(_read_command(_BUSCTL, deadline=deadline, limit=4096))
    require(type(raw) is bytes and 0 < len(raw) <= _SHOW_LIMIT and raw.endswith(b'\n'), 'NATIVE_UNIT_READBACK')
    selected = {name.encode() for name, _ in _EMPTY_ARRAYS}
    seen = set()
    for line in raw.splitlines():
        key, _, value = line.partition(b'=')
        if key in selected:
            require(value == b'', 'NATIVE_UNIT_ARRAY_ATTESTATION')
        seen.add(key)
    completed = raw+b''.join(name.encode()+b'=\n' for name, _ in _EMPTY_ARRAYS if name.encode() not in seen)
    require(len(completed) <= _SHOW_LIMIT, 'NATIVE_UNIT_READBACK')
    parse_show(completed)  # Includes unknown/duplicate keys and every non-attested property.
    require(time.monotonic() < deadline, 'NATIVE_UNIT_SHOW_TIMEOUT')
    return completed


def _proc_read(directory, name, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'NATIVE_UNIT_PROC')
        return _read_fd(fd, limit)
    finally:
        os.close(fd)


def _start(raw, pid):
    prefix, sep, tail = raw.rpartition(b') ')
    fields = tail.split()
    require(sep and prefix.startswith(str(pid).encode()+b' (') and len(fields) >= 20
            and fields[0] in (b'R', b'S', b'D', b'I'), 'NATIVE_UNIT_PROC')
    value = fields[19].decode('ascii')
    result = _integer(value)
    require(result > 0, 'NATIVE_UNIT_PROC')
    return result


def _status(raw):
    selected = {}
    for line in raw.decode('ascii').splitlines():
        key, sep, value = line.partition(':')
        if key in ('Uid', 'Gid', 'NoNewPrivs'):
            require(sep and key not in selected, 'NATIVE_UNIT_PROC')
            selected[key] = value.split()
    require(set(selected) == {'Uid', 'Gid', 'NoNewPrivs'}
            and len(selected['Uid']) == len(selected['Gid']) == 4
            and all(value == '0' for key in ('Uid', 'Gid') for value in selected[key])
            and selected['NoNewPrivs'] in (['0'], ['1']), 'NATIVE_UNIT_PROC')
    return int(selected['Uid'][1]), int(selected['Gid'][1]), int(selected['NoNewPrivs'][0])


def _process(pid):
    require(type(pid) is int and pid > 1, 'NATIVE_UNIT_PROC')
    directory = os.open(_PROC/str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        row = os.fstat(directory)
        require(stat.S_ISDIR(row.st_mode) and row.st_uid == row.st_gid == 0, 'NATIVE_UNIT_PROC')
        start = _start(_proc_read(directory, 'stat', 65536), pid)
        status = _status(_proc_read(directory, 'status', 65536))
        cmdline = _proc_read(directory, 'cmdline', 65536)
        require(cmdline.endswith(b'\0'), 'NATIVE_UNIT_PROC')
        argv = tuple(item.decode('utf-8') for item in cmdline[:-1].split(b'\0'))
        environment = _proc_read(directory, 'environ', 262144)
        require(environment.endswith(b'\0'), 'NATIVE_UNIT_PROC')
        invocations = [item.partition(b'=')[2] for item in environment[:-1].split(b'\0')
                       if item.partition(b'=')[0] == b'INVOCATION_ID']
        require(len(invocations) == 1, 'NATIVE_UNIT_PROC')
        invocation = invocations[0].decode('ascii')
        no_atime = getattr(os, 'O_NOATIME', None)
        require(type(no_atime) is int and no_atime > 0, 'NATIVE_UNIT_NOATIME')
        # exe/cwd are intentional kernel /proc magic links. All other process
        # files and the PID directory are opened with O_NOFOLLOW.
        executable = os.open('exe', os.O_RDONLY | os.O_NONBLOCK | no_atime, dir_fd=directory)
        try:
            image = os.fstat(executable)
            signature = _metadata(image)
            require(stat.S_ISREG(image.st_mode) and image.st_uid == 0 and image.st_nlink > 0
                    and not image.st_mode & 0o022 and image.st_size <= 64*1024*1024, 'NATIVE_UNIT_PROC')
            executable_sha256 = hashlib.sha256(_read_fd(executable, 64*1024*1024)).hexdigest()
            require(_metadata(os.fstat(executable)) == signature, 'NATIVE_UNIT_PROC_CHANGED')
        finally:
            os.close(executable)
        cwd = os.readlink('cwd', dir_fd=directory)
        cgroup = _proc_read(directory, 'cgroup', 4096).decode('ascii').strip()
        boot_fd = os.open(_PROC/'sys/kernel/random/boot_id', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            require(stat.S_ISREG(os.fstat(boot_fd).st_mode), 'NATIVE_UNIT_PROC')
            boot = _read_fd(boot_fd, 128)
        finally:
            os.close(boot_fd)
        require(0 < len(boot) <= 128, 'NATIVE_UNIT_PROC')
        boot_id = boot.decode('ascii').strip()
        require(_status(_proc_read(directory, 'status', 65536)) == status
                and _start(_proc_read(directory, 'stat', 65536), pid) == start, 'NATIVE_UNIT_PROC_CHANGED')
        return ProcessSnapshot(pid, *status, start, boot_id, executable_sha256, argv, cgroup, cwd, invocation)
    finally:
        os.close(directory)


def collect_loaded(pid, *, expected_python_sha256, expected_unit_sha256):
    """Collect fixed host facts; this is not installation/issue acceptance."""
    try:
        require(sys.platform == 'linux' and os.geteuid() == 0, 'NATIVE_UNIT_LINUX_ROOT')
        require(type(pid) is int and pid > 1, 'NATIVE_UNIT_PROC')
        unit_before = _protected_unit(expected_unit_sha256)
        process_before = _process(pid)
        before = _show()
        after = _show()
        process_after = _process(pid)
        unit_after = _protected_unit(expected_unit_sha256)
        require(unit_before == unit_after, 'NATIVE_UNIT_FILE_CHANGED')
        return validate_loaded(unit_before, before, after, process_before, process_after,
                               expected_python_sha256=expected_python_sha256)
    except Refusal:
        raise
    except Exception:
        raise Refusal('NATIVE_UNIT_COLLECTION') from None
