"""Supervisor transition and crash contracts; fake ports are not live acceptance."""
from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops import light_native_service_authority as a
from ops import light_native_service_supervisor as s
from tests.test_light_native_service_authority import equal_stat_rows, loaded_fixture


class MemoryJournal:
    """Fault-injection store, deliberately separate from Linux evidence tests."""
    def __init__(self):
        self.rows = []
        self.lock = threading.Lock()
        self.fail = None

    @contextmanager
    def exclusive(self):
        if not self.lock.acquire(blocking=False):
            raise s.LockBusy()
        try:
            yield
        finally:
            self.lock.release()

    def records(self):
        return deepcopy(self.rows)

    def intent(self, index, value):
        if index != len(self.rows):
            raise FileExistsError()
        self.rows.append((None if self.fail == 'empty-directory' else deepcopy(value), None))
        if self.fail in ('empty-directory', 'after-intent'):
            raise RuntimeError('private-token must not escape')

    def done(self, index, value):
        if self.fail == 'before-done':
            raise RuntimeError('private-credential must not escape')
        intent, done = self.rows[index]
        if done is not None:
            raise FileExistsError()
        self.rows[index] = (intent, deepcopy(value))
        if self.fail == 'after-done':
            raise RuntimeError('durable done but caller ACK lost')


class PrimaryMachine:
    def __init__(self, host):
        self.host = host
        self.reads = []
        self.calls = []
        self.cycles = []
        self.just_issued = False
        self.block = None
        self.fail = None
        self.readback_drift = False
        self.stale = False

    def observe(self, context):
        self.reads.append(context)
        count = len(self.cycles)
        previous = self.host.policy.predecessor if not count else dict(
            plan_sha256=self.cycles[-1]['plan_sha256'], terminal_sha256=self.cycles[-1]['terminal_sha256'],
            sequence=self.cycles[-1]['sequence'])
        state = ('COMPLETE_ACKNOWLEDGED' if self.just_issued else
                 'EXHAUSTED' if count == len(self.host.policy.plans) else 'READY')
        self.just_issued = False
        value = dict(version=1, kind='LIGHT_NATIVE_SERVICE_PRIMARY', manifest_sha256=self.host.accepted,
            policy_sha256=self.host.policy.digest, source=self.host.source,
            observed_at=int(time.time())-(90 if self.stale else 0), state=self.block or state,
            issued=count, predecessor=previous,
            cycle_sha256=None if not count else a.sha(a.encoded(self.cycles[-1])),
            live_verified=self.block is None, controls_restored=self.block is None)
        if self.readback_drift and count:
            value['predecessor']['terminal_sha256'] = '0'*64
        return value

    def issue(self, context):
        self.calls.append(context)
        if self.fail == 'before':
            raise RuntimeError('private-token and uncertain outcome')
        index = len(self.cycles)
        cycle = dict(audit='LIGHT_LANE_CYCLE', state='COMPLETE_HOLD',
            plan_sha256=self.host.policy.plans[index].plan_sha256,
            sequence=self.host.policy.predecessor['sequence']+index+1,
            dispatch_id=f'dispatch-{index}', task_id=f'task-{index}', terminal_sha256=str(index+1)*64,
            result_code='AUDIT_PASSED', controls_restored=True)
        self.cycles.append(cycle)
        self.just_issued = True
        if self.fail == 'after':
            raise RuntimeError('private-credential after actual completion')
        if self.fail == 'ack-missing': self.block = 'COMPLETE_ACK_MISSING'
        result = dict(audit='LIGHT_LANE_ISSUER', state='ISSUED_COMPLETE_HOLD', issued=index+1,
                      remaining=len(self.host.policy.plans)-index-1, cycle=deepcopy(cycle))
        if self.fail == 'result': result['cycle']['controls_restored'] = False
        return result


@contextmanager
def machine():
    with loaded_fixture() as f:
        primary = PrimaryMachine(f.host)
        supervisor = s.Supervisor(f.host, primary_observe=primary.observe, issue_once=primary.issue)
        journal = MemoryJournal()
        supervisor.journal = journal
        yield supervisor, primary, journal, f


