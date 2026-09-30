"""Offline input and fail-closed tests; never touch production paths."""
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from ops.incident import light_pilot_parent_repair_20260928 as repair
from ops.native_permission_hold_guard import EXPECTED_TARGET


class ParentRepairTests(unittest.TestCase):
    def test_exact_retained_request_and_scope_before_any_effect(self):
        permit = b'{"fixed":"opaque"}'
        class Control:
            plan = SimpleNamespace(ROOT=Path('/private-pilot'))
            canonical = staticmethod(lambda v: json.dumps(v, sort_keys=True,
                separators=(',', ':'), ensure_ascii=True).encode())
            digest = staticmethod(lambda v: hashlib.sha256(v).hexdigest())
            strict_json = staticmethod(lambda v, limit: json.loads(v))
            read = staticmethod(lambda path, limit: permit)
        # The receipt is private in production. A local substitute checks the
        # fixed guard path without making or reading a production request.
        old_permit = repair.PERMIT
        try:
            repair.PERMIT = hashlib.sha256(permit).hexdigest()
            value = {'accepted_permit_sha256': repair.PERMIT, 'request': {
                'source': repair.SOURCE, 'package_sha256': repair.PACKAGE,
                'permit_sha256': repair.PERMIT, 'baseline_sha256': repair.BASELINE,
                'dispatch_id': repair.DISPATCH, 'duration_seconds': 720,
                'scope': {'version': 1, 'operation': 'native_single_pilot',
                    'source': repair.SOURCE, 'target': EXPECTED_TARGET,
                    'plan_sha256': '3af45c61ece27fa57f6ebf3f26c304e46667d3585ce732392989143850ff06ab'}}}
            request, digest = repair._request(Control, Control.canonical(value))
            self.assertEqual(hashlib.sha256(request).hexdigest(), digest)
            self.assertEqual(json.loads(request)['permit_b64'], base64.b64encode(permit).decode())
            value['request']['scope']['operation'] = 'other'
            with self.assertRaisesRegex(RuntimeError, 'PARENT_SCOPE'):
                repair._request(Control, Control.canonical(value))
        finally:
            repair.PERMIT = old_permit

    def test_existing_symlink_is_refused_before_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            candidate = Path(folder) / 'bridge-school'
            candidate.symlink_to(Path(folder) / 'missing')
            with self.assertRaisesRegex(RuntimeError, 'PARENT_ALREADY_EXISTS'):
                repair._absent(candidate, 'PARENT_ALREADY_EXISTS')

    def test_create_only_parent_with_root_readback(self):
        if __import__('os').geteuid() != 0:
            self.skipTest('root-owned filesystem check')
        with tempfile.TemporaryDirectory() as folder:
            candidate = Path(folder) / 'bridge-school'
            calls = []
            with patch.object(repair, 'PARENT', candidate):
                inode = repair._create_parent(lambda: calls.append('current'))
                self.assertEqual(len(calls), 2)
                self.assertEqual(inode, candidate.stat().st_ino)
                self.assertEqual(candidate.stat().st_mode & 0o777, 0o755)
                self.assertEqual(list(candidate.iterdir()), [])
                with self.assertRaisesRegex(RuntimeError, 'PARENT_ALREADY_EXISTS'):
                    repair._create_parent(lambda: None)


if __name__ == '__main__':
    unittest.main()
