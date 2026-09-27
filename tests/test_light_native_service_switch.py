"""Faults must not turn uncertain pilot state into a second task or restart."""
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock
import os
import pytest
from ops import light_native_service_switch as target
from ops import oracle_light_active_hold_attest as hold


def prior():
    return hold.HoldIdentity('autopilot-lite-vnic',123,'a'*32,
        str(target.plan.LIGHT/'releases'/('b'*40)),'c'*64)


@pytest.mark.skipif(os.geteuid()!=0,reason='root file metadata')
def test_repeated_deny_preserves_accepted_permit_and_leaves_hold(tmp_path,monkeypatch):
    monkeypatch.setattr(target,'CONTROL',tmp_path)
    admission=tmp_path/'admission';admission.write_bytes(b'PILOT\n');admission.chmod(0o640)
    permit=tmp_path/'permit.json';permit.write_bytes(b'accepted immutable permit')
    target.deny_admission()
    target.deny_admission()
    assert admission.read_bytes()==b'HOLD\n'
    assert admission.stat().st_mode & 0o777 == 0o640
    assert permit.read_bytes()==b'accepted immutable permit'
    assert set(p.name for p in tmp_path.iterdir())=={'admission','permit.json'}


def test_unowned_pilot_refuses_stop_and_restore(monkeypatch):
    deny=Mock(); command=Mock()
    monkeypatch.setattr(target,'deny_admission',deny)
    monkeypatch.setattr(target,'show',lambda *a:dict(LoadState='loaded',InvocationID='wrong',WorkingDirectory='/candidate',Description='Bridge native pilot '+'e'*64))
    monkeypatch.setattr(target,'command',command)
    with pytest.raises(RuntimeError,match='PILOT_HOST_UNOWNED_UNIT'):
        target.restore(prior(),{},Path('/candidate'),pilot_invocation='expected',protected_digest='d'*64,request_digest='e'*64,pilot_seconds=120)
    deny.assert_called_once();command.assert_not_called()


@pytest.mark.parametrize('fault',['process','backend','config'])
def test_drain_or_config_failure_never_starts_hold(monkeypatch,fault):
    calls=[]
    monkeypatch.setattr(target,'attest_pilot_execution',lambda *a:None)
    monkeypatch.setattr(target,'attest_hardening',lambda *a,**k:None)
    monkeypatch.setattr(target,'deny_admission',lambda: calls.append('deny'))
    monkeypatch.setattr(target,'show',lambda *a:dict(LoadState='loaded',InvocationID='expected',WorkingDirectory='/candidate',Description='Bridge native pilot '+'e'*64))
    monkeypatch.setattr(target,'command',lambda *a,**k:calls.append(a))
    def check(name):
        def run(*a):
            calls.append(name)
            if name==fault:raise RuntimeError('injected')
        return run
    monkeypatch.setattr(target,'no_processes',check('process'))
    monkeypatch.setattr(target,'empty_runtime_backends',check('backend'))
    monkeypatch.setattr(target,'unchanged_files',check('config'))
    with pytest.raises(RuntimeError,match='injected'):
        target.restore(prior(),{},Path('/candidate'),pilot_invocation='expected',protected_digest='d'*64,request_digest='e'*64,pilot_seconds=120)
    assert calls[0]=='deny'
    assert ('/usr/bin/systemctl','stop',target.plan.PILOT_UNIT) in calls
    assert ('/usr/bin/systemctl','start',hold.UNIT) not in calls


def test_restore_is_service_evidence_not_terminal_database_acceptance(monkeypatch):
    calls=[]
    monkeypatch.setattr(target,'attest_pilot_execution',lambda *a:None)
    monkeypatch.setattr(target,'attest_hardening',lambda *a,**k:None)
    restored=hold.ServiceHoldIdentity(**{**asdict(prior()),'pid':456,'invocation_id':'d'*32})
    monkeypatch.setattr(target,'deny_admission',lambda:calls.append('deny'))
    def show(unit,fields):
        if unit==target.plan.PILOT_UNIT:
            return dict(LoadState='loaded',InvocationID='expected',WorkingDirectory='/candidate',Description='Bridge native pilot '+'e'*64)
        return dict(MainPID='456',InvocationID='d'*32,NRestarts='0')
    monkeypatch.setattr(target,'show',show)
    monkeypatch.setattr(target,'command',lambda *a,**k:calls.append(a))
    monkeypatch.setattr(target,'no_processes',lambda *a:calls.append('process drained'))
    monkeypatch.setattr(target,'empty_runtime_backends',lambda *a:calls.append('backend drained'))
    monkeypatch.setattr(target,'unchanged_files',lambda *a:calls.append('config unchanged'))
    monkeypatch.setattr(hold,'service_hold_identity',lambda:restored)
    monkeypatch.setattr(target,'attest_hardening',lambda *a,**k:None)
    observed=target.restore(prior(),{},Path('/candidate'),pilot_invocation='expected',protected_digest='d'*64,request_digest='e'*64,pilot_seconds=120)
    assert type(observed) is hold.ServiceHoldIdentity
    assert calls.index('backend drained')<calls.index(('/usr/bin/systemctl','start',hold.UNIT))


