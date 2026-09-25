"""Exercise composition with the real queue/authority/delivery modules."""
from copy import deepcopy

import pytest

from oracle_autopilot import codex_cli_bridge as bridge
from oracle_autopilot.light_native_adapter import LightNativeAdapter, ProviderTarget, FALSE_FLAGS


@pytest.fixture
def rig():
    dispatch = '12345678-1234-4234-8234-123456789012'
    request = dict(dispatch_id=dispatch,
        reservation_id='12345678-1234-4234-8234-123456789013',
        expected_head_sha='a'*40, branch='codex/test', mode='READ_ONLY',
        target_pr=1546, task_fingerprint='b'*64,
        assignment=dict(dispatch_id=dispatch, execution_scope='REPOSITORY', can_repair=False, task_kind='REPOSITORY_AUDIT',
            task_spec_json=dict(repository='olegmed1-art/bridge-video-free', target_pr=1546,
                                expected_head_sha='a'*40, execution_mode='READ_ONLY')))
    spec = request['assignment']['task_spec_json']
    spec.update(assignment_schema='SLAVIK_DISPATCH_ASSIGNMENT_V1', exact_head_binding=True,
                cost_cap_microusd=0, max_repair_attempts=0)
    spec.update({key: False for key in FALSE_FLAGS})
    state = dict(row=dict(state='RESERVED', request=deepcopy(request),
                         provider_task_id=None, terminal=None),
                 intent=False, creation=None, calls=[], gate_calls=0, deny_at=None,
                 creates=0, finishes=0, allowed=True)

    def effect(name):
        state['calls'].append((name, state['gate_calls']))

    def gate(req, target):
        assert target == ProviderTarget("a"*32)
        assert req == request
        state['gate_calls'] += 1
        return state['allowed'] and state['gate_calls'] != state['deny_at']

    def rpc(sql, params):
        effect(sql)
        if 'snapshot' in sql:
            payload = deepcopy(state['row'])
        elif 'current' in sql:
            payload = True
        elif 'begin' in sql:
            payload = not state['intent']
            state['intent'] = True
        elif 'ack' in sql:
            state['row'].update(state='SUBMITTED', provider_task_id=params[1])
            payload = True
        elif 'finish' in sql:
            state['row'].update(state='TERMINAL', terminal=bridge.parse(params[2]))
            state['finishes'] += 1
            payload = True
        else:
            raise AssertionError('unexpected RPC, including reserve')
        return {'payload': payload}

    def read_pr(number):
        effect('read_pr')
        repo = dict(full_name='olegmed1-art/bridge-video-free')
        return dict(number=number, state='open', head=dict(sha='a'*40, ref='codex/test', repo=repo),
                    base=dict(repo=repo))

    class Provider:
        def lookup(self, req, *, target):
            assert target == ProviderTarget("a"*32)
            effect('lookup')
            return deepcopy(state['creation'])

        def submit(self, req, *, target):
            assert target == ProviderTarget("a"*32)
            effect('submit')
            state['creates'] += 1
            state['creation'] = dict(state='SUBMITTED', provider_task_id='task_e_adapter',
                                     prompt_sha256=bridge.digest(bridge.prompt_for(req)))
            return deepcopy(state['creation'])

        def collect(self, dispatch_id, *, target):
            assert target == ProviderTarget("a"*32)
            effect('collect')
            report = {k: request[k] for k in ('dispatch_id', 'expected_head_sha',
                                             'target_pr', 'task_fingerprint')}
            report.update(status='SUCCEEDED', result_code='VERIFIED', summary='Checked.')
            return dict(state='RESULT_RETRIEVED', provider_task_id='task_e_adapter',
                        report=report, report_sha256=bridge.digest(bridge.canonical(report)))

    def make(**overrides):
        args = dict(profile='light', target=ProviderTarget('a'*32), rpc=rpc, read_pr=read_pr, provider=Provider(), gate=gate)
        args.update(overrides)
        return LightNativeAdapter(request, **args)

    return request, state, make


def test_one_item_completes_and_terminal_replay_never_resubmits(rig):
    request, state, make = rig
    adapter = make()
    result = adapter.step()
    assert result['state'] == 'DONE'
    assert result['terminal']['status'] == 'SUCCEEDED'
    assert adapter.step() == result
    assert state['creates'] == state['finishes'] == 1
    # Each external port call has its own fresh successful admission check.
    checks = [check for _, check in state['calls']]
    assert len(checks) == len(set(checks))


@pytest.mark.parametrize('denied', range(1, 15))
def test_hold_at_each_boundary_stops_before_next_effect(rig, denied):
    request, state, make = rig
    state['deny_at'] = denied
    with pytest.raises(RuntimeError, match='LIGHT_PILOT_NOT_ADMITTED'):
        make().step()
    assert state['gate_calls'] == denied
    assert all(check < denied for _, check in state['calls'])
    assert state['creates'] <= 1


@pytest.mark.parametrize('value', [False, None, 0, 1, 'ACTIVE', 'HOLD', {}])
def test_absent_or_non_boolean_authorization_has_zero_calls(rig, value):
    _, state, make = rig
    with pytest.raises(RuntimeError, match='LIGHT_PILOT_NOT_ADMITTED'):
        make(gate=lambda request, target: value).step()
    assert state['calls'] == []


