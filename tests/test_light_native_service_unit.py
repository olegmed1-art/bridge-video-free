"""Portable synthetic read ports and separate actual Linux component evidence.

Neither category is actual PID1/unit installation proof. A disposable real
systemd fixture is a separate caller/integration obligation.
"""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from ops import light_native_service_unit as u


def process_fixture():
    return u.ProcessSnapshot(2345, 0, 0, 1, 10000, 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        'a'*64, ('/usr/bin/python3', '-I', '-S', '-B', '/var/lib/bridge-light-native-service/launcher.py'),
        '0::/system.slice/bridge-light-native-supervisor.service',
        '/var/lib/bridge-light-native-service', 'f'*32)


def show_fixture():
    # Independent representative systemctl --show values. This fixture is not a
    # receipt from a real host and must never be reported as one.
    return dict(
        LoadState='loaded', ActiveState='active', SubState='running', Type='exec',
        MainPID='2345', ControlPID='0', InvocationID='f'*32, NRestarts='0',
        FragmentPath='/etc/systemd/system/bridge-light-native-supervisor.service',
        SourcePath='', DropInPaths='', NeedDaemonReload='no', Transient='no', UnitFileState='enabled',
        User='root', Group='root', UMask='0077', WorkingDirectory='/var/lib/bridge-light-native-service',
        Environment='PATH=/usr/bin:/bin LC_ALL=C PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1',
        EnvironmentFiles='', PassEnvironment='', UnsetEnvironment='PYTHONHOME PYTHONPATH',
        RootDirectory='', RootImage='', PAMName='', SupplementaryGroups='', DynamicUser='no',
        ExecCondition='', ExecStartPre='', ExecStartPost='', ExecReload='', ExecStop='', ExecStopPost='',
        ExecStart='{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -I -S -B '
            '/var/lib/bridge-light-native-service/launcher.py ; ignore_errors=no ; '
            'start_time=[Mon 2026-10-19 12:00:00 UTC] ; stop_time=[n/a] ; pid=2345 ; code=(null) ; status=0/0 }',
        Restart='on-failure', RestartUSec='5s', RuntimeMaxUSec='infinity', KillMode='control-group',
        TimeoutStopUSec='30s', StartLimitIntervalUSec='1min', StartLimitBurst='3',
        Nice='10', CPUQuotaPerSecUSec='1s', MemoryHigh='536870912', MemoryMax='805306368', TasksMax='64',
        NoNewPrivileges='yes', PrivateTmp='yes', PrivateDevices='yes', PrivateNetwork='no',
        ProtectHome='yes', ProtectSystem='strict', ProtectKernelTunables='yes', ProtectKernelModules='yes',
        ProtectControlGroups='yes', RestrictSUIDSGID='yes', ReadWritePaths='/var/lib/bridge-light-native-service',
        ControlGroup='/system.slice/bridge-light-native-supervisor.service')


def show_raw(value=None):
    value = show_fixture() if value is None else value
    return ''.join(key+'='+value[key]+'\n' for key in sorted(value)).encode('ascii')


def reset_accounting_fixture():
    value = show_fixture()
    # Independent literal for the observed systemd255 post-reload form. MainPID
    # and /proc remain real identity inputs; this command accounting is zero.
    value['ExecStart'] = ('{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -I -S -B '
        '/var/lib/bridge-light-native-service/launcher.py ; ignore_errors=no ; '
        'start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }')
    return value


# Independent literals from the observed systemd255 omission/query shape.
OMITTED_ARRAY_PROPERTIES = ('EnvironmentFiles', 'ExecCondition', 'ExecStartPre',
    'ExecStartPost', 'ExecReload', 'ExecStop', 'ExecStopPost')
EMPTY_ARRAY_LINES = (b'{"type":"a(sb)","data":[]}\n'+
                     b'{"type":"a(sasbttttuii)","data":[]}\n'*6)
BUSCTL_COMMAND = ('/usr/bin/busctl', '--system', '--json=short', 'get-property',
    'org.freedesktop.systemd1',
    '/org/freedesktop/systemd1/unit/bridge_2dlight_2dnative_2dsupervisor_2eservice',
    'org.freedesktop.systemd1.Service', 'EnvironmentFiles', 'ExecCondition', 'ExecStartPre',
    'ExecStartPost', 'ExecReload', 'ExecStop', 'ExecStopPost')


def omitted_show():
    value = show_fixture()
    for key in OMITTED_ARRAY_PROPERTIES: del value[key]
    return show_raw(value)


@contextmanager
def command_read_ports(outputs, codes=None):
    """Keep actual bounded reader; substitute pipe/process readiness only."""
    codes = [0]*len(outputs) if codes is None else codes
    processes = []
    for index, code in enumerate(codes):
        process = Mock(); process.stdout.fileno.return_value = 27+index
        process.wait.return_value = code; process.poll.return_value = code
        processes.append(process)
    selector = Mock(); selector.__enter__ = Mock(return_value=selector)
    selector.__exit__ = Mock(return_value=False); selector.select.return_value = [(None, 1)]
    chunks = [chunk for output in outputs for chunk in (output, b'')]
    with patch.object(u.subprocess, 'Popen', side_effect=processes) as launch, \
         patch.object(u.selectors, 'DefaultSelector', return_value=selector), \
         patch.object(u.os, 'set_blocking') as nonblocking, \
         patch.object(u.os, 'read', side_effect=chunks), \
         patch.object(u, '_read_command', wraps=u._read_command) as reader:
        yield SimpleNamespace(launch=launch, processes=processes, nonblocking=nonblocking, reader=reader)


