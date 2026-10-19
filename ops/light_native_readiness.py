"""Pure, momentary qualification of fixed read-only evidence; never a permit.

The authenticated root adapter must obtain complete primary evidence and supply
the independently pinned target/query/profile identities. Accepted byte hashes
bind integrity/provenance inputs; hashing a model assertion is not acceptance.
This module performs no I/O, SQL, provider calls, locking or service operations.
READY_FOR_TRANSITION cannot replace transition guards, a live fence, admission,
historical reconciliation or a separately accepted finite Agreement.
"""
from dataclasses import dataclass, field
import re

from ops import light_native_legacy_transition as transition


encoded = transition.encoded
sha = transition.sha
MAX_COUNT = 10**9
TASK_STATUSES = ('NEW', 'VALIDATING', 'READY', 'RUNNING', 'WAITING_EXTERNAL', 'EVALUATING',
                 'OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP', 'DONE', 'CANCELLED')
LIVE_TASK_STATUSES = TASK_STATUSES[:6]
OUTBOX_STATUSES = ('PENDING', 'CLAIMED', 'RETRY', 'PUBLISHED', 'DELIVERY_FAILED', 'SENT',
                   'CALLBACK_ACCEPTED', 'FAILED_CLOSED')
LIVE_OUTBOX_STATUSES = OUTBOX_STATUSES[:6]
OBLIGATION_COUNTS = ('retained_task_leases', 'retained_outbox_claims', 'resource_leases',
    'probe_leases', 'nonterminal_native_receipts', 'active_work_items', 'legacy_backends',
    'provider_probe_inflight')
DB_KEYS = ('version', 'kind', 'started_at', 'observed_at', 'database_identity_sha256',
    'snapshot_identity_sha256', 'query_profile_sha256', 'scope', 'transaction_read_only',
    'isolation', 'statement_timeout_ms', 'controls', 'task_total', 'task_status_counts',
    'outbox_total', 'outbox_status_counts', 'obligation_counts')
PROVIDER_KEYS = ('version', 'kind', 'started_at', 'observed_at', 'provider_identity_sha256',
    'profile_sha256', 'coverage', 'external_inventory_sha256', 'journal_inventory_sha256',
    'inflight_count', 'external_unresolved_count', 'journal_unresolved_count')
HOST_KEYS = ('version', 'kind', 'observed_at', 'legacy', 'native', 'fence', 'lease')


class Unknown(ValueError):
    pass


def _need(condition):
    if not condition:
        raise Unknown('READINESS_EVIDENCE_UNKNOWN')


def _keys(value, names):
    _need(type(value) is dict and set(value) == set(names))


def _integer(value, maximum=2**63 - 1):
    _need(type(value) is int and 0 <= value <= maximum)


def _pin(value):
    _need(type(value) is str and re.fullmatch('[0-9a-f]{64}', value))


def _expected(actual, expected):
    _pin(actual)
    _pin(expected)
    _need(actual == expected)


def _fresh(observed_at, now):
    _integer(observed_at)
    _need(0 <= now - observed_at <= 30)


def _read(raw, accepted, names, kind):
    _need(type(raw) is bytes and 0 < len(raw) <= 65536)
    _pin(accepted)
    _need(sha(raw) == accepted)
    value = transition.parse(raw)
    _keys(value, names)
    _need(type(value['version']) is int and value['version'] == 1 and value['kind'] == kind)
    return value


def _times(value, now, maximum_duration):
    _fresh(value['observed_at'], now)
    _fresh(value['started_at'], now)
    _need(0 <= value['observed_at'] - value['started_at'] <= maximum_duration)


def _counts(value, names):
    _keys(value, names)
    for count in value.values():
        _integer(count, MAX_COUNT)
    return value


def _positive(value):
    return type(value) is int and 0 < value <= MAX_COUNT


@dataclass(frozen=True)
class Component:
    state: str
    reasons: tuple[str, ...]


def _component(blockers, unknown):
    return Component('NOT_READY' if blockers else 'UNKNOWN' if unknown else 'READY',
                     tuple(sorted(set(blockers + unknown))))


