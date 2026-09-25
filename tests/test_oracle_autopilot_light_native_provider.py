"""Filesystem journal and real subprocess construction; never call the cloud."""
from dataclasses import asdict
from copy import deepcopy
import subprocess

import pytest

from oracle_autopilot import codex_cli_bridge as bridge
from oracle_autopilot import light_native_provider as module
from oracle_autopilot.light_native_adapter import ProviderTarget


@pytest.fixture
def rig(tmp_path, monkeypatch):
    dispatch = '12345678-1234-4234-8234-123456789012'
    request = dict(dispatch_id=dispatch,
        reservation_id='12345678-1234-4234-8234-123456789013',
        expected_head_sha='a'*40, branch='codex/test', mode='READ_ONLY',
        target_pr=1546, task_fingerprint='b'*64,
        assignment=dict(dispatch_id=dispatch, execution_scope='REPOSITORY', can_repair=False,
            task_spec_json=dict(repository='olegmed1-art/bridge-video-free', target_pr=1546,
                                expected_head_sha='a'*40, execution_mode='READ_ONLY')))
    target = ProviderTarget('a'*32)
    monkeypatch.setattr(module, 'LIGHT_ROOT', tmp_path)
    state_dir = tmp_path / 'runtime/codex-dispatch'
    journal = state_dir / (dispatch+'.json')
    state = dict(calls=[], timeout=False, invalid=False)

    def run(argv, **kwargs):
        state['calls'].append((argv, kwargs))
        assert argv[0] == str(tmp_path / 'runtime-bin/codex')
        assert argv[1:3] == ['-c', 'forced_login_method="chatgpt"']
        assert kwargs['env'] == dict(HOME=str(tmp_path/'runtime'),
            CODEX_HOME=str(tmp_path/'runtime/codex-home'),
            PATH='/usr/local/bin:/usr/bin:/bin', LANG='C.UTF-8', LC_ALL='C.UTF-8')
        assert kwargs['timeout'] == 90 and kwargs['capture_output'] is True
        assert kwargs.get('shell', False) is False
        if argv[4] == 'exec':
            saved = bridge.parse(journal.read_text())
            assert saved['state'] == 'SUBMISSION_UNKNOWN'
            assert saved['provider_binding'] == asdict(target)
            assert saved['request'] == request
            assert saved['prompt_sha256'] == bridge.digest(kwargs['input'])
            assert argv[5:7] == ['--env', target.environment_id]
            assert 'bridge-video-free' not in argv
            if state['timeout']:
                raise subprocess.TimeoutExpired(argv, 90)
            output = 'ambiguous' if state['invalid'] else 'https://chatgpt.com/codex/tasks/task_e_bound'
        elif argv[4] == 'status':
            output = '[READY] test\n'
        else:
            assert argv[4] == 'diff'
            report = {k:request[k] for k in ('dispatch_id', 'expected_head_sha', 'target_pr', 'task_fingerprint')}
            report.update(status='SUCCEEDED', result_code='VERIFIED', summary='Checked.', evidence=[])
            path = bridge.report_path(request)
            output = (f'diff --git a/{path} b/{path}\nnew file mode 100644\n'
                      f'--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+'
                      +bridge.canonical(report)+'\n')
        return subprocess.CompletedProcess(argv, 0, output, '')

    monkeypatch.setattr(module.subprocess, 'run', run)
    return request, target, module.LightProvider(target), state, state_dir, journal


def test_exact_target_saved_before_create_and_terminal_cache_is_frozen(rig, monkeypatch):
    req, target, provider, state, state_dir, journal = rig
    monkeypatch.setenv('DATABASE_URL', 'must-not-inherit')
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-inherit')
    globals_before = bridge.PROFILE, bridge.CLI, bridge.STATE
    first = provider.submit(req, target=target)
    assert provider.submit(req, target=target) == first
    assert provider.lookup(req, target=target) == first
    result = provider.collect(req['dispatch_id'], target=target)
    assert result['state'] == 'RESULT_RETRIEVED'
    assert provider.collect(req['dispatch_id'], target=target) == result
    assert len(state['calls']) == 3  # create, status, diff only once
    assert (bridge.PROFILE, bridge.CLI, bridge.STATE) == globals_before
    assert journal.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('failure', ['timeout', 'invalid'])
def test_unknown_creation_keeps_binding_and_never_retries(rig, failure):
    req, target, provider, state, _, journal = rig
    state[failure] = True
    first = provider.submit(req, target=target)
    assert first['state'] == 'SUBMISSION_UNKNOWN'
    assert provider.submit(req, target=target) == first
    assert provider.collect(req['dispatch_id'], target=target) == first
    assert bridge.parse(journal.read_text())['provider_binding'] == asdict(target)
    assert len(state['calls']) == 1


@pytest.mark.parametrize('operation', ['lookup', 'submit', 'collect'])
def test_wrong_captured_target_has_no_file_or_process_effect(rig, operation):
    req, _, provider, state, state_dir, _ = rig
    value = req['dispatch_id'] if operation == 'collect' else req
    with pytest.raises(ValueError, match='PROVIDER_BINDING_CONFLICT'):
        getattr(provider, operation)(value, target=ProviderTarget('b'*32))
    assert not state_dir.exists() and not state['calls']


