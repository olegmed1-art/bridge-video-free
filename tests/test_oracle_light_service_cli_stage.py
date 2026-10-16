"""Root-only offline fixtures: no service, network, credential or task execution."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
import oracle_light_service_cli_stage as stage


@unittest.skipUnless(os.geteuid() == 0, 'root ownership invariants require isolated root CI')
class ServiceStageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='light-stage-test-', dir=Path.home()))
        self.parent = self.root / 'production'
        self.parent.mkdir(mode=0o755)
        self.archive = self.root / 'input.tgz'
        self.events = []

    def tearDown(self):
        for path in self.root.rglob('*'):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)
        shutil.rmtree(self.root)

    def fixture(self, extra=(), omit=()):
        manifest = {'name': '@openai/codex', 'version': '0.157.0-linux-arm64', 'os': ['linux'], 'cpu': ['arm64']}
        members = [('package/package.json', json.dumps(manifest).encode(), tarfile.REGTYPE)]
        members += [('package/' + name, b'fixture-not-executable\n', tarfile.REGTYPE) for name in sorted(stage.EXECUTABLES) if name not in omit]
        members += [('package/' + stage.VENDOR + 'codex-resources/data.json', b'{}', tarfile.REGTYPE)]
        members += list(extra)
        with tarfile.open(self.archive, 'w:gz') as tar:
            for name, body, kind in members:
                info = tarfile.TarInfo(name)
                info.type = kind
                info.mode = 0o777
                info.size = len(body) if kind == tarfile.REGTYPE else 0
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    info.linkname = '/etc/passwd'
                tar.addfile(info, io.BytesIO(body) if kind == tarfile.REGTYPE else None)
        return base64.b64encode(hashlib.sha512(self.archive.read_bytes()).digest()).decode()

    def run_stage(self, pre=lambda: None, post=lambda: None, digest=None):
        with patch.object(stage, 'INTEGRITY', digest or self.fixture()):
            stage.transact(self.parent, self.archive, pre, post, self.events.append)

    def test_success_preserves_resources_and_fixed_wrapper(self):
        calls = []
        self.run_stage(lambda: calls.append('pre'), lambda: calls.append('post'))
        target = self.parent / 'runtime-bin'
        self.assertEqual(calls, ['pre', 'pre', 'post'])
        self.assertEqual((target / 'codex').read_text(), stage.WRAPPER)
        self.assertEqual((target / stage.NATIVE).stat().st_mode & 0o777, 0o555)
        self.assertEqual((target / stage.VENDOR / 'codex-resources/data.json').stat().st_mode & 0o777, 0o444)
        self.assertEqual(self.events[-1]['action'], 'STAGED_VERIFIED')
        self.assertFalse(self.events[-1]['tasks_started'])
        self.assertEqual(list(self.parent.glob('.native-cli-stage-*')), [])

    def test_wrong_integrity_never_promotes(self):
        self.fixture()
        with self.assertRaisesRegex(RuntimeError, 'ARCHIVE_INTEGRITY'):
            self.run_stage(digest='invalid')
        self.assertFalse((self.parent / 'runtime-bin').exists())

    def test_existing_target_untouched(self):
        target = self.parent / 'runtime-bin'
        target.mkdir()
        (target / 'sentinel').write_text('keep')
        with self.assertRaisesRegex(RuntimeError, 'TARGET_EXISTS'):
            self.run_stage()
        self.assertEqual((target / 'sentinel').read_text(), 'keep')

    def test_unsafe_members_rejected_before_promotion(self):
        cases = [('package/../escape', b'x', tarfile.REGTYPE),
                 ('/package/escape', b'x', tarfile.REGTYPE),
                 ('package//escape', b'x', tarfile.REGTYPE),
                 ('package/package.json', b'x', tarfile.REGTYPE),
                 ('package/codex', b'x', tarfile.REGTYPE),
                 ('package/link', b'', tarfile.SYMTYPE),
                 ('package/hard', b'', tarfile.LNKTYPE),
                 ('package/fifo', b'', tarfile.FIFOTYPE),
                 ('package/device', b'', tarfile.CHRTYPE),
                 ('package/' + 'a/' * 13 + 'x', b'x', tarfile.REGTYPE)]
        for item in cases:
            with self.subTest(item=item[0]):
                digest = self.fixture([item])
                with self.assertRaises(RuntimeError):
                    self.run_stage(digest=digest)
                self.assertFalse((self.parent / 'runtime-bin').exists())

    def test_missing_helper_rejected(self):
        digest = self.fixture(omit={stage.VENDOR + 'codex-path/rg'})
        with self.assertRaisesRegex(RuntimeError, 'RESOURCES_MISSING'):
            self.run_stage(digest=digest)

    def test_file_size_cap(self):
        digest = self.fixture()
        with patch.object(stage, 'MAX_FILE', 2):
            with self.assertRaisesRegex(RuntimeError, 'EXPANDED_SIZE'):
                self.run_stage(digest=digest)

    def test_source_symlink_and_hardlink_rejected(self):
        digest = self.fixture()
        saved = self.root / 'original'
        self.archive.rename(saved)
        self.archive.symlink_to(saved)
        with self.assertRaises(OSError):
            self.run_stage(digest=digest)
        self.archive.unlink()
        os.link(saved, self.archive)
        with self.assertRaisesRegex(RuntimeError, 'ARCHIVE_SIZE'):
            self.run_stage(digest=digest)

    def test_parent_writable_or_symlink_rejected(self):
        self.parent.chmod(0o775)
        with self.assertRaisesRegex(RuntimeError, 'PARENT_DRIFT'):
            self.run_stage()
        self.parent.chmod(0o755)
        original = self.root / 'original'
        self.parent.rename(original)
        self.parent.symlink_to(original)
        with self.assertRaises(OSError):
            self.run_stage()

    def test_repeated_preflight_failure_never_promotes(self):
        count = 0
        def pre():
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError('MAIN_DRIFT')
        with self.assertRaisesRegex(RuntimeError, 'MAIN_DRIFT'):
            self.run_stage(pre=pre)
        self.assertFalse((self.parent / 'runtime-bin').exists())

    def test_postflight_failure_quarantines_exact_tree(self):
        calls = []
        def post():
            calls.append('post')
            if len(calls) == 1:
                raise RuntimeError('ATTESTATION_FAILURE')
        with self.assertRaisesRegex(RuntimeError, 'ATTESTATION_FAILURE'):
            self.run_stage(post=post)
        self.assertEqual(len(calls), 2)
        self.assertFalse((self.parent / 'runtime-bin').exists())
        quarantines = list(self.parent.glob('.native-cli-stage-*/tree'))
        self.assertEqual(len(quarantines), 1)
        stage.inventory(quarantines[0])
        self.assertEqual(self.events[-1]['rollback'], 'TARGET_ABSENT_VERIFIED')
        self.assertEqual(self.events[-1]['post_rollback_hold'], 'VERIFIED')

    def test_drift_fails_closed_without_deleting_target(self):
        def post():
            path = self.parent / 'runtime-bin' / 'extra'
            path.write_text('concurrent change')
            path.chmod(0o444)
            raise RuntimeError('DRIFT')
        with self.assertRaisesRegex(RuntimeError, 'DRIFT'):
            self.run_stage(post=post)
        self.assertTrue((self.parent / 'runtime-bin' / 'extra').exists())
        self.assertEqual(self.events[-1]['rollback'], 'UNCERTAIN')

    def test_atomic_no_replace(self):
        source, target = self.parent / 'source', self.parent / 'target'
        source.mkdir()
        target.mkdir()
        with stage.directory_handle(os.open(self.parent, os.O_RDONLY | os.O_DIRECTORY)) as handle:
            with self.assertRaises(FileExistsError):
                stage.rename_no_replace(handle.fd, 'source', 'target')
        self.assertTrue(source.exists())
        self.assertTrue(target.exists())


if __name__ == '__main__':
    unittest.main()
