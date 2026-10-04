"""Synthetic observer ports with real intake/terminal/goal/work verification.

Filesystem authenticity is tested separately by the retained/Snapshot suite.
Here local() is an explicit validated-input port; host/provider/SQL transports
are synthetic. Plan, terminal_evidence, _terminal_rows, verify_terminal and
observe_terminal are real production functions, never permissive replacements.
"""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
import os
import sys

import pytest

from database import light_native_pilot_intake as intake
from ops import light_native_lane_controller as owner
from ops import light_native_lane_feed as feed
from ops import light_native_lane_owner as acceptance
from ops import light_native_retirement as r
from ops import light_native_retirement_live as live
from oracle_autopilot import light_native_lane as lane
from test_light_native_lane_owner import setup as terminal_setup
from test_light_native_retirement_faults import _record
from test_light_native_retirement import retained, request_bytes


class Rows:
    def __init__(self, value):
        self.value = value
    def fetchone(self):
        return deepcopy(self.value)
    def fetchall(self):
        return deepcopy(self.value)


@pytest.fixture
def observer_ports(tmp_path, monkeypatch):
    # The existing terminal fixture uses fake prepare to construct a complete
    # retained receipt. Its durable manifest port is explicitly in-memory here.
    manifests = {}
    def save(path, value):
        raw = intake.encoded(value)
        manifests[str(path)] = raw
        return r.sha(raw)
    def load(path, pin):
        raw = manifests[str(path)]
        assert r.sha(raw) == pin
        return r.parse(raw)
    monkeypatch.setattr(intake.engine, 'save_manifest', save)
    monkeypatch.setattr(intake.engine, 'load_manifest', load)
    conn, prior_plan, receipt, intent, terminal, permit, provider, calls = terminal_setup(tmp_path, monkeypatch)
    conn.config['enabled'] = False
    conn.role['can_repair'] = True
    conn.events.clear()
    original_execute = conn.execute
    counts = [0, 0, 0, 0]
    faults = {}
    def execute(sql, args=()):
        if sql.startswith('SELECT (SELECT count(*) FROM autopilot.project_work_item'):
            conn.events.append(sql)
            return Rows(tuple(counts))
        if sql.startswith('SELECT task_id::text,run_kind FROM autopilot.project_work_task'):
            conn.events.append(sql)
            return Rows([(receipt['task_id'], 'AUDIT')])
        if sql.startswith('SELECT count(*) FROM autopilot.role_dispatch_outbox WHERE prior_task_id'):
            conn.events.append(sql)
            return Rows((0,))
        result = original_execute(sql, args)
        if 'project_work_item w' in sql and faults.get('work'):
            row = deepcopy(result.fetchone()[0])
            row['last_task_id'] = '99999999-9999-4999-8999-999999999999'
            return Rows((row,))
        return result
    conn.execute = execute

    failed_value = deepcopy(prior_plan.value)
    failed_value['work_key'] = 'synthetic-canceled-work'
    failed_value['target_pr'] = 1151
    failed_value['task_spec_json']['target_pr'] = 1151
    failed_raw = r.encoded(failed_value)
    failed_plan = intake.Plan(failed_raw, r.sha(failed_raw))
    anchor_intent = r.parse(intent)
    anchor_intent.update(sequence=2, previous_terminal_sha256='d'*64)
    anchor_intent = r.encoded(anchor_intent)
    old_terminal = r.parse(terminal)
    anchor_terminal = r.encoded(lane.terminal_record(anchor_intent, old_terminal['request'], old_terminal['result']))
    envelope = dict(version=1, plan_sha256=prior_plan.digest, dispatch_id=receipt['dispatch_id'],
        task_id=receipt['task_id'], provider_task_id=provider['provider_task_id'],
        request=deepcopy(old_terminal['request']), result=deepcopy(old_terminal['result']['terminal']))
    previous = dict(sequence=2, plan_sha256=prior_plan.digest, terminal_sha256=r.sha(r.encoded(envelope)))
    value = r.parse(_record())
    value.update(plan_sha256=failed_plan.digest, failed_work_key=failed_plan.value['work_key'],
        failed_target_pr=failed_plan.value['target_pr'], repository=failed_plan.value['repository'],
        predecessor=previous, retained_runtime_source=prior_plan.value['source'])
    legacy = intake.hold.attest()  # Existing fixture stub; no host read.
    native = {'InvocationID': 'synthetic-held-process'}
    data = dict(raw_plan=failed_raw, prior_plan=prior_plan.raw, terminal=r.encoded(envelope),
        receipt=receipt, complete={'native': native},
        prepare={'accepted_agreement_sha256': 'e'*64},
        before=dict(version=1, plan_sha256=failed_plan.digest, target=intake.EXPECTED_TARGET,
            native_config=deepcopy(conn.config), autopilot_role=deepcopy(conn.role)),
        baseline=dict(source=failed_plan.value['source'], package_sha256=value['retained_runtime_sha256'],
            scope_sha256=failed_plan.scope_digest, agreement_sha256='e'*64,
            prior=asdict(legacy), protected={}, protected_sha256=r.sha(r.encoded({}))))
    # Prefix validity belongs to feed.verify_serial_hold; this synthetic port
    # supplies its checked return shape, with a fully real sequence-2 terminal.
    history = [(r.encoded({'dispatch_id': 'synthetic-prefix-'+str(n)}), b'{}') for n in range(2)]
    history.append((anchor_intent, anchor_terminal))
    control = tmp_path/'control'
    (control/'jobs').mkdir(parents=True)
    for raw, _ in history:
        (control/'jobs'/r.parse(raw)['dispatch_id']).mkdir()
    ledger = tmp_path/'ledger'
    ledger.mkdir()
    monkeypatch.setattr(owner.install, 'CONTROL', control)
    monkeypatch.setattr(owner.install, 'LEDGER', ledger)
    monkeypatch.setattr(owner.install, 'RETAINED_SOURCE', prior_plan.value['source'])
    monkeypatch.setattr(owner.execution, 'stopped', lambda: None)
    monkeypatch.setattr(live, 'local', lambda view, record, pins: deepcopy(data))
    monkeypatch.setattr(feed, 'verify_serial_hold', lambda source, cursor: (deepcopy(native), deepcopy(history)))
    accepted = lane.encoded(lane.acceptance_record(anchor_intent, anchor_terminal))
    def root_record(path, limit=65536):
        if path == control/'current.json':
            return history[-1][0]
        if path == control/'jobs'/receipt['dispatch_id']/'permit.json':
            return permit
        if path == control/'jobs'/receipt['dispatch_id']/'accepted-terminal.json':
            return accepted
        raise AssertionError('Unexpected synthetic host read')
    monkeypatch.setattr(feed, 'root_record', root_record)
    provider_hook = [lambda: None]
    def fresh(source, request, environment):
        assert source == prior_plan.value['source']
        assert request == envelope['request']
        calls.append('fresh_provider')
        provider_hook[0]()
        return deepcopy(provider)
    monkeypatch.setattr(acceptance, 'fresh_provider_result', fresh)
    checks = []
    view = SimpleNamespace(rows={'synthetic': 'validated-local-port'}, check=lambda: checks.append('snapshot'))
    guard = SimpleNamespace(assert_current=lambda: checks.append('authority'))
    return SimpleNamespace(value=value, data=data, conn=conn, counts=counts, faults=faults,
        history=history, previous=previous, envelope=envelope, view=view, guard=guard,
        provider_hook=provider_hook, provider_calls=calls, control=control, checks=checks,
        descendants=[], latest=previous)


