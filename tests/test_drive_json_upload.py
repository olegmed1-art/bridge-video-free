"""Offline regression tests for per-upload temporary file isolation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_drive_3_1_free as drive


class UploadJSONTests(unittest.TestCase):
    def test_overlapping_uploads_keep_same_name_and_separate_payloads(self):
        paths = []
        def upload(token, parent, path, mime):
            path = Path(path)
            paths.append(path)
            self.assertEqual(path.name, 'result.json')
            self.assertEqual(mime, 'application/json')
            if len(paths) == 1:
                drive.upload_json(token, parent, 'result.json', {'value': 'inner'})
                self.assertEqual(json.loads(path.read_text()), {'value': 'outer'})
            else:
                self.assertEqual(json.loads(path.read_text()), {'value': 'inner'})
            return {'id': 'synthetic'}
        with tempfile.TemporaryDirectory() as root:
            with patch.object(tempfile, 'tempdir', root), patch.object(drive, 'upload_file', upload):
                drive.upload_json('synthetic', 'parent', 'result.json', {'value': 'outer'})
            self.assertEqual(list(Path(root).iterdir()), [])
        self.assertNotEqual(paths[0].parent, paths[1].parent)

    def test_failure_cleans_private_workspace(self):
        paths = []
        def fail(token, parent, path, mime):
            paths.append(Path(path))
            self.assertEqual(Path(path).parent.stat().st_mode & 0o777, 0o700)
            raise RuntimeError('synthetic upload failure')
        with patch.object(drive, 'upload_file', fail):
            with self.assertRaises(RuntimeError):
                drive.upload_json('synthetic', 'parent', 'result.json', {})
        self.assertFalse(paths[0].exists())
        self.assertFalse(paths[0].parent.exists())

    def test_invalid_names_are_rejected_before_upload(self):
        with patch.object(drive, 'upload_file') as upload:
            for name in ['', '.', '..', '../outside.json', '/tmp/outside.json', 'sub/file.json']:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    drive.upload_json('synthetic', 'parent', name, {})
            upload.assert_not_called()


if __name__ == '__main__':
    unittest.main()