def test_cgroup_population_is_checked_even_if_systemd_omits_path(tmp_path,monkeypatch):
    monkeypatch.setattr(target,'CGROUP_ROOT',tmp_path)
    directory=tmp_path/'system.slice'/target.plan.PILOT_UNIT
    directory.mkdir(parents=True)
    (directory/'cgroup.events').write_text('populated 1\nfrozen 0\n')
    monkeypatch.setattr(target,'show',lambda *a:dict(MainPID='0',ControlPID='0',ActiveState='inactive',ControlGroup=''))
    with pytest.raises(RuntimeError,match='PILOT_HOST_CGROUP_POPULATED'):
        target.no_processes(target.plan.PILOT_UNIT)
    (directory/'cgroup.events').write_text('populated 0\nfrozen 0\n')
    target.no_processes(target.plan.PILOT_UNIT)


def test_snapshot_cannot_substitute_five_arbitrary_paths(monkeypatch):
    fake={f'/other/{n}':dict(mode=0o600,limit=8,sha256='a'*64) for n in range(5)}
    digest=target.hashlib.sha256(target.release.encoded(fake)).hexdigest()
    reader=Mock();monkeypatch.setattr(hold,'read',reader)
    with pytest.raises(RuntimeError,match='PILOT_HOST_CONFIG_INVENTORY'):
        target.unchanged_files(fake,prior(),digest)
    reader.assert_not_called()


@pytest.mark.parametrize('field,value',[('NoNewPrivileges','no'),('ProtectSystem','full'),('MemoryMax','infinity'),('ReadWritePaths','/')])
def test_live_hardening_drift_refuses(field,value,monkeypatch):
    expected={**target.plan.HARDENING}
    expected.pop('CPUQuota');expected.pop('TimeoutStopSec')
    expected.update(CPUQuotaPerSecUSec='1s',TimeoutStopUSec='30s',MemoryHigh='536870912',MemoryMax='805306368',Restart='on-failure')
    expected[field]=value
    monkeypatch.setattr(target,'show',lambda *a:expected)
    with pytest.raises(RuntimeError,match='PILOT_HOST_HARDENING_CHANGED'):
        target.attest_hardening()


@pytest.mark.parametrize('text,seconds',[('30min',1800),('1min 30s',90),('120s',120)])
def test_systemd_finite_duration(text,seconds):
    assert target.duration_seconds(text)==seconds


@pytest.mark.parametrize('text',['infinity','30min garbage','1s1s','-1s','0.5s',''])
def test_systemd_unknown_duration_refuses(text):
    with pytest.raises(RuntimeError,match='PILOT_HOST_SUPERVISOR_DURATION'):
        target.duration_seconds(text)


def run_setup(monkeypatch):
    from datetime import datetime,timezone,timedelta
    from ops.native_maintenance_agreement import Agreement,COVERAGE
    from ops.native_maintenance_workflow_pause import digest
    now=datetime.now(timezone.utc)
    fmt=lambda date: date.strftime('%Y-%m-%dT%H:%M:%SZ')
    scope={'source':'f'*40}
    record=dict(version=1,owner='olegmed1-art',operation_digest=digest(scope),
        not_before=fmt(now-timedelta(seconds=1)),expires_at=fmt(now+timedelta(minutes=5)),
        coverage=COVERAGE,evidence='test independently accepted window')
    agreement=Agreement(record,digest(record),scope)
    calls=[]
    monkeypatch.setattr(hold,'read',lambda *a:b'HOLD\n')
    for name in ('unchanged_files','arm_identity','unit_absent','attest_hardening',
                 'no_processes','empty_runtime_backends','activate_admission'):
        monkeypatch.setattr(target,name,lambda *a,_name=name,**kw:calls.append(_name))
    monkeypatch.setattr(hold,'service_hold_identity',lambda:hold.ServiceHoldIdentity(**asdict(prior())))
    monkeypatch.setattr(target.release.staging,'require_current_main',lambda *a:calls.append('fresh main'))
    monkeypatch.setattr(target.release,'retain',lambda *a:calls.append('retain'))
    args=dict(prior=prior(),protected={},protected_digest='d'*64,source='f'*40,
        request_digest='e'*64,seconds=120,agreement=agreement,scope=digest(scope),
        dispatch_id='dispatch',directory=target.plan.ROOT/('e'*64))
    return args,calls


