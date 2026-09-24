"""Offline upload regressions: no JSON filesystem storage or shared state."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import run_drive_3_1_free as drive


class UploadJSONTests(unittest.TestCase):
    def test_overlapping_uploads_preserve_payload_and_drive_contract(self):
        bodies = []
        def post(url, *, headers, data, timeout):
            bodies.append(data)
            self.assertEqual(timeout, 180)
            self.assertEqual(headers['Authorization'], 'Bearer synthetic')
            self.assertIn('uploadType=multipart', url)
            self.assertIn(b'"name": "result.json"', data)
            self.assertIn(b'"parents": ["parent"]', data)
            self.assertIn(b'Content-Type: application/json', data)
            if len(bodies) == 1:
                drive.upload_json('synthetic', 'parent', 'result.json', {'value': 'inner'})
                self.assertIn('наружный'.encode(), data)
                self.assertNotIn(b'inner', data)
            else:
                self.assertIn(b'inner', data)
            response = Mock()
            response.json.return_value = {'id': 'synthetic'}
            return response
        with patch.object(drive.requests, 'post', side_effect=post):
            with patch.object(Path, 'write_text', side_effect=AssertionError('disk write')):
                with patch.object(tempfile, 'TemporaryDirectory', side_effect=AssertionError('tempfile')):
                    self.assertEqual(drive.upload_json('synthetic', 'parent', 'result.json', {'value': 'наружный'}), {'id': 'synthetic'})
        self.assertEqual(len(bodies), 2)

    def test_failure_leaves_no_json_on_disk(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(tempfile, 'tempdir', root):
                with patch.object(drive.requests, 'post', side_effect=RuntimeError('synthetic failure')):
                    with self.assertRaises(RuntimeError):
                        drive.upload_json('synthetic', 'parent', 'result.json', {})
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_invalid_names_are_rejected_before_upload(self):
        with patch.object(drive.requests, 'post') as upload:
            for name in ['', '.', '..', '../outside.json', '/tmp/outside.json', 'sub/file.json']:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    drive.upload_json('synthetic', 'parent', name, {})
            upload.assert_not_called()

    def test_file_upload_preserves_binary_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'report.pdf'
            path.write_bytes(b'%PDF-\x00\xff')
            response = Mock()
            response.json.return_value = {'id': 'pdf'}
            with patch.object(drive.requests, 'post', return_value=response) as post:
                self.assertEqual(drive.upload_file('synthetic', 'parent', path, 'application/pdf'), {'id': 'pdf'})
            self.assertIn(b'%PDF-\x00\xff', post.call_args.kwargs['data'])
            self.assertIn(b'"name": "report.pdf"', post.call_args.kwargs['data'])
            response.raise_for_status.assert_called_once()


if __name__ == '__main__':
    unittest.main()