def observe(t):
    return live.observer(t.view, t.value, t.conn, t.guard, pins={},
                         latest=t.latest, descendants=t.descendants)


def add_descendant(t):
    envelope = deepcopy(t.envelope)
    envelope.update(plan_sha256='a'*64, dispatch_id='44444444-4444-4444-8444-444444444444',
                    task_id='55555555-5555-4555-8555-555555555555')
    envelope['request']['dispatch_id'] = envelope['dispatch_id']
    intent = {'dispatch_id': envelope['dispatch_id']}
    terminal = dict(request=deepcopy(envelope['request']), result=dict(
        provider_task_id=envelope['provider_task_id'], terminal=deepcopy(envelope['result'])))
    t.history.append((r.encoded(intent), r.encoded(terminal)))
    (t.control/'jobs'/envelope['dispatch_id']).mkdir()
    item = dict(sequence=3, plan_sha256=envelope['plan_sha256'], terminal_sha256=r.sha(r.encoded(envelope)),
                dispatch_id=envelope['dispatch_id'], predecessor=deepcopy(t.previous),
                work_key='synthetic-authorized-new-work', terminal=envelope)
    t.descendants.append(item)
    t.latest = {k: item[k] for k in ('sequence', 'plan_sha256', 'terminal_sha256')}
    return item


