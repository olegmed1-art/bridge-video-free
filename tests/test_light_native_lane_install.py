"""Dormant installation faults must leave the legacy service untouched."""
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from ops import light_native_lane_install as target
from test_light_native_pilot_release import value, committed_tree

SOURCE = 'a'*40


def test_unit_has_two_hold_barriers_no_credentials_and_no_boot_enable():
    unit = target.render(SOURCE).decode()
    assert 'AUTOPILOT_ADMISSION_MODE=HOLD' in unit
    assert 'PrivateNetwork=yes' in unit and 'Restart=no' in unit
    assert 'ReadWritePaths='+str(target.STATE)+'\n' in unit
    assert 'EnvironmentFile' not in unit and '[Install]' not in unit
    assert 'WantedBy' not in unit and 'DATABASE' not in unit
    assert 'worker_v17' not in unit
    with pytest.raises(RuntimeError): target.render('../x')


def test_bootstrap_acceptance_covers_action_and_all_bytes(tmp_path):
    revision = committed_tree(tmp_path)
    raw = target.release.package(tmp_path,revision)
    digest = hashlib.sha256(raw).hexdigest()
    compile(target.program(raw,revision,digest,'install-hold'),'generated','exec')
    with pytest.raises(RuntimeError): target.program(raw,revision,digest,'RUN')
    with pytest.raises(RuntimeError): target.program(raw+b' ',revision,digest,'install-hold')


def test_root_writes_refuse_writable_ancestors_and_symlinks(tmp_path):
    # pytest /tmp is deliberately writable, so root_parent must reject it.
    with pytest.raises(RuntimeError,match='PARENT'): target.root_parent(tmp_path)
    alias = tmp_path/'alias';alias.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(RuntimeError,match='PARENT'): target.root_parent(alias)