def validate(*, before=None, after=None, first=None, last=None, unit=None, python=None):
    return u.validate_loaded(u.render() if unit is None else unit,
        show_raw() if before is None else before, show_raw() if after is None else after,
        process_fixture() if first is None else first, process_fixture() if last is None else last,
        expected_python_sha256='a'*64 if python is None else python)


@contextmanager
def synthetic_read_ports(helper=u, *, python_sha256='a'*64):
    """Keep actual collector/validator; fake only its three fixed OS read ports."""
    process = helper.ProcessSnapshot(**{**process_fixture().__dict__, 'exe_sha256': python_sha256})
    with patch.object(helper.sys, 'platform', 'linux'), \
         patch.object(helper.os, 'geteuid', return_value=0, create=True), \
         patch.object(helper, '_protected_unit', return_value=helper.render()) as unit, \
         patch.object(helper, '_process', return_value=process) as captured, \
         patch.object(helper, '_show', return_value=show_raw()) as show:
        yield SimpleNamespace(unit=unit, process=captured, show=show)


@contextmanager
def synthetic_proc_files():
    """In-memory open/read descriptors; actual _process parsing remains in use."""
    image = b'synthetic-python-image'
    files = dict(stat=b'2345 (python3) S '+b'0 '*18+b'10000 0\n',
        status=b'Name:\tpython3\nUid:\t0\t0\t0\t0\nGid:\t0\t0\t0\t0\nNoNewPrivs:\t1\n',
        cmdline=b'\0'.join(arg.encode() for arg in process_fixture().argv)+b'\0',
        environ=b'PATH=/usr/bin:/bin\0INVOCATION_ID='+b'f'*32+b'\0',
        exe=image, cgroup=(process_fixture().cgroup+'\n').encode(),
        boot_id=(process_fixture().boot_id+'\n').encode())
    descriptors = {}; opened = []; next_fd = 100
    def opened_file(path, flags, *, dir_fd=None):
        nonlocal next_fd
        name = str(path).replace('\\', '/').split('/')[-1]
        if name == '2345': name = 'directory'
        if name not in files and name != 'directory': raise FileNotFoundError()
        next_fd += 1
        descriptors[next_fd] = [name, files.get(name, b''), 0]
        opened.append((name, flags, dir_fd))
        return next_fd
    def row(fd):
        name, data, _ = descriptors[fd]
        mode = stat.S_IFDIR | 0o555 if name == 'directory' else stat.S_IFREG | (0o755 if name == 'exe' else 0o444)
        return SimpleNamespace(st_dev=1, st_ino=fd, st_mode=mode, st_uid=0, st_gid=0,
            st_nlink=1, st_size=len(data) if name == 'exe' else 0,
            st_atime_ns=1, st_mtime_ns=2, st_ctime_ns=3, st_rdev=0)
    def read(fd, size):
        descriptor = descriptors[fd]
        data = descriptor[1][descriptor[2]:descriptor[2]+size]
        descriptor[2] += len(data)
        return data
    with patch.object(u.os, 'O_DIRECTORY', 0x10000, create=True), \
         patch.object(u.os, 'O_NOFOLLOW', 0x20000, create=True), \
         patch.object(u.os, 'O_NONBLOCK', 0x40000, create=True), \
         patch.object(u.os, 'O_NOATIME', 0x80000, create=True), \
         patch.object(u.os, 'open', side_effect=opened_file), patch.object(u.os, 'fstat', side_effect=row), \
         patch.object(u.os, 'read', side_effect=read), \
         patch.object(u.os, 'close', side_effect=lambda fd: descriptors.pop(fd)), \
         patch.object(u.os, 'readlink', return_value=process_fixture().cwd):
        yield SimpleNamespace(files=files, descriptors=descriptors, opened=opened,
                              image_sha256=hashlib.sha256(image).hexdigest())


