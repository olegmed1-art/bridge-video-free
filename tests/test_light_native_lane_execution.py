"""Bounded execution order and mandatory deny/stop on uncertain outcomes."""
from dataclasses import asdict
import os
import time
from types import SimpleNamespace
import pytest
from ops import light_native_lane_execution as target

SOURCE=target.install.RETAINED_SOURCE
DIGEST='d'*64


def test_transient_plan_never_edits_or_enables_the_installed_unit():
    prior=target.install.hold.HoldIdentity('autopilot-lite-vnic',123,'b'*32,
        str(target.plan.source_path('a'*40)),'private')
    command=target.command(SOURCE,prior,120,DIGEST)
    assert '--unit='+target.UNIT in command
    assert '--property=RuntimeMaxSec=120' in command
    assert '--property=Restart=no' in command
    assert '--property=PrivateNetwork=no' in command
    assert '--property=Environment=AUTOPILOT_ADMISSION_MODE=NATIVE' in command
    assert str(target.install.UNIT_FILE) not in ' '.join(command)
    assert command[command.index('--')+1:]==target.argv(SOURCE)
    supervisor=target.supervisor_command(DIGEST,120)
    assert any(x.startswith('--property=ExecStopPost=') and x.endswith(' restore '+DIGEST) for x in supervisor)
    assert '--property=RuntimeMaxSec=120' in supervisor
    for bad in (0,1801,True):
        with pytest.raises(RuntimeError):target.command(SOURCE,prior,bad,DIGEST)
    with pytest.raises(RuntimeError):target.command('a'*40,prior,120,DIGEST)


@pytest.fixture
def host(tmp_path,monkeypatch):
    events=[]
    install=target.install
    for key in ('CONTROL','STATE'):
        path=tmp_path/key;path.mkdir(mode=0o700);monkeypatch.setattr(install,key,path)
    (install.STATE/'pilot.lock').touch(mode=0o600)
    (install.CONTROL/'admission').write_bytes(b'HOLD\n');(install.CONTROL/'admission').chmod(0o640)
    unitfile=tmp_path/'native.service';unitfile.write_bytes(install.render(SOURCE));unitfile.chmod(0o644)
    monkeypatch.setattr(install,'UNIT_FILE',unitfile)
    prior=install.hold.HoldIdentity('autopilot-lite-vnic',123,'b'*32,str(target.plan.source_path('a'*40)),'private')
    original=dict(ActiveState='active',SubState='running',MainPID='555',InvocationID='a'*32,NRestarts='0')
    value=dict(source=SOURCE,controller_source='c'*40,expires_at=int(time.time())+1000,seconds=120,
        prior=asdict(prior),protected={},protected_sha256='f'*64,original=original)
    state=dict(permanent=original.copy(),transient=False)
    monkeypatch.setattr(target,'request',lambda digest:(value,prior,tmp_path))
    monkeypatch.setattr(target.os,'uname',lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
    monkeypatch.setattr(target.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid()))
    monkeypatch.setattr(install,'root_parent',lambda path:None)
    monkeypatch.setattr(install,'verify_unit',lambda source:events.append('original-unit'))
    monkeypatch.setattr(install,'verify_hold_process',lambda *a:original.copy())
    monkeypatch.setattr(install.hold,'service_hold_identity',lambda:prior)
    monkeypatch.setattr(install.release.staging,'require_current_main',lambda source:events.append('main'))
    monkeypatch.setattr(target.switch,'unchanged_files',lambda *a:events.append('protected'))
    monkeypatch.setattr(target.switch,'unit_absent',lambda unit:events.append('transient-absent'))
    monkeypatch.setattr(target,'arm',lambda *a:events.append('armed'))
    monkeypatch.setattr(target,'feed',lambda value:SimpleNamespace(check=lambda:events.append('permit')))
    monkeypatch.setattr(target,'owned',lambda *a:state['transient'])
    row=dict(ActiveState='active',SubState='running',MainPID='777',InvocationID='b'*32,NRestarts='0')
    monkeypatch.setattr(target,'process',lambda value:row.copy())
    messages=iter([['{"audit":"LIGHT_NATIVE_LANE","state":"HOLD"}'],
        ['{"audit":"LIGHT_NATIVE_LANE","state":"AWAITING_OWNER","terminal_sha256":"'+'e'*64+'"}']])
    monkeypatch.setattr(target,'journal',lambda invocation:next(messages))
    monkeypatch.setattr(install,'show',lambda unit,fields:{k:state['permanent'][k] for k in fields})
    def stopped():
        assert state['permanent']['MainPID']=='0'
        events.append('original-drained')
    monkeypatch.setattr(install,'stopped',stopped)
    def transient_stopped():
        assert not state['transient']
        events.append('transient-drained')
    monkeypatch.setattr(target,'stopped',transient_stopped)
    def command(*args,**kwargs):
        events.append(args)
        if args[:2]==('/usr/bin/systemctl','stop'):
            if args[2]==install.UNIT:state['permanent'].update(ActiveState='inactive',SubState='dead',MainPID='0')
            elif args[2]==target.UNIT:state['transient']=False
            else:raise AssertionError('must never stop legacy')
        elif args[0]=='/usr/bin/systemd-run':state['transient']=True
        else:raise AssertionError('unexpected mutation')
    monkeypatch.setattr(target.switch,'command',command)
    old_admission=target.admission
    def admission(raw):
        events.append(raw)
        old_admission(raw)
    monkeypatch.setattr(target,'admission',admission)
    return events,value,state,tmp_path