class SupervisorContracts(unittest.TestCase):
    def test_lock_rejects_equal_tuple_ns_drift_before_and_after_scope(self):
        row, changed = equal_stat_rows()
        fake_fcntl = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=lambda *args: None)
        for stage in ('before', 'after'):
            snapshots = [changed] if stage == 'before' else [row, changed]
            with self.subTest(stage=stage), patch.dict(sys.modules, {'fcntl': fake_fcntl}), \
                 patch.object(a, 'root_directory'), patch.object(a.os, 'open', return_value=17), \
                 patch.object(a.os, 'close'), patch.object(a.os, 'fstat', return_value=row), \
                 patch.object(a.os, 'O_NOFOLLOW', 0x20000, create=True), \
                 patch.object(a.os, 'O_NONBLOCK', 0x800, create=True), \
                 patch.object(Path, 'stat', side_effect=snapshots):
                with self.assertRaisesRegex(a.Refusal, 'NATIVE_SERVICE_LOCK'):
                    with s.Journal(Path('accepted-root')).exclusive():
                        self.assertEqual(stage, 'after')

    def test_serial_completion_fresh_readback_restart_then_exhaustion(self):
        with machine() as (supervisor, primary, journal, f):
            self.assertEqual(supervisor.step(), s.Outcome('ISSUED_COMPLETE_HOLD', 1))
            self.assertEqual(len(primary.reads), 2)
            restarted = s.Supervisor(f.host, primary_observe=primary.observe, issue_once=primary.issue)
            restarted.journal = journal
            self.assertEqual(restarted.step(), s.Outcome('ISSUED_COMPLETE_HOLD', 2))
            self.assertEqual(restarted.step(), s.Outcome('EXHAUSTED', 2))
            self.assertEqual(restarted.step(), s.Outcome('EXHAUSTED', 2))
            self.assertEqual(len(primary.calls), 2)
            self.assertEqual(len(primary.reads), 6)
            self.assertEqual(journal.rows[1][0]['predecessor']['terminal_sha256'], '1'*64)

    def test_unknown_and_all_partial_or_missing_ack_states_never_issue(self):
        for state in sorted(s.BLOCKED):
            with self.subTest(state=state), machine() as (supervisor, primary, journal, f):
                primary.block = state
                self.assertEqual(supervisor.step(), s.Outcome('RECONCILIATION_REQUIRED', 0))
                self.assertFalse(primary.calls)
                self.assertFalse(journal.rows)

    def test_failed_or_lost_issue_ack_never_replays_even_if_primary_complete(self):
        for fault in ('before', 'after', 'ack-missing', 'result'):
            with self.subTest(fault=fault), machine() as (supervisor, primary, journal, f):
                primary.fail = fault
                first = supervisor.step()
                self.assertEqual(first.state, 'RECONCILIATION_REQUIRED')
                self.assertEqual(len(primary.calls), 1)
                self.assertIsNotNone(journal.rows[0][0])
                self.assertIsNone(journal.rows[0][1])
                primary.fail = None
                primary.block = None
                before_reads = len(primary.reads)
                restarted = s.Supervisor(f.host, primary_observe=primary.observe, issue_once=primary.issue)
                restarted.journal = journal
                self.assertEqual(restarted.step().state, 'RECONCILIATION_REQUIRED')
                self.assertGreater(len(primary.reads), before_reads)
                self.assertEqual(len(primary.calls), 1)
                self.assertNotIn('private-', str(first.public()))
                self.assertNotIn(b'private-', a.encoded(journal.rows))

    def test_durable_intent_write_fault_and_empty_directory_block_restart(self):
        for fault in ('empty-directory', 'after-intent', 'before-done'):
            with self.subTest(fault=fault), machine() as (supervisor, primary, journal, f):
                journal.fail = fault
                self.assertEqual(supervisor.step().state, 'RECONCILIATION_REQUIRED')
                calls = len(primary.calls)
                journal.fail = None
                self.assertEqual(supervisor.step().state, 'RECONCILIATION_REQUIRED')
                self.assertEqual(len(primary.calls), calls)

    def test_lost_local_done_ack_uses_existing_done_without_replaying_cycle(self):
        with machine() as (supervisor, primary, journal, f):
            journal.fail = 'after-done'
            self.assertEqual(supervisor.step().state, 'RECONCILIATION_REQUIRED')
            self.assertIsNotNone(journal.rows[0][1])
            journal.fail = None
            self.assertEqual(supervisor.step(), s.Outcome('ISSUED_COMPLETE_HOLD', 2))
            self.assertEqual(len(primary.calls), 2)

    def test_readback_mismatch_keeps_durable_intent_and_no_completion(self):
        with machine() as (supervisor, primary, journal, f):
            primary.readback_drift = True
            self.assertEqual(supervisor.step().state, 'RECONCILIATION_REQUIRED')
            self.assertIsNone(journal.rows[0][1])
            primary.readback_drift = False
            self.assertEqual(supervisor.step().state, 'RECONCILIATION_REQUIRED')
            self.assertEqual(len(primary.calls), 1)

    def test_stale_unbound_or_unverified_primary_refuses_before_intent(self):
        for fault in ('stale', 'manifest', 'source', 'policy', 'verified', 'extra', 'count', 'predecessor'):
            with self.subTest(fault=fault), machine() as (supervisor, primary, journal, f):
                if fault == 'stale': primary.stale = True
                original = primary.observe
                def changed(context):
                    value = original(context)
                    if fault == 'manifest': value['manifest_sha256'] = '0'*64
                    if fault == 'source': value['source'] = '0'*40
                    if fault == 'policy': value['policy_sha256'] = '0'*64
                    if fault == 'verified': value['live_verified'] = False
                    if fault == 'extra': value['credential'] = 'private-token'
                    if fault == 'count': value['issued'] = True
                    if fault == 'predecessor': value['predecessor']['terminal_sha256'] = '0'*64
                    return value
                supervisor.primary_observe = changed
                self.assertEqual(supervisor.step().state, 'RECONCILIATION_REQUIRED')
                self.assertFalse(primary.calls)
                self.assertFalse(journal.rows)

    def test_concurrent_step_cannot_observe_or_issue(self):
        with machine() as (supervisor, primary, journal, f), journal.exclusive():
            self.assertEqual(supervisor.step(), s.Outcome('BUSY', 0))
            self.assertFalse(primary.reads)
            self.assertFalse(primary.calls)

    def test_port_io_failure_cannot_masquerade_as_busy_lock(self):
        with machine() as (supervisor, primary, journal, f):
            def unavailable(context):
                raise BlockingIOError('private-credential')
            supervisor.issue_once = unavailable
            self.assertEqual(supervisor.step().state, 'RECONCILIATION_REQUIRED')
            self.assertIsNotNone(journal.rows[0][0])
            self.assertIsNone(journal.rows[0][1])

    def test_stop_expiry_and_process_drift_do_not_start_cycle(self):
        for fault in ('stop', 'expired', 'process'):
            with self.subTest(fault=fault), machine() as (supervisor, primary, journal, f):
                if fault == 'stop': (f.root/'STOP').write_text('stop')
                if fault == 'expired': f.host.monotonic_deadline_ns = 0
                if fault == 'process': a.host_identity.return_value = None
                self.assertEqual(supervisor.step().state, 'AUTHORITY_CLOSED')
                self.assertFalse(primary.calls)
                self.assertFalse(primary.reads)

    def test_corrupt_local_history_never_reinterpreted_as_ready(self):
        with machine() as (supervisor, primary, journal, f):
            self.assertEqual(supervisor.step().issued, 1)
            journal.rows[0][1]['terminal_sha256'] = '0'*64
            self.assertEqual(supervisor.step().state, 'RECONCILIATION_REQUIRED')
            self.assertEqual(len(primary.calls), 1)

    def test_missing_retained_context_blocks_advancing_completed_history(self):
        with machine() as (supervisor, primary, journal, f):
            self.assertEqual(supervisor.step().issued, 1)
            context_raw = a.encoded(journal.rows[0][0]['context'])
            del f.records[f.root/'contexts'/(a.sha(context_raw)+'.json')]
            self.assertEqual(supervisor.step().state, 'RECONCILIATION_REQUIRED')
            self.assertEqual(len(primary.calls), 1)

    def test_event_loop_remains_dormant_without_renewal_or_repair(self):
        with machine() as (supervisor, primary, journal, f):
            shutdown, wakeup = threading.Event(), threading.Event()
            with patch.object(wakeup, 'wait', side_effect=lambda _: shutdown.set()):
                result = supervisor.serve(shutdown, wakeup, heartbeat_seconds=1)
            self.assertEqual(result, s.Outcome('EXHAUSTED', 2))
            self.assertEqual(len(primary.calls), 2)
            self.assertFalse(hasattr(supervisor, 'repair'))
            self.assertFalse(hasattr(f.host, 'renew'))


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'NOTRUN: real Linux flock/create-only/fsync tests require Linux root')
class LinuxJournalEvidence(unittest.TestCase):
    def test_actual_lock_serializes_handles_and_done_is_create_only(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(a, 'root_directory'):
            root = Path(directory)
            root.chmod(0o700)
            first, second = s.Journal(root), s.Journal(root)
            with first.exclusive():
                with self.assertRaises(s.LockBusy):
                    with second.exclusive(): pass
                first.intent(0, dict(immutable='intent'))
                with self.assertRaises(FileExistsError): first.intent(0, {})
                first.done(0, dict(immutable='done'))
                with self.assertRaises(FileExistsError): first.done(0, {})
                self.assertEqual(first.records(), [({'immutable': 'intent'}, {'immutable': 'done'})])

    def test_actual_partial_file_remains_and_refuses_parse(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(a, 'root_directory'):
            root = Path(directory)
            root.chmod(0o700)
            journal = s.Journal(root)
            journal.intent(0, dict(immutable='intent'))
            path = root/'journal'/'0000'/'done.json'
            path.touch(mode=0o600)
            with self.assertRaises(a.Refusal): journal.records()
            self.assertTrue(path.exists())
            with self.assertRaises(FileExistsError): journal.done(0, {'no': 'retry'})


if __name__ == '__main__':
    unittest.main()
