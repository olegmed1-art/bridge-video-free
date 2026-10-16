import os
import base64
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ops import native_maintenance_store as store
from ops import native_maintenance_store_runner as runner
from ops.native_maintenance_workflow_pause import Refused


@unittest.skipUnless(os.getuid() == 0, 'root filesystem contracts')
class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='native-store-test-', dir='/root')
        self.parent = Path(self.temporary.name)
        self.root = self.parent / store.NAME
        self.addCleanup(self.temporary.cleanup)
        self.parent_patch = patch.object(store, 'PARENT', self.parent)
        self.mount_patch = patch.object(store, 'persistent_mount', return_value='ext4')
        self.parent_patch.start()
        self.mount_patch.start()
        self.addCleanup(self.parent_patch.stop)
        self.addCleanup(self.mount_patch.stop)

    def test_real_process_journal_reopen_copy_restore_and_idempotence(self):
        self.assertEqual(store.prepare(), 'ext4')
        self.assertEqual({x.name for x in self.root.iterdir()}, {'lock', 'VERSION'})
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        inode = self.root.stat().st_ino
        operation = self.root / ('a' * 64)
        operation.mkdir(mode=0o700)
        (operation / 'private-record').write_bytes(b'preserve-existing-operator-data')
        self.assertEqual(store.prepare(), 'ext4')
        self.assertEqual(self.root.stat().st_ino, inode)
        self.assertEqual((operation / 'private-record').read_bytes(), b'preserve-existing-operator-data')

    def test_existing_wrong_mode_and_symlink_not_repaired(self):
        self.root.mkdir(mode=0o755)
        with self.assertRaises(Refused): store.prepare()
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o755)
        self.root.rmdir()
        target = self.parent / 'outside'
        target.mkdir(mode=0o700)
        self.root.symlink_to(target, target_is_directory=True)
        with self.assertRaises(Refused): store.prepare()
        self.assertEqual(list(target.iterdir()), [])

    def test_untrusted_parent_and_volatile_mount_before_creation(self):
        self.parent.chmod(0o777)
        with self.assertRaises(Refused): store.prepare()
        self.assertFalse(self.root.exists())
        self.parent.chmod(0o700)
        with patch.object(store, 'persistent_mount', side_effect=Refused('volatile')):
            with self.assertRaises(Refused): store.prepare()
        self.assertFalse(self.root.exists())

    def test_corrupt_marker_and_hardlink_are_preserved(self):
        store.prepare()
        marker = self.root / 'VERSION'
        marker.write_bytes(b'partial')
        with self.assertRaises(Refused): store.prepare()
        self.assertEqual(marker.read_bytes(), b'partial')
        marker.write_bytes(store.VERSION)
        os.link(marker, self.parent / 'linked-marker')
        with self.assertRaises(Refused): store.prepare()

    def test_uncertain_probe_remains_and_blocks_retry(self):
        with patch.object(store.subprocess, 'run', side_effect=subprocess.TimeoutExpired('probe', 10)):
            with self.assertRaises(subprocess.TimeoutExpired): store.prepare()
        remaining = sorted(p.name for p in self.root.iterdir())
        self.assertTrue(any(p.startswith('probe-') for p in remaining))
        with self.assertRaises(Refused): store.prepare()
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), remaining)

    def test_concurrent_store_owner_is_not_preempted(self):
        import fcntl
        store.prepare()
        with (self.root / 'lock').open('rb') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError): store.prepare()


class RunnerTests(unittest.TestCase):
    def test_volatile_and_readonly_mounts_refused(self):
        for output in ('tmpfs rw,nosuid', 'overlay rw', 'ext4 ro', 'xfs rw\next4 rw', ''):
            result = subprocess.CompletedProcess([], 0, output, '')
            with self.subTest(output=output), patch.object(store.subprocess, 'run', return_value=result):
                with self.assertRaises(Refused): store.persistent_mount(Path('/var/lib'))
        for fs in ('ext4', 'xfs', 'btrfs'):
            with patch.object(store.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, fs+' rw,relatime', '')):
                self.assertEqual(store.persistent_mount(Path('/var/lib')), fs)

    def test_wrong_context_never_contacts_network(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(runner.urllib.request, 'build_opener') as network:
            with self.assertRaises(runner.bundle.BundleError): runner.source_check('a' * 40)
            network.assert_not_called()

    def test_bootstrap_uses_independent_lifetime_and_verified_payload(self):
        # Compile real generated wrapper; missing/mismatched payload must fail
        # before extraction or preparation. The supervisor stub only runs code
        # locally; real PID1 lifetime is separately exercised by its existing CI.
        decoder = Path(runner.bundle.__file__).read_bytes()
        lifetime = b'def managed(code, encoded, source, run):\n exec(code,{})\n return 0\n'
        with patch.object(runner.bundle, 'git', side_effect=[lifetime, decoder]):
            wrapper = runner.bootstrap('.', 'a' * 40, 'b' * 64, '1-1')
        result = subprocess.run([os.sys.executable, '-I', '-B', '-S', '-c', wrapper],
                                input=b'{}', capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        payload = runner.bundle.canonical(dict(version=1, source_sha='a' * 40,
            files={path: base64.b64encode(
                b"def main(): print('FIXED_PREPARATION_ENTRY')\n" if path == 'ops/native_maintenance_store.py'
                else b'# source\n').decode() for path in runner.bundle.FILES}))
        with patch.object(runner.bundle, 'git', side_effect=[lifetime, decoder]):
            wrapper = runner.bootstrap('.', 'a' * 40, runner.bundle.digest(payload), '1-1')
        result = subprocess.run([os.sys.executable, '-I', '-B', '-S', '-c', wrapper],
                                input=payload, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'FIXED_PREPARATION_ENTRY\n')


if __name__ == '__main__':
    unittest.main()
