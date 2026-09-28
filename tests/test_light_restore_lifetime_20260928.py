"""Offline incident PID1/RPC budget checks; never invokes systemd."""
import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ops.incident import light_restore_lifetime_20260928 as life


class LifetimeTests(unittest.TestCase):
    def test_fixed_supervised_command_has_hard_limit_and_no_restart(self):
        unit = 'bridge-native-ro-' + 'a' * 12 + '-123-1-' + 'b' * 16 + '.service'
        command = life.command(unit, 'print(1)')
        self.assertEqual(command[:4], ['/usr/bin/systemd-run', '--quiet', '--wait', '--pipe'])
        self.assertIn('--property=RuntimeMaxSec=600s', command)
        self.assertIn('--property=Restart=no', command)
        self.assertIn('--property=KillMode=control-group', command)
        self.assertIn('--property=NoNewPrivileges=yes', command)
        self.assertIn('--description=Light incident restore AFTER', command)
        self.assertEqual(sum(arg.startswith('--description=') for arg in command), 1)
        self.assertEqual(command[-6:], ['/usr/bin/python3', '-I', '-B', '-S', '-c', 'print(1)'])
        with self.assertRaisesRegex(RuntimeError, 'INCIDENT_UNIT_COMMAND'):
            life.command(unit, '')

    def test_wrapper_timeout_keeps_cleanup_owned_to_one_unit(self):
        unit = 'bridge-native-ro-' + 'a' * 12 + '-123-1-' + 'b' * 16 + '.service'
        with patch.object(life.os, 'getuid', return_value=0), \
             patch.object(life, 'new_unit', return_value=unit), \
             patch.object(life, 'cleanup') as cleanup, \
             patch.object(life.subprocess, 'run', side_effect=subprocess.TimeoutExpired('systemd-run', 608)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                life.managed('print(1)', 'YWJj', 'a' * 40, '123-1')
            self.assertEqual(run.call_args.kwargs['timeout'], 608)
            cleanup.assert_called_once_with(unit)

    def test_rpc_clock_is_one_fixed_deadline_and_latches_expiry(self):
        reader, writer = os.pipe()
        try:
            with patch.object(life.time, 'monotonic', return_value=100):
                channel = life.channel(reader, writer, 'a' * 64)
            self.assertEqual(channel.deadline, 800)
            self.assertEqual(type(channel).__bases__[0].__name__, 'Channel')
            with patch.object(life.time, 'monotonic', return_value=799):
                channel.alive()
            self.assertEqual(channel.deadline, 800)
            with patch.object(life.time, 'monotonic', return_value=800):
                with self.assertRaisesRegex(Exception, 'RPC_UNAVAILABLE'):
                    channel.alive()
            self.assertEqual(channel.deadline, 800)
        finally:
            os.close(reader)
            os.close(writer)

    def test_rpc_rejects_invalid_binding_and_same_fd(self):
        reader, writer = os.pipe()
        try:
            with self.assertRaisesRegex(RuntimeError, 'INCIDENT_RPC_PROFILE'):
                life.channel(reader, writer, 'bad')
            with self.assertRaisesRegex(RuntimeError, 'INCIDENT_RPC_PROFILE'):
                life.channel(reader, reader, 'a' * 64)
        finally:
            os.close(reader)
            os.close(writer)

    def test_runtime_profile_refuses_changed_pid1_limit(self):
        unit = 'bridge-native-ro-' + 'a' * 12 + '-123-1-' + 'b' * 16 + '.service'
        state = dict(life.PROPERTIES, RuntimeMaxUSec='2min 20s')
        with patch.object(life, 'show', return_value=state):
            with self.assertRaisesRegex(RuntimeError, 'INCIDENT_UNIT_PROFILE'):
                life.identity(unit)

    def test_inventory_refusals_distinguish_query_row_and_missing_self(self):
        unit = 'bridge-native-ro-' + 'a' * 12 + '-123-1-' + 'b' * 16 + '.service'
        supervisor = object.__new__(life.Supervisor)
        supervisor.unit = unit
        cases = (
            (SimpleNamespace(returncode=0, stdout=b'x' * 65537), 'INCIDENT_UNIT_INVENTORY_QUERY'),
            (SimpleNamespace(returncode=0, stdout=b'bad-row\n'), 'INCIDENT_UNIT_INVENTORY_ROW'),
            (SimpleNamespace(returncode=0, stdout=b''), 'INCIDENT_UNIT_INVENTORY_INCOMPLETE'),
        )
        with patch.object(life.Supervisor, 'assert_alive'), patch.object(life, 'ctl') as ctl:
            for response, code in cases:
                with self.subTest(code=code):
                    ctl.return_value = response
                    with self.assertRaisesRegex(RuntimeError, '^' + code + '$'):
                        supervisor.assert_exclusive()


if __name__ == '__main__':
    unittest.main()