@dataclass(frozen=True)
class Readiness:
    """Diagnostic value, deliberately not an authorization/capability type."""
    state: str
    evaluated_at: int
    component_states: tuple[tuple[str, str], ...]
    reasons: tuple[str, ...]
    evidence_sha256: tuple[tuple[str, str | None], ...]
    activation_authorized: bool = field(default=False, init=False)
    native_admission_authorized: bool = field(default=False, init=False)
    transition_authorized: bool = field(default=False, init=False)
    incident_closed: bool = field(default=False, init=False)
    historical_chain_verified: bool = field(default=False, init=False)
    original_baseline_matched: bool = field(default=False, init=False)
    replay_authorized: bool = field(default=False, init=False)
    new_task_authorized: bool = field(default=False, init=False)

    def public(self):
        # No raw observations, target names, SQL, owner identity or authority.
        return dict(version=1, audit='LIGHT_NATIVE_READINESS', state=self.state,
            evaluated_at=self.evaluated_at, component_states=dict(self.component_states),
            reasons=list(self.reasons), evidence_sha256=dict(self.evidence_sha256),
            activation_authorized=False, native_admission_authorized=False,
            transition_authorized=False, incident_closed=False, historical_chain_verified=False,
            original_baseline_matched=False, replay_authorized=False, new_task_authorized=False)


def _database(raw, accepted, identity, query_profile, now):
    blockers, unknown = [], []
    try:
        value = _read(raw, accepted, DB_KEYS, 'LIGHT_NATIVE_DATABASE_OBSERVATION')
        _times(value, now, 5)
        _expected(value['database_identity_sha256'], identity)
        _expected(value['query_profile_sha256'], query_profile)
        _pin(value['snapshot_identity_sha256'])
        _need(value['scope'] == 'ALL_ROWS' and value['transaction_read_only'] is True and
              value['isolation'] == 'REPEATABLE_READ' and
              type(value['statement_timeout_ms']) is int and value['statement_timeout_ms'] == 5000)
    except (Unknown, transition.Refusal, TypeError, ValueError, KeyError):
        return Component('UNKNOWN', ('DATABASE_EVIDENCE_UNKNOWN',)), None
    try:
        controls = value['controls']
        _keys(controls, ('native_enabled', 'planner_enabled', 'autopilot_role_enabled', 'autopilot_can_repair'))
        _need(all(type(flag) is bool for flag in controls.values()))
        # can_repair is recorded retained capability. A disabled role cannot
        # grant authority through this read-only classifier, regardless of it.
    except (Unknown, TypeError, KeyError):
        unknown.append('DATABASE_CONTROLS_UNKNOWN')
    if type(value['controls']) is dict:
        if value['controls'].get('native_enabled') is True:
            blockers.append('DATABASE_NATIVE_ENABLED')
        if value['controls'].get('planner_enabled') is True or value['controls'].get('autopilot_role_enabled') is True:
            blockers.append('DATABASE_PRODUCER_FENCE_NOT_PROVEN')
    for label, counts_key, total_key, statuses, live in (
        ('TASK', 'task_status_counts', 'task_total', TASK_STATUSES, LIVE_TASK_STATUSES),
        ('OUTBOX', 'outbox_status_counts', 'outbox_total', OUTBOX_STATUSES, LIVE_OUTBOX_STATUSES)):
        try:
            counts = _counts(value[counts_key], statuses)
            _integer(value[total_key], MAX_COUNT)
            _need(sum(counts.values()) == value[total_key])
        except (Unknown, TypeError, KeyError):
            unknown.append('DATABASE_' + label + '_COUNTS_UNKNOWN')
        if type(value[counts_key]) is dict and any(_positive(value[counts_key].get(status)) for status in live):
            blockers.append('DATABASE_LIVE_' + label)
    try:
        counts = _counts(value['obligation_counts'], OBLIGATION_COUNTS)
    except (Unknown, TypeError, KeyError):
        unknown.append('DATABASE_OBLIGATION_COUNTS_UNKNOWN')
    if type(value['obligation_counts']) is dict:
        for name in OBLIGATION_COUNTS:
            if _positive(value['obligation_counts'].get(name)):
                blockers.append('DATABASE_' + name.upper())
    return _component(blockers, unknown), value


