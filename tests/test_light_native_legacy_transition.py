"""Portable trusted-port contracts. No production/systemd/SQL authority is used."""
import dataclasses
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location('legacy_transition_under_test',
    Path(__file__).resolve().parents[1] / 'ops' / 'light_native_legacy_transition.py')
k = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = k
SPEC.loader.exec_module(k)


class Clock:
    now = 1000
    elapsed = 0
    boot = '00000000-0000-0000-0000-000000000001'

    def wall(self):
        return self.now

    def mono(self):
        return self.elapsed

    def boottime(self):
        return int((1000000 + self.elapsed) * 10**9)


class MemoryJournal:
    """Create-only model; real root-private descriptor tests are separate."""
    def __init__(self, events):
        self.rows, self.events = {}, events
        self.fail_role = None

    def retain(self, role, raw):
        if role in self.rows:
            raise FileExistsError(role)
        self.rows[role] = raw
        self.events.append('retain:' + role)
        if role == self.fail_role:
            self.rows[role] = b'{'
            raise OSError('simulated partial durable write')

    def read(self, role):
        return self.rows.get(role)


def before(now=1000):
    source = '1' * 40
    release = '/srv/synthetic/releases/' + source
    return k.Snapshot(now, 'synthetic.invalid', 'synthetic-legacy.service', source,
        '2' * 64, release, 'RUNNING', 'ACTIVE', 101, 'a' * 32, 100,
        '3' * 64, '4' * 64, k.drop_bytes(release, 'ACTIVE'))


def native(now=1000):
    return k.NativeHold(now, 'synthetic-native.service', 'RUNNING', 'HOLD', 202,
                        'b' * 32, 200, '5' * 64)


def request(*, start_hold=False, recovery=False, expiry=1200, **changes):
    value = dict(version=1, kind='LEGACY_HOLD_TRANSITION', source='6' * 40,
        controller_sha256='7' * 64, legacy_source='1' * 40,
        legacy_bundle_sha256='2' * 64, original_baseline_sha256='8' * 64,
        original_inventory_sha256='9' * 64, before=before().binding(),
        native_before=native().binding(), start_hold=start_hold, max_seconds=60,
        cleanup_script_sha256='c' * 64, forward_agreement=None,
        active_recovery_agreement=None)
    value.update(changes)
    value['forward_agreement'] = dict(version=1, kind='LEGACY_TRANSITION_FORWARD',
        operation_sha256=k.scope_sha256(value, 'FORWARD'), not_before=900,
        expires_at=expiry, evidence_sha256='d' * 64)
    if recovery:
        value['active_recovery_agreement'] = dict(version=1,
            kind='LEGACY_TRANSITION_ACTIVE_RECOVERY',
            operation_sha256=k.scope_sha256(value, 'RECOVERY'), not_before=950,
            expires_at=1250, evidence_sha256='e' * 64)
    raw = k.encoded(value)
    return k.Request.load(raw, k.sha(raw),
        accepted_forward_agreement_sha256=k.sha(k.encoded(value['forward_agreement'])),
        accepted_active_recovery_agreement_sha256=(
            k.sha(k.encoded(value['active_recovery_agreement'])) if recovery else None))


