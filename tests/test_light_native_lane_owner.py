from copy import deepcopy
import pytest

from ops import light_native_lane_owner as owner
from oracle_autopilot import light_native_lane as lane
from test_light_native_pilot_intake import fixture, gates, FakeConn, target, HEAD, DISPATCH, TASK


def setup(tmp_path, monkeypatch):
    plan, agreement, prior, baseline, accepted = fixture()
    gates(monkeypatch, prior)
    conn = FakeConn()
    conn.spec = plan.value['task_spec_json']
    receipt = target.prepare(conn, plan, agreement, baseline, accepted, tmp_path/'before.json',
        target_open=True, observed_head_sha=HEAD, observed_branch=plan.value['branch'])
    conn.terminal_stage = True
    request = conn.native_request = dict(dispatch_id=DISPATCH, assignment=receipt['assignment'],
        branch=plan.value['branch'], reservation_id='44444444-4444-4444-8444-444444444444',
        expected_head_sha=HEAD, mode='READ_ONLY', target_pr=1150, task_fingerprint='c'*64)
    report = dict(status='SUCCEEDED', result_code='AUDIT_PASSED', summary='Exact fixture passed.')
    provider = dict(state='RESULT_RETRIEVED', provider_task_id='task_e_fixture', changes='', report=report)
    conn.result = dict(**report, target_head_sha=HEAD,
        provider_evidence_sha256=lane.digest(lane.encoded(provider)))
    permit = lane.encoded(dict(source=plan.value['source'], environment_id='a'*32,
        dispatch={k:v for k,v in request.items() if k!='reservation_id'}))
    intent = lane.encoded(dict(version=1, source=plan.value['source'], sequence=0, dispatch_id=DISPATCH,
        permit_sha256=lane.digest(permit), previous_terminal_sha256=None))
    terminal = lane.encoded(lane.terminal_record(intent, request,
        dict(state='DONE', dispatch_id=DISPATCH, provider_task_id='task_e_fixture', terminal=conn.result)))
    conn.read_only = True
    calls = []
    def read(source, bound, environment):
        assert source == plan.value['source'] and bound == request and environment == 'a'*32
        calls.append('fresh_provider')
        return deepcopy(provider)
    monkeypatch.setattr(owner, 'fresh_provider_result', read)
    return conn, plan, receipt, intent, terminal, permit, provider, calls


def test_acceptance_requires_fresh_provider_and_exact_database_terminal(tmp_path, monkeypatch):
    conn, plan, receipt, intent, terminal, permit, provider, calls = setup(tmp_path, monkeypatch)
    start = len(conn.events)
    result = owner.verify_terminal(conn, plan, receipt, intent, terminal, permit)
    assert result == lane.encoded(lane.acceptance_record(intent, terminal))
    assert calls == ['fresh_provider']
    sql = conn.events[start:]
    assert any('REPEATABLE READ READ ONLY' in item for item in sql)
    assert any('native_cli_receipt' in item for item in sql)
    assert any('project_work_item' in item for item in sql)
    assert not any('UPDATE ' in item or 'INSERT ' in item or 'DELETE ' in item for item in sql)


@pytest.mark.parametrize('fault', ['pending', 'source_changes', 'provider_id', 'report', 'db_terminal', 'goal'])
def test_disagreement_never_yields_owner_acceptance(tmp_path, monkeypatch, fault):
    conn, plan, receipt, intent, terminal, permit, provider, calls = setup(tmp_path, monkeypatch)
    if fault == 'pending': provider['state'] = 'RUNNING'
    elif fault == 'source_changes': provider['changes'] = 'diff --git a/source.py b/source.py'
    elif fault == 'provider_id': provider['provider_task_id'] = 'task_e_other'
    elif fault == 'report': provider['report']['summary'] = 'Different.'
    elif fault == 'db_terminal': conn.result = dict(conn.result, summary='Drifted.')
    else: conn.goal = dict(conn.goal, unexpected='changed')
    with pytest.raises(RuntimeError):
        owner.verify_terminal(conn, plan, receipt, intent, terminal, permit)


def test_wrong_permit_rejected_before_cloud_or_db(tmp_path, monkeypatch):
    conn, plan, receipt, intent, terminal, permit, provider, calls = setup(tmp_path, monkeypatch)
    before = len(conn.events)
    with pytest.raises(RuntimeError):
        owner.verify_terminal(conn, plan, receipt, intent, terminal, b'{}')
    assert not calls and len(conn.events) == before


