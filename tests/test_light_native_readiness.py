"""Synthetic classification only: no SQL, provider, host or authority actions."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
# Import the actual package dependency with -I, without cwd/module search.
if 'ops' not in sys.modules:
    package = types.ModuleType('ops')
    package.__path__ = [str(ROOT / 'ops')]
    sys.modules['ops'] = package


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


r = load('readiness_under_test', ROOT / 'ops' / 'light_native_readiness.py')
fixtures = load('transition_readiness_fixtures', ROOT / 'tests' / 'test_light_native_legacy_transition.py')
k = r.transition

# Independent contract from the reviewed task constraint and the current
# eight-state outbox constraint. Fixtures and expected outcomes must not follow
# the classifier's own LIVE_* partition when that partition is regressed.
EXPECTED_LIVE_TASKS = ('NEW', 'VALIDATING', 'READY', 'RUNNING', 'WAITING_EXTERNAL', 'EVALUATING')
EXPECTED_TERMINAL_TASKS = ('OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP', 'DONE', 'CANCELLED')
EXPECTED_LIVE_OUTBOX = ('PENDING', 'CLAIMED', 'RETRY', 'PUBLISHED', 'DELIVERY_FAILED', 'SENT')
EXPECTED_TERMINAL_OUTBOX = ('CALLBACK_ACCEPTED', 'FAILED_CLOSED')
EXPECTED_OBLIGATIONS = ('retained_task_leases', 'retained_outbox_claims', 'resource_leases',
    'probe_leases', 'nonterminal_native_receipts', 'active_work_items', 'legacy_backends',
    'provider_probe_inflight')


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        # Reuse actual transition Snapshot/NativeHold fixture outputs, not a
        # second implementation of capture or of process/config validation.
        legacy = replace(fixtures.before(), state='STOPPED', admission='HOLD', pid=0,
            start_ticks=0, cgroup_empty=True,
            drop_bytes=k.drop_bytes(fixtures.before().release, 'HOLD'))
        native = fixtures.native()
        self.database = dict(version=1, kind='LIGHT_NATIVE_DATABASE_OBSERVATION',
            started_at=996, observed_at=998, database_identity_sha256='1' * 64,
            snapshot_identity_sha256='2' * 64, query_profile_sha256='3' * 64,
            scope='ALL_ROWS', transaction_read_only=True, isolation='REPEATABLE_READ',
            statement_timeout_ms=5000,
            controls=dict(native_enabled=False, planner_enabled=False,
                autopilot_role_enabled=False, autopilot_can_repair=True),
            task_total=534, task_status_counts={name: 0 for name in (*EXPECTED_LIVE_TASKS, *EXPECTED_TERMINAL_TASKS)},
            outbox_total=506, outbox_status_counts={name: 0 for name in (*EXPECTED_LIVE_OUTBOX, *EXPECTED_TERMINAL_OUTBOX)},
            obligation_counts={name: 0 for name in EXPECTED_OBLIGATIONS})
        self.database['task_status_counts'].update(DONE=140, FAILED_CLOSED=394)
        self.database['outbox_status_counts'].update(CALLBACK_ACCEPTED=336, FAILED_CLOSED=170)
        self.provider = dict(version=1, kind='LIGHT_NATIVE_PROVIDER_OBSERVATION',
            started_at=996, observed_at=998, provider_identity_sha256='4' * 64,
            profile_sha256='5' * 64, coverage='FULL_SERVICE_PROFILE',
            external_inventory_sha256='6' * 64, journal_inventory_sha256='7' * 64,
            inflight_count=0, external_unresolved_count=0, journal_unresolved_count=0)
        legacy_binding, native_binding = legacy.binding(), native.binding()
        self.host = dict(version=1, kind='LIGHT_NATIVE_HOST_FENCE_OBSERVATION', observed_at=1000,
            legacy=dict(observed_at=1000, **legacy_binding), native=dict(observed_at=1000, **native_binding),
            fence=dict(established_at=995, verified_at=1000, expires_at=1020,
                evidence_sha256='8' * 64, legacy_snapshot_sha256=k.sha(k.encoded(legacy_binding)),
                mechanism='STOPPED_CGROUP_ABSENCE'),
            lease=dict(identity_sha256='9' * 64, acquired_at=994, verified_at=1000, expires_at=1020,
                evidence_sha256='a' * 64, exclusive=True, active_consumers=0,
                scope='ALL_LEGACY_PRODUCERS', owner=dict(unit='synthetic-supervisor.service', pid=303,
                    invocation_id='c' * 32, start_ticks=300,
                    boot_id='00000000-0000-0000-0000-000000000001')))
        self.expected = dict(expected_database_identity_sha256='1' * 64,
            expected_query_profile_sha256='3' * 64, expected_provider_identity_sha256='4' * 64,
            expected_provider_profile_sha256='5' * 64, expected_host_snapshot_sha256=k.sha(k.encoded(legacy_binding)),
            expected_native_hold_sha256=k.sha(k.encoded(native_binding)), expected_lease_identity_sha256='9' * 64)

    def qualify(self, *, db='fixture', provider='fixture', host='fixture', now=1000, expected=None, accepted=None):
        values = (self.database if db == 'fixture' else db,
                  self.provider if provider == 'fixture' else provider,
                  self.host if host == 'fixture' else host)
        raw = tuple(k.encoded(value) if value is not None else None for value in values)
        pins = dict(accepted_database_sha256=k.sha(raw[0]) if raw[0] else None,
            accepted_provider_sha256=k.sha(raw[1]) if raw[1] else None,
            accepted_host_sha256=k.sha(raw[2]) if raw[2] else None)
        pins.update(accepted or {})
        return r.qualify_readiness(*raw, **{**self.expected, **(expected or {})}, **pins, now=now)

    def rebind_legacy(self):
        pin = k.sha(k.encoded({name: value for name, value in self.host['legacy'].items() if name != 'observed_at'}))
        self.expected['expected_host_snapshot_sha256'] = pin
        if self.host['fence'] is not None:
            self.host['fence']['legacy_snapshot_sha256'] = pin

    def test_ready_is_momentary_readonly_qualification_and_all_authority_false(self):
        result = self.qualify()
        self.assertEqual(result.state, 'READY_FOR_TRANSITION')
        self.assertEqual(dict(result.component_states), {'database': 'READY', 'provider': 'READY', 'host': 'READY'})
        self.assertEqual(result.reasons, ())
        public = result.public()
        for name in ('activation_authorized', 'native_admission_authorized', 'transition_authorized',
                     'incident_closed', 'historical_chain_verified', 'original_baseline_matched',
                     'replay_authorized', 'new_task_authorized'):
            self.assertIs(public[name], False)
            with self.assertRaises(ValueError): replace(result, **{name: True})
        self.assertNotIn('synthetic-supervisor', json.dumps(public))
        self.assertNotIn('legacy', public)
        self.assertNotIn('snapshot_identity_sha256', public)

    def test_known_status_contract_is_complete(self):
        self.assertEqual(set(r.TASK_STATUSES), set(EXPECTED_LIVE_TASKS + EXPECTED_TERMINAL_TASKS))
        self.assertEqual(set(r.OUTBOX_STATUSES), set(EXPECTED_LIVE_OUTBOX + EXPECTED_TERMINAL_OUTBOX))
        self.assertEqual(set(r.LIVE_TASK_STATUSES), set(EXPECTED_LIVE_TASKS))
        self.assertEqual(set(r.LIVE_OUTBOX_STATUSES), set(EXPECTED_LIVE_OUTBOX))
        self.assertEqual(set(r.TASK_STATUSES) - set(r.LIVE_TASK_STATUSES), set(EXPECTED_TERMINAL_TASKS))
        self.assertEqual(set(r.OUTBOX_STATUSES) - set(r.LIVE_OUTBOX_STATUSES), set(EXPECTED_TERMINAL_OUTBOX))
        self.assertEqual(set(r.OBLIGATION_COUNTS), set(EXPECTED_OBLIGATIONS))

    def test_database_snapshot_alone_never_overall_ready(self):
        result = self.qualify(provider=None, host=None)
        self.assertEqual(result.state, 'UNKNOWN')
        self.assertEqual(dict(result.component_states), {'database': 'READY', 'provider': 'UNKNOWN', 'host': 'UNKNOWN'})

    def test_actual_enabled_producers_and_idle_worker_are_not_ready(self):
        self.database['controls'].update(planner_enabled=True, autopilot_role_enabled=True)
        self.database['obligation_counts']['legacy_backends'] = 1
        result = self.qualify(provider=None, host=None)
        self.assertEqual(result.state, 'NOT_READY')
        self.assertIn('DATABASE_PRODUCER_FENCE_NOT_PROVEN', result.reasons)
        self.assertIn('DATABASE_LEGACY_BACKENDS', result.reasons)
        self.assertEqual(dict(result.component_states)['provider'], 'UNKNOWN')

    def test_each_producer_control_and_native_enabled_blocks(self):
        for name in ('planner_enabled', 'autopilot_role_enabled', 'native_enabled'):
            with self.subTest(control=name):
                self.setUp()
                self.database['controls'][name] = True
                self.assertEqual(self.qualify().state, 'NOT_READY')
        self.setUp()
        self.database['controls']['autopilot_can_repair'] = True
        self.assertEqual(self.qualify().state, 'READY_FOR_TRANSITION')

    def test_all_actual_task_statuses_covered_and_each_live_state_blocks(self):
        for status in EXPECTED_LIVE_TASKS:
            with self.subTest(status=status):
                self.setUp()
                self.database['task_status_counts'][status] = 1
                self.database['task_total'] += 1
                result = self.qualify()
                self.assertEqual(result.state, 'NOT_READY')
                self.assertIn('DATABASE_LIVE_TASK', result.reasons)
        for status in EXPECTED_TERMINAL_TASKS:
            with self.subTest(terminal=status):
                self.setUp()
                self.database['task_status_counts'][status] += 1
                self.database['task_total'] += 1
                self.assertEqual(self.qualify().state, 'READY_FOR_TRANSITION')

    def test_each_outbox_live_status_blocks(self):
        for status in EXPECTED_LIVE_OUTBOX:
            with self.subTest(status=status):
                self.setUp()
                self.database['outbox_status_counts'][status] = 1
                self.database['outbox_total'] += 1
                self.assertEqual(self.qualify().state, 'NOT_READY')

    def test_each_outbox_terminal_status_allows_quiet_snapshot(self):
        for status in EXPECTED_TERMINAL_OUTBOX:
            with self.subTest(status=status):
                self.setUp()
                self.database['outbox_status_counts'][status] += 1
                self.database['outbox_total'] += 1
                self.assertEqual(self.qualify().state, 'READY_FOR_TRANSITION')

    def test_every_retained_obligation_blocks_even_expired_or_idle(self):
        for name in EXPECTED_OBLIGATIONS:
            with self.subTest(obligation=name):
                self.setUp()
                self.database['obligation_counts'][name] = 1
                result = self.qualify()
                self.assertEqual(result.state, 'NOT_READY')
                self.assertIn('DATABASE_' + name.upper(), result.reasons)

    def test_omitted_new_status_and_inconsistent_totals_are_unknown(self):
        for target in ('task', 'outbox'):
            for mode in ('missing', 'extra', 'total', 'boolean', 'negative', 'huge', 'null'):
                with self.subTest(target=target, mode=mode):
                    self.setUp()
                    key = target + '_status_counts'
                    status = next(iter(self.database[key]))
                    if mode == 'missing': del self.database[key][status]
                    if mode == 'extra': self.database[key]['UNREVIEWED_STATUS'] = 0
                    if mode == 'total': self.database[target + '_total'] += 1
                    if mode == 'boolean': self.database[key][status] = False
                    if mode == 'negative': self.database[key][status] = -1
                    if mode == 'huge': self.database[key][status] = r.MAX_COUNT + 1
                    if mode == 'null': self.database[key] = None
                    self.assertEqual(self.qualify().state, 'UNKNOWN')

    def test_omitted_obligation_is_unknown_and_known_blocker_still_wins(self):
        del self.database['obligation_counts']['resource_leases']
        self.assertEqual(self.qualify().state, 'UNKNOWN')
        self.database['controls']['planner_enabled'] = True
        result = self.qualify(provider=None)
        self.assertEqual(result.state, 'NOT_READY')
        self.assertIn('DATABASE_OBLIGATION_COUNTS_UNKNOWN', result.reasons)

    def test_partial_evidence_preserves_verified_positive_blockers(self):
        for mode in ('task', 'outbox', 'lease', 'controls', 'host-owner'):
            with self.subTest(mode=mode):
                self.setUp()
                if mode == 'task':
                    self.database['task_status_counts']['RUNNING'] = 1
                    del self.database['task_status_counts']['DONE']
                if mode == 'outbox':
                    self.database['outbox_status_counts']['PUBLISHED'] = 1
                    del self.database['outbox_status_counts']['SENT']
                if mode == 'lease':
                    self.database['obligation_counts']['retained_task_leases'] = 1
                    del self.database['obligation_counts']['resource_leases']
                if mode == 'controls':
                    self.database['controls']['native_enabled'] = True
                    self.database['controls']['autopilot_can_repair'] = None
                if mode == 'host-owner':
                    self.host['lease']['exclusive'] = False
                    self.host['lease']['owner'] = None
                self.assertEqual(self.qualify().state, 'NOT_READY')

    def test_database_must_be_same_target_query_profile_and_exact_readonly_snapshot(self):
        changes = [dict(database_identity_sha256='f' * 64), dict(query_profile_sha256='f' * 64),
            dict(snapshot_identity_sha256=None), dict(scope='FILTERED'), dict(transaction_read_only=False),
            dict(transaction_read_only=1), dict(isolation='READ_COMMITTED'), dict(statement_timeout_ms=5001),
            dict(statement_timeout_ms=True), dict(started_at=992)]
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.database.update(change)
                self.assertEqual(self.qualify().state, 'UNKNOWN')

    def test_missing_or_wrong_expected_target_pins_are_unknown(self):
        for key in self.expected:
            with self.subTest(pin=key):
                self.assertEqual(self.qualify(expected={key: None}).state, 'UNKNOWN')
                self.assertEqual(self.qualify(expected={key: 'f' * 64}).state, 'UNKNOWN')

    def test_actual_external_provider_and_journal_obligations_all_block(self):
        for name in ('inflight_count', 'external_unresolved_count', 'journal_unresolved_count'):
            with self.subTest(count=name):
                self.setUp()
                self.provider[name] = 1
                result = self.qualify()
                self.assertEqual(result.state, 'NOT_READY')
                self.assertIn('PROVIDER_' + name.upper(), result.reasons)

    def test_cached_circuit_and_partial_provider_coverage_never_prove_provider_quiet(self):
        self.provider['coverage'] = 'PARTIAL'
        self.assertEqual(self.qualify().state, 'UNKNOWN')
        self.provider['external_inventory_sha256'] = None
        self.provider['journal_inventory_sha256'] = None
        self.provider['inflight_count'] = None
        self.assertEqual(self.qualify().state, 'UNKNOWN')
        self.provider['external_unresolved_count'] = 1
        self.assertEqual(self.qualify().state, 'NOT_READY')
        self.assertEqual(self.qualify(provider=dict(circuit='CLOSED', probe_in_flight=False,
            last_success_at=1)).state, 'UNKNOWN')

    def test_malformed_provider_count_identity_and_profile_are_unknown(self):
        for change in (dict(inflight_count=False), dict(inflight_count=-1),
            dict(inflight_count=r.MAX_COUNT + 1), dict(provider_identity_sha256='f' * 64),
            dict(profile_sha256='f' * 64), dict(journal_inventory_sha256=None)):
            with self.subTest(change=change):
                self.setUp()
                self.provider.update(change)
                self.assertEqual(self.qualify().state, 'UNKNOWN')

    def test_active_or_running_hold_without_real_stop_fence_is_not_ready(self):
        for admission in ('ACTIVE', 'HOLD'):
            with self.subTest(admission=admission):
                self.setUp()
                snapshot = fixtures.before()
                if admission == 'HOLD':
                    snapshot = replace(snapshot, admission='HOLD', drop_bytes=k.drop_bytes(snapshot.release, 'HOLD'))
                self.host['legacy'] = dict(observed_at=1000, **snapshot.binding())
                self.host['fence'], self.host['lease'] = None, None
                self.rebind_legacy()
                result = self.qualify()
                self.assertEqual(result.state, 'NOT_READY')
                self.assertIn('HOST_LEGACY_NOT_FENCED', result.reasons)
                self.assertIn('HOST_FENCE_UNKNOWN', result.reasons)

    def test_loaded_hold_with_active_drop_or_pending_reload_is_not_ready(self):
        self.host['legacy']['drop_base64'] = fixtures.before().binding()['drop_base64']
        self.rebind_legacy()
        self.assertEqual(self.qualify().state, 'NOT_READY')
        self.setUp()
        self.host['legacy']['needs_reload'] = True
        self.rebind_legacy()
        self.assertEqual(self.qualify().state, 'NOT_READY')

    def test_native_drift_or_non_hold_is_unknown_and_never_ready(self):
        for field, value in (('admission', 'ACTIVE'), ('pid', True), ('configuration_sha256', 'f' * 64),
                             ('invocation_id', 'f' * 32), ('state', 'unknown')):
            with self.subTest(field=field):
                self.setUp()
                self.host['native'][field] = value
                self.assertEqual(self.qualify().state, 'UNKNOWN')

    def test_fence_and_lease_need_actual_binding_exclusivity_identity_and_owner(self):
        changes = [('fence', {'legacy_snapshot_sha256': 'f' * 64}),
            ('fence', {'mechanism': 'DB_SNAPSHOT_ONLY'}), ('fence', {'evidence_sha256': None}),
            ('lease', {'identity_sha256': 'f' * 64}), ('lease', {'evidence_sha256': None}),
            ('lease', {'scope': 'LEGACY_PID_ONLY'}), ('lease', {'exclusive': 1}),
            ('lease', {'active_consumers': False}), ('lease', {'acquired_at': 996})]
        for key, change in changes:
            with self.subTest(key=key, change=change):
                self.setUp()
                self.host[key].update(change)
                self.assertEqual(self.qualify().state, 'UNKNOWN')
        for change in (dict(pid=0), dict(pid=True), dict(start_ticks=0), dict(invocation_id='bad'),
                       dict(boot_id='bad'), dict(unit=fixtures.before().unit)):
            with self.subTest(owner=change):
                self.setUp()
                self.host['lease']['owner'].update(change)
                self.assertEqual(self.qualify().state, 'UNKNOWN')
        for change in (dict(exclusive=False), dict(active_consumers=1)):
            with self.subTest(blocker=change):
                self.setUp()
                self.host['lease'].update(change)
                self.assertEqual(self.qualify().state, 'NOT_READY')

    def test_host_expiry_is_not_ready_and_later_evaluation_cannot_reuse_readiness(self):
        self.assertEqual(self.qualify(now=1020).state, 'NOT_READY')
        self.assertEqual(self.qualify(now=1031).state, 'UNKNOWN')

    def test_pre_fence_or_after_final_verification_snapshots_are_unknown(self):
        for component, change in (('database', dict(started_at=994)),
                                 ('provider', dict(started_at=994)),
                                 ('database', dict(observed_at=1001)),
                                 ('provider', dict(observed_at=1001))):
            with self.subTest(component=component, change=change):
                self.setUp()
                getattr(self, component).update(change)
                result = self.qualify(now=1001)
                self.assertEqual(result.state, 'UNKNOWN')
                self.assertIn(component.upper() + '_OUTSIDE_HOST_FENCE', result.reasons)

    def test_future_and_stale_observations_are_unknown(self):
        for name in ('database', 'provider', 'host'):
            for timestamp in (969, 1001):
                with self.subTest(component=name, timestamp=timestamp):
                    self.setUp()
                    getattr(self, name)['observed_at'] = timestamp
                    self.assertEqual(self.qualify().state, 'UNKNOWN')

    def test_noncanonical_duplicate_trailing_float_and_oversized_bytes_are_unknown(self):
        normal = k.encoded(self.database)
        bad = [normal + b'\n', normal.replace(b'"version":1', b'"version":1,"version":1'),
               normal.replace(b'"version":1', b'"version":1.0'), b' ' * 65537]
        for raw in bad:
            with self.subTest(size=len(raw)):
                result = r.qualify_readiness(raw, k.encoded(self.provider), k.encoded(self.host),
                    **self.expected, accepted_database_sha256=k.sha(raw),
                    accepted_provider_sha256=k.sha(k.encoded(self.provider)),
                    accepted_host_sha256=k.sha(k.encoded(self.host)), now=1000)
                self.assertEqual(result.state, 'UNKNOWN')
        self.assertEqual(self.qualify(accepted={'accepted_database_sha256': 'f' * 64}).state, 'UNKNOWN')

    def test_unexpected_fields_and_boolean_version_do_not_silently_expand_schema(self):
        for name in ('database', 'provider', 'host'):
            for change in ({'version': True}, {'command': 'arbitrary'}, {'source_policy': 'wildcard'}):
                with self.subTest(component=name, change=change):
                    self.setUp()
                    getattr(self, name).update(change)
                    self.assertEqual(self.qualify().state, 'UNKNOWN')

    def test_qualification_does_not_mutate_inputs_or_claim_historical_closure(self):
        old = deepcopy((self.database, self.provider, self.host))
        result = self.qualify()
        self.assertEqual((self.database, self.provider, self.host), old)
        self.assertFalse(result.replay_authorized)
        self.assertFalse(result.incident_closed)

    def test_snapshot_start_must_be_fresh_and_now_is_strict_integer(self):
        self.database.update(started_at=965, observed_at=970)
        self.assertEqual(self.qualify().state, 'UNKNOWN')
        for now in (True, -1, 1000.0):
            with self.subTest(now=now), self.assertRaises(r.Unknown):
                self.qualify(now=now)


if __name__ == '__main__':
    unittest.main()