class Ports:
    """Synthetic fixed targets; simulated records do not prove real drain."""
    def __init__(self, clock, events):
        self.clock, self.events = clock, events
        self.current, self.native = before(), native()
        self.cleanup_phase = 'MAIN'
        self.armed = None
        self.forward_error = False
        self.recovery_error = False
        self.ack_loss = None
        self.drain_change = None
        self.effect_hook = None
        self.snapshot_hook = None
        self.native_hook = None
        self.verify_hook = None
        self.effect_counts = {'stop': 0, 'drop': 0, 'reload': 0, 'start:HOLD': 0, 'start:ACTIVE': 0}

    def guard_forward(self, req):
        self.events.append('guard:forward')
        if self.forward_error:
            raise RuntimeError('source or STOP guard denied')

    def guard_recovery(self, req):
        self.events.append('guard:recovery')
        if self.recovery_error:
            raise RuntimeError('recovery source or STOP guard denied')

    def snapshot(self, req):
        self.events.append('snapshot')
        if self.snapshot_hook:
            self.snapshot_hook(self)
        return dataclasses.replace(self.current, observed_at=self.clock.now)

    def native_hold(self, req):
        self.events.append('native')
        if self.native_hook:
            self.native_hook(self)
        return dataclasses.replace(self.native, observed_at=self.clock.now)

    def arm_cleanup(self, req):
        self.events.append('arm')
        self.armed = k.ArmedCleanup(self.clock.now, req.digest,
            'synthetic-transition-supervisor.service', 303, 'c' * 32, 300,
            req.value['cleanup_script_sha256'], req.digest, req.value['max_seconds'], 20)
        return self.armed

    def verify_cleanup(self, req, original):
        self.events.append('verify')
        if self.verify_hook:
            self.verify_hook(self)
        return dataclasses.replace(self.armed, observed_at=self.clock.now,
            phase=self.cleanup_phase, pid=404 if self.cleanup_phase == 'CLEANUP' else 303,
            start_ticks=400 if self.cleanup_phase == 'CLEANUP' else 300)

    def lifecycle_clock(self, req):
        self.events.append('lifecycle-clock')
        return k.LifecycleClock(self.clock.now, self.clock.boot, self.clock.boottime())

    def drain(self, req, snapshot):
        self.events.append('drain')
        phase_pid, ticks = (404, 400) if self.cleanup_phase == 'CLEANUP' else (303, 300)
        value = k.DrainProof(self.clock.now, req.digest, k.sha(k.encoded(snapshot.binding())),
            dict(identity_sha256='d' * 64, snapshot_sha256=k.sha(k.encoded(snapshot.binding())),
                native_enabled=False, legacy_backends=[], live_tasks=[], active_outbox=[],
                worker_leases=[], probe_leases=[]),
            dict(evidence_sha256='f' * 64, inflight=[], unknown=[]),
            dict(evidence_sha256='0' * 64, owner_pid=phase_pid, owner_start_ticks=ticks,
                exclusive=True, active_consumers=[]))
        return self.drain_change(value) if self.drain_change else value

    def _effect(self, name, callback):
        self.effect_counts[name] += 1
        self.events.append('effect:' + name)
        if self.effect_hook:
            self.effect_hook(self, name)
        if self.ack_loss == (name, 'BEFORE'):
            raise OSError('unknown ACK before simulated effect')
        callback()
        if self.ack_loss == (name, 'AFTER'):
            raise OSError('unknown ACK after simulated effect')

    def stop_expected(self, req, snapshot):
        def stop():
            if self.current.binding() != snapshot.binding():
                raise RuntimeError('expected invocation changed')
            self.current = dataclasses.replace(self.current, state='STOPPED', pid=0,
                start_ticks=0, cgroup_empty=True)
        self._effect('stop', stop)

    def replace_drop_cas(self, req, old, new):
        def swap():
            if self.current.state != 'STOPPED' or self.current.drop_bytes != old:
                raise RuntimeError('CAS did not match')
            self.current = dataclasses.replace(self.current, drop_bytes=new, needs_reload=True)
        self._effect('drop', swap)

    def reload_expected(self, req):
        def reload():
            if self.current.state != 'STOPPED':
                raise RuntimeError('reload expected stopped')
            admission = 'HOLD' if self.current.drop_bytes == k.drop_bytes(self.current.release, 'HOLD') else 'ACTIVE'
            self.current = dataclasses.replace(self.current, admission=admission, needs_reload=False)
        self._effect('reload', reload)

    def start_once(self, req, *, admission):
        def start():
            if self.current.state != 'STOPPED' or self.current.admission != admission or self.current.needs_reload:
                raise RuntimeError('start expected loaded stopped configuration')
            pid = 500 + sum(self.effect_counts.values())
            self.current = dataclasses.replace(self.current, state='RUNNING', pid=pid,
                invocation_id=format(pid, '032x'), start_ticks=pid * 10, cgroup_empty=False)
        self._effect('start:' + admission, start)


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.events = Clock(), []
        self.journal = MemoryJournal(self.events)
        self.ports = Ports(self.clock, self.events)

    def run_forward(self, req=None):
        self.req = req or request()
        return k.run_transition(self.req, ports=self.ports, journal=self.journal,
            clock=self.clock.wall, monotonic=self.clock.mono, boottime_ns=self.clock.boottime)

    def contain(self):
        self.ports.cleanup_phase = 'CLEANUP'
        return k.contain_transition(self.req, ports=self.ports, journal=self.journal,
                                   clock=self.clock.wall)

    def rollback(self):
        self.ports.cleanup_phase = 'CLEANUP'
        return k.rollback_transition(self.req, ports=self.ports, journal=self.journal,
                                    clock=self.clock.wall, boottime_ns=self.clock.boottime)

    def test_stopped_hold_order_and_no_authority_flags(self):
        result = self.run_forward()
        self.assertEqual(result.state, 'COMPLETED_STOPPED_HOLD')
        self.assertEqual(self.ports.current.state, 'STOPPED')
        self.assertEqual(self.ports.current.admission, 'HOLD')
        self.assertEqual(self.ports.effect_counts, {'stop': 1, 'drop': 1, 'reload': 1, 'start:HOLD': 0, 'start:ACTIVE': 0})
        order = ['retain:request', 'retain:intent', 'arm', 'retain:armed',
                 'retain:stop-intent', 'effect:stop', 'retain:stop-observed', 'drain',
                 'retain:hold-drop-intent', 'effect:drop', 'retain:reload-intent', 'effect:reload', 'retain:completed']
        indexes = [self.events.index(item) for item in order]
        self.assertEqual(indexes, sorted(indexes))
        for index, item in enumerate(self.events):
            if item.startswith('effect:'):
                self.assertIn('native', self.events[:index])
                self.assertIn('native', self.events[index + 1:])
        for field in ('historical_chain_verified', 'original_baseline_matched', 'incident_closed',
                      'replay_authorized', 'native_admission_authorized', 'new_task_authorized'):
            self.assertIs(result.public()[field], False)
        self.assertEqual(self.req.value['original_baseline_sha256'], '8' * 64)

    def test_optional_hold_start_once_and_observed_cleanup(self):
        result = self.run_forward(request(start_hold=True))
        self.assertEqual(result.state, 'COMPLETED_RUNNING_HOLD')
        self.assertEqual(self.ports.effect_counts['start:HOLD'], 1)
        count = dict(self.ports.effect_counts)
        self.assertEqual(self.contain().state, 'SAFE_RUNNING_HOLD')
        self.assertEqual(count, self.ports.effect_counts)
        self.assertEqual(k.run_transition(self.req, ports=self.ports, journal=self.journal,
            clock=self.clock.wall, monotonic=self.clock.mono).state, 'UNKNOWN')
        self.assertEqual(count, self.ports.effect_counts)

    def test_request_exact_canonical_pins_and_agreement_scope(self):
        req = request(recovery=True)
        for raw in (req.raw + b'\n', req.raw.replace(b'"version":1', b'"version":1,"version":1', 1)):
            with self.assertRaises(k.Refusal):
                k.Request.load(raw, k.sha(raw), accepted_forward_agreement_sha256=req.accepted_forward_agreement_sha256,
                    accepted_active_recovery_agreement_sha256=req.accepted_active_recovery_agreement_sha256)
        with self.assertRaises(k.Refusal):
            dataclasses.replace(req, digest='f' * 64).check()
        value = req.value
        value['start_hold'] = True
        raw = k.encoded(value)
        with self.assertRaises(k.Refusal):
            k.Request.load(raw, k.sha(raw), accepted_forward_agreement_sha256=req.accepted_forward_agreement_sha256,
                accepted_active_recovery_agreement_sha256=req.accepted_active_recovery_agreement_sha256)

    def test_invalid_current_snapshot_and_foreign_native_are_refused(self):
        changes = [dict(pid=True), dict(control_pid=99), dict(state='ACTIVE'),
            dict(drop_bytes=b'[Service]\nEnvironment=AUTOPILOT_ADMISSION_MODE=ACTIVE\n'),
            dict(invocation_id='d' * 32), dict(immutable_config_sha256='a' * 64),
            dict(needs_reload=True), dict(cgroup_empty=True)]
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.ports.current = dataclasses.replace(self.ports.current, **change)
                self.assertEqual(self.run_forward().state, 'REFUSED_BEFORE_EFFECT')
                self.assertFalse(any(self.ports.effect_counts.values()))
        self.setUp()
        self.ports.native = dataclasses.replace(self.ports.native, configuration_sha256='a' * 64)
        self.assertEqual(self.run_forward().state, 'REFUSED_BEFORE_EFFECT')
        self.assertFalse(any(self.ports.effect_counts.values()))

    def test_preflight_denial_and_request_alone_leave_active_untouched(self):
        for mode in ('expired', 'guard', 'request-only', 'arm-failure'):
            with self.subTest(mode=mode):
                self.setUp()
                self.req = request(expiry=999 if mode == 'expired' else 1200)
                if mode == 'guard':
                    self.ports.forward_error = True
                if mode == 'request-only':
                    self.journal.retain('request', self.req.raw)
                if mode == 'arm-failure':
                    self.ports.arm_cleanup = lambda req: True
                self.assertEqual(self.run_forward(self.req).state, 'REFUSED_BEFORE_EFFECT')
                self.assertEqual(self.contain().state, 'ORIGINAL_ACTIVE_UNTOUCHED')
                self.assertFalse(any(self.ports.effect_counts.values()))
                self.assertEqual(self.ports.current.binding(), before().binding())

    def test_refused_preflight_is_durable_attempt_barrier(self):
        self.ports.forward_error = True
        self.assertEqual(self.run_forward().state, 'REFUSED_BEFORE_EFFECT')
        self.assertIn('unknown', self.journal.rows)
        self.ports.forward_error = False
        self.assertEqual(self.run_forward(self.req).state, 'REFUSED_BEFORE_EFFECT')
        self.assertFalse(any(self.ports.effect_counts.values()))
        self.assertEqual(self.contain().state, 'ORIGINAL_ACTIVE_UNTOUCHED')

    def test_partial_stop_intent_never_performs_or_retries_stop(self):
        self.journal.fail_role = 'stop-intent'
        self.assertEqual(self.run_forward().state, 'UNKNOWN')
        self.assertEqual(self.ports.effect_counts['stop'], 0)
        self.assertEqual(self.contain().state, 'UNKNOWN')
        self.assertEqual(self.ports.effect_counts['stop'], 0)

    def test_partial_stop_intent_plus_actual_absence_never_authorizes_active(self):
        self.journal.fail_role = 'stop-intent'
        self.assertEqual(self.run_forward(request(recovery=True)).state, 'UNKNOWN')
        self.ports.current = dataclasses.replace(self.ports.current, state='STOPPED',
            pid=0, start_ticks=0, cgroup_empty=True)
        self.assertEqual(self.rollback().state, 'UNKNOWN')
        self.assertEqual(self.ports.effect_counts['start:ACTIVE'], 0)

    def test_partial_or_wrong_bound_journal_records_never_authorize_active(self):
        for role in ('intent', 'armed', 'stop-intent', 'stop-observed', 'hold-drop-intent',
                     'hold-dropped', 'reload-intent', 'hold-configured', 'completed', 'recovery-anchor'):
            with self.subTest(role=role):
                self.setUp()
                self.assertEqual(self.run_forward(request(recovery=True)).state, 'COMPLETED_STOPPED_HOLD')
                self.journal.rows[role] = b'{'
                self.assertEqual(self.rollback().state, 'UNKNOWN')
                self.assertEqual(self.ports.effect_counts['start:ACTIVE'], 0)
        for change in (dict(request_sha256='f' * 64), dict(role='active-start-intent')):
            with self.subTest(change=change):
                self.setUp()
                self.run_forward(request(recovery=True))
                value = k.parse(self.journal.rows['stop-intent'])
                self.journal.rows['stop-intent'] = k.encoded({**value, **change})
                self.assertEqual(self.rollback().state, 'UNKNOWN')
                self.assertEqual(self.ports.effect_counts['start:ACTIVE'], 0)

    def test_recovery_journal_requires_observed_ack_and_order(self):
        for role in ('stop-observed', 'hold-dropped', 'hold-configured'):
            with self.subTest(missing_ack=role):
                self.setUp()
                self.run_forward(request(recovery=True))
                del self.journal.rows[role]
                self.assertEqual(self.rollback().state, 'UNKNOWN')
                self.assertEqual(self.ports.effect_counts['start:ACTIVE'], 0)
        self.setUp()
        self.run_forward(request(recovery=True))
        original = k.parse(self.journal.rows['stop-observed'])
        original['immutable_config_sha256'] = 'f' * 64
        self.journal.rows['stop-observed'] = k.encoded(original)
        self.assertEqual(self.rollback().state, 'UNKNOWN')
        self.assertEqual(self.ports.effect_counts['start:ACTIVE'], 0)

    def test_cleanup_hook_must_be_actual_pid1_owned_and_unchanged(self):
        for change in (dict(exec_stop_post_request_sha256='a' * 64), dict(script_sha256='a' * 64),
                       dict(user='nobody'), dict(restart='always'), dict(phase='CLEANUP'),
                       dict(pid=0), dict(runtime_max_seconds=61)):
            with self.subTest(change=change):
                self.setUp()
                original = self.ports.arm_cleanup
                def malformed(req, original=original, change=change):
                    self.ports.armed = dataclasses.replace(original(req), **change)
                    return self.ports.armed
                self.ports.arm_cleanup = malformed
                self.assertEqual(self.run_forward().state, 'REFUSED_BEFORE_EFFECT')
                self.assertEqual(self.ports.effect_counts['stop'], 0)

    def test_cleanup_owner_replacement_is_refused(self):
        def replace(ports):
            ports.armed = dataclasses.replace(ports.armed, invocation_id='a' * 32)
        self.ports.verify_hook = replace
        self.assertEqual(self.run_forward().state, 'REFUSED_BEFORE_EFFECT')
        self.assertEqual(self.ports.effect_counts['stop'], 0)

    def test_stop_ack_loss_after_effect_can_only_contain_observed_absence(self):
        self.ports.ack_loss = ('stop', 'AFTER')
        self.assertEqual(self.run_forward().state, 'UNKNOWN')
        self.assertEqual(self.ports.current.state, 'STOPPED')
        self.clock.now = 1300  # Narrow denial remains allowed after forward expiry.
        self.assertEqual(self.contain().state, 'SAFE_STOPPED_HOLD')
        self.assertEqual(self.ports.effect_counts['stop'], 1)
        self.assertEqual(self.ports.effect_counts['drop'], 1)

    def test_stop_ack_loss_before_effect_does_not_retry_or_stop_original_active(self):
        self.ports.ack_loss = ('stop', 'BEFORE')
        self.assertEqual(self.run_forward().state, 'UNKNOWN')
        self.assertEqual(self.contain().state, 'UNKNOWN')
        self.assertEqual(self.ports.current.binding(), before().binding())
        self.assertEqual(self.ports.effect_counts['stop'], 1)

    def test_drop_ack_loss_readback_or_unknown_without_retry(self):
        for when in ('BEFORE', 'AFTER'):
            with self.subTest(when=when):
                self.setUp()
                self.ports.ack_loss = ('drop', when)
                self.assertEqual(self.run_forward().state, 'UNKNOWN')
                contained = self.contain()
                self.assertEqual(contained.state, 'UNKNOWN' if when == 'BEFORE' else 'SAFE_STOPPED_HOLD')
                self.assertEqual(self.ports.effect_counts['drop'], 1)
                self.assertEqual(self.ports.effect_counts['stop'], 1)

    def test_reload_ack_loss_readback_or_unknown_without_retry(self):
        for when in ('BEFORE', 'AFTER'):
            with self.subTest(when=when):
                self.setUp()
                self.ports.ack_loss = ('reload', when)
                self.assertEqual(self.run_forward().state, 'UNKNOWN')
                self.assertEqual(self.contain().state, 'UNKNOWN' if when == 'BEFORE' else 'SAFE_STOPPED_HOLD')
                self.assertEqual(self.ports.effect_counts['reload'], 1)

    def test_real_drain_record_required_and_all_obligation_sets_empty(self):
        for field, value in (('database', {'native_enabled': True}),
                             ('database', {'legacy_backends': [88]}),
                             ('database', {'live_tasks': ['synthetic-task']}),
                             ('database', {'active_outbox': ['synthetic']}),
                             ('provider', {'unknown': ['synthetic-job']}),
                             ('provider', {'inflight': ['synthetic-job']}),
                             ('leases', {'exclusive': False}),
                             ('leases', {'owner_pid': 999}),
                             ('leases', {'active_consumers': ['other']})):
            with self.subTest(field=field, value=value):
                self.setUp()
                self.ports.drain_change = lambda proof, field=field, value=value: dataclasses.replace(
                    proof, **{field: {**getattr(proof, field), **value}})
                self.assertEqual(self.run_forward().state, 'UNKNOWN')
                self.assertEqual(self.ports.effect_counts['stop'], 1)
                self.assertEqual(self.ports.effect_counts['drop'], 0)
        self.setUp()
        self.ports.drain_change = lambda proof: True
        self.assertEqual(self.run_forward().state, 'UNKNOWN')
        self.assertEqual(self.ports.effect_counts['drop'], 0)

    def test_drain_bound_to_request_snapshot_and_fresh_after_read(self):
        for change in (dict(request_sha256='f' * 64), dict(snapshot_sha256='f' * 64), dict(observed_at=969)):
            with self.subTest(change=change):
                self.setUp()
                self.ports.drain_change = lambda proof, change=change: dataclasses.replace(proof, **change)
                self.assertEqual(self.run_forward().state, 'UNKNOWN')
                self.assertEqual(self.ports.effect_counts['drop'], 0)
        self.setUp()
        def slow(proof):
            self.clock.now += 31
            return proof
        self.ports.drain_change = slow
        self.assertEqual(self.run_forward().state, 'UNKNOWN')
        self.assertEqual(self.ports.effect_counts['drop'], 0)

    def test_nested_database_snapshot_must_match_actual_stopped_snapshot(self):
        self.ports.drain_change = lambda proof: dataclasses.replace(proof,
            database={**proof.database, 'snapshot_sha256': 'e' * 64})
        self.assertEqual(self.run_forward().state, 'UNKNOWN')
        self.assertEqual(self.ports.effect_counts['drop'], 0)

    def test_observation_timestamp_uses_clock_after_delayed_port(self):
        # A syscall crossing a wall-clock second produces a later observed_at.
        # Reading clock before the port would incorrectly reject these records.
        for name in ('snapshot', 'native_hold', 'arm_cleanup', 'verify_cleanup', 'drain', 'lifecycle_clock'):
            with self.subTest(port=name):
                self.setUp()
                original = getattr(self.ports, name)
                delayed_once = [False]
                def delayed(*args, original=original, delayed_once=delayed_once, **kwargs):
                    if not delayed_once[0]:
                        self.clock.now += 1
                        delayed_once[0] = True
                    return original(*args, **kwargs)
                setattr(self.ports, name, delayed)
                self.assertEqual(self.run_forward(request(recovery=True)).state, 'COMPLETED_STOPPED_HOLD')

    def test_future_observations_are_rejected(self):
        for name in ('snapshot', 'native_hold', 'arm_cleanup', 'verify_cleanup', 'drain', 'lifecycle_clock'):
            with self.subTest(port=name):
                self.setUp()
                original = getattr(self.ports, name)
                def future(*args, original=original, **kwargs):
                    return dataclasses.replace(original(*args, **kwargs), observed_at=self.clock.now + 1)
                setattr(self.ports, name, future)
                result = self.run_forward(request(recovery=True))
                self.assertTrue(result.unknown)
                self.assertEqual(self.ports.effect_counts['drop'], 0)

    def test_native_drift_after_stop_blocks_every_further_effect(self):
        def drift(ports, effect):
            if effect == 'stop':
                ports.native = dataclasses.replace(ports.native, admission='ACTIVE')
        self.ports.effect_hook = drift
        self.assertEqual(self.run_forward().state, 'UNKNOWN')
        self.assertEqual(self.ports.effect_counts['drop'], 0)
        self.assertEqual(self.contain().state, 'UNKNOWN')

    def test_identity_drift_after_intent_blocks_stop(self):
        original = self.journal.retain
        def retain(role, raw):
            original(role, raw)
            if role == 'stop-intent':
                self.ports.current = dataclasses.replace(self.ports.current, invocation_id='d' * 32)
        self.journal.retain = retain
        self.assertEqual(self.run_forward().state, 'UNKNOWN')
        self.assertEqual(self.ports.effect_counts['stop'], 0)
        self.assertEqual(self.contain().state, 'UNKNOWN')

    def test_expiry_and_monotonic_deadline_before_stop_leave_active(self):
        for mode in ('wall', 'monotonic', 'backwards', 'STOP'):
            with self.subTest(mode=mode):
                self.setUp()
                original = self.ports.arm_cleanup
                def arm(req, mode=mode):
                    value = original(req)
                    if mode == 'wall': self.clock.now = 1200
                    if mode == 'monotonic': self.clock.elapsed = 60
                    if mode == 'backwards': self.clock.now = 999
                    if mode == 'STOP': self.ports.forward_error = True
                    return dataclasses.replace(value, observed_at=self.clock.now)
                self.ports.arm_cleanup = arm
                self.assertEqual(self.run_forward().state, 'REFUSED_BEFORE_EFFECT')
                self.assertEqual(self.contain().state, 'ORIGINAL_ACTIVE_UNTOUCHED')
                self.assertEqual(self.ports.effect_counts['stop'], 0)

    def test_active_recovery_needs_separate_scope_and_actual_drain(self):
        for mode in ('missing', 'expired', 'guard', 'database-busy'):
            with self.subTest(mode=mode):
                self.setUp()
                self.assertEqual(self.run_forward(request(recovery=mode != 'missing')).state, 'COMPLETED_STOPPED_HOLD')
                if mode == 'expired': self.clock.now = 1250
                if mode == 'guard': self.ports.recovery_error = True
                if mode == 'database-busy':
                    self.ports.drain_change = lambda proof: dataclasses.replace(proof,
                        database={**proof.database, 'live_tasks': ['synthetic']})
                self.assertEqual(self.rollback().state, 'UNKNOWN')
                self.assertEqual(self.ports.effect_counts['start:ACTIVE'], 0)
                self.assertEqual(self.ports.current.admission, 'HOLD')

    def test_recovery_uses_original_boot_and_monotonic_deadline_without_rebase(self):
        for mode in ('backwards-wall', 'elapsed', 'new-boot'):
            with self.subTest(mode=mode):
                self.setUp()
                self.run_forward(request(recovery=True))
                anchor = self.journal.rows['recovery-anchor']
                if mode == 'backwards-wall': self.clock.now = 980
                if mode == 'elapsed': self.clock.elapsed = 100000
                if mode == 'new-boot': self.clock.boot = '00000000-0000-0000-0000-000000000002'
                self.assertEqual(self.rollback().state, 'UNKNOWN')
                self.assertEqual(self.ports.effect_counts['start:ACTIVE'], 0)
                self.assertEqual(self.journal.rows['recovery-anchor'], anchor)
                self.assertEqual(self.contain().state, 'SAFE_STOPPED_HOLD')

    def test_latest_cleanup_callback_cannot_extend_forward_or_active_deadline(self):
        for mode in ('forward-wall', 'recovery-wall', 'recovery-boottime'):
            with self.subTest(mode=mode):
                self.setUp()
                if mode != 'forward-wall':
                    self.assertEqual(self.run_forward(request(recovery=True)).state, 'COMPLETED_STOPPED_HOLD')
                counts = [0]
                role = 'stop-intent' if mode == 'forward-wall' else 'active-start-intent'
                def expire(ports):
                    if role in self.journal.rows:
                        counts[0] += 1
                        if counts[0] == 2:
                            if mode == 'forward-wall': self.clock.now = 1200
                            if mode == 'recovery-wall': self.clock.now = 1250
                            if mode == 'recovery-boottime': self.clock.elapsed = 100000
                self.ports.verify_hook = expire
                result = self.run_forward() if mode == 'forward-wall' else self.rollback()
                self.assertEqual(result.state, 'UNKNOWN')
                self.assertEqual(self.ports.effect_counts['stop' if mode == 'forward-wall' else 'start:ACTIVE'], 0)

    def test_stale_drain_after_late_callbacks_cannot_start_active(self):
        self.run_forward(request(recovery=True))
        delayed = [False]
        def delay(ports):
            if 'active-start-intent' in self.journal.rows and not delayed[0]:
                delayed[0] = True
                self.clock.now += 31
                self.clock.elapsed += 31
        self.ports.verify_hook = delay
        self.assertEqual(self.rollback().state, 'UNKNOWN')
        self.assertEqual(self.ports.effect_counts['start:ACTIVE'], 0)

    def test_actual_stopped_tail_must_match_validated_journal_tail(self):
        self.run_forward(request(recovery=True))
        self.ports.current = dataclasses.replace(self.ports.current, invocation_id='f' * 32)
        self.assertEqual(self.rollback().state, 'UNKNOWN')
        self.assertEqual(self.ports.effect_counts['start:ACTIVE'], 0)

    def test_recovery_expiry_after_last_snapshot_cannot_start_active(self):
        self.run_forward(request(recovery=True))
        delayed = [False]
        def delay(ports):
            if 'active-start-intent' in self.journal.rows and not delayed[0]:
                delayed[0] = True
                self.clock.now = 1250
        self.ports.snapshot_hook = delay
        self.assertEqual(self.rollback().state, 'UNKNOWN')
        self.assertEqual(self.ports.effect_counts['start:ACTIVE'], 0)

    def test_forward_success_rechecks_actual_legacy_after_callbacks(self):
        changed = [False]
        def drift(ports):
            if ports.current.admission == 'HOLD' and not changed[0]:
                changed[0] = True
                ports.current = dataclasses.replace(before(), pid=909, invocation_id='d' * 32, start_ticks=9090)
        self.ports.verify_hook = drift
        self.assertEqual(self.run_forward().state, 'UNKNOWN')
        self.assertNotIn('completed', self.journal.rows)

    def test_containment_success_rechecks_actual_legacy_after_callbacks(self):
        self.run_forward(request(start_hold=True))
        changed = [False]
        def drift(ports):
            if not changed[0]:
                changed[0] = True
                ports.current = dataclasses.replace(before(), pid=909, invocation_id='d' * 32, start_ticks=9090)
        self.ports.native_hook = drift
        self.assertEqual(self.contain().state, 'UNKNOWN')
        self.assertEqual(self.ports.effect_counts['stop'], 1)

    def test_active_recovery_once_from_running_hold_then_readback_only(self):
        self.assertEqual(self.run_forward(request(start_hold=True, recovery=True)).state, 'COMPLETED_RUNNING_HOLD')
        self.assertEqual(self.rollback().state, 'ACTIVE_RESTORED')
        self.assertEqual(self.ports.effect_counts['stop'], 2)
        self.assertEqual(self.ports.effect_counts['start:ACTIVE'], 1)
        counts = dict(self.ports.effect_counts)
        self.assertEqual(self.rollback().state, 'ACTIVE_OBSERVED')
        self.assertEqual(self.ports.effect_counts, counts)
        self.assertEqual(self.contain().state, 'SAFE_STOPPED_HOLD')
        self.assertEqual(self.ports.effect_counts['start:ACTIVE'], 1)

    def test_active_start_lost_ack_never_starts_twice(self):
        self.assertEqual(self.run_forward(request(recovery=True)).state, 'COMPLETED_STOPPED_HOLD')
        self.ports.ack_loss = ('start:ACTIVE', 'AFTER')
        result = self.rollback()
        self.assertEqual(result.state, 'UNKNOWN')
        self.assertEqual(self.ports.current.admission, 'ACTIVE')
        readback = self.rollback()
        self.assertEqual(readback.state, 'ACTIVE_OBSERVED')
        self.assertTrue(readback.unknown)
        self.assertEqual(self.ports.effect_counts['start:ACTIVE'], 1)

    def test_active_recovery_failure_after_config_change_contains_hold(self):
        self.assertEqual(self.run_forward(request(recovery=True)).state, 'COMPLETED_STOPPED_HOLD')
        self.ports.ack_loss = ('reload', 'AFTER')
        self.assertEqual(self.rollback().state, 'UNKNOWN')
        self.assertEqual(self.ports.current.admission, 'HOLD')
        self.assertEqual(self.ports.current.state, 'STOPPED')
        self.assertEqual(self.ports.effect_counts['start:ACTIVE'], 0)

    def test_unreadable_journal_never_escapes_or_proves_no_effect(self):
        self.journal.read = lambda role: (_ for _ in ()).throw(OSError('unreadable'))
        self.assertEqual(self.run_forward().state, 'UNKNOWN')
        self.assertEqual(self.contain().state, 'UNKNOWN')
        self.assertFalse(any(self.ports.effect_counts.values()))


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0,
    'NOTRUN: requires Linux root for actual root-private descriptor journal; full systemd fixture is separate')