def test_hold_after_intent_does_not_regrant_creation(rig):
    _, state, make = rig
    # initial, snapshot, lookup, PR, current, begin, submit
    state['deny_at'] = 7
    adapter = make()
    with pytest.raises(RuntimeError):
        adapter.step()
    assert state['intent'] and state['creates'] == 0
    state['deny_at'] = None
    assert adapter.step()['state'] == 'SUBMISSION_UNKNOWN'
    assert state['creates'] == 0


def test_hold_after_create_recovers_same_provider_task(rig):
    _, state, make = rig
    state['deny_at'] = 8
    adapter = make()
    with pytest.raises(RuntimeError):
        adapter.step()
    assert state['creates'] == 1
    state['deny_at'] = None
    assert adapter.step()['state'] == 'DONE'
    assert state['creates'] == 1


@pytest.mark.parametrize('field,value', [('mode','VERIFY'), ('expected_head_sha','c'*40),
                                       ('dispatch_id','12345678-1234-4234-8234-123456789099')])
def test_changed_snapshot_is_rejected_before_provider_or_mutation(rig, field, value):
    _, state, make = rig
    state['row']['request'][field] = value
    with pytest.raises(ValueError, match='LIGHT_PILOT_BINDING_CHANGED'):
        make().step()
    assert len(state['calls']) == 1
    assert not state['intent']


@pytest.mark.parametrize('profile', ['ubuntu', 'service', None, ''])
def test_no_profile_fallback(rig, profile):
    _, state, make = rig
    with pytest.raises(ValueError, match='LIGHT_PROFILE_REQUIRED'):
        make(profile=profile)
    assert not state['calls']


def test_repair_rejected_before_any_effect(rig):
    req, state, make = rig
    req['mode'] = req['assignment']['task_spec_json']['execution_mode'] = 'REPAIR'
    req['assignment']['can_repair'] = True
    with pytest.raises(ValueError, match='LIGHT_PILOT_MODE_FORBIDDEN'):
        make()
    assert not state['calls']


@pytest.mark.parametrize('flag', FALSE_FLAGS)
@pytest.mark.parametrize('value', [None, True, 0])
def test_unsafe_or_missing_spec_rejected_without_effect(rig, flag, value):
    req, state, make = rig
    req['assignment']['task_spec_json'][flag] = value
    with pytest.raises(ValueError, match='LIGHT_PILOT_SCOPE_FORBIDDEN'):
        make()
    assert state['gate_calls'] == 0 and not state['calls']


@pytest.mark.parametrize('field', ['cost_cap_microusd', 'max_repair_attempts'])
@pytest.mark.parametrize('value', [False, None, 1, '0'])
def test_cost_and_repair_bounds_are_exact_integer_zero(rig, field, value):
    req, state, make = rig
    req['assignment']['task_spec_json'][field] = value
    with pytest.raises(ValueError, match='LIGHT_PILOT_SCOPE_FORBIDDEN'):
        make()
    assert not state['calls']


def test_snapshot_bool_integer_type_confusion_rejected(rig):
    _, state, make = rig
    state['row']['request']['assignment']['task_spec_json']['paid_action'] = 0
    with pytest.raises(ValueError, match='LIGHT_PILOT_BINDING_CHANGED'):
        make().step()
    assert len(state['calls']) == 1


def test_caller_and_gate_mutation_do_not_change_captured_request(rig):
    req, state, make = rig
    seen = []
    def gate(copy, target):
        seen.append(deepcopy(copy))
        copy['assignment']['task_spec_json']['paid_action'] = True
        return True
    adapter = make(gate=gate)
    original = deepcopy(req)
    req['assignment']['task_spec_json']['paid_action'] = True
    # First provider lookup sees only the captured request, even though the
    # caller and each gate invocation attempted to change it.
    class StopProvider:
        def lookup(self, copy, *, target):
            assert copy == original
            copy['mode'] = 'REPAIR'
            raise ConnectionError('STOP_BEFORE_CREATION')
    adapter._provider = StopProvider()
    with pytest.raises(ConnectionError, match='STOP_BEFORE_CREATION'):
        adapter.step()
    assert all(copy == original for copy in seen)
    assert adapter._request == original
    assert not state['intent']


@pytest.mark.parametrize('kwargs', [dict(profile='ubuntu'), dict(repository='other/repo'),
                                  dict(environment_id='bridge-video-free'),
                                  dict(environment_id='BRIDGE-VIDEO-FREE'),
                                  dict(environment_id='Bridge-Video-Free'),
                                  dict(environment_id=''), dict(environment_id='bad id')])
def test_invalid_provider_target_rejected(kwargs):
    fields = dict(environment_id='a'*32)
    fields.update(kwargs)
    with pytest.raises(ValueError, match='LIGHT_PROVIDER_TARGET_INVALID'):
        ProviderTarget(**fields)


def test_verify_mode_does_not_confuse_role_repair_capability_with_permission(rig):
    req, state, make = rig
    req['mode'] = req['assignment']['task_spec_json']['execution_mode'] = 'VERIFY'
    req['assignment']['task_kind'] = 'REPOSITORY_VERIFY'
    req['assignment']['can_repair'] = True
    state['row']['request'] = deepcopy(req)
    assert make().step()['state'] == 'DONE'
    assert state['creates'] == 1


def test_legacy_global_bridge_cannot_be_used_as_bound_provider(rig):
    _, state, make = rig
    with pytest.raises(TypeError):
        make(provider=bridge).step()
    assert not state['intent'] and state['creates'] == 0
