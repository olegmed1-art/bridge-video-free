"""Isolated root filesystem faults; no ARM native code executes in CI fixtures."""
import base64
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from ops import native_maintenance_driver as driver
from ops.native_maintenance_workflow_pause import Refused, encoded


FILES = {'psycopg/__init__.py': b'# synthetic fixture\n',
         'psycopg_binary/__init__.py': b'# synthetic fixture\n',
         'typing_extensions.py': b'# synthetic fixture\n'}


def wheel(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return stream.getvalue()


def payload(raw):
    return encoded(dict(version=1, wheels={'fixture.whl': base64.b64encode(raw).decode()}))


class DecodeTests(unittest.TestCase):
    def test_exact_bytes_required_and_unknown_wheels_refused(self):
        raw = wheel(FILES)
        pins = {'fixture.whl': (driver.sha(raw), len(raw))}
        with patch.object(driver, 'WHEELS', pins):
            self.assertEqual(driver.decode(payload(raw)), FILES)
            with self.assertRaises(Refused):
                driver.decode(payload(raw[:-1] + b'x'))
            with self.assertRaisesRegex(Refused, 'WIRE_SHAPE'):
                driver.decode(encoded(dict(version=1, wheels={})))

    def test_unsafe_paths_pth_symlink_duplicate_and_bounds_refused(self):
        for path in ('../escape', '/absolute', 'psycopg/../../escape', 'psycopg/./hidden',
                     'psycopg/start.pth', 'psycopg\\evil.py'):
            with self.subTest(path=path):
                raw = wheel({**FILES, path: b'x'})
                with patch.object(driver, 'WHEELS', {'fixture.whl': (driver.sha(raw), len(raw))}), \
                        self.assertRaises(Refused):
                    driver.decode(payload(raw))
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            entry = zipfile.ZipInfo('psycopg/link')
            entry.external_attr = 0o120777 << 16
            archive.writestr(entry, '/tmp/anything')
        raw = stream.getvalue()
        with patch.object(driver, 'WHEELS', {'fixture.whl': (driver.sha(raw), len(raw))}), \
                self.assertRaisesRegex(Refused, 'ZIP_TYPE'):
            driver.decode(payload(raw))
        with self.assertRaisesRegex(Refused, 'WIRE_SIZE'):
            driver.decode(b'x' * (driver.MAX_WIRE + 1))

    def test_missing_required_import_is_refused(self):
        raw = wheel({'typing_extensions.py': b''})
        with patch.object(driver, 'WHEELS', {'fixture.whl': (driver.sha(raw), len(raw))}), \
                self.assertRaisesRegex(Refused, 'IMPORTS_MISSING'):
            driver.decode(payload(raw))


@unittest.skipUnless(os.getuid() == 0, 'requires isolated root-owned fixture')
class DriverStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.identity = dict(python='CI-fixture', binary_sha256='a' * 64,
                             architecture='CI-fixture', wheels_digest='b' * 64)
        self.root = self.parent / driver.NAME / driver.sha(encoded(self.identity))
        raw = wheel(FILES)
        self.data = payload(raw)
        self.patches = [patch.object(driver, 'PARENT', self.parent),
                        patch.object(driver, 'WHEELS', {'fixture.whl': (driver.sha(raw), len(raw))}),
                        patch.object(driver, 'python_identity', return_value=self.identity),
                        patch.object(driver, 'persistent_mount', return_value='ext4'),
                        patch.object(driver, 'trusted_parent'),  # /tmp is not a production parent.
                        patch.object(driver, 'smoke', return_value=dict(driver='3.3.4', impl='binary', libpq=180000))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_private_install_and_identical_reuse(self):
        first = driver.prepare(self.data)
        self.assertFalse(first['reused'])
        self.assertEqual(first['installed_bytes'], sum(map(len, FILES.values())))
        driver.verify_tree(self.root, FILES, self.identity)
        self.assertTrue(driver.prepare(self.data)['reused'])

    def test_partial_install_blocks_without_overwrite(self):
        self.root.mkdir(mode=0o700, parents=True)
        self.root.parent.chmod(0o700)
        with self.assertRaisesRegex(Refused, 'INCOMPLETE'):
            driver.prepare(self.data)
        self.assertEqual(list(self.root.iterdir()), [])
        driver.smoke.assert_not_called()

    def test_changed_file_blocks_reuse_and_is_preserved(self):
        driver.prepare(self.data)
        target = self.root / 'site' / 'typing_extensions.py'
        target.write_bytes(b'changed')
        with self.assertRaises(Refused):
            driver.prepare(self.data)
        self.assertEqual(target.read_bytes(), b'changed')

    def test_extra_file_or_directory_blocks(self):
        driver.prepare(self.data)
        extra = self.root / 'site' / 'unexpected'
        extra.mkdir(mode=0o700)
        with self.assertRaisesRegex(Refused, 'EXTRA_DIRECTORY'):
            driver.prepare(self.data)

    def test_parent_symlink_and_shared_permissions_refused(self):
        other = self.parent / 'other'
        other.mkdir(mode=0o700)
        (self.parent / driver.NAME).symlink_to(other, target_is_directory=True)
        with self.assertRaises(Refused):
            driver.prepare(self.data)
        self.assertEqual(list(other.iterdir()), [])

    def test_hardlink_or_untrusted_file_mode_blocks(self):
        driver.prepare(self.data)
        target = self.root / 'site' / 'typing_extensions.py'
        os.link(target, self.parent / 'alias')
        with self.assertRaisesRegex(Refused, 'FILE_IDENTITY'):
            driver.prepare(self.data)

    def test_import_failure_leaves_evidence_and_never_claims_success(self):
        driver.smoke.side_effect = Refused('DRIVER_IMPORT_REFUSED')
        with self.assertRaisesRegex(Refused, 'IMPORT_REFUSED'):
            driver.prepare(self.data)
        self.assertTrue((self.root / 'READY.json').exists())
        driver.verify_tree(self.root, FILES, self.identity)


if __name__ == '__main__':
    unittest.main()
