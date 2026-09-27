"""Delayed startup must refuse before effects; no machine clock comparison."""
import os
import unittest
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from ops import native_maintenance_budgets as budgets
from ops import native_maintenance_checkpoint_transport as rpc
from ops import native_maintenance_stage_host as host
from ops import native_maintenance_runtime as runtime


class StartupTests(unittest.TestCase):
    def setUp(self):
        a, b = os.pipe()
        c, d = os.pipe()
        for fd in (a, b, c, d): self.addCleanup(os.close, fd)
        with patch.object(budgets.time, 'monotonic', return_value=1000.):
            self.runner = rpc.Channel(a, d, 'a'*64, seconds=budgets.STAGE_RPC_SECONDS)
            self.host = rpc.Channel(c, b, 'a'*64, seconds=budgets.STAGE_RPC_SECONDS)
        self.digest = 'b'*64

    def ready(self):
        self.host.send(budgets.startup_record('NATIVE_STAGE_READY', self.host, self.digest))

    def test_latest_admission_fits_whole_supervisor_and_cleanup_without_renewal(self):
        with patch.object(budgets.time, 'monotonic', return_value=1040.):
            self.ready()
            budgets.admit_runner(self.runner, self.digest, Mock())
            self.assertEqual(self.host.receive(), budgets.startup_record('NATIVE_STAGE_START', self.host, self.digest))
        self.assertEqual(self.runner.deadline, 1210.)
        # Even if PID1 started only immediately before READY, its cap, kill,
        # wrapper and cleanup fit with the explicit transport reserve.
        tail = max(budgets.STAGE_RUNTIME_SECONDS + budgets.STAGE_KILL_SECONDS,
                   budgets.STAGE_WRAPPER_SECONDS) + budgets.STAGE_CLEANUP_SECONDS
        self.assertEqual(self.runner.deadline - (1040. + tail), 12.)
        with patch.object(budgets.time, 'monotonic', return_value=1041.):
            with self.assertRaisesRegex(Exception, 'ADMISSION_REUSED'):
                budgets.admit_runner(self.runner, self.digest, Mock())
        self.assertTrue(self.runner.failed)

    def test_late_ready_or_slow_authorization_never_sends_start(self):
        for delay, slow_guard in [(40.001, False), (0., True)]:
            with self.subTest(delay=delay, guard=slow_guard):
                self.runner.stage_admission_started = False
                self.runner.failed = False
                clock = [1000. + delay]
                def guard():
                    if slow_guard: clock[0] = 1040.001
                with patch.object(budgets.time, 'monotonic', side_effect=lambda: clock[0]):
                    self.ready()
                    with patch.object(self.runner, 'send', wraps=self.runner.send) as send:
                        with self.assertRaisesRegex(Exception, 'STARTUP_TOO_SLOW'):
                            budgets.admit_runner(self.runner, self.digest, guard)
                        send.assert_not_called()
                self.assertTrue(self.runner.failed)
                self.assertEqual(self.runner.deadline, 1210.)

    def test_ready_binding_and_unexpected_early_rpc_are_refused(self):
        for record in [dict(kind='NATIVE_STAGE_READY', binding='c'*64, request_digest=self.digest),
                       dict(kind='NATIVE_STAGE_READY', binding='a'*64, request_digest='c'*64),
                       dict(kind='RPC', binding='a'*64),
                       dict(kind='NATIVE_STAGE_READY', binding='a'*64, request_digest=self.digest, extra=True)]:
            self.runner.stage_admission_started = False
            self.runner.failed = False
            with patch.object(budgets.time, 'monotonic', return_value=1001.):
                self.host.send(record)
                with patch.object(self.runner, 'send') as send:
                    with self.assertRaisesRegex(Exception, 'READY_BINDING'):
                        budgets.admit_runner(self.runner, self.digest, Mock())
                    send.assert_not_called()

    def test_host_start_checks_supervisor_and_original_run_after_wait(self):
        events = []
        supervisor = NS(assert_alive=lambda: events.append('supervisor'))
        guard = lambda: events.append('run')
        with patch.object(budgets.time, 'monotonic', return_value=1005.):
            self.runner.send(budgets.startup_record('NATIVE_STAGE_START', self.runner, self.digest))
            budgets.admit_host(self.host, self.digest, supervisor, guard)
            self.assertEqual(self.runner.receive(), budgets.startup_record('NATIVE_STAGE_READY', self.runner, self.digest))
        self.assertEqual(events, ['supervisor','run','supervisor','run'])
        self.assertEqual(self.host.deadline, 1210.)

    def test_expiry_while_waiting_for_start_prevents_admission(self):
        guard = Mock(side_effect=[None, RuntimeError('RUN_BINDING_EXPIRED')])
        with patch.object(budgets.time, 'monotonic', return_value=1005.):
            self.runner.send(budgets.startup_record('NATIVE_STAGE_START', self.runner, self.digest))
            with self.assertRaisesRegex(Exception, 'RUN_BINDING_EXPIRED'):
                budgets.admit_host(self.host, self.digest, NS(assert_alive=Mock()), guard)
        self.assertTrue(self.host.failed)
        self.assertEqual(self.host.deadline, 1210.)

    def test_production_entrypoint_never_enters_runtime_without_valid_start(self):
        @contextmanager
        def loaded(_): yield NS(connect=Mock()), None
        wheels, raw = b'CI-wheel', b'CI-request'
        envelope = dict(driver=rpc.pack(wheels), request=rpc.pack(raw), manifest=rpc.pack(b'manifest'),
                        credential='CI', token='CI', job_id=1)
        run = Mock(job_id=1, assert_running=Mock())
        packet = Mock(scope_digest='c'*64, stage='prepare', assert_bound=Mock())
        channel = Mock(binding='a'*64)
        channel.receive.return_value = dict(kind='NATIVE_STAGE_START', binding='wrong', request_digest=self.digest)
        with patch.object(host.os, 'getuid', return_value=0), \
             patch.object(host.os, 'uname', return_value=NS(nodename='autopilot-lite-vnic')), \
             patch.object(host, 'read_request', return_value=raw), \
             patch.object(host, 'AcceptedRequest'), patch.object(host, 'loaded_runtime', loaded), \
             patch.object(host.rpc, 'Channel', return_value=channel), \
             patch.object(host, 'StageRunBinding', return_value=run), patch.object(host, 'StageSupervisor', return_value=NS(assert_alive=Mock())), \
             patch.object(runtime, 'DerivedStagePacket', return_value=packet), \
             patch.object(runtime, 'run_identity', return_value={}), \
             patch('ops.native_maintenance_owner_attest.parameters', return_value={}), \
             patch.object(runtime, 'stage') as stage:
            with self.assertRaisesRegex(Exception, 'START_BINDING'):
                host._main('a'*40, 1, 1, self.digest, 'a'*64, host.bundle.digest(wheels), envelope, Mock())
            stage.assert_not_called()
        self.assertTrue(channel.failed)


if __name__ == '__main__': unittest.main()
