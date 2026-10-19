"""Fixed read-only CURRENT legacy witness; private bytes, never an admission API.

This captures neither missing historical Ti nor a HoldIdentity/continuity proof.
The authenticated owner must pin this helper and independently accept its output.
No consumer is connected. No database, remote, service mutation or CLI is exposed.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import stat
import time


SERVICE_FIELDS = frozenset(('ActiveState', 'SubState', 'MainPID', 'NRestarts',
    'InvocationID', 'WorkingDirectory', 'User', 'Group', 'Environment',
    'EnvironmentFiles', 'ExecStart', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload'))
LIMIT = 65536


class CaptureUnknown(ValueError):
    """Preserve evidence; fixed categories never include secret/error values."""


def _need(condition, code):
    if not condition:
        raise CaptureUnknown('LEGACY_CAPTURE_UNKNOWN_' + code)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value, length=64):
    _need(type(value) is str and re.fullmatch('[0-9a-f]{' + str(length) + '}', value), 'PIN')


class _LinuxReader:
    """Fixed local read ports. Existing modules are imported only on Linux use."""
    def __init__(self):
        import os
        import pwd
        from ops import oracle_light_active_hold_attest as hold
        from ops import oracle_light_stopped_hold_upgrade as upgrade
        from ops import oracle_light_runtime_hold_install as staging
        from ops import light_native_service_switch as switch
        self.os, self.hold = os, hold
        self.upgrade, self.staging, self.switch = upgrade, staging, switch
        account = pwd.getpwnam('school-autopilot')
        self.layout = dict(unit=hold.UNIT, base=str(hold.BASE), drop=str(hold.DROP),
            environment=str(hold.ENV), route=str(hold.ROUTE / 'route.json'),
            release_root=str(switch.plan.LIGHT / 'releases'), python=switch.plan.PYTHON,
            executable=str(Path(switch.plan.PYTHON).resolve(strict=True)),
            hostname='autopilot-lite-vnic', uid=account.pw_uid, gid=account.pw_gid,
            base_sha256=hold.BASE_SHA, pin_keys=set(hold.PIN_KEYS))
        expected = {**switch.plan.HARDENING}
        expected.pop('CPUQuota')
        expected.pop('TimeoutStopSec')
        expected.update(CPUQuotaPerSecUSec='1s', TimeoutStopUSec='30s', MemoryHigh='536870912',
                        MemoryMax='805306368', Restart='on-failure')
        self.hardening_expected = expected

    def host(self):
        return dict(uid=self.os.geteuid(), hostname=self.os.uname().nodename)

    def service(self):
        return self.hold.service()  # Retains EnvironmentFiles as a list.

    def protected(self, release):
        files = {self.layout['base']: (0o644, 65536), self.layout['drop']: (0o644, 4096),
                 self.layout['environment']: (0o600, 262144),
                 release + '/ops/autopilot/broker-hold.env': (0o444, 4096),
                 self.layout['route']: (0o644, 4096)}
        result = {}
        for name, (mode, limit) in files.items():
            result[name] = self._protected_file(name, mode, limit)
        return result

    def _protected_file(self, name, mode, limit):
        """Root-anchored, nonblocking descriptor read; never reopen via a helper.

        Even a FIFO substituted between stat/open cannot block the open or be
        read: the opened descriptor must pass regular-file fstat first.
        """
        os = self.os
        _need(type(name) is str and name.startswith('/') and
              all(part not in ('', '.', '..') for part in name.split('/')[1:]), 'FILE_PATH')
        metadata = lambda info: [info.st_dev, info.st_ino, info.st_uid, info.st_gid,
                                 info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        file_identity = lambda info: (tuple(metadata(info)), info.st_nlink)
        directory_identity = lambda info: (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME | os.O_CLOEXEC
        leaf_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME | os.O_CLOEXEC
        descriptors, anchors = [], []
        leaf_fd = None
        try:
            parent = os.open('/', directory_flags)
            descriptors.append(parent)
            root = os.fstat(parent)
            _need(stat.S_ISDIR(root.st_mode) and root.st_uid == 0 and not root.st_mode & 0o022, 'FILE_PARENT')
            root_identity = directory_identity(root)
            parts = name.split('/')[1:]
            for component in parts[:-1]:
                before = os.stat(component, dir_fd=parent, follow_symlinks=False)
                child = os.open(component, directory_flags, dir_fd=parent)
                descriptors.append(child)
                opened = os.fstat(child)
                _need(stat.S_ISDIR(opened.st_mode) and opened.st_uid == 0 and not opened.st_mode & 0o022 and
                      directory_identity(before) == directory_identity(opened), 'FILE_PARENT')
                anchors.append((parent, component, child, directory_identity(opened)))
                parent = child
            leaf = parts[-1]
            before = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            leaf_fd = os.open(leaf, leaf_flags, dir_fd=parent)
            opened = os.fstat(leaf_fd)
            _need(stat.S_ISREG(opened.st_mode) and opened.st_uid == 0 and
                  stat.S_IMODE(opened.st_mode) == mode and opened.st_nlink == 1 and
                  0 <= opened.st_size <= limit and file_identity(before) == file_identity(opened), 'FILE_METADATA')
            chunks, length = [], 0
            while length <= limit:
                chunk = os.read(leaf_fd, min(65536, limit + 1 - length))
                if not chunk:
                    break
                chunks.append(chunk)
                length += len(chunk)
            _need(length <= limit and length == opened.st_size, 'FILE_SIZE')
            after = os.fstat(leaf_fd)
            linked = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            _need(file_identity(opened) == file_identity(after) == file_identity(linked), 'FILE_DRIFT')
            for directory, component, child, identity in anchors:
                _need(directory_identity(os.fstat(child)) == identity ==
                      directory_identity(os.stat(component, dir_fd=directory, follow_symlinks=False)), 'FILE_PARENT_DRIFT')
            _need(directory_identity(os.fstat(descriptors[0])) == root_identity ==
                  directory_identity(os.stat('/', follow_symlinks=False)), 'FILE_PARENT_DRIFT')
            return dict(mode=mode, limit=limit, metadata=metadata(after), raw=b''.join(chunks))
        finally:
            if leaf_fd is not None:
                os.close(leaf_fd)
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def hardening(self):
        actual = self.switch.show(self.layout['unit'], list(self.hardening_expected))
        self.switch.attest_hardening()
        return actual

    def validate_loaded(self, service, release, admission):
        # Only this copy is normalized for the existing stopped validator. The
        # witness always retains the complete actual, unmodified service map.
        checked = deepcopy(service)
        checked.update(ActiveState='inactive', SubState='dead', MainPID='0')
        checked['Environment'] = ' '.join('AUTOPILOT_ADMISSION_MODE=HOLD'
            if token == 'AUTOPILOT_ADMISSION_MODE=ACTIVE' else token
            for token in checked['Environment'].split())
        checked['EnvironmentFiles'] = tuple(checked['EnvironmentFiles'])
        h = dict(UNIT_PATH=self.layout['base'], DROP=self.layout['drop'],
                 ENV_PATH=self.layout['environment'], PYTHON=self.layout['python'])
        self.upgrade.validate_stopped(checked, h, Path(release), True)
        contract = self.staging.execution_contract(service['ExecStart'])
        expected = 'path=' + self.layout['python'] + ' ; argv[]=' + self.layout['python'] + \
                   ' -m oracle_autopilot.worker_v17 ; ignore_errors=no'
        _need(contract == expected, 'EXECUTION_CONTRACT')
        return contract

    def environment(self, raw):
        return self.hold.env(raw)

    def desired_drop(self, release):
        return self.upgrade.desired_drop(Path(release))

    def verify_runtime(self, release, bundle):
        self.staging.verify_release(Path(release), bundle)

    def _proc(self, pid, leaf, limit):
        fd = self.os.open('/proc/' + str(pid) + '/' + leaf,
                          self.os.O_RDONLY | self.os.O_NOFOLLOW | self.os.O_NONBLOCK)
        with self.os.fdopen(fd, 'rb') as stream:
            raw = stream.read(limit + 1)
        _need(len(raw) <= limit, 'PROCESS_SIZE')
        return raw

    def process(self, pid):
        first = self._proc(pid, 'stat', 16384)
        _need(first.partition(b'(')[0].strip() == str(pid).encode(), 'PROCESS_PID')
        fields = first[first.rfind(b')') + 2:].split()
        _need(len(fields) >= 20, 'PROCESS_STAT')
        status = self._proc(pid, 'status', 65536).decode()
        ids = {}
        for line in status.splitlines():
            key, _, value = line.partition(':')
            if key in ('Uid', 'Gid'):
                _need(key not in ids, 'PROCESS_IDS')
                ids[key] = [int(item) for item in value.split()]
        raw_env = self._proc(pid, 'environ', 262144)
        environment = {}
        for row in raw_env.split(b'\0'):
            if not row:
                continue
            key, sep, value = row.partition(b'=')
            _need(sep and key.decode() not in environment, 'PROCESS_ENVIRONMENT')
            environment[key.decode()] = value.decode()
        argv = self._proc(pid, 'cmdline', 65536)
        _need(argv.endswith(b'\0'), 'PROCESS_ARGV')
        proc = Path('/proc/' + str(pid))
        info = proc.stat()
        result = dict(pid=pid, uid=info.st_uid, gid=info.st_gid, uids=ids.get('Uid'), gids=ids.get('Gid'),
                      cwd=str((proc / 'cwd').resolve(strict=True)),
                      executable=str((proc / 'exe').resolve(strict=True)),
                      argv=[part.decode() for part in argv[:-1].split(b'\0')],
                      start_ticks=int(fields[19]), environment=environment)
        last = self._proc(pid, 'stat', 16384)
        # CPU counters change during capture; kernel start identity must not.
        last_fields = last[last.rfind(b')') + 2:].split()
        _need(last.partition(b'(')[0].strip() == str(pid).encode() and
              len(last_fields) >= 20 and last_fields[19] == fields[19], 'PROCESS_REPLACED')
        return result

    def absence(self):
        self.switch.no_processes(self.layout['unit'])
        value = self.switch.show(self.layout['unit'], ['MainPID', 'ControlPID', 'ActiveState', 'ControlGroup'])
        path = self.switch.CGROUP_ROOT / ('system.slice/' + self.layout['unit']) / 'cgroup.events'
        value['cgroup_present'] = path.exists()
        value['cgroup_populated'] = dict(line.split() for line in path.read_text().splitlines()).get('populated') if path.exists() else None
        return value


def _bundle(bundle, accepted, layout):
    _digest(accepted)
    _need(type(bundle) is dict and set(bundle) == {'revision', 'files', 'sha256'}, 'BUNDLE_SCHEMA')
    _digest(bundle['revision'], 40)
    _need(type(bundle['files']) is dict and 1 <= len(bundle['files']) <= 256, 'BUNDLE_SCHEMA')
    for name, raw in bundle['files'].items():
        _need(type(name) is str and (re.fullmatch(r'(?:oracle_autopilot|autopilot_phase3b)/[a-z0-9_]+\.py', name)
              or name in ('ops/autopilot/broker-release.json', 'ops/autopilot/broker-hold.env', 'ops/autopilot/previous-hold.conf'))
              and type(raw) is str and len(raw.encode()) <= 600000, 'BUNDLE_MEMBER')
    _need('ops/autopilot/broker-hold.env' in bundle['files'] and
          len(_canonical(bundle)) <= 4194304 and bundle['sha256'] == accepted ==
          _sha(_canonical(dict(revision=bundle['revision'], files=bundle['files']))), 'BUNDLE_DIGEST')
    return layout['release_root'] + '/' + bundle['revision']


def _observe(reader, release, bundle):
    layout = reader.layout
    host = reader.host()
    _need(host == dict(uid=0, hostname=layout['hostname']), 'HOST')
    service = deepcopy(reader.service())
    _need(type(service) is dict and set(service) == SERVICE_FIELDS and
          all(type(v) is str and len(v.encode()) <= 16384 for k, v in service.items() if k != 'EnvironmentFiles') and
          type(service['EnvironmentFiles']) is list and len(service['EnvironmentFiles']) == 2 and
          all(type(v) is str and len(v) <= 1024 for v in service['EnvironmentFiles']), 'SERVICE_SCHEMA')
    _need(re.fullmatch('[0-9]+', service['MainPID']) and re.fullmatch('[0-9]+', service['NRestarts']), 'SERVICE_NUMBERS')
    pid = int(service['MainPID'])
    running = service['ActiveState'] == 'active' and service['SubState'] == 'running' and pid > 0
    stopped = service['ActiveState'] == 'inactive' and service['SubState'] == 'dead' and pid == 0
    _need(running or stopped, 'STATE')
    admission = [token for token in service['Environment'].split() if token.startswith('AUTOPILOT_ADMISSION_MODE=')]
    _need(len(admission) == 1 and admission[0] in ('AUTOPILOT_ADMISSION_MODE=HOLD', 'AUTOPILOT_ADMISSION_MODE=ACTIVE'), 'ADMISSION')
    mode = admission[0].partition('=')[2]
    _need(running or mode == 'HOLD', 'STOPPED_ACTIVE')
    _need((running and re.fullmatch('[0-9a-f]{32}', service['InvocationID'])) or
          (stopped and (service['InvocationID'] == '' or re.fullmatch('[0-9a-f]{32}', service['InvocationID']))), 'INVOCATION')
    contract = reader.validate_loaded(service, release, mode)
    files = deepcopy(reader.protected(release))
    pin_path = release + '/ops/autopilot/broker-hold.env'
    expected_files = {layout['base']: (0o644, 65536), layout['drop']: (0o644, 4096),
                      layout['environment']: (0o600, 262144), pin_path: (0o444, 4096), layout['route']: (0o644, 4096)}
    _need(type(files) is dict and set(files) == set(expected_files), 'FILE_INVENTORY')
    protected, metadata = {}, {}
    for name, (mode_bits, limit) in expected_files.items():
        row = files[name]
        _need(type(row) is dict and set(row) == {'mode', 'limit', 'metadata', 'raw'} and
              type(row['mode']) is int and type(row['limit']) is int and
              (row['mode'], row['limit']) == (mode_bits, limit) and type(row['raw']) is bytes and
              len(row['raw']) <= limit and type(row['metadata']) is list and len(row['metadata']) == 8 and
              all(type(item) is int for item in row['metadata']) and row['metadata'][2] == 0 and
              stat.S_ISREG(row['metadata'][4]) and stat.S_IMODE(row['metadata'][4]) == mode_bits and
              row['metadata'][5] == len(row['raw']), 'FILE_METADATA')
        protected[name] = dict(mode=mode_bits, limit=limit, sha256=_sha(row['raw']))
        metadata[name] = row['metadata']
    _need(protected[layout['base']]['sha256'] == layout['base_sha256'], 'BASE_UNIT')
    expected_drop = reader.desired_drop(release)
    if mode == 'ACTIVE':
        expected_drop = expected_drop.replace(b'AUTOPILOT_ADMISSION_MODE=HOLD', b'AUTOPILOT_ADMISSION_MODE=ACTIVE')
    _need(files[layout['drop']]['raw'] == expected_drop and
          files[pin_path]['raw'] == bundle['files']['ops/autopilot/broker-hold.env'].encode(), 'DROP_OR_PINS')
    _need(json.loads(files[layout['route']]['raw']) == {'version': 1, 'backend': 'neon', 'database': 'autopilot', 'epoch': 0}, 'ROUTE')
    environment = reader.environment(files[layout['environment']]['raw'])
    pins = reader.environment(files[pin_path]['raw'])
    _need('AUTOPILOT_ADMISSION_MODE' not in environment and 'AUTOPILOT_DATABASE_URL' in environment and
          not any(key in environment for key in ('PYTHONPATH', 'PYTHONHOME')) and set(pins) == layout['pin_keys'], 'ENVIRONMENT')
    hardening = deepcopy(reader.hardening())
    _need(hardening == reader.hardening_expected, 'HARDENING')
    reader.verify_runtime(release, bundle)
    if running:
        process = deepcopy(reader.process(pid))
        _need(type(process) is dict and set(process) == {'pid', 'uid', 'gid', 'uids', 'gids', 'cwd', 'executable', 'argv', 'start_ticks', 'environment'}, 'PROCESS_SCHEMA')
        _need(type(process['pid']) is int and process['pid'] == pid and type(process['uid']) is int and
              type(process['gid']) is int and process['uid'] == layout['uid'] and process['gid'] == layout['gid'] and
              process['uids'] == [layout['uid']] * 4 and process['gids'] == [layout['gid']] * 4 and
              process['cwd'] == release and process['executable'] == layout['executable'] and
              process['argv'] == [layout['python'], '-m', 'oracle_autopilot.worker_v17'] and
              type(process['start_ticks']) is int and process['start_ticks'] > 0, 'PROCESS_IDENTITY')
        live = process.pop('environment')
        _need(type(live) is dict and all(type(k) is str and type(v) is str for k, v in live.items()) and
              not any(key in live for key in ('PYTHONPATH', 'PYTHONHOME')), 'PROCESS_ENVIRONMENT')
        expected_env = {**environment, **pins, 'AUTOPILOT_ADMISSION_MODE': mode}
        _need(all(live.get(key) == value for key, value in expected_env.items()) and
              {k: v for k, v in live.items() if k.startswith('AUTOPILOT_')} ==
              {k: v for k, v in expected_env.items() if k.startswith('AUTOPILOT_')}, 'LIVE_ENVIRONMENT')
        process.update(kind='RUNNING_PROCESS', environment_sha256=_sha(_canonical(live)))
    else:
        process = deepcopy(reader.absence())
        _need(type(process) is dict and set(process) == {'MainPID', 'ControlPID', 'ActiveState', 'ControlGroup', 'cgroup_present', 'cgroup_populated'} and
              process['MainPID'] == process['ControlPID'] == '0' and process['ActiveState'] == 'inactive' and
              process['ControlGroup'] in ('', '/system.slice/' + layout['unit']) and
              type(process['cgroup_present']) is bool and
              process['cgroup_populated'] == ('0' if process['cgroup_present'] else None), 'STOPPED_ABSENCE')
        process['kind'] = 'STOPPED_ABSENCE'
    inputs = dict(service=service, environment=protected[layout['environment']]['sha256'],
                  pins=protected[pin_path]['sha256'], route=protected[layout['route']]['sha256'],
                  drop=protected[layout['drop']]['sha256'])
    return dict(hostname=host['hostname'], state='RUNNING' if running else 'STOPPED', admission=mode,
                service=service, fingerprint_inputs=inputs, fingerprint_sha256=_sha(_canonical(inputs)),
                execution_contract=contract, protected=protected, protected_metadata=metadata,
                protected_sha256=_sha(_canonical(protected)), hardening=hardening,
                hardening_sha256=_sha(_canonical(hardening)), process=process)


def _capture_current(reader, *, runtime_bundle, accepted_runtime_bundle_sha256,
                     controller_source, accepted_controller_sha256, clock=time.time):
    """Private test seam: fixed named read ports, no caller command/action port."""
    try:
        _digest(controller_source, 40)
        _digest(accepted_controller_sha256)
        bundle = deepcopy(runtime_bundle)
        release = _bundle(bundle, accepted_runtime_bundle_sha256, reader.layout)
        started = int(clock())
        first = _observe(reader, release, bundle)
        second = _observe(reader, release, bundle)
        ended = int(clock())
        _need(first == second, 'DRIFT')
        _need(0 <= started <= ended <= started + 30, 'CAPTURE_WINDOW')
        result = dict(version=1, audit='LEGACY_CURRENT_CAPTURE', assurance='CURRENT_OBSERVATION_ONLY',
            observed_at=ended, capture_started_at=started, controller_source=controller_source,
            controller_package_sha256=accepted_controller_sha256, runtime_source=bundle['revision'],
            runtime_bundle_sha256=accepted_runtime_bundle_sha256, installed_release=release,
            historical_chain_verified=False, admission_authorized=False, retirement_authorized=False,
            **first)
        raw = _canonical(result)
        _need(len(raw) <= LIMIT, 'OUTPUT_SIZE')
        return raw
    except CaptureUnknown:
        raise
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise CaptureUnknown('LEGACY_CAPTURE_UNKNOWN_READ_OR_VALIDATION') from None


def capture_current_legacy(*, runtime_bundle, accepted_runtime_bundle_sha256,
                           controller_source, accepted_controller_sha256):
    """Return private canonical current witness from the fixed Linux reader.

    All pins are authenticated loader inputs; recording them is not acceptance.
    Source installation/transport and independent acceptance remain separate.
    This cannot replace a historical witness, call a consumer or create authority.
    """
    try:
        reader = _LinuxReader()
    except Exception:
        raise CaptureUnknown('LEGACY_CAPTURE_UNKNOWN_LINUX_READER') from None
    return _capture_current(reader, runtime_bundle=runtime_bundle,
        accepted_runtime_bundle_sha256=accepted_runtime_bundle_sha256,
        controller_source=controller_source, accepted_controller_sha256=accepted_controller_sha256)
