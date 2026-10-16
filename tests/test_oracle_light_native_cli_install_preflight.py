"""Read-only host inventory never reads session contents or trusts non-HOLD."""
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops import oracle_light_native_cli_install_preflight as probe


class InstallPreflightTests(unittest.TestCase):
    def test_non_hold_does_not_inspect_profiles(self):
        with patch.object(probe.os,'geteuid',return_value=0), patch.object(
                probe.os,'uname',return_value=SimpleNamespace(nodename='autopilot-lite-vnic')), patch.object(
                probe.subprocess,'run',return_value=SimpleNamespace(
                    stdout='ActiveState=active\nSubState=running\nEnvironment=AUTOPILOT_ADMISSION_MODE=ACTIVE\n')), patch.object(
                probe,'profile') as profile:
            with self.assertRaisesRegex(ValueError,'NOT_ACTIVE_HOLD'):
                probe.main()
        profile.assert_not_called()

    def test_private_directory_rejects_symlink_and_group_writes(self):
        private=SimpleNamespace(st_mode=0o40700,st_uid=1000)
        with patch.object(Path,'lstat',return_value=private):
            self.assertEqual(probe.directory_status(Path('/private'),1000),'PROFILE_OWNED')
        for mode in (0o40770,0o120777):
            with patch.object(Path,'lstat',return_value=SimpleNamespace(st_mode=mode,st_uid=1000)):
                self.assertEqual(probe.directory_status(Path('/unsafe'),1000),'REVIEW_REQUIRED')

    def test_credential_directory_requires_private_profile_ownership(self):
        for mode, owner, expected in ((0o40700,1000,'PROFILE_PRIVATE'),
                                      (0o40755,1000,'REVIEW_REQUIRED'),
                                      (0o40700,0,'REVIEW_REQUIRED'),
                                      (0o120777,1000,'REVIEW_REQUIRED')):
            with patch.object(Path,'lstat',return_value=SimpleNamespace(st_mode=mode,st_uid=owner)):
                self.assertEqual(probe.credential_directory_status(Path('/auth'),1000),expected)

    def test_inventory_does_not_execute_unverified_runtime(self):
        user=SimpleNamespace(pw_uid=1000,pw_gid=1000)
        with patch.object(probe.pwd,'getpwnam',return_value=user), patch.object(
                probe,'directory_status',return_value='PROFILE_OWNED'), patch.object(
                probe,'binary_candidate',return_value=Path('/usr/bin/node')), patch.object(
                probe.subprocess,'run') as run:
            result=probe.profile('ubuntu',Path('/home/ubuntu'),Path('/home/ubuntu/.codex'),
                                 Path('/home/ubuntu/.local/share/slavik-codex/node_modules/.bin'))
        self.assertEqual(result['node'],'PRESENT_UNVERIFIED')
        run.assert_not_called()

    def test_output_has_only_allowlisted_states(self):
        with patch.object(probe,'held'), patch.object(
                probe.os,'uname',return_value=SimpleNamespace(machine='aarch64')), patch.object(probe,'profile',return_value={
                'node':'ABSENT','npm':'ABSENT','home':'ROOT_OWNED',
                'credential_directory':'MISSING','install_parent':'REVIEW_REQUIRED'}):
            output=io.StringIO()
            with contextlib.redirect_stdout(output):
                probe.main()
        self.assertNotIn('/home/',output.getvalue())
        self.assertNotIn('token',output.getvalue().lower())
        self.assertIn('"installation_action": "NONE"',output.getvalue())
        self.assertIn('"architecture": "linux-arm64"',output.getvalue())


if __name__ == '__main__':
    unittest.main()