class CollectorContract(unittest.TestCase):
    def test_protected_unit_metadata_mode_links_hash_and_ns_drift_are_checked(self):
        raw = u.render(); pin = hashlib.sha256(raw).hexdigest()
        row = SimpleNamespace(st_dev=1, st_ino=2, st_mode=stat.S_IFREG | 0o644,
            st_uid=0, st_gid=0, st_nlink=1, st_size=len(raw),
            st_atime_ns=1, st_mtime_ns=2, st_ctime_ns=3, st_rdev=0)
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        cases = [(None, None), ('st_mode', stat.S_IFREG | 0o666), ('st_uid', 1000),
                 ('st_gid', 1000), ('st_nlink', 2), ('st_size', 65537)]
        for key, value in cases:
            changed = row if key is None else SimpleNamespace(**{**row.__dict__, key: value})
            with self.subTest(key=key), patch.object(Path, 'lstat', return_value=directory), \
                 patch.object(Path, 'stat', return_value=changed), \
                 patch.object(u.os, 'O_NOATIME', 0x80000, create=True), \
                 patch.object(u.os, 'O_NOFOLLOW', 0x20000, create=True), \
                 patch.object(u.os, 'O_NONBLOCK', 0x40000, create=True), \
                 patch.object(u.os, 'open', return_value=17) as opened, \
                 patch.object(u.os, 'fstat', return_value=changed), patch.object(u.os, 'close'), \
                 patch.object(u, '_read_fd', return_value=raw):
                if key is None:
                    self.assertEqual(u._protected_unit(pin), raw)
                    self.assertTrue(opened.call_args.args[1] & u.os.O_NOFOLLOW)
                    self.assertTrue(opened.call_args.args[1] & u.os.O_NOATIME)
                    with self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_PIN$'): u._protected_unit('b'*64)
                else:
                    with self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_FILE$'): u._protected_unit(pin)
        for endpoint in ('fd', 'path'):
            changed = SimpleNamespace(**{**row.__dict__, 'st_mtime_ns': row.st_mtime_ns+1})
            with self.subTest(endpoint=endpoint), patch.object(Path, 'lstat', return_value=directory), \
                 patch.object(Path, 'stat', return_value=changed if endpoint == 'path' else row), \
                 patch.object(u.os, 'O_NOATIME', 0x80000, create=True), \
                 patch.object(u.os, 'O_NOFOLLOW', 0x20000, create=True), \
                 patch.object(u.os, 'O_NONBLOCK', 0x40000, create=True), \
                 patch.object(u.os, 'open', return_value=17), patch.object(u.os, 'close'), \
                 patch.object(u.os, 'fstat', side_effect=[row, changed if endpoint == 'fd' else row]), \
                 patch.object(u, '_read_fd', return_value=raw):
                with self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_FILE_CHANGED$'): u._protected_unit(pin)

    def test_noatime_missing_never_opens_the_unit(self):
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        with patch.object(Path, 'lstat', return_value=directory), \
             patch.object(u.os, 'O_NOATIME', None, create=True), patch.object(u.os, 'open') as opened:
            with self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_NOATIME$'): u._protected_unit('a'*64)
            opened.assert_not_called()

    def test_fixed_collector_brackets_both_queries_with_unit_and_process_reads(self):
        with synthetic_read_ports() as ports:
            result = u.collect_loaded(2345, expected_python_sha256='a'*64,
                                      expected_unit_sha256=hashlib.sha256(u.render()).hexdigest())
            self.assertEqual(result.process, process_fixture())
            self.assertEqual(ports.unit.call_count, 2)
            self.assertEqual(ports.process.call_count, 2)
            self.assertEqual(ports.show.call_count, 2)
            ports.process.assert_called_with(2345)

    def test_collector_refuses_before_after_drift_and_never_exposes_os_exception_text(self):
        for endpoint in ('unit', 'process', 'show'):
            with self.subTest(endpoint=endpoint), synthetic_read_ports() as ports:
                port = getattr(ports, endpoint)
                port.side_effect = [port.return_value, b'changed' if endpoint != 'process'
                                    else replace(port.return_value, start_ticks=10001)]
                with self.assertRaises(u.Refusal):
                    u.collect_loaded(2345, expected_python_sha256='a'*64, expected_unit_sha256='b'*64)
        with synthetic_read_ports() as ports:
            ports.unit.side_effect = OSError('private-exception-material')
            with self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_COLLECTION$'):
                u.collect_loaded(2345, expected_python_sha256='a'*64, expected_unit_sha256='b'*64)

    def test_actual_proc_parser_uses_status_nnp_and_independent_environment_invocation(self):
        with synthetic_proc_files() as files:
            result = u._process(2345)
            self.assertEqual(result, replace(process_fixture(), exe_sha256=files.image_sha256))
            self.assertFalse(files.descriptors)
            for name, flags, _ in files.opened:
                if name != 'exe': self.assertTrue(flags & u.os.O_NOFOLLOW, name)
            files.files['status'] = files.files['status'].replace(b'NoNewPrivs:\t1', b'NoNewPrivs:\t0')
            self.assertEqual(u._process(2345).no_new_privs, 0)
            files.files['environ'] = b'INVOCATION_ID='+b'e'*32+b'\0'
            self.assertEqual(u._process(2345).invocation_id, 'e'*32)

    def test_proc_parser_missing_duplicate_unbounded_or_nonroot_inputs_refuse(self):
        bad = [('status', b'Uid:\t0\t1\t0\t0\nGid:\t0\t0\t0\t0\nNoNewPrivs:\t1\n'),
               ('status', b'Uid:\t0\t0\t0\t0\nGid:\t0\t0\t0\t0\n'),
               ('status', b'Uid:\t0\t0\t0\t0\nGid:\t0\t0\t0\t0\nNoNewPrivs:\t1\nNoNewPrivs:\t1\n'),
               ('status', b'x'*65537), ('cmdline', b'not-nul-terminated'),
               ('environ', b'PATH=/usr/bin\0'),
               ('environ', b'INVOCATION_ID='+b'f'*32+b'\0INVOCATION_ID='+b'f'*32+b'\0'),
               ('environ', b'x'*262145), ('boot_id', b'x'*129), ('stat', b'2345 (python3) Z '+b'0 '*25)]
        for key, value in bad:
            with self.subTest(key=key, prefix=value[:12]), synthetic_proc_files() as files:
                files.files[key] = value
                with self.assertRaises(u.Refusal): u._process(2345)
                self.assertFalse(files.descriptors)

    def test_fixed_show_and_busctl_commands_reuse_bounded_reader_and_one_deadline(self):
        with command_read_ports([show_raw(), EMPTY_ARRAY_LINES]) as ports:
            self.assertEqual(u._show(), show_raw())
            self.assertEqual([entry.args[0] for entry in ports.launch.call_args_list], [
                ('/usr/bin/systemctl', '--no-pager', 'show', '--all', u.UNIT,
                 '--property='+','.join(u.SHOW_PROPERTIES)), BUSCTL_COMMAND])
            self.assertEqual([entry.kwargs['limit'] for entry in ports.reader.call_args_list], [65536, 4096])
            self.assertEqual(ports.reader.call_args_list[0].kwargs['deadline'],
                             ports.reader.call_args_list[1].kwargs['deadline'])
            self.assertEqual(ports.nonblocking.call_args_list, [call(27, False), call(28, False)])
            for entry, process in zip(ports.launch.call_args_list, ports.processes):
                self.assertEqual(entry.kwargs['stdin'], u.subprocess.DEVNULL)
                self.assertEqual(entry.kwargs['stderr'], u.subprocess.DEVNULL)
                self.assertEqual(entry.kwargs['env']['LC_ALL'], 'C')
                process.stdout.close.assert_called_once()

    def test_both_command_output_budgets_and_exit_codes_are_enforced(self):
        cases = (([b'x'*65537], [0], 'NATIVE_UNIT_SHOW_LIMIT'),
                 ([show_raw()], [1], 'NATIVE_UNIT_SHOW'),
                 ([show_raw(), b'x'*4097], [0, 0], 'NATIVE_UNIT_SHOW_LIMIT'),
                 ([show_raw(), EMPTY_ARRAY_LINES], [0, 1], 'NATIVE_UNIT_SHOW'))
        for outputs, codes, reason in cases:
            with self.subTest(lengths=[len(value) for value in outputs], codes=codes), \
                 command_read_ports(outputs, codes):
                with self.assertRaisesRegex(u.Refusal, '^'+reason+'$'): u._show()

    def test_show_timeout_kills_only_its_own_read_process(self):
        process = Mock(); process.stdout.fileno.return_value = 27; process.poll.return_value = None
        selector = Mock(); selector.__enter__ = Mock(return_value=selector); selector.__exit__ = Mock(return_value=False)
        with patch.object(u.subprocess, 'Popen', return_value=process), \
             patch.object(u.selectors, 'DefaultSelector', return_value=selector), \
             patch.object(u.os, 'set_blocking'), patch.object(u.time, 'monotonic', side_effect=[0, 0, 11]):
            with self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_SHOW_TIMEOUT$'): u._show()
            process.kill.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=2)

    def test_genuine_seven_property_omission_needs_typed_evidence(self):
        omitted = omitted_show()
        with self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_READBACK$'): u.parse_show(omitted)
        with command_read_ports([omitted, EMPTY_ARRAY_LINES]):
            augmented = u._show()
        self.assertEqual(u.parse_show(augmented), u.parse_show(show_raw()))
        self.assertEqual(augmented[len(omitted):], b''.join(key.encode()+b'=\n' for key in OMITTED_ARRAY_PROPERTIES))

    def test_typed_array_evidence_rejects_missing_duplicate_extra_wrong_or_nonempty_json(self):
        rows = EMPTY_ARRAY_LINES.splitlines(keepends=True)
        malformed = [b'', b'not-json\n', b'\xff\n', EMPTY_ARRAY_LINES[:-1],
            EMPTY_ARRAY_LINES.replace(b'\n', b'\r\n'), b''.join(rows[:6]),
            EMPTY_ARRAY_LINES+rows[-1], rows[1]+rows[0]+b''.join(rows[2:]),
            b'{"type":"a(sb)","data":[],"data":[]}\n'+b''.join(rows[1:]),
            b'{"type":"a(sb)","data":[],"\\u0064ata":[]}\n'+b''.join(rows[1:]),
            b'{"type":"a(sb)","data":[],"extra":0}\n'+b''.join(rows[1:]),
            b'{"type":"a(sb)"}\n'+b''.join(rows[1:]),
            b'{"data":[]}\n'+b''.join(rows[1:]),
            b'{"type":"a(sb)","data":NaN}\n'+b''.join(rows[1:]),
            b'{"type":"a(sb)","data":'+b'['*1100+b']'*1100+b'}\n'+b''.join(rows[1:]),
            b'[]\n'+b''.join(rows[1:])]
        for index in range(7):
            for change in ({'type': 'as'}, {'data': [['/tmp/command', False]]},
                           {'data': False}, {'data': None}, {'data': {}}, {'data': 0}):
                value = json.loads(rows[index]); value.update(change)
                altered = rows[:]; altered[index] = json.dumps(value).encode()+b'\n'
                malformed.append(b''.join(altered))
        for index, evidence in enumerate(malformed):
            with self.subTest(index=index), command_read_ports([omitted_show(), evidence]):
                with self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_ARRAY_ATTESTATION$'): u._show()

    def test_only_attested_missing_keys_are_completed_and_conflicts_refuse(self):
        variants = []
        missing_user = show_fixture(); del missing_user['User']
        variants.extend((show_raw(missing_user), show_raw()+b'User=root\n', show_raw()+b'UnknownKey=\n'))
        for key in OMITTED_ARRAY_PROPERTIES:
            changed = show_fixture(); changed[key] = '/tmp/nonempty'
            variants.append(show_raw(changed))
        for index, raw in enumerate(variants):
            with self.subTest(index=index), command_read_ports([raw, EMPTY_ARRAY_LINES]):
                with self.assertRaises(u.Refusal): u._show()

    def test_total_readback_limit_after_attested_augmentation_is_enforced(self):
        raw = omitted_show()+b'X='+(b'x'*(65536-len(omitted_show())-3))+b'\n'
        self.assertEqual(len(raw), 65536)
        with command_read_ports([raw, EMPTY_ARRAY_LINES]):
            with self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_READBACK$'): u._show()

    def test_shared_deadline_cannot_expire_before_or_after_busctl(self):
        for moments, launches in (([0]*5+[11], 1), ([0]*9+[11], 2)):
            with self.subTest(launches=launches), command_read_ports([show_raw(), EMPTY_ARRAY_LINES]) as ports, \
                 patch.object(u.time, 'monotonic', side_effect=moments):
                with self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_SHOW_TIMEOUT$'): u._show()
                self.assertEqual(ports.launch.call_count, launches)

    def test_each_of_the_two_collector_observations_attests_arrays_independently(self):
        wrong = EMPTY_ARRAY_LINES.replace(b'"data":[]', b'"data":[0]', 1)
        for bad_observation in (0, 1):
            replies = [omitted_show(), EMPTY_ARRAY_LINES, omitted_show(), EMPTY_ARRAY_LINES]
            replies[bad_observation*2+1] = wrong
            with self.subTest(bad_observation=bad_observation), \
                 patch.object(u.sys, 'platform', 'linux'), patch.object(u.os, 'geteuid', return_value=0, create=True), \
                 patch.object(u, '_protected_unit', return_value=u.render()), \
                 patch.object(u, '_process', return_value=process_fixture()), \
                 command_read_ports(replies) as ports:
                with self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_ARRAY_ATTESTATION$'):
                    u.collect_loaded(2345, expected_python_sha256='a'*64,
                                     expected_unit_sha256=hashlib.sha256(u.render()).hexdigest())
                self.assertEqual(ports.launch.call_count, 2*(bad_observation+1))


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'NOTRUN: actual /proc and protected-file collection requires Linux root')
class LinuxCollectorEvidence(unittest.TestCase):
    def test_actual_owned_process_snapshot_matches_independent_proc_status(self):
        # A real /proc component test with an explicitly synthetic invocation
        # marker, not a systemd-unit/installation/host-authority proof.
        program = 'import sys;print("ready",flush=True);sys.stdin.buffer.read(1)'
        child = u.subprocess.Popen([sys.executable, '-I', '-B', '-S', '-c', program],
            stdin=u.subprocess.PIPE, stdout=u.subprocess.PIPE, stderr=u.subprocess.DEVNULL,
            env={'PATH': '/usr/bin:/bin', 'INVOCATION_ID': 'f'*32})
        try:
            with u.selectors.DefaultSelector() as ready:
                ready.register(child.stdout, u.selectors.EVENT_READ)
                self.assertTrue(ready.select(5), 'owned process did not become ready')
            self.assertEqual(child.stdout.readline(16), b'ready\n')
            value = u._process(child.pid)
            root = Path('/proc')/str(child.pid)
            status = dict(line.split(':', 1) for line in (root/'status').read_text().splitlines())
            self.assertEqual(value.pid, child.pid)
            self.assertEqual(value.uid, int(status['Uid'].split()[1]))
            self.assertEqual(value.gid, int(status['Gid'].split()[1]))
            self.assertEqual(value.no_new_privs, int(status['NoNewPrivs']))
            self.assertEqual(value.argv, tuple(item.decode() for item in (root/'cmdline').read_bytes()[:-1].split(b'\0')))
            self.assertEqual(value.cwd, os.readlink(root/'cwd'))
            child.stdin.write(b'x'); child.stdin.flush(); child.wait(timeout=5)
        finally:
            if child.poll() is None: child.kill(); child.wait(timeout=2)
            child.stdin.close(); child.stdout.close()

    def test_actual_protected_unit_read_retains_bytes_and_rejects_links_and_ns_drift(self):
        with tempfile.TemporaryDirectory(dir='/root') as directory:
            path = Path(directory)/'unit.service'
            raw = u.render(); path.write_bytes(raw); path.chmod(0o644)
            pin = hashlib.sha256(raw).hexdigest()
            with patch.object(u, 'UNIT_FILE', path):
                self.assertEqual(u._protected_unit(pin), raw)
                actual = u._read_fd
                def drift(fd, limit):
                    result = actual(fd, limit)
                    row = path.stat(); os.utime(path, ns=(row.st_atime_ns, row.st_mtime_ns+1))
                    return result
                with patch.object(u, '_read_fd', side_effect=drift):
                    with self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_FILE_CHANGED$'):
                        u._protected_unit(pin)
                link = Path(directory)/'hardlink'; link.hardlink_to(path)
                with self.assertRaises(u.Refusal): u._protected_unit(pin)
                link.unlink(); path.unlink(); path.symlink_to(Path(directory)/'missing')
                with self.assertRaises(OSError): u._protected_unit(pin)


