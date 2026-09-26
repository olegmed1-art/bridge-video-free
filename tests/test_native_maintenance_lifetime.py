import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ops import native_maintenance_lifetime as lifetime
from ops import native_maintenance_readonly_transport as transport
from ops import native_maintenance_bundle as bundle


class LifetimeTests(unittest.TestCase):
    def test_unit_identity_rejects_input_and_existing_units(self):
        for source, run in [('main', '1-1'), ('a' * 40, '../unit'), ('a' * 40, '1-1;whoami')]:
            with self.assertRaises(RuntimeError):
                lifetime.new_unit(source, run)
        with patch.object(lifetime, 'ctl', return_value=subprocess.CompletedProcess([], 0, b'loaded\n')):
            with self.assertRaisesRegex(RuntimeError, 'UNIT_ALREADY_EXISTS'):
                lifetime.new_unit('a' * 40, '1-1')

    def test_unit_command_uses_fixed_lifetime_properties(self):
        unit = 'bridge-native-ro-' + 'a' * 12 + '-123-1-' + 'b' * 16 + '.service'
        command = lifetime.command(unit, 'pass', 100)
        for flag in ('--property=KillMode=control-group', '--property=ExitType=main',
                     '--property=RuntimeMaxSec=100s', '--property=TimeoutStopSec=2s',
                     '--property=PrivateTmp=yes', '--property=Restart=no'):
            self.assertIn(flag, command)
        self.assertNotIn('--scope', command)
        for bad in ('school-autopilot-production-light.service', unit + ';echo'):
            with self.assertRaises(RuntimeError):
                lifetime.command(bad, 'pass', 100)

    def test_empty_requires_observed_group_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inode = root.stat().st_ino
            (root / 'cgroup.events').write_text('populated 1\n')
            self.assertFalse(lifetime.empty(root, inode))
            (root / 'cgroup.events').write_text('populated 0\n')
            self.assertTrue(lifetime.empty(root, inode))
            with self.assertRaisesRegex(RuntimeError, 'CGROUP_REPLACED'):
                lifetime.empty(root, inode + 1)

    @unittest.skipUnless(os.getuid() == 0 and Path('/run/systemd/system').is_dir(), 'real systemd required')
    def test_real_managed_transport_roundtrip(self):
        source = 'a' * 40
        files = {path: base64.b64encode(b'# fixture\n').decode() for path in bundle.FILES}
        audit = {'audit': 'ACTIVE_HOLD_PASS', 'light_active': True, 'admission': 'HOLD',
                 'live_dsn_matches_root_file': True, 'database_login': 'READ_ONLY_PASS',
                 'database_binding': 'NEON_PROJECT_BRANCH_ENDPOINT_PASS',
                 'queue_nonterminal': 0, 'same_invocation': True}
        files['ops/oracle_light_active_hold_attest.py'] = base64.b64encode(
            ('print(' + repr(json.dumps(audit)) + ')').encode()).decode()
        payload = bundle.canonical({'version': 1, 'source_sha': source, 'files': files})
        def source_read(repo, *args):
            return (Path(__file__).resolve().parents[1] / args[-1].split(':', 1)[1]).read_bytes()
        for mode, behavior, expected in [('probe', 'cancel', 'CANCELLED'),
                                         ('audit', 'audit', 'ACTIVE_HOLD_PASS')]:
            with patch.object(bundle, 'git', source_read):
                code = transport.bootstrap('.', source, bundle.digest(payload), mode, '1-1')
            self.assertEqual(transport.exchange([sys.executable, '-I', '-B', '-S', '-c', code],
                                               payload, behavior), expected)


if __name__ == '__main__':
    unittest.main()