class LinuxJournalTests(unittest.TestCase):
    def test_actual_root_private_create_only_and_fifo_refusal(self):
        # /tmp is intentionally ineligible: Journal requires every ancestor root-private.
        with tempfile.TemporaryDirectory(prefix='legacy-transition-', dir='/run') as directory:
            os.chmod(directory, 0o700)
            target = Path(directory) / 'journal'
            journal = k.Journal(target)
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            journal.retain('request', b'{"version":1}')
            self.assertEqual(k.Journal.open(target).read('request'), b'{"version":1}')
            with self.assertRaises(FileExistsError):
                journal.retain('request', b'{}')
            fifo = target / 'intent.json'
            os.mkfifo(fifo, 0o600)
            script = (
                'import importlib.util,sys\n'
                's=importlib.util.spec_from_file_location("kernel",sys.argv[1])\n'
                'm=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)\n'
                'try: m.Journal.open(sys.argv[2]).read("intent")\n'
                'except m.Refusal: print("FIFO_REFUSED")\n'
                'else: raise AssertionError("FIFO accepted")\n')
            result = subprocess.run([sys.executable, '-I', '-B', '-S', '-c', script,
                str(Path(k.__file__).resolve()), str(target)], capture_output=True,
                text=True, timeout=3, check=True)
            self.assertEqual(result.stdout.strip(), 'FIFO_REFUSED')

    def test_actual_journal_rejects_symlink_hardlink_and_loose_mode(self):
        with tempfile.TemporaryDirectory(prefix='legacy-transition-', dir='/run') as directory:
            os.chmod(directory, 0o700)
            target = Path(directory) / 'journal'
            journal = k.Journal(target)
            journal.retain('request', b'{}')
            path = target / 'intent.json'
            path.symlink_to(target / 'request.json')
            with self.assertRaises(OSError): journal.read('intent')
            path.unlink()
            os.link(target / 'request.json', path)
            with self.assertRaises(k.Refusal): journal.read('intent')
            path.unlink()
            os.chmod(target / 'request.json', 0o644)
            with self.assertRaises(k.Refusal): journal.read('request')


if __name__ == '__main__':
    unittest.main()
