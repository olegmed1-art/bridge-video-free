from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from ops import oracle_light_resume as target
from ops import oracle_light_resume_probe as probe
from ops import oracle_light_stopped_hold_upgrade as upgrade


def baseline():
    return [dict(task_id=probe.CANARY, status='DONE', attempts=1,
        lease_epoch=1, goal_type='CHATGPT_ROLE_DISPATCH_V1', lease_until=None,
        cost_reserved_microusd=0, cost_actual_microusd=0, cost_cap_microusd=0)]


def test_done_only_with_empty_queue():
    probe.validate_queue(baseline(), dict(active_workers=0, probe_reservations=0))


@pytest.mark.parametrize('field,value', [('status','FAILED_CLOSED'), ('status','READY'),
    ('attempts',2), ('lease_epoch',2), ('task_id','another'), ('lease_until','future'),
    ('goal_type','another'), ('cost_actual_microusd',1)])
def test_drift_blocks(field,value):
    rows=baseline(); rows[0][field]=value
    with pytest.raises(ValueError):
        probe.validate_queue(rows,dict(active_workers=0,probe_reservations=0))


@pytest.mark.parametrize('rows,capacity', [([],dict(active_workers=0,probe_reservations=0)),
    (baseline()*2,dict(active_workers=0,probe_reservations=0)),
    (baseline(),dict(active_workers=1,probe_reservations=0)),
    (baseline(),dict(active_workers=0,probe_reservations=1))])
def test_missing_canary_or_competing_work_blocks(rows,capacity):
    with pytest.raises(ValueError): probe.validate_queue(rows,capacity)


def test_exact_installed_bundle_preserved():
    p=target.packet('a'*40,'preflight')
    assert p['bundle']['sha256']==target.BUNDLE_SHA
    assert p['bundle']['revision']==target.INSTALLED
    assert "row['status'] == 'DONE'" in p['probe_source']
    assert "row['status'] == 'FAILED_CLOSED'" in p['bundle']['files']['oracle_autopilot/light_recovery_probe.py']


def harness(monkeypatch,tmp_path, fail=None):
    release=tmp_path/'release'
    h={'UNIT':'light.service','UNIT_PATH':tmp_path/'unit','DROP':tmp_path/'drop',
       'ENV_PATH':tmp_path/'env','PYTHON':'/usr/bin/python3'}
    env={'AUTOPILOT_WORKER_ID':'oracle-autopilot-light-1','AUTOPILOT_ADMISSION_MODE':'HOLD'}
    before=dict(ActiveState='inactive',SubState='dead',MainPID='0',NeedDaemonReload='no',
        WorkingDirectory=str(release),User='school-autopilot',Group='school-autopilot',
        FragmentPath=str(h['UNIT_PATH']),DropInPaths=str(h['DROP']),
        Environment='AUTOPILOT_ADMISSION_MODE=HOLD',
        EnvironmentFiles=(str(h['ENV_PATH'])+' (ignore_errors=no)',
            str(release/upgrade.EXTRA_ENV)+' (ignore_errors=no)'),
        ExecStart='path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -m oracle_autopilot.worker_v17 ;',
        InvocationID='0'*32,NRestarts='0')
    current=deepcopy(before); files={h['DROP']:upgrade.desired_drop(release)}; events=[]
    failed=False
    def fault(stage):
        nonlocal failed
        if fail==stage and not failed:
            failed=True
            raise RuntimeError(stage)
    def run(*args):
        events.append(args)
        if args[0]=='journalctl':
            fault('journal')
            return ('worker_started worker_id=oracle-autopilot-light-1\n'
                'parallel_work_manifest_reconciled sha256=manifest items=4 registered=1 replayed=True')
        if args[1]=='daemon-reload':
            current['Environment']='AUTOPILOT_ADMISSION_MODE='+('ACTIVE' if b'=ACTIVE' in files[h['DROP']] else 'HOLD')
            fault('reload')
        elif args[1]=='start':
            current.update(ActiveState='active',SubState='running',MainPID='42',InvocationID='1'*32)
            fault('start')
        elif args[1]=='stop': current.update(ActiveState='inactive',SubState='dead',MainPID='0')
    def replace(path,expected,new,read,sync):
        assert files[path]==expected
        files[path]=new
        fault('replace_after')
    def fresh():
        if current['MainPID']=='42': fault('post_probe')
        return {'manifest_sha256':'manifest'}
    def stable():
        if current['MainPID']=='42': fault('post_stable')
        else: fault('pre_stable')
    h['read_owned']=lambda p,mode:files[p]
    u=dict(require=upgrade.require,desired_drop=upgrade.desired_drop,
        validate_stopped=upgrade.validate_stopped,service_state=lambda unit:dict(current),replace_drop=replace)
    s=dict(run=run,fsync_directory=lambda p:None,
        process_environment=lambda pid:{**env,'AUTOPILOT_ADMISSION_MODE':'ACTIVE'})
    monkeypatch.setattr(target.time,'sleep',lambda _:fault('soak'))
    monkeypatch.setattr(target,'Path',lambda _:SimpleNamespace(resolve=lambda:release))
    return lambda:target.activate(u,h,s,release,env,before,stable,fresh),current,files,h,events


def test_success_has_one_start_and_no_restarts(monkeypatch,tmp_path):
    call,state,files,h,events=harness(monkeypatch,tmp_path)
    result=call()
    assert result['admission']=='ACTIVE' and result['main_pid']==42
    assert events.count(('systemctl','start','light.service'))==1
    assert not any(e[1]=='stop' for e in events)


@pytest.mark.parametrize('failure',['pre_stable','replace_after','reload','start','soak','journal','post_probe','post_stable'])
def test_faults_leave_stopped_hold(monkeypatch,tmp_path,failure):
    call,state,files,h,events=harness(monkeypatch,tmp_path,failure)
    with pytest.raises(RuntimeError):call()
    assert state['MainPID']=='0' and state['ActiveState']=='inactive'
    assert b'ADMISSION_MODE=HOLD' in files[h['DROP']]
    assert state['Environment']=='AUTOPILOT_ADMISSION_MODE=HOLD'
    if failure=='pre_stable':assert not events
