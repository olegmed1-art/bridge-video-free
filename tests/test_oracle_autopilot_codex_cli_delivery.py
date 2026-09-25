"""Fault injection across a real filesystem journal and a transactional fake queue."""
from copy import deepcopy
import subprocess

import pytest

from oracle_autopilot import codex_cli_bridge as bridge
from oracle_autopilot.codex_cli_delivery import advance


class Queue:
    def __init__(self, request):
        self.row = dict(state='RESERVED', request=deepcopy(request), provider_task_id=None,
                        terminal=None)
        self.fail_ack = None
        self.fail_finish = None
        self.released = 0
        self.submission_started = False

    def begin_submission(self, request):
        if self.submission_started:
            return False
        self.submission_started = True
        return True

    def snapshot(self, dispatch_id):
        return deepcopy(self.row)

    def acknowledge(self, request, task_id, prompt_sha256):
        failure, self.fail_ack = self.fail_ack, None
        if failure == 'before':
            raise ConnectionError('ACK_BEFORE_COMMIT')
        self.row.update(state='SUBMITTED', provider_task_id=task_id)
        if failure == 'after':
            raise ConnectionError('ACK_AFTER_COMMIT')

    def finish(self, request, task_id, terminal):
        failure, self.fail_finish = self.fail_finish, None
        if failure == 'before':
            raise ConnectionError('FINISH_BEFORE_COMMIT')
        if self.row['state'] != 'TERMINAL':
            self.released += 1
            self.row.update(state='TERMINAL', terminal=deepcopy(terminal))
        if failure == 'after':
            raise ConnectionError('FINISH_AFTER_COMMIT')


class Authority:
    current = True
    head = 'a'*40
    def inspect(self, request):
        return dict(current=self.current, head_sha=self.head, open=True)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, 'STATE', tmp_path)
    dispatch = '12345678-1234-4234-8234-123456789012'
    request = dict(dispatch_id=dispatch, reservation_id='12345678-1234-4234-8234-123456789013',
                   expected_head_sha='a'*40, branch='codex/test', mode='READ_ONLY',
                   target_pr=1546, task_fingerprint='b'*64,
                   assignment=dict(dispatch_id=dispatch, execution_scope='REPOSITORY', can_repair=False,
                       task_spec_json=dict(repository='olegmed1-art/bridge-video-free', target_pr=1546,
                                           expected_head_sha='a'*40, execution_mode='READ_ONLY')))
    calls = dict(create=0, status='[READY]', fail_create=False)
    def run(args, input_text=None, **kwargs):
        if args[1] == 'exec':
            calls['create'] += 1
            if calls['fail_create']:
                raise subprocess.TimeoutExpired('codex', 90)
            output = 'https://chatgpt.com/codex/tasks/task_e_recovery\n'
        elif args[1] == 'status':
            output = calls['status']+' test\n'
        else:
            assert args[1] == 'diff'
            report = {k:request[k] for k in ('dispatch_id','expected_head_sha','target_pr','task_fingerprint')}
            report.update(status='SUCCEEDED', result_code='VERIFIED', summary='Checked.', evidence=[])
            path = bridge.report_path(request)
            output = (f'diff --git a/{path} b/{path}\nnew file mode 100644\n'
                      f'--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+'
                      +bridge.canonical(report)+'\n')
            if request['mode']=='REPAIR':
                target='tools/artifact_manifest_v1.py'
                output+=(f'diff --git a/{target} b/{target}\n'
                         f'--- a/{target}\n+++ b/{target}\n@@ -1 +1 @@\n-old\n+new\n')
        return subprocess.CompletedProcess(args, 0, output, '')
    monkeypatch.setattr(bridge, 'run_cli', run)
    return request, Queue(request), Authority(), calls


@pytest.mark.parametrize('operation,when', [('ack','before'),('ack','after'),
                                         ('finish','before'),('finish','after')])
def test_lost_response_recovers_without_duplicate_execution_or_release(rig, operation, when):
    request, queue, authority, calls = rig
    setattr(queue, 'fail_'+operation, when)
    with pytest.raises(ConnectionError):
        advance(request['dispatch_id'], queue, authority)
    result = advance(request['dispatch_id'], queue, authority)
    assert result['state'] == 'DONE'
    assert result['terminal']['status'] == 'SUCCEEDED'
    assert calls['create'] == 1
    assert queue.released == 1
    assert advance(request['dispatch_id'], queue, authority) == result
    assert queue.released == 1