def _provider(raw, accepted, identity, profile, now):
    blockers, unknown = [], []
    try:
        value = _read(raw, accepted, PROVIDER_KEYS, 'LIGHT_NATIVE_PROVIDER_OBSERVATION')
        _times(value, now, 30)
        _expected(value['provider_identity_sha256'], identity)
        _expected(value['profile_sha256'], profile)
        _need(value['coverage'] in ('FULL_SERVICE_PROFILE', 'PARTIAL'))
    except (Unknown, transition.Refusal, TypeError, ValueError, KeyError):
        return Component('UNKNOWN', ('PROVIDER_EVIDENCE_UNKNOWN',)), None
    if value['coverage'] != 'FULL_SERVICE_PROFILE':
        unknown.append('PROVIDER_COVERAGE_INCOMPLETE')
    for name in ('external_inventory_sha256', 'journal_inventory_sha256'):
        try:
            _pin(value[name])
        except Unknown:
            unknown.append('PROVIDER_' + name.upper() + '_UNKNOWN')
    for name in ('inflight_count', 'external_unresolved_count', 'journal_unresolved_count'):
        try:
            _integer(value[name], MAX_COUNT)
            if value[name]:
                blockers.append('PROVIDER_' + name.upper())
        except Unknown:
            unknown.append('PROVIDER_' + name.upper() + '_UNKNOWN')
    return _component(blockers, unknown), value


def _host(raw, accepted, expected_legacy, expected_native, expected_lease, now):
    blockers, unknown = [], []
    try:
        value = _read(raw, accepted, HOST_KEYS, 'LIGHT_NATIVE_HOST_FENCE_OBSERVATION')
        _fresh(value['observed_at'], now)
        legacy = value['legacy']
        _keys(legacy, set(transition.Snapshot.__dataclass_fields__) - {'drop_bytes'} | {'drop_base64'})
        binding = {key: item for key, item in legacy.items() if key != 'observed_at'}
        snapshot = transition.Snapshot.from_binding(binding)
        # Existing exact current-process/configuration validator, no HoldIdentity.
        snapshot = transition.Snapshot(observed_at=legacy['observed_at'],
            **{key: getattr(snapshot, key) for key in transition.Snapshot.__dataclass_fields__ if key != 'observed_at'})
        snapshot.validate(now)
        _expected(sha(encoded(binding)), expected_legacy)
        _need(legacy['observed_at'] <= value['observed_at'])
    except (Unknown, transition.Refusal, TypeError, ValueError, KeyError):
        return Component('UNKNOWN', ('HOST_EVIDENCE_UNKNOWN',)), None
    if (snapshot.state != 'STOPPED' or not snapshot.cgroup_empty or snapshot.admission != 'HOLD' or snapshot.needs_reload or
            snapshot.drop_bytes != transition.drop_bytes(snapshot.release, 'HOLD')):
        blockers.append('HOST_LEGACY_NOT_FENCED')
    try:
        native = value['native']
        _keys(native, transition.NativeHold.__dataclass_fields__)
        _fresh(native['observed_at'], now)
        _need(native['observed_at'] <= value['observed_at'])
        native_binding = {key: item for key, item in native.items() if key != 'observed_at'}
        _expected(sha(encoded(native_binding)), expected_native)
        transition.NativeHold(**native).validate(now)
    except (Unknown, transition.Refusal, TypeError, ValueError, KeyError):
        unknown.append('HOST_NATIVE_HOLD_UNKNOWN')
    fence, lease = value['fence'], value['lease']
    try:
        _keys(fence, ('established_at', 'verified_at', 'expires_at', 'evidence_sha256',
                      'legacy_snapshot_sha256', 'mechanism'))
        for name in ('established_at', 'verified_at', 'expires_at'):
            _integer(fence[name])
        _fresh(fence['verified_at'], now)
        _pin(fence['evidence_sha256'])
        _need(fence['legacy_snapshot_sha256'] == expected_legacy and
              fence['mechanism'] == 'STOPPED_CGROUP_ABSENCE' and
              fence['established_at'] <= legacy['observed_at'] <= fence['verified_at'] <= value['observed_at'] and
              0 < fence['expires_at'] - fence['verified_at'] <= 300)
        if fence['expires_at'] <= now:
            blockers.append('HOST_FENCE_EXPIRED')
    except (Unknown, TypeError, KeyError):
        unknown.append('HOST_FENCE_UNKNOWN')
        fence = None
    try:
        _keys(lease, ('identity_sha256', 'acquired_at', 'verified_at', 'expires_at', 'evidence_sha256',
                      'exclusive', 'active_consumers', 'scope', 'owner'))
        _expected(lease['identity_sha256'], expected_lease)
        _pin(lease['evidence_sha256'])
        for name in ('acquired_at', 'verified_at', 'expires_at'):
            _integer(lease[name])
        _fresh(lease['verified_at'], now)
        _need(lease['acquired_at'] <= lease['verified_at'] <= value['observed_at'] and
              0 < lease['expires_at'] - lease['verified_at'] <= 300 and
              type(lease['exclusive']) is bool and lease['scope'] == 'ALL_LEGACY_PRODUCERS')
        _integer(lease['active_consumers'], MAX_COUNT)
        if not lease['exclusive'] or lease['active_consumers']:
            blockers.append('HOST_LEASE_NOT_EXCLUSIVE')
        if lease['expires_at'] <= now:
            blockers.append('HOST_LEASE_EXPIRED')
        owner = lease['owner']
        _keys(owner, ('unit', 'pid', 'invocation_id', 'start_ticks', 'boot_id'))
        _need(type(owner['unit']) is str and re.fullmatch(r'[A-Za-z0-9_.@-]{1,190}\.service', owner['unit']) and
              owner['unit'] not in (snapshot.unit, value['native']['unit']))
        _integer(owner['pid'], 2**31 - 1)
        _integer(owner['start_ticks'])
        _need(owner['pid'] > 1 and owner['start_ticks'] > 0 and type(owner['invocation_id']) is str and
              re.fullmatch('[0-9a-f]{32}', owner['invocation_id']) and type(owner['boot_id']) is str and
              re.fullmatch('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', owner['boot_id']))
    except (Unknown, TypeError, KeyError):
        unknown.append('HOST_LEASE_UNKNOWN')
        lease = None
    window = None
    if fence is not None and lease is not None:
        if lease['acquired_at'] > fence['established_at']:
            unknown.append('HOST_FENCE_PRECEDES_LEASE')
        else:
            window = (fence['established_at'], min(fence['verified_at'], lease['verified_at']))
    return _component(blockers, unknown), window


