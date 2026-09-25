"""The native CLI probe must keep HOLD and suppress provider output."""
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops import oracle_light_native_cli_readiness as probe


class ReadinessTests(unittest.TestCase):
    def test_absent_cli_never_executes(self):
        with patch.object(Path, 'is_file', return_value=False), patch.object(
                probe.subprocess, 'run') as run:
            self.assertEqual(probe.profile_status('ubuntu', Path('/absent'),
                                                   Path('/home/ubuntu'), Path('/home/ubuntu/.codex')),
                             'CLI_ABSENT')
            run.assert_not_called()

    def test_ready_status_never_prints_cli_output(self):
        output = io.StringIO()
        with patch.object(Path, 'is_file', return_value=True), patch.object(
                probe.os, 'access', return_value=True), patch.object(
                probe.pwd, 'getpwnam', return_value=SimpleNamespace(pw_gid=1000, pw_uid=1000)), patch.object(
                probe.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout='PRIVATE_ACCOUNT',
                    stderr='Logged in using ChatGPT\nPRIVATE_ERROR')) as run:
            with contextlib.redirect_stdout(output):
                state = probe.profile_status('ubuntu', Path('/cli'), Path('/home/ubuntu'),
                                             Path('/home/ubuntu/.codex'))
        self.assertEqual(state, 'CLI_AUTH_READY')
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(run.call_args.kwargs['env']['PATH'],
                         '/home/ubuntu/.nvm/versions/node/v22.23.2/bin:/usr/local/bin:/usr/bin:/bin')

    def test_non_hold_prevents_any_cli_call(self):
        output = io.StringIO()
        with patch.object(probe.os, 'geteuid', return_value=0), patch.object(
                probe.os, 'uname', return_value=SimpleNamespace(nodename='autopilot-lite-vnic')), patch.object(
                probe.subprocess, 'run', return_value=SimpleNamespace(
                    stdout='ActiveState=active\nSubState=running\n'
                           'Environment=AUTOPILOT_ADMISSION_MODE=HOLD AUTOPILOT_ADMISSION_MODE=ACTIVE\n')), patch.object(
                probe, 'profile_status') as profile:
            with contextlib.redirect_stdout(output):
                with self.assertRaisesRegex(ValueError, 'NOT_ACTIVE_HOLD'):
                    probe.main()
        profile.assert_not_called()
        self.assertEqual(output.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
