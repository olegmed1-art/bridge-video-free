"""Source contract plus opt-in isolated Linux root -> real worker regression.

Never executes the installer: only its directory helper and spool-leaf loop.
"""
import ast
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'ops/oracle_universal_video_install.sh'
GUARD = ROOT / 'ops/oracle_universal_video_spool_guard.sh'
LEAVES = ('inbox', 'running', 'done', 'failed', 'results', 'progress', 'attempts')


def layout_fragment():
    text = INSTALLER.read_text()
    helper = re.search(r'^ensure_real_dir\(\)\{\n.*?^\}', text, re.M | re.S)
    loop = re.search(r'^for d in inbox .*?^done$', text, re.M | re.S)
    assert helper and loop
    return 'set -euo pipefail\ndie(){ echo "$*" >&2; exit 1; }\n' + helper[0] + '\n' + loop[0]


class SourceContract(unittest.TestCase):
    def test_worker_installer_guard_and_write_probe_agree(self):
        worker = ast.parse((ROOT / 'universal_video/spool_worker.py').read_text())
        dirs = next(n for n in worker.body if isinstance(n, ast.FunctionDef) and n.name == '_dirs')
        names = next(n for n in ast.walk(dirs) if isinstance(n, ast.DictComp)).generators[0].iter
        self.assertEqual(tuple(ast.literal_eval(names)), LEAVES)
        self.assertIn('for d in ' + ' '.join(LEAVES) + '; do', layout_fragment())
        self.assertIn('for leaf in ' + ' '.join(LEAVES) + '; do', GUARD.read_text())
        self.assertIn('"results", "attempts"):', INSTALLER.read_text())


CHILD = r'''
import os, sys, types
from pathlib import Path
assert os.getuid() == os.geteuid() == 65534
assert os.getgid() == os.getegid() == 65534
assert os.getgroups() == []
def forbidden(*args, **kwargs):
    raise AssertionError('External transport forbidden')
transport = types.ModuleType('requests')
for name in ('get', 'post', 'put', 'delete', 'request', 'Session'):
    setattr(transport, name, forbidden)
sys.modules['requests'] = transport
def audit(event, args):
    if event.startswith('socket.') or event in ('subprocess.Popen', 'os.system'):
        forbidden()
sys.addaudithook(audit)
from universal_video.spool_worker import recover_orphaned_jobs
from universal_video.lifecycle import atomic_json
import fcntl
root = Path(sys.argv[1])
with (root / '.workload.lock').open('rb') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    assert not any(recover_orphaned_jobs(root).values())
atomic_json(root / 'attempts' / 'probe.json', {'probe': True})
print('REAL_WORKER_RECOVERY_AND_ATTEMPT_WRITE_PASS')
'''


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0
                     and os.environ.get('UV_LAYOUT_ISOLATED') == '1',
                     'Requires explicitly isolated Linux root runner')
class RootWorkerLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/var/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.parent.chmod(0o755)
        self.base = self.parent / 'base'
        self.spool = self.base / 'spool'
        self.spool.mkdir(parents=True)
        for p in (self.base, self.spool):
            os.chown(p, 0, 65534)
            p.chmod(0o750)
        for name in LEAVES[:-1]:
            p = self.spool / name
            p.mkdir()
            os.chown(p, 65534, 65534)
            p.chmod(0o750)
        lock = self.spool / '.workload.lock'
        lock.touch()
        os.chown(lock, 0, 65534)
        lock.chmod(0o640)

    def provision(self):
        return subprocess.run(['bash', '-c', layout_fragment()], text=True,
            capture_output=True, env={'PATH': '/usr/local/bin:/usr/bin:/bin',
            'BASE_DIR': str(self.base), 'USER_NAME': '65534', 'GROUP_NAME': '65534'}, timeout=20)

    def worker(self):
        return subprocess.run([sys.executable, '-B', '-c', CHILD, str(self.spool)],
            user=65534, group=65534, extra_groups=[], cwd=ROOT, text=True,
            capture_output=True, timeout=20,
            env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'PYTHONPATH': str(ROOT),
                 'PYTHONDONTWRITEBYTECODE': '1', 'UNIVERSAL_VIDEO_QUEUE_BACKEND': 'spool_only'})

    def guard(self):
        import pwd, grp
        return subprocess.run(['bash', str(GUARD), 'verify', str(self.base),
            'root', pwd.getpwuid(65534).pw_name, grp.getgrgid(65534).gr_name],
            text=True, capture_output=True, timeout=20)

    def test_legacy_failure_then_install_and_reinstall(self):
        self.assertNotEqual(self.guard().returncode, 0)
        before = self.worker()
        self.assertNotEqual(before.returncode, 0)
        self.assertIn('PermissionError', before.stderr)
        self.assertIn('/attempts', before.stderr)
        for _ in range(2):
            result = self.provision()
            self.assertEqual(result.returncode, 0, result.stderr)
            guard = self.guard()
            self.assertEqual(guard.returncode, 0, guard.stderr)
            worker = self.worker()
            self.assertEqual(worker.returncode, 0, worker.stderr)
            self.assertIn('REAL_WORKER_RECOVERY_AND_ATTEMPT_WRITE_PASS', worker.stdout)
        info = self.spool.stat()
        self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)), (0, 65534, 0o750))

    def test_existing_directory_repaired_without_recursive_changes(self):
        attempts = self.spool / 'attempts'
        attempts.mkdir(mode=0o700)
        evidence = attempts / 'retained'
        evidence.write_bytes(b'keep')
        evidence.chmod(0o600)
        original = evidence.stat()
        outside = self.parent / 'outside'
        outside.write_bytes(b'untouched')
        outside_original = outside.stat()
        (attempts / 'nested-link').symlink_to(outside)
        self.assertNotEqual(self.guard().returncode, 0)
        result = self.provision()
        self.assertEqual(result.returncode, 0, result.stderr)
        info = attempts.stat()
        self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)), (65534, 65534, 0o750))
        self.assertEqual(evidence.read_bytes(), b'keep')
        now = evidence.stat()
        self.assertEqual((now.st_uid, now.st_gid, now.st_mode, now.st_ino),
                         (original.st_uid, original.st_gid, original.st_mode, original.st_ino))
        self.assertEqual(outside.read_bytes(), b'untouched')
        outside_now = outside.stat()
        self.assertEqual((outside_now.st_uid, outside_now.st_gid, outside_now.st_mode),
                         (outside_original.st_uid, outside_original.st_gid, outside_original.st_mode))
        self.assertTrue((attempts / 'nested-link').is_symlink())

    def test_unsafe_attempts_entries_rejected(self):
        attempts = self.spool / 'attempts'
        outside = self.parent / 'outside'
        outside.mkdir(mode=0o700)
        metadata = outside.stat()
        for kind in ('directory-link', 'dangling-link', 'file'):
            with self.subTest(kind=kind):
                if kind == 'file':
                    attempts.write_bytes(b'keep')
                else:
                    attempts.symlink_to(outside if kind == 'directory-link' else self.parent / 'missing')
                result = self.provision()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('unsafe directory path', result.stderr)
                self.assertEqual(outside.stat().st_mode, metadata.st_mode)
                self.assertEqual(outside.stat().st_uid, metadata.st_uid)
                attempts.unlink()


if __name__ == '__main__':
    unittest.main()