@pytest.mark.parametrize('binding_change', ['missing', 'null', 'environment_id', 'profile', 'repository'])
@pytest.mark.parametrize('operation', ['lookup', 'submit', 'collect'])
def test_changed_journal_binding_denies_even_cached_result(rig, binding_change, operation):
    req, target, provider, state, _, journal = rig
    provider.submit(req, target=target)
    provider.collect(req['dispatch_id'], target=target)
    saved = bridge.parse(journal.read_text())
    if binding_change == 'missing':
        del saved['provider_binding']
    elif binding_change == 'null':
        saved['provider_binding'] = None
    else:
        saved['provider_binding'][binding_change] = 'other'
    bridge.save(journal, saved)
    calls = len(state['calls'])
    value = req['dispatch_id'] if operation == 'collect' else req
    with pytest.raises(ValueError, match='PROVIDER_BINDING_CONFLICT'):
        getattr(provider, operation)(value, target=target)
    assert len(state['calls']) == calls


@pytest.mark.parametrize('operation', ['lookup', 'submit', 'collect'])
def test_legacy_caller_cannot_adopt_bound_journal(rig, monkeypatch, operation):
    req, target, provider, state, state_dir, _ = rig
    provider.submit(req, target=target)
    provider.collect(req['dispatch_id'], target=target)
    monkeypatch.setattr(bridge, 'STATE', state_dir)
    value = req['dispatch_id'] if operation == 'collect' else req
    with pytest.raises(ValueError, match='PROVIDER_BINDING_CONFLICT'):
        getattr(bridge, operation)(value)
    assert len(state['calls']) == 3


def test_legacy_unbound_journal_remains_byte_compatible(rig, monkeypatch):
    req, _, _, _, state_dir, journal = rig
    calls = []
    def legacy(args, prompt):
        calls.append(args)
        assert args[2:4] == ['--env', 'bridge-video-free']
        assert 'provider_binding' not in bridge.parse(journal.read_text())
        return subprocess.CompletedProcess(args, 0, 'https://chatgpt.com/codex/tasks/task_e_old', '')
    monkeypatch.setattr(bridge, 'STATE', state_dir)
    monkeypatch.setattr(bridge, 'run_cli', legacy)
    first = bridge.submit(req)
    expected = dict(request=req, state='SUBMITTED', prompt_sha256=bridge.digest(bridge.prompt_for(req)),
                    provider_task_id='task_e_old', provider_task_url='https://chatgpt.com/codex/tasks/task_e_old')
    assert journal.read_text() == bridge.canonical(expected)+'\n'
    assert bridge.submit(req) == first and len(calls) == 1


def test_request_replay_type_conflict_denies_provider(rig):
    req, target, provider, state, _, _ = rig
    provider.submit(req, target=target)
    changed = deepcopy(req)
    changed['assignment']['can_repair'] = 0
    with pytest.raises(ValueError, match='DISPATCH_REPLAY_CONFLICT'):
        provider.submit(changed, target=target)
    assert len(state['calls']) == 1


def test_collect_validates_creation_dispatch_before_cached_result(rig):
    req, target, provider, state, _, journal = rig
    provider.submit(req, target=target)
    provider.collect(req['dispatch_id'], target=target)
    saved = bridge.parse(journal.read_text())
    other = '12345678-1234-4234-8234-123456789099'
    saved['request']['dispatch_id'] = saved['request']['assignment']['dispatch_id'] = other
    bridge.save(journal, saved)
    with pytest.raises(ValueError, match='DISPATCH_REPLAY_CONFLICT'):
        provider.collect(req['dispatch_id'], target=target)
    assert len(state['calls']) == 3


@pytest.mark.parametrize('operation', ['lookup', 'submit', 'collect'])
def test_explicit_null_journal_cannot_masquerade_as_legacy(rig, monkeypatch, operation):
    req, target, provider, state, state_dir, journal = rig
    provider.submit(req, target=target)
    saved = bridge.parse(journal.read_text())
    saved['provider_binding'] = None
    bridge.save(journal, saved)
    monkeypatch.setattr(bridge, 'STATE', state_dir)
    value = req['dispatch_id'] if operation == 'collect' else req
    with pytest.raises(ValueError, match='PROVIDER_BINDING_CONFLICT'):
        getattr(bridge, operation)(value)
    assert len(state['calls']) == 1


@pytest.mark.parametrize('operation', ['lookup', 'submit', 'collect'])
def test_bound_calls_require_explicit_context_before_any_effect(rig, operation):
    req, target, _, state, state_dir, _ = rig
    value = req['dispatch_id'] if operation == 'collect' else req
    with pytest.raises(ValueError, match='BOUND_PROVIDER_CONTEXT_REQUIRED'):
        getattr(bridge, operation)(value, binding=asdict(target))
    assert not state_dir.exists() and not state['calls']


@pytest.mark.parametrize('bad', [{}, {'profile': 'light'},
    {'profile': 'ubuntu', 'repository': 'olegmed1-art/bridge-video-free', 'environment_id': 'a'*32},
    {'profile': 'light', 'repository': 'other/repo', 'environment_id': 'a'*32},
    {'profile': 'light', 'repository': 'olegmed1-art/bridge-video-free', 'environment_id': 'BRIDGE-VIDEO-FREE'}])
def test_malformed_binding_has_no_side_effect(rig, bad):
    req, _, _, state, state_dir, _ = rig
    with pytest.raises(ValueError, match='PROVIDER_BINDING_INVALID'):
        bridge.submit(req, state_dir=state_dir, binding=bad, runner=lambda *a: pytest.fail('runner'))
    assert not state_dir.exists() and not state['calls']