@pytest.mark.skipif(os.geteuid()!=0,reason='root-owned admission')
def test_one_dispatch_requires_armed_supervisor_and_cleanup_stops_before_readback(host):
    events,value,state,root=host
    result=target.run_once(DIGEST,lambda permit:True,live_guard=lambda:None)
    assert result['state']=='READBACK_REQUIRED'
    assert events.index('armed') < events.index(('/usr/bin/systemctl','stop',target.install.UNIT))
    assert events.index('original-drained') < next(i for i,x in enumerate(events) if isinstance(x,tuple) and x[0]=='/usr/bin/systemd-run')
    assert events[-1] != b'RUN\n' and b'RUN\n' in events
    restored=target.restore(DIGEST)
    assert restored['state']=='STOPPED_HOLD' and restored['terminal_verified'] is False
    assert not restored['db_controls_restored']
    assert events.index(b'HOLD\n') < events.index(('/usr/bin/systemctl','stop',target.UNIT))
    assert (target.install.CONTROL/'admission').read_bytes()==b'HOLD\n'
    assert not state['transient'] and state['permanent']['MainPID']=='0'
    assert (root/'launch-intent.json').exists() and (root/'stopped-hold.json').exists()
    assert not any(isinstance(e,tuple) and 'start' in e for e in events)
    # ExecStopPost can run twice; it never retries or restarts a task.
    assert target.restore(DIGEST)==restored


@pytest.mark.skipif(os.geteuid()!=0,reason='root-owned admission')
@pytest.mark.parametrize('fault',['unarmed','gate','start_ack','hold_marker','expired'])
def test_supervised_failure_never_grants_run_and_restoration_preserves_evidence(host,monkeypatch,fault):
    events,value,state,root=host
    gate=lambda permit:True
    if fault=='unarmed':
        def fail(*a):raise RuntimeError('not armed')
        monkeypatch.setattr(target,'arm',fail)
    elif fault=='gate':gate=lambda permit:False
    elif fault=='start_ack':
        old=target.switch.command
        def lost(*args,**kw):
            old(*args,**kw)
            if args[0]=='/usr/bin/systemd-run':raise RuntimeError('lost ACK')
        monkeypatch.setattr(target.switch,'command',lost)
    elif fault=='hold_marker':
        monkeypatch.setattr(target,'journal',lambda invocation:['{"audit":"LIGHT_NATIVE_LANE_QUARANTINED"}'])
        monkeypatch.setattr(target.time,'sleep',lambda seconds:None)
    else:value['expires_at']=int(time.time())-1
    with pytest.raises(RuntimeError):target.run_once(DIGEST,gate,live_guard=lambda:None)
    assert b'RUN\n' not in events
    result=target.restore(DIGEST)
    assert result['state'] in ('ORIGINAL_HOLD','STOPPED_HOLD')
    assert (target.install.CONTROL/'admission').read_bytes()==b'HOLD\n'
    assert not state['transient']


@pytest.mark.parametrize('original_running',[True,False])
@pytest.mark.skipif(os.geteuid()!=0,reason='root-owned admission')
def test_cleanup_before_transient_exists_accepts_only_exact_not_found(host,monkeypatch,original_running):
    events,value,state,root=host
    if not original_running:
        state['permanent'].update(ActiveState='inactive',SubState='dead',MainPID='0')
    # Exercise actual not-found probes: no empty WorkingDirectory/ControlGroup
    # properties are invented for a unit systemd has never loaded.
    def show(unit,fields):
        assert unit==target.UNIT
        assert fields==['LoadState','ActiveState','MainPID']
        return {'LoadState':'not-found','ActiveState':'inactive','MainPID':'0'}
    monkeypatch.setattr(target.switch,'show',show)
    # fixture overrides are replaced with module originals saved below.
    monkeypatch.setattr(target,'owned',REAL_OWNED)
    monkeypatch.setattr(target,'stopped',REAL_STOPPED)
    result=target.restore(DIGEST)
    assert result['state']==('ORIGINAL_HOLD' if original_running else 'STOPPED_HOLD')
    assert not any(isinstance(e,tuple) for e in events)
    assert (target.install.CONTROL/'admission').read_bytes()==b'HOLD\n'


REAL_OWNED=target.owned
REAL_STOPPED=target.stopped



def test_restoration_imports_without_site_packages_or_database_driver():
    import subprocess,sys
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    code="import sys;sys.path.insert(0,sys.argv[1]);from ops import light_native_lane_execution;assert 'psycopg' not in sys.modules"
    result=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(root)],capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr.decode()


