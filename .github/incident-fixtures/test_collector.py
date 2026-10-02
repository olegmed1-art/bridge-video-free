"""Real Linux filesystem fixtures; no production paths outside disposable tmpfs."""
import base64
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('collector', HERE/'collector.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
# Synthetic fixed bytes only. No historical incident payload is published.
# Replace fixture binding constants/path segments; collector implementation unchanged.
POLICY_BYTES = b'{"fixture":"synthetic-policy","version":1}'
PLAN_BYTES = b'{"fixture":"synthetic-plan","version":1}'
old_policy, old_plan = c.POLICY, c.PLAN
c.POLICY = hashlib.sha256(POLICY_BYTES).hexdigest()
c.PLAN = hashlib.sha256(PLAN_BYTES).hexdigest()
c.SCOPES = {path.replace(old_policy, c.POLICY).replace(old_plan, c.PLAN): names
            for path, names in c.SCOPES.items()}
BASE = Path('/'+c.BASE)


class CollectorFixtures(unittest.TestCase):
    def setUp(self):
        for child in BASE.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        for path in c.SCOPES:
            Path('/'+path).mkdir(mode=0o700, parents=True, exist_ok=True)
        # mkdir(parents=True) uses umask for intermediate nodes.
        for directory in [BASE, *[p for p in BASE.rglob('*') if p.is_dir()]]:
            directory.chmod(0o700)
        self.policy = BASE/'issuers'/c.POLICY/'policy.json'
        self.plan = BASE/c.PLAN/'plan.json'
        self.write(self.policy, POLICY_BYTES)
        self.write(self.plan, PLAN_BYTES)
        self.lock = BASE/'issuers'/'cycle.lock'
        self.write(self.lock, b'')
        self.write(BASE/'cycles'/'cycle.lock', b'')

    def write(self, path, data=b'{}'):
        path.write_bytes(data)
        path.chmod(0o600)

    def refused(self):
        with self.assertRaises((ValueError, OSError, KeyError)):
            c.collect()

    def test_01_hash_metadata_only(self):
        result = c.collect()
        self.assertFalse(result['issue_allowed'])
        self.assertFalse(result['live_verified'])
        self.assertEqual(result['records'][str(self.plan)[1:]]['sha256'], c.PLAN)
        self.assertNotIn('base64', result['records'][str(self.plan)[1:]])

    def test_02_private_exact_bytes(self):
        row = c.collect(True)['records'][str(self.plan)[1:]]
        self.assertEqual(base64.b64decode(row['base64']), self.plan.read_bytes())

    def test_03_wrong_mode(self):
        self.plan.chmod(0o644)
        self.refused()

    def test_04_wrong_owner(self):
        os.chown(self.plan, 65534, 65534)
        self.refused()

    def test_05_symlink_and_dangling(self):
        self.plan.unlink()
        self.plan.symlink_to(self.policy)
        self.refused()
        self.plan.unlink()
        self.plan.symlink_to(BASE/'does-not-exist')
        self.refused()

    def test_06_symlink_parent(self):
        original = self.plan.parent
        moved = BASE/'moved'
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
        self.refused()

    def test_07_hardlink(self):
        os.link(self.plan, BASE/'second-link')
        self.refused()

    def test_08_missing_lock_never_created(self):
        self.lock.unlink()
        self.refused()
        self.assertFalse(self.lock.exists())

    def test_09_busy_lock_nonblocking(self):
        fd = os.open(self.lock, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.refused()
        finally:
            os.close(fd)

    def test_10_wrong_policy_or_plan(self):
        for path in (self.policy, self.plan):
            original = path.read_bytes()
            self.write(path, b'{}')
            self.refused()
            self.write(path, original)

    def test_11_in_place_drift(self):
        read = os.read
        changed = False
        def mutate(fd, size):
            nonlocal changed
            data = read(fd, size)
            if not changed and os.fstat(fd).st_ino == self.plan.stat().st_ino:
                changed = True
                self.write(self.plan, b'changed')
            return data
        with mock.patch.object(c.os, 'read', side_effect=mutate):
            self.refused()
        self.assertTrue(changed)

    def test_12_inode_replacement(self):
        read = os.read
        changed = False
        def replace(fd, size):
            nonlocal changed
            data = read(fd, size)
            if not changed and os.fstat(fd).st_ino == self.plan.stat().st_ino:
                changed = True
                replacement = self.plan.parent/'replacement'
                self.write(replacement, self.plan.read_bytes())
                replacement.replace(self.plan)
            return data
        with mock.patch.object(c.os, 'read', side_effect=replace):
            self.refused()
        self.assertTrue(changed)

    def test_13_unknown_name_bounded_not_ignored(self):
        self.write(self.plan.parent/'SECRET-NAME-MUST-NOT-APPEAR', b'private')
        result = c.collect()
        row = result['inventories'][str(self.plan.parent)[1:]]
        self.assertEqual(row['unexpected_count'], 1)
        self.assertNotIn('SECRET-NAME', json.dumps(result))
        self.assertFalse(result['issue_allowed'])

    def test_14_later_and_recovery_records_present(self):
        for name in ('execution.json', 'publication.json', 'controls-restored.json',
                     'restart-intent.json', 'recovery-intent.json', 'complete.json'):
            self.write(self.plan.parent/name)
        result = c.collect()
        for name in ('execution.json', 'publication.json', 'controls-restored.json',
                     'restart-intent.json', 'recovery-intent.json', 'complete.json'):
            self.assertTrue(result['records'][str(self.plan.parent/name)[1:]]['present'])
        self.assertFalse(result['live_verified'])

    def test_15_no_atime_or_journal_changes(self):
        # Directory enumeration itself affects atime; warm inventory, then fixed paths.
        paths = [BASE, *BASE.rglob('*')]
        before = {str(p): (p.lstat().st_atime_ns, c.meta(p.lstat())) for p in paths}
        c.collect()
        after = {str(p): (p.lstat().st_atime_ns, c.meta(p.lstat())) for p in paths}
        self.assertEqual(before, after)

    def test_16_wrong_lock_mode(self):
        self.lock.chmod(0o644)
        self.refused()

    def test_17_fifo_refused_without_wait(self):
        self.plan.unlink()
        os.mkfifo(self.plan, 0o600)
        self.refused()

    def test_18_isolated_child(self):
        # Run an isolated fixture child: import this fixed fixture module, then
        # invoke the unchanged collector with the same synthetic bindings.
        code = ("import importlib.util,json; "
                "s=importlib.util.spec_from_file_location('fixture','/fixtures/test_collector.py'); "
                "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                "print(json.dumps(m.c.collect()))")
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code],
                                capture_output=True, timeout=5, env={'PATH': '/usr/local/bin:/usr/bin:/bin'})
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertFalse(json.loads(result.stdout)['issue_allowed'])


def main():
    assert sys.platform == 'linux' and os.geteuid() == 0 and os.getegid() == 0
    assert os.environ.get('INCIDENT_FIXTURE_CONTAINER') == '1'
    assert os.listdir('/sys/class/net') == ['lo']
    mounts = Path('/proc/mounts').read_text().splitlines()
    assert any(line.split()[1:3] == [str(BASE), 'tmpfs'] for line in mounts)
    assert stat.S_IMODE(BASE.stat().st_mode) == 0o700
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CollectorFixtures)
    assert suite.countTestCases() == 18
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    assert result.testsRun == 18 and not result.skipped and result.wasSuccessful()
    print('INCIDENT_COLLECTOR_FIXTURES_PASS: 18 tests, real UID/GID 0, zero skips')


if __name__ == '__main__':
    main()
