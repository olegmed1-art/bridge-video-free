"""Isolated installation/history/rollback contracts, never production readiness."""
import base64
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

if os.name != 'posix':
    raise unittest.SkipTest('Installer imports real Linux flock; run this module in Linux CI')

from ops import light_native_service_history as h
from ops import light_native_service_install as install

REAL_COMMAND, REAL_OBSERVED = install.command, install.observed

REPO = Path(__file__).resolve().parents[1]


def package(source='a'*40):
    files = {name: dict(sha256=h.sha((REPO/name).read_bytes()),
                       base64=base64.b64encode((REPO/name).read_bytes()).decode()) for name in h.FILES}
    return h.encoded(dict(version=1, kind='LIGHT_NATIVE_CONTROL_SOURCE', source=source, files=files))


def payload(raw, **changes):
    now = datetime.now(timezone.utc)
    value = dict(version=1, kind='LIGHT_NATIVE_DORMANT_INSTALL_ONLY', action='install', source='a'*40,
        controller_source='a'*40, package_sha256=h.sha(raw), qualification_sha256='c'*64,
        not_before=(now-timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ'),
        expires_at=(now+timedelta(seconds=240)).strftime('%Y-%m-%dT%H:%M:%SZ'),
        expected_root='ABSENT', expected_unit='ABSENT', installation_sha256=None, history_sha256=None)
    value.update(changes)
    wire = h.encoded(value)
    return wire, h.sha(wire)


class ActionContracts(unittest.TestCase):
    def test_exact_selected_source_and_independent_digests(self):
        raw = package()
        wire, pin = payload(raw)
        self.assertEqual(install.action(wire, pin, raw)[0]['source'], 'a'*40)
        for changes in ({'controller_source': 'b'*40}, {'source': 'main'}, {'package_sha256': '0'*64},
                        {'qualification_sha256': None}, {'expected_root': 'ANY'}, {'action': 'enable'},
                        {'installation_sha256': 'c'*64}):
            wire, pin = payload(raw, **changes)
            with self.subTest(changes=changes), self.assertRaises(h.Refusal):
                install.action(wire, pin, raw)

    def test_finite_original_install_window(self):
        raw = package()
        for end in ('2026-10-19T00:00:00Z', '2026-10-19T00:05:01Z', 'never'):
            wire, pin = payload(raw, not_before='2026-10-19T00:00:00Z', expires_at=end)
            with self.subTest(end=end), self.assertRaises(h.Refusal):
                install.action(wire, pin, raw)

    def test_new_controller_can_select_exact_older_rollback_only(self):
        raw = package()
        wire, pin = payload(raw, action='rollback', controller_source='b'*40,
            expected_root='INSTALLED_DORMANT', expected_unit='DISABLED_INACTIVE',
            installation_sha256='d'*64, history_sha256='e'*64)
        self.assertEqual(install.action(wire, pin, raw)[0]['controller_source'], 'b'*40)
        wire, pin = payload(raw, action='rollback')
        with self.assertRaises(h.Refusal):
            install.action(wire, pin, raw)

    def test_job_clock_is_original_and_cannot_extend(self):
        guard = install.InstallationRunBinding('a'*40, 1, 1, object())
        now = datetime.now(timezone.utc).timestamp()
        stamp = datetime.fromtimestamp(now-200, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        mono = guard.mono_start
        with patch.object(install.time, 'time', return_value=guard.wall_start), \
             patch.object(install.time, 'monotonic', return_value=mono):
            guard.observe_job_clock({'started_at': stamp})
        original = guard.deadline
        with patch.object(install.time, 'time', return_value=guard.wall_start+1), \
             patch.object(install.time, 'monotonic', return_value=mono+1):
            guard.observe_job_clock({'started_at': stamp})
        self.assertLessEqual(guard.deadline, original)
        with self.assertRaises(h.Refusal):
            guard.observe_job_clock({'started_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})

    def test_no_mutation_before_authentication(self):
        raw = package()
        wire, pin = payload(raw)
        with patch.object(install, 'operation_lock') as lock, self.assertRaises(h.Refusal):
            install.operate(wire, pin, raw, object())
        lock.assert_not_called()


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Requires actual Linux root metadata; no local emulation')
class LinuxInstallationContracts(unittest.TestCase):
    """Actual protected filesystem; synthetic GHA/systemctl observation ports."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='native-install-contract-', dir='/root')
        self.addCleanup(temp.cleanup)
        self.scope = Path(temp.name)
        self.root, self.history = self.scope/'service', self.scope/'history'
        self.unit_file = self.scope/'supervisor.service'
        self.registry = self.scope/'registry'
        for port, changes in ((install, dict(ROOT=self.root, HISTORY=self.history, UNIT_FILE=self.unit_file,
                                    HOST=os.uname().nodename, UNIT_LOOKUP=(self.scope/'lookup',))),
                              (install.unit, dict(ROOT=self.root, UNIT_FILE=self.unit_file)),
                              (h, dict(ROOT=self.registry, RELEASES=self.registry/'releases',
                                       HISTORY=self.registry/'history'))):
            active = patch.multiple(port, **changes)
            active.start()
            self.addCleanup(active.stop)
        self.observation = patch.object(install, 'observed', side_effect=self.observed)
        self.observation.start()
        self.addCleanup(self.observation.stop)
        self.commands = []
        active = patch.object(install, 'command', side_effect=lambda *argv: self.commands.append(argv) or b'')
        active.start()
        self.addCleanup(active.stop)
        active = patch.object(install, 'consumer_identity', return_value={'fixture': 'synthetic active consumer'})
        active.start()
        self.addCleanup(active.stop)
        self.raw = package()
        self.guard = install.InstallationRunBinding('a'*40, 1, 1, object())
        active = patch.object(self.guard, 'assert_current')
        active.start()
        self.addCleanup(active.stop)

    def observed(self):
        return install.unit_state(present=self.unit_file.exists())

    def run_install(self):
        wire, pin = payload(self.raw)
        return install.operate(wire, pin, self.raw, self.guard)

    def rollback(self, result):
        wire, pin = payload(self.raw, action='rollback', expected_root='INSTALLED_DORMANT',
            expected_unit='DISABLED_INACTIVE', installation_sha256=result['installation_sha256'],
            history_sha256=result['history_sha256'])
        return install.operate(wire, pin, self.raw, self.guard)

    def test_history_complete_precedes_install_and_rollback_retains_exact_new_bytes(self):
        original = h.new_directory
        def traced(path):
            if path == self.root:
                rows = [p for p in self.history.iterdir() if p.is_dir()]
                self.assertEqual(len(rows), 1)
                self.assertEqual(h.parse(h.read(rows[0]/'manifest.json'))['root'], 'ABSENT')
                self.assertFalse(h.parse(h.read(rows[0]/'complete.json'))['secrets_included'])
            original(path)
        with patch.object(h, 'new_directory', side_effect=traced):
            result = self.run_install()
        before = {p.name: h.read(p) for p in self.root.iterdir()}
        unit_before = install.unit_bytes()
        self.assertTrue(result['software_installed'])
        self.assertFalse(result['end_to_end_accepted'])
        self.assertFalse(result['service_started'])
        outcome = self.rollback(result)
        self.assertTrue(outcome['previous_absence_restored'])
        retained = self.history/result['history_sha256']
        self.assertEqual({p.name: h.read(p) for p in (retained/'retired-root').iterdir()}, before)
        self.assertEqual((retained/'retired-unit.service').read_bytes(), unit_before)
        self.assertFalse(self.root.exists())
        self.assertFalse(self.unit_file.exists())
        self.assertEqual(self.commands, [('/usr/bin/systemctl', 'daemon-reload')]*2)

    def test_partial_install_is_a_reconciliation_barrier(self):
        with patch.object(install, 'new_unit', side_effect=OSError('fixture crash')), self.assertRaises(OSError):
            self.run_install()
        before = sorted(str(p.relative_to(self.scope)) for p in self.scope.rglob('*'))
        with self.assertRaises(h.Refusal):
            self.run_install()
        self.assertEqual(before, sorted(str(p.relative_to(self.scope)) for p in self.scope.rglob('*')))

    def test_existing_target_is_never_archived_or_replaced(self):
        self.root.mkdir(mode=0o700)
        secret = self.root/'live.env'
        secret.write_bytes(b'fixture, never copy')
        with self.assertRaises(h.Refusal):
            self.run_install()
        self.assertEqual(secret.read_bytes(), b'fixture, never copy')
        self.assertEqual({p.name for p in self.history.iterdir()}, {'operation.lock'})

    def test_rollback_refuses_mutable_data_without_moving_it(self):
        result = self.run_install()
        h.retain(self.root/'manifest.json', b'{"fixture":"live authority"}')
        with self.assertRaises(h.Refusal):
            self.rollback(result)
        self.assertTrue(self.unit_file.exists())
        self.assertTrue((self.root/'manifest.json').exists())

    def test_rollback_refuses_corrupt_history_completion(self):
        result = self.run_install()
        completion = self.history/result['history_sha256']/'complete.json'
        completion.write_bytes(b'{}')
        with self.assertRaises(h.Refusal):
            self.rollback(result)
        self.assertTrue(self.root.exists())
        self.assertTrue(self.unit_file.exists())

    def test_real_lock_excludes_a_second_operation(self):
        self.history.mkdir(mode=0o700)
        with install.operation_lock(), self.assertRaises(BlockingIOError):
            self.run_install()
        self.assertFalse(self.root.exists())

    def test_preexisting_dropin_refuses_before_any_target_creation(self):
        directory = self.scope/'lookup'/(install.UNIT+'.d')
        directory.mkdir(parents=True, mode=0o700)
        (directory/'unexpected.conf').write_bytes(b'[Service]\nExecStart=unexpected\n')
        with self.assertRaises(h.Refusal):
            self.run_install()
        self.assertFalse(self.root.exists())
        self.assertFalse(self.unit_file.exists())
        self.assertEqual({p.name for p in self.history.iterdir()}, {'operation.lock'})

    def test_guard_api_gap_unit_replacement_is_detected_before_rename(self):
        result = self.run_install()
        calls = []
        def interleave():
            calls.append(1)
            if len(calls) == 4:  # operate, initial, before intent, before last filesystem checks
                self.unit_file.write_bytes(b'privileged concurrent replacement\n')
        with patch.object(self.guard, 'assert_current', side_effect=interleave), self.assertRaises(h.Refusal):
            self.rollback(result)
        self.assertEqual(len(calls), 4)
        self.assertEqual(self.unit_file.read_bytes(), b'privileged concurrent replacement\n')
        self.assertFalse((self.history/result['history_sha256']/'retired-unit.service').exists())
        self.assertTrue(self.root.exists())

    def test_insufficient_completion_reserve_never_creates_unit(self):
        self.guard.deadline = install.time.monotonic()+89
        with self.assertRaises(h.Refusal):
            self.run_install()
        self.assertFalse(self.unit_file.exists())
        self.assertFalse(self.root.exists())

    @unittest.skipUnless(os.environ.get('GITHUB_ACTIONS') == 'true', 'Actual hosted CI PID1 only')
    def test_actual_pid1_load_and_restore_absence_without_start(self):
        # This exercises the real systemd command/parser with an isolated name.
        # GHA/host and filesystem path ports remain synthetic; never a production proof.
        import uuid
        name = 'bridge-dormant-contract-'+uuid.uuid4().hex+'.service'
        path = Path('/etc/systemd/system')/name
        with patch.multiple(install, UNIT=name, UNIT_FILE=path), \
             patch.multiple(install.unit, UNIT=name, UNIT_FILE=path), \
             patch.object(install, 'command', side_effect=REAL_COMMAND), \
             patch.object(install, 'observed', side_effect=REAL_OBSERVED):
            try:
                result = self.run_install()
                self.assertEqual(REAL_OBSERVED(), install.unit_state(present=True))
                self.rollback(result)
                self.assertEqual(REAL_OBSERVED(), install.unit_state(present=False))
            finally:
                if path.exists():
                    self.assertEqual(REAL_OBSERVED(), install.unit_state(present=True))
                    self.assertEqual(path.read_bytes(), install.unit.render())
                    path.unlink()  # Only this test's unique, never-started CI unit.
                    REAL_COMMAND('/usr/bin/systemctl', 'daemon-reload')

    def test_bootstrap_real_isolated_process_and_preimport_tamper_refusal(self):
        result = self.run_install()
        launcher = self.root/'launcher.py'
        code = ('import pathlib,types; p=pathlib.Path('+repr(str(launcher))+'); '
                'm=types.ModuleType("_qualified_entry"); m.__file__=str(p); '
                'exec(compile(p.read_bytes(),str(p),"exec"),m.__dict__); '
                'm.ROOT=p.parent; raise SystemExit(m.main())')
        first = subprocess.run([sys.executable, '-I', '-B', '-S', '-c', code], capture_output=True, timeout=10)
        self.assertEqual(first.returncode, 0, first.stdout)
        self.assertIn(b'RUNTIME_INTEGRATION_REQUIRED', first.stdout)
        victim = self.root/'light_native_service_supervisor.py'
        victim.write_bytes(b'raise RuntimeError("MUST NEVER EXECUTE TAMPERED CODE")\n')
        second = subprocess.run([sys.executable, '-I', '-B', '-S', '-c', code], capture_output=True, timeout=10)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(second.stderr, b'')
        self.assertNotIn(b'MUST NEVER', second.stdout)
        self.assertFalse(result['issue_authorized'])


if __name__ == '__main__':
    unittest.main()
