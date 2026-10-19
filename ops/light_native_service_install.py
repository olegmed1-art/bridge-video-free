"""First, stopped Native HOST installation with an absence snapshot and rollback.

No active service is replaced. The existing legacy and native consumer units
are never stopped or edited. This code installs only the already qualified
dormant entry and the unchanged prospective read-only unit; it cannot enable,
start, issue work, accept a task policy/window, or change HOLD.
"""
from datetime import datetime, timezone
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from ops import light_native_service_history as history
from ops import light_native_service_unit as unit
from ops.native_maintenance_run_guard import RunBinding


ROOT = Path('/var/lib/bridge-light-native-service')
HISTORY = Path('/var/lib/bridge-light-native-service-history')
UNIT = 'bridge-light-native-supervisor.service'
UNIT_FILE = Path('/etc/systemd/system')/UNIT
HOST = 'autopilot-lite-vnic'
CONSUMERS = ('school-autopilot-production-light.service', 'school-autopilot-native-light.service')
UNIT_LOOKUP = tuple(Path(p) for p in ('/etc/systemd/system.control', '/run/systemd/system.control',
    '/run/systemd/transient', '/run/systemd/generator.early', '/etc/systemd/system',
    '/etc/systemd/system.attached', '/run/systemd/system', '/run/systemd/system.attached',
    '/run/systemd/generator', '/usr/local/lib/systemd/system', '/usr/lib/systemd/system',
    '/lib/systemd/system', '/run/systemd/generator.late'))
WORKFLOW = '.github/workflows/light-native-service-install.yml'
WORKFLOW_SHA256 = '4116cd7f334156f119f7592d598bf8a7fb15195539bf2e75d0227cf64b60958b'
ASSETS = {
    'launcher.py': 'ops/light_native_service_bootstrap.py',
    'light_native_service_authority.py': 'ops/light_native_service_authority.py',
    'light_native_service_supervisor.py': 'ops/light_native_service_supervisor.py',
    'light_native_service_unit.py': 'ops/light_native_service_unit.py'}
require = history.require


class InstallationRunBinding(RunBinding):
    workflow = WORKFLOW
    workflow_sha256 = WORKFLOW_SHA256
    job_name = 'install'
    job_names = ('contract', 'install')
    events = ('workflow_dispatch',)
    duration_limit = 300

    def __init__(self, source, run_id, attempt, api):
        self.started_at = None
        self.wall_start, self.mono_start = time.time(), time.monotonic()
        super().__init__(source, run_id, attempt, api, seconds=300)

    def observe_job_clock(self, job):
        stamp = job.get('started_at')
        require(history.timestamp(stamp), 'NATIVE_INSTALL_JOB_CLOCK')
        require(self.started_at is None or self.started_at == stamp, 'NATIVE_INSTALL_JOB_CLOCK')
        self.started_at = stamp
        start = datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()
        now, mono = time.time(), time.monotonic()
        require(0 <= now-start < 300 and abs((now-self.wall_start)-(mono-self.mono_start)) <= 5,
                'NATIVE_INSTALL_JOB_CLOCK')
        self.deadline = min(self.deadline, mono+300-(now-start))

    def assert_current(self):
        super().assert_current()
        self.assert_running()  # Authenticated main/run/job on every mutation boundary.


