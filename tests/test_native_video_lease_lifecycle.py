"""Bounded native execution regressions. No SQL, network or runtime execution.

Protocol doubles only prove client ordering; engine fencing remains a separate
qualification. Injected clocks exercise deadlines without sleeps.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace, ModuleType
import sys
import unittest
from unittest.mock import patch, Mock

from universal_video import native_video_binding_provider as provider
from universal_video import owner_assignment_enrollment as enrollment

# Explicit fail-fast import dependencies for this offline Python runtime.
# These tests never qualify HTTP, Linux locks or production filesystem access.
_http = ModuleType('requests')
for _name in ('get', 'post', 'put', 'patch', 'delete', 'request', 'Session'):
    setattr(_http, _name, Mock(side_effect=AssertionError('HTTP_FORBIDDEN')))
_fcntl = ModuleType('fcntl')
_fcntl.flock = Mock(side_effect=AssertionError('NATIVE_FLOCK_NOT_QUALIFIED'))
with patch.dict(sys.modules, {'requests': _http, 'fcntl': _fcntl}):
    from universal_video import neon_worker as worker


KEY = 'db-login:autopilot_light_worker_login'
TASK = '00000000-0000-4000-8000-000000000001'
JOB = '00000000-0000-4000-8000-000000000002'
BATCH = '00000000-0000-4000-8000-000000000003'
TOKEN = '00000000-0000-4000-8000-000000000004'


class Clock:
    wall = 2_000_000_000.
    mono = 100.
    def time(self): return self.wall
    def monotonic(self): return self.mono


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def inputs(clock):
    claim = dict(job_id=JOB, batch_id=BATCH, lease_token=TOKEN, attempt_count=1,
                 lease_expires_at=stamp(clock.wall + 120))
    snapshot = dict(schema='native-video-db-snapshot/v1', task_id=TASK,
                    lease_epoch=1, worker_id=KEY, claim=deepcopy(claim),
                    expires_at_utc=stamp(clock.wall + 100),
                    binding_sha256='a'*64, assignment_sha256='b'*64)
    return snapshot, claim


class PgHeartbeatDouble:
    """No SQL execution; native session transaction/identity protocol only."""
    def __init__(self):
        self.info = SimpleNamespace(host=provider.HOST, port=5432,
            transaction_status=SimpleNamespace(name='IDLE'))
        self.calls = 0
        self.lost = False
    @contextmanager
    def transaction(self):
        self.info.transaction_status.name = 'INTRANS'
        try:
            yield self
            if self.lost: raise OSError('lost commit')
        finally: self.info.transaction_status.name = 'IDLE'
    @contextmanager
    def cursor(self): yield self
    def execute(self, sql, params=None):
        if sql in enrollment.LOCAL_SETTINGS: self.rows = []
        elif sql == enrollment.SQL_IDENTITY:
            self.rows = [dict(database_name='neondb', session_login=provider.WORKER,
                             effective_login=provider.WORKER, backend_pid=17)]
        elif sql == provider.SQL['heartbeat']:
            self.calls += 1
            self.rows = [dict(result=dict(renewed=True))]
        else: raise AssertionError('unexpected protocol SQL')
    def fetchall(self): return self.rows


class HeartbeatSessionTests(unittest.TestCase):
    def session(self):
        pg = PgHeartbeatDouble()
        target = enrollment.EnrollmentTarget('neondb', provider.OWNER,
            provider.WORKER, provider.HOST, 5432, 'a'*64)
        return pg, provider.NativeVideoSession(connection=pg, target=target, worker=True)

    def test_two_acknowledged_heartbeats_are_allowed(self):
        pg, session = self.session()
        session.call('heartbeat', (TASK, 1, 120))
        session.call('heartbeat', (TASK, 1, 120))
        self.assertEqual(pg.calls, 2)

    def test_unknown_heartbeat_remains_sticky_and_never_replays(self):
        pg, session = self.session()
        pg.lost = True
        with self.assertRaises(provider.NativeBindingRefused):
            session.call('heartbeat', (TASK, 1, 120))
        pg.lost = False
        with self.assertRaises(provider.NativeBindingRefused):
            session.call('heartbeat', (TASK, 1, 120))
        self.assertEqual(pg.calls, 1)


class NativeBudgetTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.snapshot, self.claim = inputs(self.clock)

    def budget(self, **changes):
        from universal_video.native_video_lease import NativeExecutionBudget
        options = dict(snapshot=self.snapshot, claim=self.claim, required_seconds=60,
                       cleanup_seconds=5, wall=self.clock.time, clock=self.clock.monotonic)
        options.update(changes)
        return NativeExecutionBudget.from_snapshot(**options)

    def test_bound_uses_earlier_snapshot_deadline_and_cleanup_reserve(self):
        budget = self.budget()
        self.assertEqual(budget.work_seconds, 60)
        self.assertAlmostEqual(budget.remaining(), 95.)
        budget.require_work_window(self.claim, KEY)

    def test_earlier_video_lease_limits_budget(self):
        self.claim['lease_expires_at'] = stamp(self.clock.wall + 30)
        self.snapshot['claim'] = deepcopy(self.claim)
        with self.assertRaisesRegex(RuntimeError, 'BUDGET_INSUFFICIENT'):
            self.budget()

    def test_missing_or_nonpositive_declared_duration_is_refused(self):
        for value in (None, False, 0, -1, float('inf'), float('nan')):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'DECLARED_BOUND'):
                self.budget(required_seconds=value)

    def test_insufficient_or_expired_snapshot_refuses_before_work(self):
        for delta in (0, -1, 60):
            self.snapshot['expires_at_utc'] = stamp(self.clock.wall + delta)
            with self.subTest(delta=delta), self.assertRaises(RuntimeError): self.budget()

    def test_task_epoch_worker_and_enrollment_binding_are_required(self):
        for key, value in [('task_id', 'bad'), ('lease_epoch', True),
                           ('lease_epoch', 2), ('worker_id', 'other'),
                           ('binding_sha256', None), ('assignment_sha256', None)]:
            changed = self.snapshot | {key: value}
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                self.budget(snapshot=changed)

    def test_snapshot_claim_cannot_substitute_lease(self):
        changed = deepcopy(self.snapshot)
        changed['claim']['lease_token'] = TASK
        with self.assertRaisesRegex(RuntimeError, 'CLAIM_BINDING'):
            self.budget(snapshot=changed)

    def test_budget_checks_exact_claim_and_worker(self):
        budget = self.budget()
        with self.assertRaises(RuntimeError): budget.check(self.claim | {'job_id': TASK}, KEY)
        with self.assertRaises(RuntimeError): budget.check(self.claim, 'other')

    def test_inputs_detached_and_deadline_does_not_follow_wall_clock_backwards(self):
        budget = self.budget()
        self.snapshot['expires_at_utc'] = stamp(self.clock.wall + 1000)
        self.clock.wall -= 100
        self.clock.mono += 96
        with self.assertRaisesRegex(RuntimeError, 'EXPIRED'): budget.check(self.claim, KEY)

    def test_expired_budget_never_revives_after_clock_reset(self):
        budget = self.budget()
        self.clock.mono += 96
        with self.assertRaises(RuntimeError): budget.check(self.claim, KEY)
        self.clock.mono -= 96
        with self.assertRaises(RuntimeError): budget.check(self.claim, KEY)

    def test_elapsed_bootstrap_can_refuse_full_work_window(self):
        budget = self.budget()
        self.clock.mono += 40
        self.clock.wall += 40
        with self.assertRaisesRegex(RuntimeError, 'BUDGET_INSUFFICIENT'):
            budget.require_work_window(self.claim, KEY)

    def test_native_process_without_budget_refuses_before_any_side_effect(self):
        with patch.object(worker, 'access_token', side_effect=AssertionError('must not read Drive')):
            with self.assertRaisesRegex(RuntimeError, 'NATIVE_EXECUTION_BUDGET_REQUIRED'):
                worker.process_claim('not-a-dsn', self.claim, KEY,
                                     processor=lambda _: self.fail('processor called'))

    def test_budget_does_not_enable_legacy_finish_or_retry_without_private_handoff(self):
        budget = self.budget()
        with patch.object(worker, 'access_token', side_effect=AssertionError('must not read Drive')):
            with self.assertRaisesRegex(RuntimeError, 'NATIVE_PRIVATE_HANDOFF_REQUIRED'):
                worker.process_claim('not-a-dsn', self.claim, KEY, native_budget=budget,
                                     processor=lambda _: self.fail('processor called'))

    def test_native_heartbeat_never_calls_legacy_renewal(self):
        budget = self.budget()
        heartbeat = worker._Heartbeat('not-a-dsn', self.claim, KEY, native_budget=budget)
        waits = []
        def wait(seconds):
            waits.append(seconds)
            return len(waits) > 1
        heartbeat.stop = SimpleNamespace(wait=wait)
        with patch.object(worker, 'heartbeat_job', side_effect=AssertionError('legacy renewal')):
            heartbeat._run()
        self.assertIsNone(heartbeat.error)
        self.assertLess(waits[0], 120)

    def test_native_budget_passes_through_original_private_callsite(self):
        from universal_video.private_one_job_entrypoint import _process_private_claim
        budget = self.budget()
        with patch.dict(sys.modules, {'universal_video.neon_worker': worker}), \
                patch.object(worker, 'process_claim', return_value={'result': 'data'}) as call:
            result = _process_private_claim('dsn', self.claim, KEY, subset_adapter=None,
                staging_handoff=None, native_budget=budget)
        self.assertEqual(result, {'result': 'data'})
        self.assertIs(call.call_args.kwargs['native_budget'], budget)

    def test_native_processing_timeout_uses_explicit_bound_not_six_hour_default(self):
        budget = self.budget()
        with patch.object(worker.signal, 'SIGALRM', 14, create=True), \
                patch.object(worker.signal, 'ITIMER_REAL', 0, create=True), \
                patch.object(worker.signal, 'getsignal', return_value='previous'), \
                patch.object(worker.signal, 'signal') as handler, \
                patch.object(worker.signal, 'setitimer', create=True) as timer:
            with worker._processing_timeout(native_budget=budget): pass
        self.assertEqual(timer.call_args_list[0].args, (0, 60.))
        self.assertEqual(timer.call_args_list[-1].args, (0, 0))
        self.assertEqual(handler.call_args_list[-1].args, (14, 'previous'))

    def test_legacy_heartbeat_schedule_is_unchanged(self):
        heartbeat = worker._Heartbeat('dsn', self.claim, 'legacy-worker')
        waits = []
        heartbeat.stop = SimpleNamespace(wait=lambda seconds: waits.append(seconds) or True)
        heartbeat._run()
        self.assertEqual(waits, [300])

    def test_expired_native_heartbeat_records_failure_without_any_renewal(self):
        budget = self.budget()
        heartbeat = worker._Heartbeat('not-a-dsn', self.claim, KEY, native_budget=budget)
        self.clock.mono += 96
        self.clock.wall += 96
        waits = []
        heartbeat.stop = SimpleNamespace(wait=lambda seconds: waits.append(seconds) or len(waits) > 1)
        with patch.object(worker, 'heartbeat_job', side_effect=AssertionError('legacy renewal')):
            heartbeat._run()
        self.assertRegex(str(heartbeat.error), 'EXPIRED')

    def test_run_callsite_refuses_native_budget_absence_before_preparation(self):
        from universal_video import private_one_job_entrypoint as entry
        with patch.object(entry, 'prepare_private_one_job', side_effect=AssertionError('DB preparation')):
            with self.assertRaisesRegex(RuntimeError, 'NATIVE_EXECUTION_BUDGET_REQUIRED'):
                entry.run_private_one_job(queue_dsn='dsn', expected_database='neondb',
                    expected_login=provider.WORKER, claim=self.claim, expected_batch_id=BATCH,
                    expected_job_id=JOB, expected_lease_token=TOKEN, expected_attempt_count=1,
                    expected_source_file_id='source123456', worker_key=KEY,
                    admission_id=TASK, source_fingerprint='c'*64, subset_adapter=None)

    def resident_arguments(self):
        return dict(queue_dsn='dsn', expected_database='neondb', expected_login=provider.WORKER,
                    expected_batch_id=BATCH, expected_job_id=JOB, expected_source_file_id='source123456',
                    expected_attempt_count=1, admission_id=TASK, source_fingerprint='c'*64,
                    approval_waiter=Mock(), worker_key=KEY, claim_reserver=Mock(),
                    claim_publisher=Mock(), claim_committer=Mock())

    def test_unwired_native_resident_refuses_before_legacy_claim_or_any_io(self):
        for budget in (None, self.budget()):
            args = self.resident_arguments()
            with patch.object(worker, 'shared_workload_lock', side_effect=AssertionError('native IO')):
                with self.assertRaisesRegex(RuntimeError, 'NATIVE_ORIGINAL_CLAIM_BINDING_REQUIRED'):
                    worker.process_one_private_neon(**args, native_budget=budget)
            args['claim_reserver'].assert_not_called()
            args['claim_publisher'].assert_not_called()
            args['claim_committer'].assert_not_called()

    def test_legacy_resident_cannot_accept_native_budget(self):
        args = self.resident_arguments() | {'worker_key': 'legacy-worker'}
        with self.assertRaisesRegex(RuntimeError, 'NATIVE_EXECUTION_WRONG_LANE'):
            worker.process_one_private_neon(**args, native_budget=self.budget())
        args['claim_reserver'].assert_not_called()


if __name__ == '__main__': unittest.main()
