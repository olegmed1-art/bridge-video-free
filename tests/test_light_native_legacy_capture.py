"""Synthetic stdlib current-capture contracts; no Linux/production claims."""
import ast
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('legacy_capture_under_test', ROOT / 'ops' / 'light_native_legacy_capture.py')
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)
# The private Linux reader's actual validation code must use Linux path syntax
# when driven by synthetic ports on Windows. No production reader is constructed.
c.Path = PurePosixPath


def existing_function(filename, name, **globals_):
    """Execute the real stdlib-only validator, avoiding Linux-only module imports."""
    tree = ast.parse((ROOT / 'ops' / filename).read_text(encoding='utf-8'))
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    namespace = dict(re=re, shlex=shlex, require=c._need, check=c._need,
                     Blocked=c.CaptureUnknown, EXTRA_ENV='ops/autopilot/broker-hold.env', **globals_)
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), namespace)
    return namespace[name]


class Reader(c._LinuxReader):
    """Only fixed read ports are faked; loaded/runtime syntax validators are real."""
    def __init__(self, state='ACTIVE'):
        self.layout = dict(unit='synthetic.service', base='/synthetic/unit', drop='/synthetic/drop',
            environment='/synthetic/environment', route='/synthetic/route', release_root='/synthetic/releases',
            python='/synthetic/python', executable='/synthetic/python-real', hostname='synthetic-host',
            uid=1001, gid=1002, base_sha256=c._sha(b'synthetic unit\n'), pin_keys={'AUTOPILOT_SYNTHETIC_PIN'})
        self.revision = '1' * 40
        self.release = self.layout['release_root'] + '/' + self.revision
        self.bundle = dict(revision=self.revision, files={
            'oracle_autopilot/worker_v17.py': '# synthetic only\n',
            'ops/autopilot/broker-hold.env': 'AUTOPILOT_SYNTHETIC_PIN=synthetic-value\n'})
        self.bundle['sha256'] = c._sha(c._canonical(self.bundle))
        self.upgrade = SimpleNamespace(
            validate_stopped=existing_function('oracle_light_stopped_hold_upgrade.py', 'validate_stopped'),
            desired_drop=existing_function('oracle_light_stopped_hold_upgrade.py', 'desired_drop'))
        self.staging = SimpleNamespace(execution_contract=existing_function('oracle_light_runtime_hold_install.py', 'execution_contract'))
        self.env_function = existing_function('oracle_light_active_hold_attest.py', 'env')
        self.hardening_expected = dict(User='school-autopilot', Group='school-autopilot', NoNewPrivileges='yes',
            ProtectSystem='strict', PrivateTmp='yes', Restart='on-failure')
        self.hardening_value = deepcopy(self.hardening_expected)
        running = state != 'STOPPED'
        mode = state if running else 'HOLD'
        self.service_value = dict(ActiveState='active' if running else 'inactive',
            SubState='running' if running else 'dead', MainPID='101' if running else '0', NRestarts='0',
            InvocationID='2' * 32 if running else '', WorkingDirectory=self.release,
            User='school-autopilot', Group='school-autopilot',
            Environment='PYTHONUNBUFFERED=1 AUTOPILOT_ADMISSION_MODE=' + mode,
            EnvironmentFiles=['/synthetic/environment (ignore_errors=no)', self.release + '/ops/autopilot/broker-hold.env (ignore_errors=no)'],
            ExecStart='{ path=/synthetic/python ; argv[]=/synthetic/python -m oracle_autopilot.worker_v17 ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }',
            FragmentPath='/synthetic/unit', DropInPaths='/synthetic/drop', NeedDaemonReload='no')
        drop = self.upgrade.desired_drop(PurePosixPath(self.release))
        if mode == 'ACTIVE':
            drop = drop.replace(b'ADMISSION_MODE=HOLD', b'ADMISSION_MODE=ACTIVE')
        raw_files = {'/synthetic/unit': (0o644, 65536, b'synthetic unit\n'),
                     '/synthetic/drop': (0o644, 4096, drop),
                     '/synthetic/environment': (0o600, 262144, b'AUTOPILOT_DATABASE_URL=synthetic-no-connection\n'),
                     self.release + '/ops/autopilot/broker-hold.env': (0o444, 4096, self.bundle['files']['ops/autopilot/broker-hold.env'].encode()),
                     '/synthetic/route': (0o644, 4096, b'{"version":1,"backend":"neon","database":"autopilot","epoch":0}')}
        self.files = {name: dict(mode=mode, limit=limit, raw=raw,
                        metadata=[1, 100 + i, 0, 0, stat.S_IFREG | mode, len(raw), 10, 10])
                      for i, (name, (mode, limit, raw)) in enumerate(raw_files.items())}
        self.process_value = dict(pid=101, uid=1001, gid=1002, uids=[1001] * 4, gids=[1002] * 4,
            cwd=self.release, executable='/synthetic/python-real', start_ticks=12345,
            argv=['/synthetic/python', '-m', 'oracle_autopilot.worker_v17'], environment={
                'AUTOPILOT_DATABASE_URL': 'synthetic-no-connection',
                'AUTOPILOT_SYNTHETIC_PIN': 'synthetic-value', 'AUTOPILOT_ADMISSION_MODE': mode,
                'PATH': '/synthetic/bin'})
        self.absence_value = dict(MainPID='0', ControlPID='0', ActiveState='inactive',
            ControlGroup='', cgroup_present=False, cgroup_populated=None)
        self.calls = {name: 0 for name in ('host', 'service', 'protected', 'hardening', 'runtime', 'process', 'absence')}
        self.drift_port = None

    def _read(self, name, value):
        self.calls[name] += 1
        result = deepcopy(value)
        if self.drift_port == name and self.calls[name] == 2:
            if name == 'service':
                result['NRestarts'] = '1'
            elif name == 'protected':
                result['/synthetic/environment']['metadata'][1] += 1
            elif name == 'hardening':
                result['NoNewPrivileges'] = 'no'
            elif name == 'process':
                result['start_ticks'] += 1
            elif name == 'absence':
                result.update(ControlGroup='/system.slice/synthetic.service', cgroup_present=True, cgroup_populated='0')
            elif name == 'host':
                result['hostname'] = 'another-host'
        return result

    def host(self):
        return self._read('host', dict(uid=0, hostname='synthetic-host'))

    def service(self):
        return self._read('service', self.service_value)

    def protected(self, release):
        if release != self.release:
            raise AssertionError('wrong fixed release')
        return self._read('protected', self.files)

    def hardening(self):
        return self._read('hardening', self.hardening_value)

    def process(self, pid):
        if pid != 101:
            raise AssertionError('wrong fixed PID')
        return self._read('process', self.process_value)

    def absence(self):
        return self._read('absence', self.absence_value)

    def verify_runtime(self, release, bundle):
        self.calls['runtime'] += 1
        if release != self.release or bundle != self.bundle:
            raise RuntimeError('synthetic immutable runtime changed')

    def environment(self, raw):
        return self.env_function(raw)

    def desired_drop(self, release):
        return self.upgrade.desired_drop(PurePosixPath(release))


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.reader = Reader()

    def capture(self, reader=None, **overrides):
        reader = reader or self.reader
        args = dict(runtime_bundle=reader.bundle, accepted_runtime_bundle_sha256=reader.bundle['sha256'],
                    controller_source='a' * 40, accepted_controller_sha256='b' * 64, clock=lambda: 123456)
        args.update(overrides)
        return c._capture_current(reader, **args)

    def unknown(self, reader=None, **overrides):
        with self.assertRaises(c.CaptureUnknown) as raised:
            self.capture(reader, **overrides)
        self.assertTrue(str(raised.exception).startswith('LEGACY_CAPTURE_UNKNOWN_'))

    def test_active_full_current_witness_and_repeated_fixed_reads(self):
        raw = self.capture()
        witness = json.loads(raw)
        self.assertEqual(raw, c._canonical(witness))
        self.assertLessEqual(len(raw), c.LIMIT)
        self.assertEqual(witness['assurance'], 'CURRENT_OBSERVATION_ONLY')
        self.assertEqual(witness['admission'], 'ACTIVE')
        self.assertEqual(witness['service'], self.reader.service_value)
        self.assertEqual(set(witness['service']), c.SERVICE_FIELDS)
        self.assertIsInstance(witness['service']['EnvironmentFiles'], list)
        self.assertEqual(witness['fingerprint_sha256'], c._sha(c._canonical(witness['fingerprint_inputs'])))
        self.assertEqual(witness['fingerprint_inputs']['service'], self.reader.service_value)
        self.assertEqual(witness['hardening'], self.reader.hardening_expected)
        self.assertEqual(witness['process']['start_ticks'], 12345)
        self.assertNotIn('environment', witness['process'])
        self.assertNotIn(b'synthetic-no-connection', raw)
        for name in ('host', 'service', 'protected', 'hardening', 'runtime', 'process'):
            self.assertEqual(self.reader.calls[name], 2)
        self.assertEqual(self.reader.calls['absence'], 0)
        for name in ('historical_chain_verified', 'admission_authorized', 'retirement_authorized'):
            self.assertIs(witness[name], False)

    def test_running_hold_uses_same_fingerprint_formula(self):
        witness = json.loads(self.capture(Reader('HOLD')))
        fields = dict(service=witness['service'], **{key: witness['fingerprint_inputs'][key]
            for key in ('environment', 'pins', 'route', 'drop')})
        self.assertEqual(witness['fingerprint_sha256'], c._sha(c._canonical(fields)))
        self.assertEqual(witness['admission'], 'HOLD')

    def test_stopped_requires_actual_absence_and_never_reads_old_proc(self):
        reader = Reader('STOPPED')
        witness = json.loads(self.capture(reader))
        self.assertEqual(witness['state'], 'STOPPED')
        self.assertEqual(witness['process']['kind'], 'STOPPED_ABSENCE')
        self.assertEqual(reader.calls['absence'], 2)
        self.assertEqual(reader.calls['process'], 0)

    def test_full_loaded_exec_user_group_paths_and_envfiles_rejected(self):
        cases = dict(User='root', Group='root', WorkingDirectory='/synthetic/other', NeedDaemonReload='yes',
            FragmentPath='/synthetic/other', DropInPaths='/synthetic/extra',
            EnvironmentFiles=['/synthetic/environment (ignore_errors=no)'],
            ExecStart=self.reader.service_value['ExecStart'].replace('worker_v17 ;', 'worker_v17 --extra ;'))
        for key, value in cases.items():
            with self.subTest(key=key):
                reader = Reader()
                reader.service_value[key] = value
                self.unknown(reader)

    def test_extra_command_and_ignore_errors_are_not_accounting(self):
        for value in (self.reader.service_value['ExecStart'] + ' { extra command }',
                      self.reader.service_value['ExecStart'].replace('ignore_errors=no', 'ignore_errors=yes')):
            reader = Reader()
            reader.service_value['ExecStart'] = value
            self.unknown(reader)

    def test_missing_or_scalar_environmentfiles_and_unknown_service_field(self):
        for value in ('/synthetic/file', tuple(self.reader.service_value['EnvironmentFiles'])):
            reader = Reader()
            reader.service_value['EnvironmentFiles'] = value
            self.unknown(reader)
        self.reader.service_value['Unreviewed'] = 'value'
        self.unknown()

    def test_admission_duplicates_stopped_active_and_intermediate_state(self):
        for value in ('AUTOPILOT_ADMISSION_MODE=NATIVE', 'AUTOPILOT_ADMISSION_MODE=ACTIVE AUTOPILOT_ADMISSION_MODE=HOLD'):
            reader = Reader()
            reader.service_value['Environment'] = value
            self.unknown(reader)
        reader = Reader('STOPPED')
        reader.service_value['Environment'] = 'AUTOPILOT_ADMISSION_MODE=ACTIVE'
        self.unknown(reader)
        self.reader.service_value['ActiveState'] = 'activating'
        self.unknown()

    def test_full_hardening_rejects_missing_or_unsafe_values(self):
        for change in ('missing', 'unsafe', 'extra'):
            reader = Reader()
            if change == 'missing': reader.hardening_value.pop('ProtectSystem')
            elif change == 'unsafe': reader.hardening_value['NoNewPrivileges'] = 'no'
            else: reader.hardening_value['Extra'] = 'unreviewed'
            self.unknown(reader)

    def test_protected_metadata_content_inventory_and_drop_drift(self):
        for change in ('owner', 'mode', 'symlink', 'size', 'base', 'extra', 'drop'):
            with self.subTest(change=change):
                reader = Reader()
                row = reader.files['/synthetic/environment']
                if change == 'owner': row['metadata'][2] = 1001
                elif change == 'mode': row['metadata'][4] = stat.S_IFREG | 0o644
                elif change == 'symlink': row['metadata'][4] = stat.S_IFLNK | 0o600
                elif change == 'size': row['metadata'][5] += 1
                elif change == 'base': reader.files['/synthetic/unit']['raw'] = b'changed unit\n'
                elif change == 'extra': reader.files['/synthetic/extra'] = deepcopy(row)
                else: reader.files['/synthetic/drop']['raw'] += b'ExecStart=/synthetic/unreviewed\n'
                self.unknown(reader)

    def test_process_actual_uid_cwd_argv_executable_and_kernel_start(self):
        for key, value in (('uid', 0), ('gids', [0] * 4), ('cwd', '/synthetic/other'),
                           ('argv', ['/synthetic/python', '-c', 'unreviewed']),
                           ('executable', '/synthetic/other'), ('start_ticks', 0)):
            reader = Reader()
            reader.process_value[key] = value
            self.unknown(reader)

    def test_live_environment_changed_extra_or_import_override(self):
        for key, value in (('AUTOPILOT_DATABASE_URL', 'other'), ('AUTOPILOT_EXTRA', 'unreviewed'), ('PYTHONPATH', '/synthetic/other')):
            reader = Reader()
            reader.process_value['environment'][key] = value
            self.unknown(reader)

    def test_stopped_live_controlpid_or_populated_cgroup_refused(self):
        for key, value in (('ControlPID', '102'), ('ControlGroup', '/other.slice/unreviewed'), ('cgroup_populated', '1')):
            reader = Reader('STOPPED')
            reader.absence_value[key] = value
            self.unknown(reader)

    def test_repeated_read_drift_in_every_material_read_port(self):
        for name in ('host', 'service', 'protected', 'hardening', 'process', 'absence'):
            with self.subTest(port=name):
                reader = Reader('STOPPED' if name == 'absence' else 'ACTIVE')
                reader.drift_port = name
                self.unknown(reader)

    def test_runtime_and_source_pins_are_explicit_and_bundle_paths_fixed(self):
        self.unknown(controller_source='bad')
        self.unknown(accepted_controller_sha256='bad')
        self.unknown(accepted_runtime_bundle_sha256='0' * 64)
        bundle = deepcopy(self.reader.bundle)
        bundle['files']['../../unreviewed'] = 'invalid'
        bundle['sha256'] = c._sha(c._canonical({key: bundle[key] for key in ('revision', 'files')}))
        self.unknown(runtime_bundle=bundle, accepted_runtime_bundle_sha256=bundle['sha256'])

    def test_clock_window_and_raw_failure_do_not_leak_values(self):
        times = iter((100, 131))
        self.unknown(clock=lambda: next(times))
        def failure(*_): raise RuntimeError('secret synthetic-value must not escape')
        self.reader.verify_runtime = failure
        with self.assertRaises(c.CaptureUnknown) as raised:
            self.capture()
        self.assertNotIn('secret', str(raised.exception))

    def test_descriptor_rejects_nonregular_or_linked_files_before_read(self):
        # Run the actual descriptor implementation even on Windows. The Linux
        # tests below separately exercise the real kernel FIFO/open behavior.
        for file_mode, links in ((stat.S_IFIFO | 0o600, 1), (stat.S_IFREG | 0o600, 2)):
            with self.subTest(mode=file_mode, links=links):
                def info(mode, inode, nlink):
                    return SimpleNamespace(st_dev=1, st_ino=inode, st_uid=0, st_gid=0,
                        st_mode=mode, st_size=0, st_mtime_ns=1, st_ctime_ns=1, st_nlink=nlink)
                root_info = info(stat.S_IFDIR | 0o755, 1, 1)
                file_info = info(file_mode, 2, links)
                opens, closes, reads = [], [], []
                def open_(name, flags, **kwargs):
                    opens.append((name, flags, kwargs))
                    return 10 if name == '/' else 11
                def forbidden_read(*args):
                    reads.append(args)
                    raise AssertionError('nonregular/linked file was read')
                reader = object.__new__(c._LinuxReader)
                reader.os = SimpleNamespace(O_RDONLY=0, O_DIRECTORY=1, O_NOFOLLOW=2,
                    O_NONBLOCK=4, O_NOATIME=8, O_CLOEXEC=16, open=open_, close=closes.append,
                    fstat=lambda fd: root_info if fd == 10 else file_info,
                    stat=lambda name, **kwargs: root_info if name == '/' else file_info,
                    read=forbidden_read)
                with self.assertRaises(c.CaptureUnknown):
                    reader._protected_file('/synthetic-file', 0o600, 4096)
                self.assertEqual(reads, [])
                self.assertEqual(opens[1][2], {'dir_fd': 10})
                self.assertEqual(opens[1][1] & (2 | 4 | 8), 2 | 4 | 8)
                self.assertEqual(closes, [11, 10])

    @unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                         'NOTRUN on this host: real descriptor metadata subset requires Linux root')
    def test_real_root_protected_regular_symlink_and_hardlink(self):
        import pwd
        root_home = Path(pwd.getpwuid(0).pw_dir)
        with tempfile.TemporaryDirectory(prefix='legacy-capture-test-', dir=root_home) as temporary:
            base = Path(temporary)
            target = base / 'regular'
            target.write_bytes(b'synthetic descriptor evidence\n')
            target.chmod(0o600)
            reader = object.__new__(c._LinuxReader)
            reader.os = os
            record = reader._protected_file(str(target), 0o600, 4096)
            self.assertEqual(record['raw'], target.read_bytes())
            self.assertEqual(record['metadata'][2], 0)
            symlink = base / 'symlink'
            symlink.symlink_to(target)
            with self.assertRaises((c.CaptureUnknown, OSError)):
                reader._protected_file(str(symlink), 0o600, 4096)
            hardlink = base / 'hardlink'
            os.link(target, hardlink)
            with self.assertRaises(c.CaptureUnknown):
                reader._protected_file(str(target), 0o600, 4096)

    @unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                         'NOTRUN on this host: actual no-writer FIFO regression requires Linux root')
    def test_real_root_fifo_refuses_without_open_or_read_hang(self):
        import pwd
        root_home = Path(pwd.getpwuid(0).pw_dir)
        with tempfile.TemporaryDirectory(prefix='legacy-capture-fifo-', dir=root_home) as temporary:
            fifo = Path(temporary) / 'synthetic-fifo'
            os.mkfifo(fifo, 0o600)
            # A process timeout makes this test fail promptly if NONBLOCK is
            # removed. There is deliberately no writer connected to the FIFO.
            script = '''import importlib.util,os,sys
spec=importlib.util.spec_from_file_location('actual_capture',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
reader=object.__new__(module._LinuxReader);reader.os=os
def forbidden_read(*args):raise AssertionError('FIFO read before descriptor validation')
os.read=forbidden_read
try:reader._protected_file(sys.argv[2],0o600,4096)
except module.CaptureUnknown:print('FIFO_REFUSED');sys.exit(0)
sys.exit(3)
'''
            child = subprocess.run([sys.executable, '-I', '-B', '-S', '-c', script,
                                    str(ROOT / 'ops' / 'light_native_legacy_capture.py'), str(fifo)],
                                   capture_output=True, text=True, timeout=3, check=False)
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertEqual(child.stdout.strip(), 'FIFO_REFUSED')

    @unittest.skip('NOTRUN: actual root Linux metadata, systemd, /proc and source-pinned installed-runtime integration need the separately reviewed owner host run')
    def test_real_linux_capture(self):
        pass


if __name__ == '__main__':
    unittest.main()