def action(raw, accepted, package_raw):
    require(type(accepted) is str and re.fullmatch('[0-9a-f]{64}', accepted)
            and history.sha(raw) == accepted, 'NATIVE_INSTALL_NOT_ACCEPTED')
    value = history.parse(raw)
    require(type(value) is dict and set(value) == {'version', 'kind', 'action', 'source', 'controller_source',
            'package_sha256', 'qualification_sha256', 'not_before', 'expires_at',
            'expected_root', 'expected_unit', 'installation_sha256', 'history_sha256'} and type(value['version']) is int
            and value['version'] == 1 and value['kind'] == 'LIGHT_NATIVE_DORMANT_INSTALL_ONLY'
            and value['action'] in ('install', 'rollback')
            and type(value['controller_source']) is str and re.fullmatch('[0-9a-f]{40}', value['controller_source'])
            and type(value['qualification_sha256']) is str
            and re.fullmatch('[0-9a-f]{64}', value['qualification_sha256'])
            and history.timestamp(value['not_before']) and history.timestamp(value['expires_at']),
            'NATIVE_INSTALL_SCOPE')
    require((value['action'] == 'install' and value['source'] == value['controller_source']
                and value['expected_root'] == value['expected_unit'] == 'ABSENT'
                and value['installation_sha256'] is value['history_sha256'] is None)
            or (value['action'] == 'rollback' and value['expected_root'] == 'INSTALLED_DORMANT'
                and value['expected_unit'] == 'DISABLED_INACTIVE'
                and all(type(value[key]) is str and re.fullmatch('[0-9a-f]{64}', value[key])
                        for key in ('installation_sha256', 'history_sha256'))), 'NATIVE_INSTALL_SCOPE')
    source, files = history.package(package_raw, value['package_sha256'])
    require(value['source'] == source, 'NATIVE_INSTALL_SOURCE')
    start, end = (datetime.strptime(value[key], '%Y-%m-%dT%H:%M:%SZ')
                  .replace(tzinfo=timezone.utc).timestamp() for key in ('not_before', 'expires_at'))
    require(0 < end-start <= 300, 'NATIVE_INSTALL_WINDOW')
    return value, files, start, end


def command(*argv):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=15,
        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'SYSTEMD_COLORS': '0'})
    require(result.returncode == 0 and len(result.stdout) <= 32768, 'NATIVE_INSTALL_SYSTEMD')
    return result.stdout


def show(name, fields):
    raw = command('/usr/bin/systemctl', '--no-pager', 'show', '--all', name,
                  '--property='+','.join(fields))
    try:
        rows = [line.partition('=') for line in raw.decode('ascii').splitlines()]
        require(len(rows) == len(fields) and all(sep == '=' for _, sep, _ in rows),
                'NATIVE_INSTALL_UNIT')
        result = {key: value for key, _, value in rows}
        require(set(result) == set(fields), 'NATIVE_INSTALL_UNIT')
        return result
    except (ValueError, UnicodeError):
        raise history.Refusal('NATIVE_INSTALL_UNIT') from None


def observed():
    return show(UNIT, ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'UnitFileState', 'FragmentPath',
        'DropInPaths', 'SourcePath', 'Transient', 'NeedDaemonReload', 'ExecMainStartTimestampMonotonic',
        'ExecMainPID', 'InvocationID', 'NRestarts'))


def consumer_identity():
    result = {}
    for name in CONSUMERS:
        value = show(name, ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'InvocationID', 'NRestarts'))
        require(value['LoadState'] == 'loaded' and value['ActiveState'] == 'active'
            and value['SubState'] == 'running' and re.fullmatch('[1-9][0-9]*', value['MainPID'])
            and int(value['MainPID']) > 1 and re.fullmatch('[0-9a-f]{32}', value['InvocationID'])
            and re.fullmatch('0|[1-9][0-9]*', value['NRestarts']), 'NATIVE_INSTALL_CONSUMER_UNKNOWN')
        result[name] = value
    return result


def unit_state(*, present):
    return dict(LoadState='loaded' if present else 'not-found', ActiveState='inactive', SubState='dead',
        MainPID='0', UnitFileState='disabled' if present else '', FragmentPath=str(UNIT_FILE) if present else '',
        DropInPaths='', SourcePath='', Transient='no', NeedDaemonReload='no',
        ExecMainStartTimestampMonotonic='0', ExecMainPID='0', InvocationID='', NRestarts='0')


def absent():
    require(not ROOT.exists() and not ROOT.is_symlink() and not UNIT_FILE.exists()
            and not UNIT_FILE.is_symlink(), 'NATIVE_INSTALL_EXISTING_TARGET')
    row = observed()
    require(row == unit_state(present=False), 'NATIVE_INSTALL_EXISTING_TARGET')
    # An absent main unit need not expose otherwise dormant drop-ins in show.
    names = ('service.d', 'bridge-.service.d', 'bridge-light-.service.d',
             'bridge-light-native-.service.d', UNIT+'.d', UNIT)
    require(all(not (directory/name).exists() and not (directory/name).is_symlink()
                for directory in UNIT_LOOKUP for name in names), 'NATIVE_INSTALL_UNIT_NAMESPACE')
    return row