def installation(tmp_path, monkeypatch, fault):
    for name in ('UNIT_FILE','CONTROL','STATE','LEDGER'):
        monkeypatch.setattr(target,name,tmp_path/name)
    monkeypatch.setattr(target,'root_parent',lambda path:None)
    monkeypatch.setattr(target.os,'uname',lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
    user=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
    monkeypatch.setattr(target.pwd,'getpwnam',lambda name:user)
    prior=target.hold.HoldIdentity('autopilot-lite-vnic',123,'b'*32,'/old','private')
    monkeypatch.setattr(target.hold,'attest',lambda:prior)
    monkeypatch.setattr(target.release.staging,'require_current_main',lambda source:None)
    monkeypatch.setattr(target.release,'stage',lambda *a:None)
    monkeypatch.setattr(target.switch,'unit_absent',lambda *a:None)
    monkeypatch.setattr(target,'fresh_state',lambda user:target.STATE.mkdir(mode=0o700))
    events=[]
    monkeypatch.setattr(target.switch,'command',lambda *a:events.append(a))
    monkeypatch.setattr(target,'verify_config',lambda *a:None)
    invocations=iter(['1'*32,'2'*32])
    def hold(*args):
        invocation=next(invocations)
        if fault=='first-start' or (fault=='second-start' and invocation=='2'*32):
            raise RuntimeError('injected')
        return {'InvocationID':invocation,'MainPID':'321'}
    monkeypatch.setattr(target,'await_hold',hold)
    def stopped():
        events.append('drained')
        if fault=='stop':raise RuntimeError('injected')
    monkeypatch.setattr(target,'stopped',stopped)
    return events


@pytest.mark.skipif(os.geteuid()!=0,reason='real root-owned ledger')
@pytest.mark.parametrize('fault',[None,'first-start','second-start','stop'])
def test_real_ledger_faults_never_restart_legacy_or_retry_unknown(tmp_path,monkeypatch,fault):
    events=installation(tmp_path,monkeypatch,fault)
    bundle=value()
    if fault:
        with pytest.raises(RuntimeError):target.operation(bundle,SOURCE,'install-hold')
        assert not (target.LEDGER/'installed.json').exists()
        assert events[-2:]==[('/usr/bin/systemctl','stop',target.UNIT),'drained']
    else:
        result=target.operation(bundle,SOURCE,'install-hold')
        assert result['state']=='HOLD' and result['stop_rehearsal']
        assert (target.LEDGER/'installed.json').exists()
        assert (target.LEDGER/'stop-rehearsal.json').exists()
        assert events.count(('/usr/bin/systemctl','start',target.UNIT))==2
    assert target.CONTROL.joinpath('admission').read_bytes()==b'HOLD\n'
    assert not target.CONTROL.joinpath('current.json').exists()
    assert (target.LEDGER/'before.json').exists()
    assert all(target.hold.UNIT not in event for event in events if isinstance(event,tuple))
    before=list(events)
    with pytest.raises(RuntimeError,match='REQUIRES_RECONCILIATION'):
        target.operation(bundle,SOURCE,'install-hold')
    assert events==before


def test_state_creation_drops_privileges_and_scrubs_environment(monkeypatch):
    seen=[]
    monkeypatch.setattr(target.os,'setgroups',lambda groups:seen.append(('groups',groups)))
    monkeypatch.setattr(target.os,'setgid',lambda gid:seen.append(('gid',gid)))
    monkeypatch.setattr(target.os,'setuid',lambda uid:seen.append(('uid',uid)))
    def run(command,**kwargs):
        assert kwargs['env']=={'PATH':'/usr/bin:/bin'}
        assert command[-1]==str(target.STATE) and kwargs['timeout']==10
        kwargs['preexec_fn']()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(target.subprocess,'run',run)
    target.fresh_state(SimpleNamespace(pw_uid=123,pw_gid=456))
    assert seen==[('groups',[]),('gid',456),('uid',123)]


@pytest.mark.skipif(os.geteuid()!=0,reason='real root-owned ledger')
def test_failure_before_start_does_not_stop_unowned_unit(tmp_path,monkeypatch):
    events=installation(tmp_path,monkeypatch,None)
    monkeypatch.setattr(target,'fresh_state',Mock(side_effect=RuntimeError('injected')))
    with pytest.raises(RuntimeError,match='injected'):target.operation(value(),SOURCE,'install-hold')
    assert not events
    assert (target.LEDGER/'before.json').exists()


@pytest.mark.parametrize('fault',[None,'quarantined','environment','argv','journal-empty','extra-state'])
def test_active_process_is_not_enough_for_verified_hold(tmp_path,monkeypatch,fault):
    proc=tmp_path/'proc';process=proc/'123';process.mkdir(parents=True)
    candidate=tmp_path/'release';candidate.mkdir()
    (process/'cwd').symlink_to(candidate,target_is_directory=True)
    (process/'cmdline').write_bytes(b'\0'.join(x.encode() for x in
        [target.release.PYTHON,'-I','-B','-c',target.LAUNCH,str(candidate),'']))
    (process/'environ').write_bytes(b'AUTOPILOT_ADMISSION_MODE=HOLD\0PATH=/usr/bin:/bin\0')
    state=tmp_path/'state';state.mkdir(mode=0o700);(state/'pilot.lock').touch(mode=0o600)
    monkeypatch.setattr(target,'PROC',proc)
    monkeypatch.setattr(target,'STATE',state)
    monkeypatch.setattr(target.plan,'source_path',lambda source:candidate)
    monkeypatch.setattr(target,'verify_config',lambda *a:None)
    row=dict(ActiveState='active',SubState='running',MainPID='123',NRestarts='0',InvocationID='a'*32)
    monkeypatch.setattr(target,'show',lambda *a:row)
    audit='{"audit":"LIGHT_NATIVE_LANE","state":"HOLD"}'
    if fault=='quarantined':audit='{"audit":"LIGHT_NATIVE_LANE_QUARANTINED"}'
    elif fault=='journal-empty':audit=''
    elif fault=='environment':(process/'environ').write_bytes(b'AUTOPILOT_ADMISSION_MODE=HOLD\0AUTOPILOT_DATABASE_URL=secret\0')
    elif fault=='argv':(process/'cmdline').write_bytes(b'other\0')
    elif fault=='extra-state':(state/'quarantine.json').write_text('{}')
    monkeypatch.setattr(target.switch,'command',lambda *a:audit)
    user=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
    if fault:
        with pytest.raises(RuntimeError):target.verify_running(SOURCE,user)
    else:assert target.verify_running(SOURCE,user)==row


def test_show_proves_empty_environment_array_from_typed_dbus(monkeypatch):
    seen=[]
    def command(*args):
        seen.append(args)
        if args[0]=='/usr/bin/busctl':return 'a(sb) 0'
        return 'UnitFileState=static'
    monkeypatch.setattr(target.switch,'command',command)
    assert target.show(target.UNIT,['EnvironmentFiles','UnitFileState']) == dict(EnvironmentFiles='',UnitFileState='static')
    assert '--all' in seen[0] and '--property=EnvironmentFiles' not in seen[0]
    assert seen[1][-2:]==('org.freedesktop.systemd1.Service','EnvironmentFiles')
    monkeypatch.setattr(target.switch,'command',lambda *args:'')
    with pytest.raises(RuntimeError,match='UNIT_FIELDS'):target.show(target.UNIT,['UnitFileState'])
    for response in ('', 'a(sb) 1 "/unexpected.env" false', 'as 0'):
        monkeypatch.setattr(target.switch,'command',lambda *args:response)
        with pytest.raises(RuntimeError,match='ENVIRONMENT_FILES'):
            target.show(target.UNIT,['EnvironmentFiles'])


@pytest.mark.skipif(os.geteuid()!=0,reason='real root-owned retained state')
@pytest.mark.parametrize('fault',[None,'journal','started','ledger','source','start-failure'])
def test_explicit_reentry_preserves_original_source_and_refuses_uncertain_state(tmp_path,monkeypatch,fault):
    events=installation(tmp_path,monkeypatch,None)
    monkeypatch.setattr(target,'verify_config',Mock(side_effect=RuntimeError('missing property')))
    bundle=value()
    with pytest.raises(RuntimeError):target.operation(bundle,SOURCE,'install-hold')
    assert not any('start' in event for event in events)
    before=target.LEDGER.joinpath('before.json').read_bytes()
    unit=target.UNIT_FILE.read_bytes()
    monkeypatch.setattr(target,'verify_config',lambda *args:None)
    monkeypatch.setattr(target.release.staging,'verify_release',lambda path,item:None)
    monkeypatch.setattr(target.release,'stage',Mock(side_effect=AssertionError('must not stage')))
    monkeypatch.setattr(target,'RETAINED_SOURCE',SOURCE)
    row=dict(InvocationID='',ExecMainPID='0',MainPID='0',ControlPID='0',ActiveState='inactive')
    monkeypatch.setattr(target,'show',lambda *args:row)
    mains=[]
    monkeypatch.setattr(target.release.staging,'require_current_main',lambda source:mains.append(source))
    if fault=='journal':(target.STATE/'pilot.lock').touch()
    elif fault=='started':row['InvocationID']='b'*32
    elif fault=='ledger':(target.LEDGER/'installed.json').write_text('{}')
    elif fault=='source':monkeypatch.setattr(target,'RETAINED_SOURCE','c'*40)
    elif fault=='start-failure':monkeypatch.setattr(target,'await_hold',Mock(side_effect=RuntimeError('injected')))
    events.clear()
    if fault:
        with pytest.raises(RuntimeError):target.operation(bundle,SOURCE,'complete-hold',controller_source='d'*40)
        if fault!='start-failure':assert not any('start' in event for event in events)
        else:assert events[-2:]==[('/usr/bin/systemctl','stop',target.UNIT),'drained']
    else:
        result=target.operation(bundle,SOURCE,'complete-hold',controller_source='d'*40)
        assert result['state']=='HOLD' and result['source']==SOURCE
        assert events.count(('/usr/bin/systemctl','start',target.UNIT))==2
        with pytest.raises(RuntimeError,match='REENTRY_LEDGER'):
            target.operation(bundle,SOURCE,'complete-hold',controller_source='d'*40)
    assert set(mains)=={'d'*40}
    assert target.LEDGER.joinpath('before.json').read_bytes()==before
    assert target.UNIT_FILE.read_bytes()==unit


def test_retained_program_requires_independent_original_package_acceptance(tmp_path,monkeypatch):
    import json
    revision=committed_tree(tmp_path)
    raw=target.release.package(tmp_path,revision)
    digest=hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(target,'RETAINED_SOURCE',revision)
    script=target.program(raw,revision,digest,'complete-retained-hold',raw,digest)
    compile(script,'retained-bootstrap','exec')
    with pytest.raises(RuntimeError):target.program(raw,revision,digest,'complete-retained-hold',raw,'f'*64)
    with pytest.raises(RuntimeError):target.program(raw,revision,digest,'install-hold',raw,digest)
