"""Submission ambiguity and immutable provider evidence; no network calls."""
from copy import deepcopy
import subprocess

import pytest

from oracle_autopilot import codex_cli_bridge as bridge


@pytest.fixture
def request_data(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, 'STATE', tmp_path)
    dispatch = '12345678-1234-4234-8234-123456789012'
    return dict(
        dispatch_id=dispatch, reservation_id='12345678-1234-4234-8234-123456789013',
        expected_head_sha='a'*40, branch='codex/cli-test', mode='READ_ONLY',
        target_pr=1546, task_fingerprint='b'*64,
        assignment=dict(dispatch_id=dispatch, execution_scope='REPOSITORY', can_repair=False,
                        task_spec_json=dict(repository='olegmed1-art/bridge-video-free',
                                            target_pr=1546, expected_head_sha='a'*40,
                                            execution_mode='READ_ONLY')))


def response(text, code=0):
    return subprocess.CompletedProcess([], code, text, '')


def submitted(request_data, monkeypatch):
    monkeypatch.setattr(bridge, 'run_cli', lambda *args: response(
        'https://chatgpt.com/codex/tasks/task_e_test123\n'))
    return bridge.submit(request_data)


def report_patch(request_data, **updates):
    report = {key: request_data[key] for key in (
        'dispatch_id', 'expected_head_sha', 'target_pr', 'task_fingerprint')}
    report.update(status='SUCCEEDED', result_code='VERIFIED', summary='Bounded check passed.',
                  evidence=['tests/test_example.py: checked invariant.'])
    report.update(updates)
    path = bridge.report_path(request_data)
    return (f'diff --git a/{path} b/{path}\nnew file mode 100644\n'
            f'index 0000000..1234567\n--- /dev/null\n+++ b/{path}\n'
            '@@ -0,0 +1 @@\n+'+bridge.canonical(report)+'\n')


def test_submit_saves_intent_before_call_and_never_duplicates(request_data, monkeypatch):
    calls = []
    def create(args, prompt):
        saved = bridge.parse((bridge.STATE/(request_data['dispatch_id']+'.json')).read_text())
        assert saved['state'] == 'SUBMISSION_UNKNOWN'
        assert saved['prompt_sha256'] == bridge.digest(prompt)
        calls.append(args)
        return response('https://chatgpt.com/codex/tasks/task_e_test123\n')
    monkeypatch.setattr(bridge, 'run_cli', create)
    first = bridge.submit(request_data)
    assert first['state'] == 'SUBMITTED'
    assert bridge.submit(request_data) == first
    assert len(calls) == 1
    changed = deepcopy(request_data)
    changed['task_fingerprint'] = 'c'*64
    with pytest.raises(ValueError, match='DISPATCH_REPLAY_CONFLICT'):
        bridge.submit(changed)
    assert len(calls) == 1


@pytest.mark.parametrize('failure', ['timeout', 'ambiguous_output', 'process_error'])
def test_uncertain_submission_is_never_retried(request_data, monkeypatch, failure):
    calls = []
    def create(*args):
        calls.append(args)
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('codex', 90)
        return response('Could not confirm task creation', 1 if failure == 'process_error' else 0)
    monkeypatch.setattr(bridge, 'run_cli', create)
    assert bridge.submit(request_data)['state'] == 'SUBMISSION_UNKNOWN'
    assert bridge.submit(request_data)['state'] == 'SUBMISSION_UNKNOWN'
    assert len(calls) == 1


def test_collect_freezes_first_valid_result(request_data, monkeypatch):
    submitted(request_data, monkeypatch)
    calls = []
    def read(args):
        calls.append(args)
        return response('[READY] test\n' if args[1]=='status' else report_patch(request_data))
    monkeypatch.setattr(bridge, 'run_cli', read)
    first = bridge.collect(request_data['dispatch_id'])
    assert first['state'] == 'RESULT_RETRIEVED'
    assert first['changes'] == ''
    assert bridge.collect(request_data['dispatch_id']) == first
    assert len(calls) == 2


@pytest.mark.parametrize('marker,state', [
    ('[FAILED]', 'PROVIDER_TERMINAL_FAILURE'),
    ('[CANCELLED]', 'PROVIDER_TERMINAL_FAILURE'),
    ('[RUNNING]', 'WAITING_PROVIDER'),
    ('[UNKNOWN]', 'PROVIDER_STATUS_UNKNOWN'),
])
def test_provider_state_classification(request_data, monkeypatch, marker, state):
    submitted(request_data, monkeypatch)
    monkeypatch.setattr(bridge, 'run_cli', lambda args: response(marker+' task\n'))
    assert bridge.collect(request_data['dispatch_id'])['state'] == state


