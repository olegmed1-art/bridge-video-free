import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ops import native_maintenance_bundle as bundle


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.source = 'a' * 40
        self.obj = dict(version=1, source_sha=self.source,
                        files={path: base64.b64encode(b'# source\n').decode() for path in bundle.FILES})

    def payload(self):
        return bundle.canonical(self.obj)

    def refused(self, payload=None):
        payload = self.payload() if payload is None else payload
        with self.assertRaises(bundle.BundleError):
            bundle.decode(payload, self.source, bundle.digest(payload))

    def test_extraction_exact_bytes_modes_and_cleanup(self):
        payload = self.payload()
        with bundle.extracted(payload, self.source, bundle.digest(payload)) as root:
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual({str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()},
                             set(bundle.FILES + bundle.PACKAGE_MARKERS))
            for path in bundle.FILES:
                self.assertEqual((root / path).read_bytes(), b'# source\n')
                self.assertEqual((root / path).stat().st_mode & 0o777, 0o600)
        self.assertFalse(root.exists())

    def test_external_digest_and_commit_are_both_required(self):
        payload = self.payload()
        for source, digest in [('b' * 40, bundle.digest(payload)), (self.source, '0' * 64)]:
            with self.assertRaises(bundle.BundleError):
                bundle.decode(payload, source, digest)

    def test_extra_missing_traversal_files(self):
        for path in ('../escape.py', '/tmp/escape.py', 'ops/extra.py'):
            self.obj['files'][path] = 'YQ=='
            self.refused()
            del self.obj['files'][path]
        for path in bundle.FILES:
            with self.subTest(missing=path):
                value = self.obj['files'].pop(path)
                self.refused()
                self.obj['files'][path] = value

    def test_duplicate_keys_and_noncanonical_json(self):
        payload = self.payload()
        self.refused(b'{"version":1,' + payload[1:])
        self.refused(payload + b'\n')
        self.refused(json.dumps(self.obj).encode())

    def test_schema(self):
        for value in (True, '1', 2, None):
            self.obj['version'] = value
            self.refused()
        self.obj['version'] = 1
        self.obj['unexpected'] = 1
        self.refused()

    def test_base64_and_sizes(self):
        path = bundle.FILES[0]
        for value in ('', 'YQ==\n', 'YR==', '!', 1, 'A' * (bundle.MAX_FILE * 2)):
            self.obj['files'][path] = value
            self.refused()
        self.obj['files'][path] = base64.b64encode(b'x' * (bundle.MAX_FILE + 1)).decode()
        self.refused()
        self.obj['files'] = {p: base64.b64encode(b'x' * 100000).decode() for p in bundle.FILES}
        self.refused()
        self.refused(b'x' * (bundle.MAX_WIRE + 1))
        self.refused(b'[' * 2000)

    def test_validation_before_temporary_directory(self):
        with patch.object(bundle.tempfile, 'TemporaryDirectory') as temporary:
            with self.assertRaises(bundle.BundleError):
                with bundle.extracted(self.payload(), self.source, '0' * 64):
                    self.fail('invalid package entered')
            temporary.assert_not_called()

    def test_partial_write_and_body_exception_cleanup(self):
        payload = self.payload()
        actual_open = os.open
        roots = []
        def fail_second(path, *args, **kwargs):
            if str(path).endswith(bundle.FILES[1]):
                roots.append(Path(path).parents[1])
                raise OSError('injected write failure')
            return actual_open(path, *args, **kwargs)
        with patch.object(bundle.os, 'open', fail_second):
            with self.assertRaises(OSError):
                with bundle.extracted(payload, self.source, bundle.digest(payload)):
                    self.fail('partial package entered')
        self.assertFalse(roots[0].exists())
        with self.assertRaises(KeyboardInterrupt):
            with bundle.extracted(payload, self.source, bundle.digest(payload)) as root:
                raise KeyboardInterrupt
        self.assertFalse(root.exists())

    def test_git_commit_ignores_dirty_tree_replacements_and_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', directory, *args], stderr=subprocess.DEVNULL).strip()
            git('init')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            for path in bundle.FILES:
                destination = root / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b'# committed\n')
            git('add', '.')
            git('commit', '-m', 'fixture')
            source = git('rev-parse', 'HEAD').decode()
            expected = bundle.build(root, source)
            first = root / bundle.FILES[0]
            first.write_bytes(b'# replacement\n')
            git('add', '.')
            git('commit', '-m', 'replacement')
            git('replace', source, git('rev-parse', 'HEAD').decode())
            first.write_bytes(b'# dirty\n')
            with patch.dict(os.environ, {'GIT_DIR': '/nonexistent', 'GIT_WORK_TREE': '/nonexistent'}):
                self.assertEqual(bundle.build(root, source), expected)
            decoded = bundle.decode(expected, source, bundle.digest(expected))
            self.assertEqual(decoded[bundle.FILES[0]], b'# committed\n')
            first.unlink()
            first.symlink_to('/etc/passwd')
            git('add', '.')
            git('commit', '-m', 'symlink')
            with self.assertRaises(bundle.BundleError):
                bundle.build(root, git('rev-parse', 'HEAD').decode())


if __name__ == '__main__':
    unittest.main()