def test_unknown_creation_never_retries_or_releases(rig):
    request, queue, authority, calls = rig
    calls['fail_create'] = True
    for _ in range(3):
        assert advance(request['dispatch_id'], queue, authority)['state'] == 'SUBMISSION_UNKNOWN'
    assert calls['create'] == 1
    assert queue.released == 0


def test_lost_host_journal_cannot_create_second_provider_task(rig):
    request, queue, authority, calls = rig
    queue.fail_ack = 'before'
    with pytest.raises(ConnectionError):
        advance(request['dispatch_id'], queue, authority)
    (bridge.STATE/(request['dispatch_id']+'.json')).unlink()
    assert advance(request['dispatch_id'], queue, authority)['state'] == 'SUBMISSION_UNKNOWN'
    assert calls['create'] == 1 and queue.released == 0


def test_pause_after_submit_and_lost_ack_can_close_original_attempt(rig):
    request, queue, authority, calls = rig
    queue.fail_ack = 'before'
    with pytest.raises(ConnectionError):
        advance(request['dispatch_id'], queue, authority)
    authority.current = False
    result = advance(request['dispatch_id'], queue, authority)
    assert result['terminal']['status'] == 'BLOCKED'
    assert result['terminal']['result_code'] == 'TARGET_AUTHORITY_CHANGED'
    assert calls['create'] == 1 and queue.released == 1


def test_known_provider_failure_releases_original_slot(rig):
    request, queue, authority, calls = rig
    calls['status'] = '[FAILED]'
    result = advance(request['dispatch_id'], queue, authority)
    assert result['terminal']['status'] == 'BLOCKED'
    assert result['terminal']['result_code'] == 'PROVIDER_FAILED'
    assert queue.released == 1


def test_running_provider_retains_slot_and_does_not_block_reconciler(rig):
    request, queue, authority, calls = rig
    calls['status'] = '[RUNNING]'
    assert advance(request['dispatch_id'], queue, authority)['state'] == 'WAITING_PROVIDER'
    assert queue.released == 0
    calls['status'] = '[READY]'
    assert advance(request['dispatch_id'], queue, authority)['state'] == 'DONE'
    assert calls['create'] == 1


def test_stale_head_before_creation_sends_nothing(rig):
    request, queue, authority, calls = rig
    authority.head = 'c'*40
    assert advance(request['dispatch_id'], queue, authority)['state'] == 'RESERVATION_HELD'
    assert calls['create'] == 0


def test_generated_repair_is_never_claimed_as_published(rig):
    request, queue, authority, calls = rig
    request['mode'] = 'REPAIR'
    request['assignment']['can_repair'] = True
    request['assignment']['task_spec_json']['execution_mode'] = 'REPAIR'
    request['assignment']['task_spec_json']['expected_changed_files'] = ['tools/artifact_manifest_v1.py']
    queue.row['request'] = deepcopy(request)
    result = advance(request['dispatch_id'], queue, authority)
    assert result['terminal']['result_code'] == 'TARGET_PR_NOT_UPDATED'
    assert result['terminal']['status'] == 'BLOCKED'


def test_forged_ack_readback_cannot_be_finished(rig):
    request, queue, authority, calls = rig
    original = queue.acknowledge
    def corrupt(*args):
        original(*args)
        queue.row['provider_task_id'] = 'task_e_wrong'
    queue.acknowledge = corrupt
    with pytest.raises(ValueError, match='NATIVE_ACK_READBACK_CONFLICT'):
        advance(request['dispatch_id'], queue, authority)
    assert queue.released == 0


@pytest.mark.parametrize('patch', ['malformed completed output', 'x'*262145])
def test_completed_invalid_output_is_retained_and_releases_once(rig, monkeypatch, patch):
    request, queue, authority, calls = rig
    original = bridge.run_cli
    def invalid_diff(args, *rest, **kwargs):
        if args[1] == 'diff':
            return subprocess.CompletedProcess(args, 0, patch, '')
        return original(args, *rest, **kwargs)
    monkeypatch.setattr(bridge, 'run_cli', invalid_diff)
    result = advance(request['dispatch_id'], queue, authority)
    assert result['terminal']['status'] == 'BLOCKED'
    assert result['terminal']['result_code'] == 'NATIVE_REPORT_REJECTED'
    assert advance(request['dispatch_id'], queue, authority) == result
    assert calls['create'] == 1 and queue.released == 1