@pytest.mark.parametrize('updates,code', [
    ({'dispatch_id':'another'}, 'REPORT_BINDING_INVALID'),
    ({'evidence':[{'untrusted':'object'}]}, 'REPORT_EVIDENCE_INVALID'),
    ({'summary':'multiline\nsummary'}, 'REPORT_SUMMARY_INVALID'),
    ({'status':'DONE'}, 'REPORT_STATUS_INVALID'),
])
def test_untrusted_reports_fail_closed(request_data, monkeypatch, updates, code):
    submitted(request_data, monkeypatch)
    monkeypatch.setattr(bridge, 'run_cli', lambda args: response(
        '[READY] task\n' if args[1]=='status' else report_patch(request_data, **updates)))
    result = bridge.collect(request_data['dispatch_id'])
    assert result['state'] == 'RESULT_REJECTED'
    assert result['validation_error'] == code
    assert bridge.collect(request_data['dispatch_id']) == result
    assert (bridge.STATE/(request_data['dispatch_id']+'.result.json')).exists()


def test_read_only_rejects_source_changes(request_data, monkeypatch):
    submitted(request_data, monkeypatch)
    patch=report_patch(request_data)+'diff --git a/app.py b/app.py\n@@ -1 +1 @@\n-old\n+new\n'
    monkeypatch.setattr(bridge, 'run_cli', lambda args: response(
        '[READY] task\n' if args[1]=='status' else patch))
    result = bridge.collect(request_data['dispatch_id'])
    assert result['state'] == 'RESULT_REJECTED'
    assert result['validation_error'] == 'READ_ONLY_SOURCE_CHANGED'


def test_report_hunk_count_and_duplicate_keys(request_data):
    with pytest.raises(ValueError, match='REPORT_HUNK_COUNT_INVALID'):
        bridge.extract_report(report_patch(request_data).replace('+1 @@', '+1,2 @@'),
                              bridge.report_path(request_data))
    with pytest.raises(ValueError, match='DUPLICATE_JSON_KEY'):
        bridge.parse('{"status":"BLOCKED","status":"SUCCEEDED"}')


def test_cli_forces_chatgpt_and_removes_api_key_fallback(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-placeholder')
    monkeypatch.setenv('CODEX_API_KEY', 'test-placeholder')
    monkeypatch.setenv('AUTOPILOT_DATABASE_URL', 'postgresql://test-placeholder')
    monkeypatch.setenv('AUTOPILOT_TOKEN_BROKER_SECRET', 'test-placeholder')
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://invalid.example')
    def run(args, **kwargs):
        assert args[1:3] == ['-c', 'forced_login_method="chatgpt"']
        assert 'OPENAI_API_KEY' not in kwargs['env']
        assert 'CODEX_API_KEY' not in kwargs['env']
        assert set(kwargs['env']) == {'HOME', 'CODEX_HOME', 'PATH', 'LANG', 'LC_ALL'}
        assert 'shell' not in kwargs
        return response('ok')
    monkeypatch.setattr(subprocess, 'run', run)
    bridge.run_cli(['login', 'status'])


def test_service_profile_stays_inside_existing_service_paths():
    try:
        bridge.configure_profile('service')
        assert str(bridge.CLI) == '/opt/bridge-school/school-autopilot/runtime-bin/codex'
        assert str(bridge.STATE) == '/opt/bridge-school/school-autopilot/runtime/codex-dispatch'
        env = bridge.child_environment()
        assert env['CODEX_HOME'] == '/opt/bridge-school/school-autopilot/runtime/codex-home'
        assert '/home/ubuntu' not in str(env)
    finally:
        bridge.configure_profile('ubuntu')


@pytest.mark.parametrize('text,code,state', [
    ('Logged in using ChatGPT\n', 0, 'CLI_AUTH_READY'),
    ('Logged in using an API key\n', 0, 'CLI_AUTH_REQUIRED'),
    ('sensitive diagnostic text', 1, 'CLI_AUTH_REQUIRED'),
])
def test_health_reports_only_auth_state(monkeypatch, text, code, state):
    monkeypatch.setattr(bridge, 'run_cli', lambda *args, **kwargs: response(text, code))
    assert bridge.health() == {'state': state, 'profile': 'ubuntu'}