def qualify_readiness(database_raw, provider_raw, host_raw, *, expected_database_identity_sha256,
    expected_query_profile_sha256, expected_provider_identity_sha256, expected_provider_profile_sha256,
    expected_host_snapshot_sha256, expected_native_hold_sha256, expected_lease_identity_sha256,
    accepted_database_sha256, accepted_provider_sha256, accepted_host_sha256, now):
    """Classify authenticated observations at now, never return authority.

    Missing/unverifiable evidence is UNKNOWN. A verified known blocker takes
    precedence as NOT_READY, preserving every component's separate state. A
    quiet snapshot remains only a point observation until producer controls,
    current STOPPED HOLD, an exclusive lease and full provider evidence agree.
    """
    _integer(now)
    db, database = _database(database_raw, accepted_database_sha256,
        expected_database_identity_sha256, expected_query_profile_sha256, now)
    provider, inventory = _provider(provider_raw, accepted_provider_sha256,
        expected_provider_identity_sha256, expected_provider_profile_sha256, now)
    host, window = _host(host_raw, accepted_host_sha256, expected_host_snapshot_sha256,
                        expected_native_hold_sha256, expected_lease_identity_sha256, now)
    # A pre-fence DB/provider observation cannot be repackaged with a later host
    # stop. Both must be captured inside the same still-verified fence/lease.
    if window is not None:
        start, verified = window
        for name, component, value in (('DATABASE', db, database), ('PROVIDER', provider, inventory)):
            if value is not None and not (start <= value['started_at'] <= value['observed_at'] <= verified):
                updated = Component('NOT_READY' if component.state == 'NOT_READY' else 'UNKNOWN',
                    tuple(sorted((*component.reasons, name + '_OUTSIDE_HOST_FENCE'))))
                if name == 'DATABASE': db = updated
                else: provider = updated
    components = (('database', db), ('provider', provider), ('host', host))
    state = ('NOT_READY' if any(value.state == 'NOT_READY' for _, value in components) else
             'UNKNOWN' if any(value.state == 'UNKNOWN' for _, value in components) else 'READY_FOR_TRANSITION')
    return Readiness(state, now, tuple((name, value.state) for name, value in components),
        tuple(sorted({reason for _, value in components for reason in value.reasons})),
        tuple((name, sha(raw) if type(raw) is bytes and len(raw) <= 65536 else None)
              for name, raw in (('database', database_raw), ('provider', provider_raw), ('host', host_raw))))
