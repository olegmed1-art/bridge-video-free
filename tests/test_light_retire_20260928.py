"""Fixed terminal retirement: real filesystem failure boundaries, no live effects."""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import pytest
from ops.incident import light_retire_20260928 as r


def directories(tmp_path):
    sources=[tmp_path/str(i) for i in range(3)]
    for p in sources:
        p.mkdir(mode=0o700); (p/'evidence').write_bytes(b'original\n')
    return sources,[p.with_name(p.name+r.SUFFIX) for p in sources]


def test_archive_retains_inodes_modes_and_bytes(tmp_path):
    src,dst=directories(tmp_path); expected=[r.inventory(p) for p in src]
    calls=[]
    r.archive_sources(src,dst,expected,lambda:calls.append(1))
    assert len(calls)==3
    assert all(not p.exists() for p in src)
    assert [r.inventory(p) for p in dst]==expected


@pytest.mark.parametrize('fault',['existing','symlink','drift'])
def test_archive_refuses_before_first_move(tmp_path,fault):
    src,dst=directories(tmp_path); expected=[r.inventory(p) for p in src]
    if fault=='existing':dst[0].mkdir()
    if fault=='symlink':dst[0].symlink_to(tmp_path/'missing')
    if fault=='drift':(src[0]/'evidence').write_bytes(b'changed')
    with pytest.raises(RuntimeError):r.archive_sources(src,dst,expected,lambda:None)
    assert all(p.exists() for p in src)


def test_partial_move_keeps_all_bytes_and_refuses_replay(tmp_path):
    src,dst=directories(tmp_path); expected=[r.inventory(p) for p in src]
    calls=[]
    def guard():
        calls.append(1)
        if len(calls)==2:raise RuntimeError('EXPIRED')
    with pytest.raises(RuntimeError,match='EXPIRED'):r.archive_sources(src,dst,expected,guard)
    assert r.inventory(dst[0])==expected[0]
    assert all(r.inventory(src[i])==expected[i] for i in [1,2])
    with pytest.raises(RuntimeError,match='RETIRE_DESTINATION_EXISTS'):
        r.archive_sources(src,dst,expected,lambda:None)


@pytest.mark.parametrize('fault',['symlink','hardlink','fifo'])
def test_inventory_rejects_unsafe_objects(tmp_path,fault):
    root=tmp_path/'root';root.mkdir();(root/'file').write_text('x')
    if fault=='symlink':(root/'bad').symlink_to(tmp_path)
    if fault=='hardlink':os.link(root/'file',root/'bad')
    if fault=='fifo':os.mkfifo(root/'bad')
    with pytest.raises(RuntimeError,match='RETIRE_INVENTORY_'):r.inventory(root)


def closed_rows():
    terminal=dict(request={'dispatch_id':r.DISPATCH},result=dict(status='SUCCEEDED',result_code='AUDIT_PASSED',provider_evidence_sha256=r.EVIDENCE))
    original=dict(native_config={'enabled':False,'cutover_at':'old'},autopilot_role={'can_repair':True,'enabled':True,'updated_at':'old'})
    rows=dict(counts=[0,0,0,1],native=dict(state='TERMINAL',provider_task_id=r.PROVIDER,request=terminal['request'],terminal=terminal['result']),
        task={'status':'DONE'},work={'state':'DONE','last_task_id':r.TASK},outbox={'status':'CALLBACK_ACCEPTED','delivery_contract_version':4},
        config=deepcopy(original['native_config']),role=deepcopy(original['autopilot_role']))
    return rows,terminal,original


def test_closed_db_state_and_role_timestamp():
    rows,t,o=closed_rows();rows['role']['updated_at']='new';r.validate_db(rows,t,o)


@pytest.mark.parametrize('fault',['active','successor','submitted','wrong_provider','wrong_terminal','work','outbox','config','role'])
def test_database_drift_refuses_retirement(fault):
    rows,t,o=closed_rows()
    if fault=='active':rows['counts'][0]=1
    if fault=='successor':rows['counts'][2]=1
    if fault=='submitted':rows['native']['state']='SUBMITTED'
    if fault=='wrong_provider':rows['native']['provider_task_id']='other'
    if fault=='wrong_terminal':rows['native']['terminal']={}
    if fault=='work':rows['work']['last_task_id']='other'
    if fault=='outbox':rows['outbox']['status']='PUBLISHED'
    if fault=='config':rows['config']['enabled']=True
    if fault=='role':rows['role']['can_repair']=False
    with pytest.raises(RuntimeError):r.validate_db(rows,t,o)


def test_canary_is_read_only_and_service_bound(monkeypatch):
    import types
    monkeypatch.setattr(r.pwd,'getpwnam',lambda n:types.SimpleNamespace(pw_uid=11,pw_gid=12))
    def run(args,**kw):
        code=args[4]
        assert "['cloud','status',task]" in code and "['cloud','diff',task,'--attempt','1']" in code
        assert "profile='light'" in code and "['cloud','exec'" not in code
        assert kw['env']=={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'}
        assert kw['timeout']==50
        return subprocess.CompletedProcess(args,0,b'SERVICE_PROFILE_CANARY_DIFF_VERIFIED\n',b'')
    monkeypatch.setattr(r.subprocess,'run',run);r.service_canary()


def test_runner_workflow_are_fixed_and_history_unchanged():
    root=Path(__file__).resolve().parents[1]
    code=(root/'ops/incident/light_retire_runner_20260928.py').read_text()
    workflow=(root/'.github/workflows/light-native-pilot-owner.yml').read_text()
    assert "SOURCE = '"+r.SOURCE+"'" in code
    assert "PACKAGE = '"+r.PACKAGE+"'" in code
    assert 'recovery/light-retire-terminal-20260928' in code and 'recovery/light-retire-terminal-20260928' in workflow
    assert "run['run_attempt']==1" in code
    assert "main['object']['sha']=='"+r.SOURCE+"'" in code
    assert 'PILOT_ACCEPTED_PAYLOAD' in code
    assert 'reset-failed' not in code # only reviewed terminal helper can retire unit


@pytest.mark.parametrize('suffix',[' } extra',' } { other }',' extra'])
def test_supervisor_trailing_commands_rejected(suffix):
    assert not r.valid_command('{ expected ;'+suffix,'{ expected ;')
    assert r.valid_command('{ expected ; status=0 ; }','{ expected ;')