def test_initial_sequence_two_real_terminal_goal_work_checks(observer_ports):
    t = observer_ports
    result = observe(t)
    assert result['latest'] == t.previous
    assert t.provider_calls == ['fresh_provider']
    assert sum('native_cli_receipt n' in q for q in t.conn.events) == 3
    assert sum('autopilot.task t WHERE task_id' in q for q in t.conn.events) == 3
    assert sum('project_work_item w' in q for q in t.conn.events) == 3
    assert not any(q.lstrip().startswith(('INSERT', 'UPDATE', 'DELETE', 'LOCK')) for q in t.conn.events)
    assert t.checks == ['authority', 'snapshot', 'authority']


def test_authorized_sequence_three_then_later_scan_uses_latest_three(observer_ports):
    t = observer_ports
    add_descendant(t)
    assert observe(t)['latest'] == t.latest
    assert observe(t)['latest'] == t.latest
    assert t.value['predecessor']['sequence'] == 2


@pytest.mark.parametrize('fault', ['terminal', 'unapproved', 'canceled-work', 'stale-latest', 'predecessor'])
def test_descendant_disagreement_refuses(observer_ports, fault):
    t = observer_ports
    item = add_descendant(t)
    if fault == 'terminal':
        changed = r.parse(t.history[3][1])
        changed['result']['terminal']['summary'] = 'Altered synthetic result'
        t.history[3] = (t.history[3][0], r.encoded(changed))
    if fault == 'unapproved':
        t.descendants.clear()
    if fault == 'canceled-work':
        item['work_key'] = t.value['failed_work_key']
    if fault == 'stale-latest':
        t.latest = t.previous
    if fault == 'predecessor':
        item['predecessor'] = dict(t.previous, terminal_sha256='0'*64)
    with pytest.raises(RuntimeError, match='LANE_RETIREMENT_(DESCENDANTS|CANCELED_WORK)'):
        observe(t)
    assert t.provider_calls == []


@pytest.mark.parametrize('fault', ['controls', 'activity'])
def test_provider_interval_database_change_is_rechecked(observer_ports, fault):
    t = observer_ports
    def mutate():
        if fault == 'controls':
            t.conn.config['enabled'] = True
        else:
            t.counts[0] = 1
    t.provider_hook[0] = mutate
    with pytest.raises(RuntimeError, match='LANE_RETIREMENT_(CONTROLS|ACTIVITY)'):
        observe(t)
    assert t.provider_calls == ['fresh_provider']


@pytest.mark.parametrize('fault', ['goal', 'work', 'retained-assignment'])
def test_real_intake_binding_helpers_reject_changed_evidence(observer_ports, fault):
    t = observer_ports
    if fault == 'goal':
        t.conn.goal['unexpected'] = 'changed synthetic DB goal'
    if fault == 'work':
        t.faults['work'] = True
    if fault == 'retained-assignment':
        t.data['receipt']['assignment'] = dict(t.data['receipt']['assignment'], task_id='9'*36)
    with pytest.raises(RuntimeError, match='PILOT_TERMINAL_(DB_DRIFT|BINDING)'):
        observe(t)
    assert t.provider_calls == []


@pytest.mark.skipif(sys.platform != 'linux' or getattr(os, 'geteuid', lambda: -1)() != 0,
                    reason='Actual Snapshot/catalogue requires isolated Linux/root')
def test_completed_descendant_policy_must_pin_exact_retirement_hash(retained, monkeypatch):
    import test_light_native_retirement as fixtures
    t = retained
    fixtures.retain_proposal(t)
    unapproved = fixtures.new_policy(t)
    unapproved['retirements'] = [dict(t.ref, record_sha256='0'*64)]
    # Build every durable descendant record under its CORRECT changed policy
    # hash. Failure must be the retirement allowlist, not a broken file hash.
    monkeypatch.setattr(fixtures, 'new_policy', lambda _: deepcopy(unapproved))
    policy, _, _ = fixtures.completed_new(t, monkeypatch)
    with pytest.raises(RuntimeError, match='LANE_RETIREMENT_DESCENDANTS'):
        with r.Snapshot(t.root, 'unused') as view:
            live.catalogue(view, [t.ref], evidence=policy['retirement_evidence'])