class UnitContract(unittest.TestCase):
    def test_closed_zero_accounting_form_retains_real_process_identity_and_distinct_hash(self):
        raw = show_raw(reset_accounting_fixture())
        self.assertEqual(u.parse_show(raw)['ExecStart'], (u.ARGV, 0, '[n/a]'))
        result = validate(before=raw, after=raw)
        self.assertEqual(result.process, process_fixture())
        self.assertGreater(result.process.pid, 1)
        self.assertEqual(result.process.start_ticks, 10000)
        self.assertNotEqual(result.loaded_sha256, validate().loaded_sha256)

    def test_zero_accounting_partial_mixed_or_nonexact_fields_refuse(self):
        reset = reset_accounting_fixture()['ExecStart']
        variants = (
            reset.replace('pid=0', 'pid=2345'), reset.replace('pid=0', 'pid=1'),
            reset.replace('pid=0', 'pid=00'), reset.replace('pid=0', 'pid='),
            reset.replace('start_time=[n/a]', 'start_time=[Mon 2026-10-19 12:00:00 UTC]'),
            reset.replace(' ; start_time=[n/a]', ''), reset.replace(' ; pid=0', ''),
            reset.replace(' ; pid=0', ' ; pid=0 ; pid=0'), reset.replace(' ; pid=0', ' ; extra=0 ; pid=0'),
            reset.replace('path=/usr/bin/python3', 'path=/bin/sh'),
            reset.replace('launcher.py ;', 'launcher.py extra ;'),
            reset.replace('ignore_errors=no', 'ignore_errors=yes'),
            reset.replace('stop_time=[n/a]', 'stop_time=[Mon 2026-10-19 12:00:00 UTC]'),
            reset.replace('code=(null)', 'code=exited'), reset.replace('status=0/0', 'status=1/1'))
        for index, accounting in enumerate(variants):
            changed = reset_accounting_fixture(); changed['ExecStart'] = accounting
            with self.subTest(index=index), self.assertRaises(u.Refusal):
                validate(before=show_raw(changed), after=show_raw(changed))

    def test_zero_accounting_never_allows_foreign_or_zero_mainpid(self):
        for pid in ('0', '1', '2346'):
            changed = reset_accounting_fixture(); changed['MainPID'] = pid
            with self.subTest(pid=pid), self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_PROCESS_CHANGED$'):
                validate(before=show_raw(changed), after=show_raw(changed))
        changed = show_fixture(); changed['ExecStart'] = changed['ExecStart'].replace('pid=2345', 'pid=2346')
        with self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_PROCESS_CHANGED$'):
            validate(before=show_raw(changed), after=show_raw(changed))

    def test_zero_accounting_preserves_each_independent_process_policy(self):
        raw = show_raw(reset_accounting_fixture())
        bad = dict(pid=2346, uid=1000, gid=1000, no_new_privs=0, start_ticks=0,
            boot_id='not-a-boot-uuid', exe_sha256='b'*64, argv=('/usr/bin/python3', '/tmp/other.py'),
            cgroup='0::/system.slice/foreign.service', cwd='/tmp', invocation_id='e'*32)
        for key, value in bad.items():
            changed = replace(process_fixture(), **{key: value})
            with self.subTest(key=key), self.assertRaises(u.Refusal):
                validate(before=raw, after=raw, first=changed, last=changed)

    def test_zero_accounting_and_process_drift_between_observations_still_refuse(self):
        reset = show_raw(reset_accounting_fixture())
        for key, value in (('start_ticks', 10001), ('boot_id', 'bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee')):
            with self.subTest(key=key), self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_PROCESS_CHANGED$'):
                validate(before=reset, after=reset, last=replace(process_fixture(), **{key: value}))
        for before, after in ((show_raw(), reset), (reset, show_raw())):
            with self.subTest(before_reset=before == reset), self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_CHANGED$'):
                validate(before=before, after=after)

    def test_renderer_is_fixed_deterministic_and_explicitly_prospective(self):
        raw = u.render()
        self.assertEqual(raw, u.render())
        self.assertTrue(raw.endswith(b'\n'))
        self.assertEqual(raw.count(b'ExecStart='), 1)
        self.assertIn(b'ExecStart=/usr/bin/python3 -I -S -B /var/lib/bridge-light-native-service/launcher.py\n', raw)
        self.assertIn(b'User=root\nGroup=root\nUMask=0077\n', raw)
        self.assertIn(b'ReadWritePaths=/var/lib/bridge-light-native-service\n', raw)
        self.assertEqual(raw.count(b'ReadWritePaths='), 1)
        self.assertNotIn(b'EnvironmentFile=', raw)
        self.assertNotIn(b'Credential', raw)
        self.assertNotIn(b'ADMISSION_MODE', raw)
        self.assertNotIn(b'/var/lib/bridge-light-native-lane', raw)
        self.assertIn('PROSPECTIVE', u.PROFILE)

    def test_all_exec_hooks_are_explicitly_empty_except_exact_start(self):
        lines = u.render().decode('ascii').splitlines()
        for key in ('ExecCondition', 'ExecStartPre', 'ExecStartPost', 'ExecReload', 'ExecStop', 'ExecStopPost'):
            self.assertEqual(lines.count(key+'='), 1)
            changed = show_fixture(); changed[key] = '{ path=/bin/true ; argv[]=/bin/true ; ignore_errors=no ; }'
            with self.subTest(key=key), self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_PROFILE$'):
                validate(before=show_raw(changed), after=show_raw(changed))

    def test_exact_readback_and_independent_process_pair_produce_frozen_binding(self):
        result = validate()
        self.assertEqual(result.profile, u.PROFILE)
        self.assertEqual(result.unit_sha256, hashlib.sha256(u.render()).hexdigest())
        self.assertEqual(result.process, process_fixture())
        self.assertEqual(result.restarts, 0)
        self.assertEqual(len(result.loaded_sha256), 64)
        with self.assertRaises(FrozenInstanceError): result.restarts = 1

    def test_accepted_duration_and_environment_order_normalization_is_exact(self):
        changed = show_fixture()
        changed.update(RestartUSec='5000ms', TimeoutStopUSec='30000000us',
            StartLimitIntervalUSec='60s', CPUQuotaPerSecUSec='1000ms',
            Environment=' '.join(reversed(changed['Environment'].split())),
            UnsetEnvironment='PYTHONPATH PYTHONHOME')
        result = validate(after=show_raw(changed))
        self.assertEqual(result.loaded_sha256, validate().loaded_sha256)

    def test_unknown_duplicate_missing_or_malformed_readback_never_parses(self):
        raw = show_raw()
        missing = show_fixture(); del missing['DropInPaths']
        variants = (raw+b'UnknownProperty=no\n', raw+b'User=root\n', show_raw(missing), raw[:-1],
            raw+b'\n', raw.replace(b'User=root\n', b'User\n'), raw.replace(b'\n', b'\r\n'),
            raw.replace(b'\n', b'\x0b', 1), raw+b'\0', b'\xff\n', b'x'*65536+b'\n', 'not bytes')
        for index, value in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(u.Refusal): u.parse_show(value)

    def test_query_property_set_is_exact_and_parser_is_order_independent(self):
        values = show_fixture()
        self.assertEqual(set(u.SHOW_PROPERTIES), set(values))
        self.assertEqual(len(u.SHOW_PROPERTIES), len(set(u.SHOW_PROPERTIES)))
        raw = ''.join(key+'='+value+'\n' for key, value in reversed(tuple(values.items()))).encode()
        self.assertEqual(u.parse_show(raw), u.parse_show(show_raw()))

    def test_loaded_paths_identity_reload_and_hardening_drift_refuse(self):
        bad = dict(LoadState='not-found', ActiveState='activating', SubState='exited', Type='simple',
            FragmentPath='/run/systemd/transient/foreign.service', SourcePath='/tmp/unit',
            DropInPaths='/etc/systemd/system/bridge-light-native-supervisor.service.d/override.conf',
            NeedDaemonReload='yes', Transient='yes', UnitFileState='disabled', User='nobody', Group='nogroup',
            UMask='0022', WorkingDirectory='/tmp', EnvironmentFiles='/tmp/env (ignore_errors=no)',
            PassEnvironment='GH_TOKEN', RootDirectory='/tmp/root', RootImage='/tmp/image', PAMName='login',
            SupplementaryGroups='users', DynamicUser='yes', Restart='always', KillMode='process',
            RuntimeMaxUSec='30s', Nice='0', MemoryHigh='1073741824', MemoryMax='1073741824', TasksMax='128',
            NoNewPrivileges='no', PrivateTmp='no', PrivateDevices='no', PrivateNetwork='yes',
            ProtectHome='no', ProtectSystem='full', ProtectKernelTunables='no', ProtectKernelModules='no',
            ProtectControlGroups='no', RestrictSUIDSGID='no', ReadWritePaths='/var/lib', ControlPID='999',
            ControlGroup='/system.slice/foreign.service', StartLimitBurst='4', RestartUSec='6s',
            TimeoutStopUSec='31s', StartLimitIntervalUSec='61s', CPUQuotaPerSecUSec='2s')
        for key, value in bad.items():
            changed = show_fixture(); changed[key] = value
            with self.subTest(key=key), self.assertRaises(u.Refusal):
                validate(before=show_raw(changed), after=show_raw(changed))

    def test_extra_environment_duplicates_and_python_path_injection_refuse(self):
        for key, value in (
                ('Environment', show_fixture()['Environment']+' PYTHONPATH=/tmp'),
                ('Environment', show_fixture()['Environment']+' LC_ALL=C'),
                ('Environment', show_fixture()['Environment'].replace('LC_ALL=C', 'LC_ALL=C.UTF-8')),
                ('Environment', show_fixture()['Environment'].replace(' ', '  ', 1)),
                ('UnsetEnvironment', 'PYTHONHOME'), ('UnsetEnvironment', 'PYTHONHOME PYTHONHOME PYTHONPATH')):
            changed = show_fixture(); changed[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(u.Refusal):
                validate(before=show_raw(changed), after=show_raw(changed))

    def test_exec_start_multiplicity_unknown_fields_and_nonexact_commands_refuse(self):
        original = show_fixture()['ExecStart']
        bad = (original+' '+original, original.replace('path=/usr/bin/python3', 'path=/bin/sh'),
            original.replace(' -I -S -B ', ' -I -S '),
            original.replace('launcher.py ;', 'launcher.py extra ;'),
            original.replace('ignore_errors=no', 'ignore_errors=yes'),
            original.replace(' ; pid=2345', ' ; alien=x ; pid=2345'),
            original.replace(' ; pid=2345', ' ; pid=2345 ; pid=2345'),
            original.replace('Mon 2026-10-19', 'Tue 2026-10-19'),
            original.replace('2026-10-19', '2026-02-30'), original.replace('stop_time=[n/a]', 'stop_time=[done]'),
            original.replace('code=(null)', 'code=exited'), original.replace('status=0/0', 'status=1/1'),
            original.replace('pid=2345', 'pid=1'), original[:-2], original.replace(' ; ', ';', 1))
        for index, value in enumerate(bad):
            changed = show_fixture(); changed['ExecStart'] = value
            with self.subTest(index=index), self.assertRaises(u.Refusal): u.parse_show(show_raw(changed))

    def test_numbers_booleans_and_durations_cannot_use_ambiguous_or_overflowing_forms(self):
        variants = [('RestartUSec', value) for value in
            ('5', '05s', '5.0s', '5s 0s', '-5s', '5seconds', 'infinity', '9223372036854775807h')]
        variants += [('MainPID', value) for value in ('02345', '+2345', '2345.0', '-1', '9223372036854775808')]
        variants += [('NoNewPrivileges', value) for value in ('true', '1', 'on', 'YES')]
        variants += [('UMask', value) for value in ('77', '0o77', '0088')]
        for key, value in variants:
            changed = show_fixture(); changed[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(u.Refusal): u.parse_show(show_raw(changed))

    def test_two_loaded_snapshots_must_agree_including_restarts_and_exec_metadata(self):
        for key, value in (('NRestarts', '1'), ('MainPID', '2346'), ('InvocationID', 'e'*32),
                           ('ExecStart', show_fixture()['ExecStart'].replace('12:00:00', '12:00:01'))):
            changed = show_fixture(); changed[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_CHANGED$'):
                validate(after=show_raw(changed))

    def test_each_process_policy_holds_even_when_both_snapshots_agree(self):
        process = process_fixture()
        bad = dict(pid=2346, uid=1000, gid=1000, no_new_privs=0, start_ticks=0,
            boot_id='not-a-boot-uuid', exe_sha256='b'*64,
            argv=('/usr/bin/python3', '/tmp/other.py'), cgroup='0::/system.slice/foreign.service',
            cwd='/tmp', invocation_id='e'*32)
        for key, value in bad.items():
            changed = replace(process, **{key: value})
            with self.subTest(key=key), self.assertRaises(u.Refusal):
                validate(first=changed, last=changed)
        for key in ('pid', 'uid', 'gid', 'no_new_privs', 'start_ticks'):
            changed = replace(process, **{key: float(getattr(process, key))})
            with self.subTest(key=key, variant='float'), self.assertRaises(u.Refusal):
                validate(first=changed, last=changed)
        with self.assertRaises(u.Refusal): validate(first=process.__dict__)

    def test_process_pair_detects_drift_between_individually_valid_snapshots(self):
        process = process_fixture()
        for key, value in (('start_ticks', 10001), ('boot_id', 'bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee')):
            changed = replace(process, **{key: value})
            # Each snapshot alone meets this helper's process policy. Historical
            # boot/start anchors are separate authority checks, not guessed here.
            validate(first=changed, last=changed)
            with self.subTest(key=key), self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_PROCESS_CHANGED$'):
                validate(last=changed)

    def test_loaded_nnp_yes_does_not_replace_effective_process_nnp_integer_one(self):
        self.assertEqual(show_fixture()['NoNewPrivileges'], 'yes')
        self.assertEqual(validate().process.no_new_privs, 1)
        for value in (0, 2, -1, True, 1.0, '1', None):
            changed = replace(process_fixture(), no_new_privs=value)
            with self.subTest(value=value), self.assertRaisesRegex(u.Refusal, '^NATIVE_UNIT_PROCESS$'):
                validate(first=changed, last=changed)

    def test_process_and_exec_pids_must_match_loaded_mainpid(self):
        for key, value in (('MainPID', '2346'),
                           ('ExecStart', show_fixture()['ExecStart'].replace('pid=2345', 'pid=2346'))):
            changed = show_fixture(); changed[key] = value
            with self.subTest(key=key), self.assertRaises(u.Refusal):
                validate(before=show_raw(changed), after=show_raw(changed))

    def test_interpreter_pin_unit_bytes_and_process_schema_are_not_selectable(self):
        for value in ('b'*64, 'A'*64, 'a'*63, True):
            with self.subTest(python=value), self.assertRaises(u.Refusal): validate(python=value)
        for value in (u.render()+b'\n', u.render().replace(b'User=root', b'User=nobody'), b''):
            with self.subTest(unit=value[:20]), self.assertRaises(u.Refusal): validate(unit=value)
        process = process_fixture()
        for key, value in (('boot_id', 'a'*36), ('invocation_id', 'f'*31),
                           ('argv', list(process.argv)), ('pid', True), ('start_ticks', 0)):
            changed = replace(process, **{key: value})
            with self.subTest(key=key), self.assertRaises(u.Refusal): validate(first=changed, last=changed)


if __name__ == '__main__':
    unittest.main()