@pytest.mark.skipif(os.geteuid()!=0,reason='root supervisor script')
@pytest.mark.parametrize('fault',[None,'script','start','kill_mode','hook','timeout'])
def test_arm_verifies_retained_script_and_actual_pid1_command(tmp_path,monkeypatch,fault):
    import hashlib
    script=tmp_path/'supervisor.py';script.write_bytes(b'# reviewed wrapper\n');script.chmod(0o600)
    value={'supervisor_sha256':hashlib.sha256(script.read_bytes()).hexdigest(),'seconds':120}
    prefix='/usr/bin/python3 -I -S -B '+str(script)
    command=lambda mode:'{ path=/usr/bin/python3 ; argv[]='+prefix+' '+mode+' '+DIGEST+' ; ignore_errors=no ; }'
    row=dict(MainPID=str(os.getpid()),ActiveState='active',User='root',Group='root',Restart='no',
        NoNewPrivileges='yes',KillMode='control-group',ExecStart=command('run'),ExecStopPost=command('restore'),
        RuntimeMaxUSec='2min',TimeoutStopUSec='2min')
    if fault=='script':script.write_bytes(b'# changed wrapper\n')
    elif fault=='start':row['ExecStart']=command('unrelated')
    elif fault=='kill_mode':row['KillMode']='process'
    elif fault=='hook':row['ExecStopPost']=''
    elif fault=='timeout':row['RuntimeMaxUSec']='infinity'
    monkeypatch.setattr(target.switch,'show',lambda unit,fields:row)
    if fault:
        with pytest.raises(RuntimeError):target.arm(DIGEST,value,tmp_path)
    else:target.arm(DIGEST,value,tmp_path)


@pytest.mark.skipif(os.geteuid()!=0,reason='root-owned admission')
def test_restore_requires_permanent_control_pid_and_descendants_drained(host,monkeypatch):
    events,value,state,root=host
    state['permanent'].update(ActiveState='inactive',SubState='dead',MainPID='0')
    def busy():raise RuntimeError('LANE_INSTALL_CGROUP_POPULATED')
    monkeypatch.setattr(target.install,'stopped',busy)
    with pytest.raises(RuntimeError,match='CGROUP_POPULATED'):target.restore(DIGEST)
    assert (target.install.CONTROL/'admission').read_bytes()==b'HOLD\n'
    assert not (root/'stopped-hold.json').exists()

@pytest.mark.skipif(os.geteuid()!=0,reason='root-owned admission')
def test_live_authority_poll_is_bounded_while_local_checks_continue(host,monkeypatch):
    events,value,state,root=host
    clock=[0.0];checks=[];local=[]
    monkeypatch.setattr(target.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(target.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    def journal(invocation):
        local.append(clock[0])
        if b'RUN\n' not in events:return ['{"audit":"LIGHT_NATIVE_LANE","state":"HOLD"}']
        if clock[0]>=64:return ['{"audit":"LIGHT_NATIVE_LANE_QUARANTINED"}']
        return []
    monkeypatch.setattr(target,'journal',journal)
    def authority():checks.append(clock[0])
    target.run_once(DIGEST,lambda permit:True,live_guard=authority)
    assert [t for t in checks if t>0]==[16,32,48,64]
    assert len(local)>30
    assert events.count('main')==3  # Before stop, start, and RUN only.


@pytest.mark.skipif(os.geteuid()!=0, reason='root-owned admission')
@pytest.mark.parametrize('fault', [None, 'gap', 'terminal'])
def test_successor_history_precedes_stop_and_retains_bounded_cleanup(host, monkeypatch, fault):
    from ops import light_native_lane_feed as owner_feed
    from oracle_autopilot import light_native_lane as lane
    events, value, state, root = host
    value['sequence'] = 1
    terminal = b'accepted-private-terminal'
    cursor = lane.encoded(dict(version=1, source=SOURCE, sequence=1,
        dispatch_id='12345678-1234-4234-8234-123456789099', permit_sha256='a'*64,
        previous_terminal_sha256=lane.digest(terminal) if fault!='terminal' else 'b'*64))
    monkeypatch.setattr(owner_feed, 'completed_history', lambda source: [] if fault=='gap' else [(b'intent', terminal)])
    monkeypatch.setattr(owner_feed, 'root_record', lambda *a: cursor)
    if fault:
        with pytest.raises(RuntimeError, match='HISTORY_CHANGED'):
            target.run_once(DIGEST, lambda permit: True, live_guard=lambda: None)
        assert not any(isinstance(e, tuple) for e in events)
        assert b'RUN\n' not in events
    else:
        assert target.run_once(DIGEST, lambda permit: True, live_guard=lambda: None)['state']=='READBACK_REQUIRED'
        assert target.restore(DIGEST)['state']=='STOPPED_HOLD'
        assert not state['transient'] and (target.install.CONTROL/'admission').read_bytes()==b'HOLD\n'