def new_unit(raw):
    history.directory(UNIT_FILE.parent)
    fd = os.open(UNIT_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o644)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    history.fsync(UNIT_FILE.parent)


def unit_bytes(*, path=None):
    path = UNIT_FILE if path is None else path
    history.directory(path.parent)
    flag = getattr(os, 'O_NOATIME', None)
    require(type(flag) is int and flag > 0, 'NATIVE_INSTALL_NOATIME')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | flag)
    try:
        row = os.fstat(fd)
        require(stat.S_ISREG(row.st_mode) and row.st_uid == row.st_gid == 0
                and row.st_nlink == 1 and stat.S_IMODE(row.st_mode) == 0o644
                and 0 < row.st_size <= 65536, 'NATIVE_INSTALL_UNIT_FILE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(65537)
        require(len(raw) == row.st_size and history.signature(row) == history.signature(os.fstat(fd))
                == history.signature(path.stat(follow_symlinks=False)), 'NATIVE_INSTALL_DRIFT')
        return raw
    finally:
        os.close(fd)


def move_static(source, destination, expected):
    """Pinned parents, immediate source identity and moved-inode readback.

    The root operator/configuration manager must obey the shared operation
    exclusion. No userspace pathname transaction can fence a hostile root actor.
    Refusal after the first move is an uncertain partial outcome, never success.
    """
    history.directory(source.parent)
    history.directory(destination.parent, private=True)
    left = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    right = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for fd, path in ((left, source.parent), (right, destination.parent)):
            row = os.fstat(fd)
            require((row.st_dev, row.st_ino) == (path.lstat().st_dev, path.lstat().st_ino),
                    'NATIVE_INSTALL_DRIFT')
        require(not destination.exists() and not destination.is_symlink()
            and history.signature(os.stat(source.name, dir_fd=left, follow_symlinks=False)) == expected,
            'NATIVE_INSTALL_DRIFT')
        os.rename(source.name, destination.name, src_dir_fd=left, dst_dir_fd=right)
        row = os.stat(destination.name, dir_fd=right, follow_symlinks=False)
        # rename changes ctime; all other pinned file metadata must survive.
        require(history.signature(row)[:-1] == expected[:-1], 'NATIVE_INSTALL_DRIFT')
        os.fsync(left)
        os.fsync(right)
    finally:
        os.close(left)
        os.close(right)


@contextmanager
def operation_lock():
    """Serialize both operations; partial intent is never reset or bypassed."""
    import fcntl
    history.directory(HISTORY.parent)
    if not HISTORY.exists() and not HISTORY.is_symlink():
        history.new_directory(HISTORY)
    history.directory(HISTORY, private=True)
    path = HISTORY/'operation.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        row = os.fstat(fd)
        require(stat.S_ISREG(row.st_mode) and row.st_uid == row.st_gid == 0
                and row.st_nlink == 1 and stat.S_IMODE(row.st_mode) == 0o600,
                'NATIVE_INSTALL_LOCK')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(history.signature(os.fstat(fd)) == history.signature(path.lstat()), 'NATIVE_INSTALL_LOCK')
        history.fsync(HISTORY)
        yield
        require(history.signature(os.fstat(fd)) == history.signature(path.lstat()), 'NATIVE_INSTALL_LOCK')
    finally:
        os.close(fd)


def operate(raw, accepted, package_raw, guard):
    """The only mutation entry; validate authority before creating the lock."""
    value, _, _, _ = action(raw, accepted, package_raw)
    require(type(guard) is InstallationRunBinding and guard.source == value['controller_source']
            and os.name == 'posix' and os.geteuid() == 0 and os.uname().nodename == HOST,
            'NATIVE_INSTALL_AUTHORITY')
    guard.assert_current()
    with operation_lock():
        return (_install if value['action'] == 'install' else _rollback)(raw, accepted, package_raw, guard)


def _install(raw, accepted, package_raw, guard):
    """One existing-owner GHA/SSH operation; no dependency or credential install."""
    value, files, start, end = action(raw, accepted, package_raw)
    require(value['action'] == 'install' and type(guard) is InstallationRunBinding
            and guard.source == value['controller_source']
            and os.name == 'posix' and os.geteuid() == 0
            and os.uname().nodename == HOST, 'NATIVE_INSTALL_AUTHORITY')
    def current(*, reserve=0):
        guard.assert_current()
        now = datetime.now(timezone.utc).timestamp()
        require(start <= now < end and min(end-now, guard.deadline-time.monotonic()) > reserve,
                'NATIVE_INSTALL_WINDOW')
    current()
    before = absent()
    consumers = consumer_identity()
    # The unit code executing in this operation must be the selected Git bytes.
    require(Path(unit.__file__).read_bytes() == files['ops/light_native_service_unit.py']
            and unit.ROOT == ROOT and unit.UNIT == UNIT and unit.UNIT_FILE == UNIT_FILE,
            'NATIVE_INSTALL_UNIT_SOURCE')
    rendered = unit.render()
    history.directory(ROOT.parent)
    history.directory(HISTORY.parent)
    if not HISTORY.exists() and not HISTORY.is_symlink():
        history.new_directory(HISTORY)
    history.directory(HISTORY, private=True)
    # A prior attempt, including a complete snapshot followed by a lost ACK,
    # is not permission to create another action ID and retry installation.
    require({p.name for p in HISTORY.iterdir()} == {'operation.lock'}, 'NATIVE_INSTALL_RECONCILIATION_REQUIRED')
    snapshot = history.encoded(dict(version=1, kind='LIGHT_NATIVE_DORMANT_TARGET_BEFORE',
        observed_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        root='ABSENT', unit='ABSENT', loaded_unit=before, selected_source=value['source'],
        consumer_identity=consumers,
        selected_package_sha256=value['package_sha256'], action_sha256=accepted,
        qualification_sha256=value['qualification_sha256'],
        rollback=dict(action='RESTORE_ABSENCE', preserve_new_code=True,
                      overwrite_mutable_data=False, restore_authority=False)))
    target_history = HISTORY/history.sha(snapshot)
    history.new_directory(target_history)
    history.retain(target_history/'manifest.json', snapshot)
    history.retain(target_history/'complete.json', history.encoded(dict(version=1,
        snapshot_sha256=history.sha(snapshot), files={}, secrets_included=False)))
    current()
    require(absent() == before, 'NATIVE_INSTALL_DRIFT')
    staged = history.stage(package_raw, value['package_sha256'])
    current()
    require(absent() == before, 'NATIVE_INSTALL_DRIFT')
    current(reserve=90)
    history.new_directory(ROOT)
    for name, package_name in ASSETS.items():
        history.retain(ROOT/name, files[package_name])
    record = history.encoded(dict(version=1, kind='LIGHT_NATIVE_DORMANT_INSTALLATION',
        source=value['source'], package_sha256=value['package_sha256'],
        qualification_sha256=value['qualification_sha256'],
        installed_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        history_sha256=history.sha(snapshot), unit_sha256=history.sha(rendered),
        files={name: history.sha(files[package_name]) for name, package_name in ASSETS.items()}))
    history.retain(ROOT/'dormant-installation.json', record)
    current(reserve=90)
    new_unit(rendered)
    require(unit_bytes() == rendered, 'NATIVE_INSTALL_DRIFT')
    current(reserve=90)
    command('/usr/bin/systemctl', 'daemon-reload')
    row = observed()
    require(row == unit_state(present=True) and consumer_identity() == consumers, 'NATIVE_INSTALL_READBACK')
    require(unit_bytes() == rendered and history.read(ROOT/'dormant-installation.json') == record
            and all(history.read(ROOT/name) == files[package_name]
                    for name, package_name in ASSETS.items()), 'NATIVE_INSTALL_DRIFT')
    current()
    result = dict(audit='LIGHT_NATIVE_DORMANT_INSTALLED', source=value['source'],
        package_sha256=value['package_sha256'], installation_sha256=history.sha(record),
        history_sha256=history.sha(snapshot), staged_history_sha256=staged['history_sha256'],
        software_installed=True, unit_enabled=False, service_started=False,
        host_issue_integrated=False, issue_authorized=False, native_admission_authorized=False,
        credentials_installed=False, end_to_end_accepted=False, consumer_identity_unchanged=True,
        consumer_identity_sha256=history.sha(history.encoded(consumers)))
    history.retain(target_history/'installed.json', history.encoded(result))
    return result


def _rollback(raw, accepted, package_raw, guard):
    """Restore the recorded absence; retain the new static files in history.

    This path is intentionally unavailable once task authority, contexts or
    mutable journals have appeared. It cannot reset an active/unknown runtime.
    A fresh qualified controller can roll back an older exact installed build.
    """
    value, files, start, end = action(raw, accepted, package_raw)
    require(value['action'] == 'rollback' and type(guard) is InstallationRunBinding
            and guard.source == value['controller_source'] and os.name == 'posix'
            and os.geteuid() == 0 and os.uname().nodename == HOST, 'NATIVE_INSTALL_AUTHORITY')
    def current(*, reserve=0):
        guard.assert_current()
        now = datetime.now(timezone.utc).timestamp()
        require(start <= now < end and min(end-now, guard.deadline-time.monotonic()) > reserve,
                'NATIVE_INSTALL_WINDOW')
    current()
    consumers = consumer_identity()
    history.directory(ROOT, private=True)
    history.directory(HISTORY, private=True)
    record_raw = history.read(ROOT/'dormant-installation.json', limit=32768)
    require(history.sha(record_raw) == value['installation_sha256'], 'NATIVE_INSTALL_ROLLBACK_BINDING')
    record = history.parse(record_raw)
    target = HISTORY/value['history_sha256']
    history.directory(target, private=True)
    before_raw = history.read(target/'manifest.json')
    before = history.parse(before_raw)
    complete_raw = history.read(target/'complete.json')
    installed_raw = history.read(target/'installed.json')
    installed = history.parse(installed_raw)
    require(history.sha(before_raw) == value['history_sha256']
            and type(record) is dict and set(record) == {'version', 'kind', 'source', 'package_sha256',
                'qualification_sha256', 'installed_at', 'history_sha256', 'unit_sha256', 'files'}
            and type(record['version']) is int and record['version'] == 1
            and record['kind'] == 'LIGHT_NATIVE_DORMANT_INSTALLATION'
            and history.timestamp(record['installed_at'])
            and type(record['qualification_sha256']) is str
            and re.fullmatch('[0-9a-f]{64}', record['qualification_sha256'])
            and type(record['unit_sha256']) is str and re.fullmatch('[0-9a-f]{64}', record['unit_sha256'])
            and record.get('source') == value['source']
            and record.get('package_sha256') == value['package_sha256']
            and record.get('history_sha256') == value['history_sha256']
            and before.get('root') == before.get('unit') == 'ABSENT'
            and before.get('selected_source') == value['source']
            and before.get('selected_package_sha256') == value['package_sha256']
            and before.get('qualification_sha256') == record['qualification_sha256']
            and before.get('rollback') == dict(action='RESTORE_ABSENCE', preserve_new_code=True,
                    overwrite_mutable_data=False, restore_authority=False)
            and complete_raw == history.encoded(dict(version=1, snapshot_sha256=value['history_sha256'],
                    files={}, secrets_included=False))
            and type(installed) is dict and installed.get('audit') == 'LIGHT_NATIVE_DORMANT_INSTALLED'
            and installed.get('installation_sha256') == value['installation_sha256']
            and installed.get('history_sha256') == value['history_sha256']
            and installed.get('source') == value['source']
            and installed.get('package_sha256') == value['package_sha256']
            and installed.get('issue_authorized') is False
            and installed.get('service_started') is False
            and before.get('loaded_unit') == unit_state(present=False)
            and set(path.name for path in ROOT.iterdir()) == {*ASSETS, 'dormant-installation.json'}
            and set(path.name for path in target.iterdir()) == {'manifest.json', 'complete.json', 'installed.json'},
            'NATIVE_INSTALL_ROLLBACK_BINDING')
    expected = {name: history.sha(files[source]) for name, source in ASSETS.items()}
    require(record.get('files') == expected
            and all(history.sha(history.read(ROOT/name)) == digest for name, digest in expected.items())
            and history.sha(unit_bytes()) == record.get('unit_sha256'), 'NATIVE_INSTALL_DRIFT')
    require(observed() == unit_state(present=True), 'NATIVE_INSTALL_ACTIVE_OR_UNKNOWN')
    # Never silently fall back to copy/delete across mount boundaries.
    require(ROOT.stat(follow_symlinks=False).st_dev == target.stat(follow_symlinks=False).st_dev
            == UNIT_FILE.stat(follow_symlinks=False).st_dev, 'NATIVE_INSTALL_ROLLBACK_FILESYSTEM')
    initial = {name: history.signature((ROOT/name).lstat()) for name in (*ASSETS, 'dormant-installation.json')}
    unit_initial = history.signature(UNIT_FILE.lstat())
    intent = history.encoded(dict(version=1, kind='LIGHT_NATIVE_DORMANT_ROLLBACK', action_sha256=accepted,
        installation_sha256=value['installation_sha256'], package_sha256=value['package_sha256'],
        started_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')))
    current()
    history.retain(target/'rollback-intent.json', intent)
    current(reserve=90)
    def root_unchanged():
        require({path.name for path in ROOT.iterdir()} == set(initial)
                and {name: history.signature((ROOT/name).lstat()) for name in initial} == initial
                and history.read(ROOT/'dormant-installation.json') == record_raw
                and all(history.sha(history.read(ROOT/name)) == digest for name, digest in expected.items()),
                'NATIVE_INSTALL_DRIFT')
    root_unchanged()
    require(history.sha(unit_bytes()) == record['unit_sha256']
            and history.read(target/'manifest.json') == before_raw
            and history.read(target/'complete.json') == complete_raw
            and history.read(target/'installed.json') == installed_raw
            and observed() == unit_state(present=True)
            and not (target/'retired-root').exists() and not (target/'retired-root').is_symlink()
            and not (target/'retired-unit.service').exists() and not (target/'retired-unit.service').is_symlink(),
            'NATIVE_INSTALL_DRIFT')
    # The potentially blocking systemctl observation above is not a last-second
    # run/main check. Observe authenticated authority again, then repeat only
    # local file checks before the material move; no command/network gap here.
    current(reserve=90)
    root_unchanged()
    require(history.sha(unit_bytes()) == record['unit_sha256']
            and history.read(target/'manifest.json') == before_raw
            and history.read(target/'complete.json') == complete_raw
            and history.read(target/'installed.json') == installed_raw, 'NATIVE_INSTALL_DRIFT')
    move_static(UNIT_FILE, target/'retired-unit.service', unit_initial)
    # Each rename is a separate material boundary. Cancellation/main drift here
    # preserves the partial unit move and ROOT in place for reconciliation.
    current(reserve=90)
    root_unchanged()
    require(history.sha(unit_bytes(path=target/'retired-unit.service')) == record['unit_sha256'],
            'NATIVE_INSTALL_DRIFT')
    move_static(ROOT, target/'retired-root', history.signature(ROOT.lstat()))
    current(reserve=90)
    command('/usr/bin/systemctl', 'daemon-reload')
    require(absent() == before['loaded_unit']
            and consumer_identity() == consumers
            and history.read(target/'retired-root'/'dormant-installation.json') == record_raw
            and all(history.sha(history.read(target/'retired-root'/name)) == digest
                    for name, digest in expected.items())
            and history.sha(unit_bytes(path=target/'retired-unit.service')) == record['unit_sha256'],
            'NATIVE_INSTALL_ROLLBACK_READBACK')
    result = dict(audit='LIGHT_NATIVE_DORMANT_ROLLED_BACK', source=value['source'],
        history_sha256=value['history_sha256'], previous_absence_restored=True,
        new_source_preserved=True, mutable_data_overwritten=False,
        authority_restored=False, end_to_end_accepted=False)
    history.retain(target/'rollback-done.json', history.encoded(result))
    current()
    return result