def test_invalid_duration_refuses_before_service_stop(monkeypatch):
    args,calls=run_setup(monkeypatch)
    args['seconds']=59
    command=Mock();monkeypatch.setattr(target,'command',command)
    with pytest.raises(RuntimeError,match='PILOT_SERVICE_DURATION'):
        target.run_once(**args)
    command.assert_not_called()
    assert not calls


def test_lost_launch_ack_is_never_retried(monkeypatch):
    args,calls=run_setup(monkeypatch)
    def command(*argv,**kw):
        calls.append(argv)
        if argv[0]=='/usr/bin/systemd-run':
            raise RuntimeError('lost launch acknowledgement')
    monkeypatch.setattr(target,'command',command)
    with pytest.raises(RuntimeError,match='lost launch acknowledgement'):
        target.run_once(**args)
    launch=[c for c in calls if type(c) is tuple and c[0]=='/usr/bin/systemd-run']
    assert len(launch)==1
    assert calls.index('arm_identity')<calls.index(('/usr/bin/systemctl','stop',hold.UNIT))
    assert 'empty_runtime_backends' in calls and 'activate_admission' not in calls
    assert ('/usr/bin/systemctl','start',hold.UNIT) not in calls
    # The already armed ExecStopPost owns restoration, even without a launch ACK.


@pytest.mark.parametrize('fault',['command','second_command','missing_envfile','admission','duplicate_env','duration',None])
def test_pilot_actual_execution_contract(fault,monkeypatch):
    launch=target.plan.pilot_command('f'*40,prior(),120,request_digest='e'*64)
    args=launch[launch.index('--')+1:]
    state=dict(ExecStart='{ path='+target.plan.PYTHON+' ; argv[]='+' '.join(args)+
        ' ; ignore_errors=no ; start_time=n/a ; stop_time=n/a ; pid=1 ; code=(null) ; status=0/0 }',
        EnvironmentFiles=str(hold.ENV)+' (ignore_errors=no) '+prior().release+
        '/ops/autopilot/broker-hold.env (ignore_errors=no)',
        Environment='PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AUTOPILOT_ADMISSION_MODE=PILOT',
        RuntimeMaxUSec='2min')
    if fault=='command':state['ExecStart']=state['ExecStart'].replace(' -I ',' -E ')
    if fault=='second_command':state['ExecStart']+=' { another command }'
    if fault=='missing_envfile':state['EnvironmentFiles']=str(hold.ENV)+' (ignore_errors=no)'
    if fault=='admission':state['Environment']=state['Environment'].replace('PILOT','HOLD')
    if fault=='duplicate_env':state['Environment']+=' AUTOPILOT_ADMISSION_MODE=HOLD'
    if fault=='duration':state['RuntimeMaxUSec']='infinity'
    monkeypatch.setattr(target,'show',lambda *a:state)
    if fault:
        with pytest.raises(RuntimeError):target.attest_pilot_execution('f'*40,prior(),120,'e'*64)
    else:target.attest_pilot_execution('f'*40,prior(),120,'e'*64)


def test_pid1_attestation_failure_keeps_gate_closed(monkeypatch):
    args,calls=run_setup(monkeypatch)
    monkeypatch.setattr(target,'command',lambda *a,**k:calls.append(a))
    monkeypatch.setattr(target,'show',lambda *a:dict(MainPID='456',InvocationID='a'*32,
        NRestarts='0',ActiveState='active',WorkingDirectory=str(target.plan.source_path('f'*40)),
        Description='Bridge native pilot '+'e'*64))
    def refuse(*a):raise RuntimeError('PID1 effective environment drift')
    monkeypatch.setattr(target,'attest_pilot_execution',refuse)
    with pytest.raises(RuntimeError,match='PID1 effective environment drift'):
        target.run_once(**args)
    assert any(type(c) is tuple and c[0]=='/usr/bin/systemd-run' for c in calls)
    assert 'activate_admission' not in calls