@pytest.mark.parametrize('fault', ['', 'journal', 'prompt', 'provider'])
def test_fresh_reader_child_is_profile_bound_readonly_and_sanitized(tmp_path, monkeypatch, fault):
    import os
    import sys
    from types import SimpleNamespace
    source = 'a'*40
    candidate = tmp_path/'releases'/source
    package = candidate/'oracle_autopilot'
    package.mkdir(parents=True)
    (package/'__init__.py').write_text('')
    # A real isolated child exercises the generated program. Only its external
    # bridge port is replaced; it has no network or real service credentials.
    (package/'codex_cli_bridge.py').write_text('''
import json,os
from pathlib import Path
LIGHT_ROOT=Path('/fixed-light')
calls=[]
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'))
def digest(x):return 'prompt-digest'
def prompt_for(request):return 'fixed prompt'
def lookup(request,*,state_dir,binding):
 assert state_dir==LIGHT_ROOT/'runtime/codex-dispatch'
 assert binding=={'profile':'light','environment_id':'a'*32,'repository':'olegmed1-art/bridge-video-free'}
 fault=Path('fault').read_text()
 return {'state':'UNKNOWN' if fault=='journal' else 'SUBMITTED',
         'prompt_sha256':'wrong' if fault=='prompt' else 'prompt-digest','provider_task_id':'task_e_fixture'}
def run_cli(args,*,timeout,profile):
 assert profile=='light' and timeout==20
 assert args[0]=='cloud' and args[1] in ('status','diff')
 assert os.environ.get('SHOULD_NOT_REACH_CHILD') is None
 calls.append(args[1])
 return '{}'
def _collect(dispatch_id,*,state_dir,binding,runner):
 runner(['cloud','status','task_e_fixture'])
 runner(['cloud','diff','task_e_fixture'])
 return {'state':'RESULT_RETRIEVED','provider_task_id':'task_e_other' if Path('fault').read_text()=='provider' else 'task_e_fixture','calls':calls}
''')
    (candidate/'fault').write_text(fault)
    real_run = owner.subprocess.run
    seen = []
    def child(command, **kwargs):
        assert command[:3] == [sys.executable, '-I', '-B']
        assert kwargs['env'] == {'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'}
        assert callable(kwargs.pop('preexec_fn'))
        assert kwargs['timeout'] == 45 and kwargs['capture_output'] is True
        seen.append(True)
        return real_run(command, **kwargs)
    monkeypatch.setenv('SHOULD_NOT_REACH_CHILD', 'parent-only')
    monkeypatch.setattr(owner.subprocess, 'run', child)
    monkeypatch.setattr(owner.release, 'PYTHON', sys.executable)
    monkeypatch.setattr(owner.bridge, 'LIGHT_ROOT', tmp_path)
    monkeypatch.setattr(owner.bridge, 'validate_request', lambda x:x)
    monkeypatch.setattr(owner.os, 'geteuid', lambda:0)
    monkeypatch.setattr(owner.pwd, 'getpwnam', lambda name:SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid()))
    if fault:
        with pytest.raises(RuntimeError, match='PROVIDER_READBACK'):
            owner.fresh_provider_result(source, {'dispatch_id':DISPATCH}, 'a'*32)
    else:
        result = owner.fresh_provider_result(source, {'dispatch_id':DISPATCH}, 'a'*32)
        assert result['calls'] == ['status', 'diff']
    assert seen == [True]


@pytest.mark.skipif(__import__('os').geteuid()!=0,reason='real root acceptance file')
@pytest.mark.parametrize('fault',[None,'provider','changed','symlink','conflict'])
def test_writer_uses_on_host_bytes_fresh_verifier_and_create_only(tmp_path,monkeypatch,fault):
    import os
    from types import SimpleNamespace
    from ops import light_native_lane_install as install
    conn, plan, receipt, intent, terminal, permit, provider, calls = setup(tmp_path,monkeypatch)
    state=tmp_path/'state';state.mkdir(mode=0o700)
    control=tmp_path/'control';job=control/'jobs'/receipt['dispatch_id'];job.mkdir(parents=True)
    monkeypatch.setattr(lane,'STATE',state)
    monkeypatch.setattr(lane,'CONTROL',control)
    monkeypatch.setattr(owner.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid()))
    monkeypatch.setattr(install,'root_parent',lambda path:None)
    for name,raw in [('00000000-intent.json',intent),('00000000-terminal.json',terminal)]:
        (state/name).write_bytes(raw);(state/name).chmod(0o600)
    (job/'permit.json').write_bytes(permit);(job/'permit.json').chmod(0o640)
    accepted=job/'accepted-terminal.json'
    if fault=='provider':provider['state']='RUNNING'
    elif fault=='symlink':
        (state/'00000000-terminal.json').unlink()
        (state/'00000000-terminal.json').symlink_to(state/'00000000-intent.json')
    elif fault=='conflict':accepted.write_bytes(b'{}');accepted.chmod(0o640)
    elif fault=='changed':
        verify=owner.verify_terminal
        def drifting(*args):
            result=verify(*args)
            (state/'00000000-terminal.json').write_bytes(b'{}')
            return result
        monkeypatch.setattr(owner,'verify_terminal',drifting)
    if fault:
        with pytest.raises((RuntimeError,OSError)):
            owner.retain_acceptance(conn,plan,receipt,0)
        assert not accepted.exists() or accepted.read_bytes()==b'{}'
    else:
        result=owner.retain_acceptance(conn,plan,receipt,0)
        assert result['state']=='ACCEPTED'
        inode=accepted.stat().st_ino
        assert accepted.stat().st_mode & 0o777 == 0o640
        assert owner.retain_acceptance(conn,plan,receipt,0)==result
        assert accepted.stat().st_ino==inode and calls==['fresh_provider','fresh_provider']
        def forbidden_write(*args,**kwargs):
            raise AssertionError('read-only observation attempted write')
        monkeypatch.setattr(install,'write_new',forbidden_write)
        assert owner.retain_acceptance(conn,plan,receipt,0,readonly=True)==result
        accepted.unlink()
        with pytest.raises(OSError):owner.retain_acceptance(conn,plan,receipt,0,readonly=True)
        assert not accepted.exists()
    assert not (control/'current.json').exists() and not (control/'admission').exists()
