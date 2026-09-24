import json
from pathlib import Path
import pytest
from ops import oracle_light_stopped_hold_upgrade as target
from ops.oracle_light_runtime_hold_install import execution_contract


def harness(tmp_path, monkeypatch, fault=None):
    old, new = tmp_path/'old', tmp_path/'new'
    drop = tmp_path/'hold.conf'
    old_drop = ('[Service]\nWorkingDirectory='+str(old)+'\nEnvironment=AUTOPILOT_ADMISSION_MODE=HOLD\n').encode()
    drop.write_bytes(old_drop)
    calls=[]
    h=dict(RELEASE=old,DROP=drop,UNIT_PATH=tmp_path/'unit',ENV_PATH=tmp_path/'secrets',PYTHON='/venv/python')
    state=dict(ActiveState='inactive',SubState='dead',MainPID='0',NeedDaemonReload='no',
        WorkingDirectory=str(old),User='school-autopilot',Group='school-autopilot',
        FragmentPath=str(h['UNIT_PATH']),DropInPaths=str(drop),NRestarts='0',InvocationID='',
        Environment='AUTOPILOT_ADMISSION_MODE=HOLD',
        EnvironmentFiles=(str(h['ENV_PATH'])+' (ignore_errors=no)',),
        ExecStart='{ path=/venv/python ; argv[]=/venv/python -m oracle_autopilot.worker_v17 ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }')
    original=state.copy()
    h['read_owned']=lambda path,mode:path.read_bytes()
    h['service']=lambda:state.copy()
    def run(*args):
        assert args == ('systemctl','daemon-reload')  # neither success nor rollback may start
        calls.append(args)
        upgraded=drop.read_bytes()!=old_drop
        state.update(WorkingDirectory=str(new if upgraded else old),
            EnvironmentFiles=(str(h['ENV_PATH'])+' (ignore_errors=no)',)+
                ((str(new/target.EXTRA_ENV)+' (ignore_errors=no)',) if upgraded else ()))
        if upgraded and fault=='reload': raise RuntimeError('reload failure')
        if upgraded and fault=='loaded_env': state['EnvironmentFiles']='wrong'
        if upgraded and fault=='restart_count': state['NRestarts']='1'
    def sync(path):
        if fault=='fsync' and drop.read_bytes()!=old_drop: raise OSError('fsync failure')
    def probe(*args):
        if fault=='probe': raise target.Blocked('COMPATIBILITY_NOT_CONFIRMED')
        return {'status':'PASS'}
    staging=dict(run=run,fsync_directory=sync,execution_contract=execution_contract)
    monkeypatch.setattr(target,'probe',probe)
    stable_calls=0
    def stable():
        nonlocal stable_calls
        stable_calls+=1
        if fault=='pre_switch' or (fault=='post_switch' and stable_calls>=2):
            raise target.Blocked('DRIFT')
    return h,staging,new,{},original,old_drop,stable,calls


@pytest.mark.parametrize('fault',[None,'reload','loaded_env','restart_count','probe','fsync','pre_switch','post_switch'])
def test_switch_and_faults_never_start_service(tmp_path,monkeypatch,capsys,fault):
    h,s,new,env,before,old_drop,stable,calls=harness(tmp_path,monkeypatch,fault)
    if fault:
        with pytest.raises((target.Blocked,RuntimeError,OSError)):
            target.switch(h,s,new,env,before,old_drop,stable)
        assert h['DROP'].read_bytes()==old_drop
    else:
        assert target.switch(h,s,new,env,before,old_drop,stable)=={'status':'PASS'}
        assert h['DROP'].read_bytes()==target.desired_drop(new)
    assert h['service']()['MainPID']=='0'
    assert all(c==('systemctl','daemon-reload') for c in calls)


def test_foreign_drop_is_not_overwritten_on_rollback(tmp_path,monkeypatch):
    h,s,new,env,before,old_drop,stable,calls=harness(tmp_path,monkeypatch)
    def foreign(*args):
        h['DROP'].write_bytes(b'foreign administrator change')
        raise target.Blocked('DRIFT')
    monkeypatch.setattr(target,'probe',foreign)
    with pytest.raises(target.Blocked,match='ROLLBACK_DROP_DRIFT'):
        target.switch(h,s,new,env,before,old_drop,stable)
    assert h['DROP'].read_bytes()==b'foreign administrator change'


def test_atomic_replacement_rejects_stale_comparison(tmp_path):
    path=tmp_path/'hold';path.write_bytes(b'foreign')
    with pytest.raises(target.Blocked,match='DROP_COMPARE_FAILED'):
        target.replace_drop(path,b'old',b'new',lambda p,m:p.read_bytes(),lambda p:None)
    assert path.read_bytes()==b'foreign'


def test_explicit_rollback_after_success_stays_stopped(tmp_path,monkeypatch):
    h,s,new,env,before,old_drop,stable,calls=harness(tmp_path,monkeypatch)
    target.switch(h,s,new,env,before,old_drop,stable)
    target.restore_stopped(h,s,new,old_drop)
    assert h['DROP'].read_bytes()==old_drop
    assert h['service']()['MainPID']=='0'
    assert calls==[('systemctl','daemon-reload')]*2


def test_rollback_refuses_running_service(tmp_path,monkeypatch):
    h,s,new,env,before,old_drop,stable,calls=harness(tmp_path,monkeypatch)
    h['service']=lambda:{**before,'MainPID':'123','ActiveState':'active'}
    with pytest.raises(target.Blocked,match='ROLLBACK_NOT_STOPPED'):
        target.restore_stopped(h,s,new,old_drop)
    assert calls==[]


def test_generated_override_contains_only_non_secret_pins():
    verifier=dict(SOURCE='s',ARTIFACT='a',POLICY='p',PROVENANCE='r')
    release=dict(schema_version=1,broker_url=target.BROKER_URL,broker_source_sha='s',
        broker_artifact_sha256='a',broker_policy_sha256='p',broker_provenance_sha256='r',
        broker_policy_version='physical-no-merge-v2')
    pins=target.release_pins(release,verifier)
    result=target.augment_bundle({'revision':'a'*40,'files':{}},pins,b'old HOLD')
    assert set(line.partition('=')[0] for line in result['files'][target.EXTRA_ENV].splitlines())==set(target.PIN_KEYS)
    assert result['files'][target.BACKUP]=='old HOLD'
    release['broker_url']='https://untrusted.example/'
    with pytest.raises(target.Blocked,match='BROKER_RELEASE_DRIFT'):
        target.release_pins(release,verifier)


@pytest.mark.parametrize('files', [
    ('/etc/secrets (ignore_errors=no)',),
    ('/etc/secrets (ignore_errors=no)', '/release/broker.env (ignore_errors=no)'),
])
def test_systemd_repeated_environment_files_preserve_order(monkeypatch, files):
    from types import SimpleNamespace
    output = ''.join(key + '=value\n' for key in target.SERVICE_KEYS if key != 'EnvironmentFiles')
    output += ''.join('EnvironmentFiles=' + value + '\n' for value in files)
    monkeypatch.setattr(target.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=output))
    assert target.service_state('unit')['EnvironmentFiles'] == files


@pytest.mark.parametrize('change', ['missing', 'reversed', 'duplicate', 'ignored', 'foreign'])
def test_environment_file_contract_rejects_drift(tmp_path, monkeypatch, change):
    h,s,new,env,before,old_drop,stable,calls=harness(tmp_path,monkeypatch)
    state={**before, 'WorkingDirectory': str(new)}
    files=[str(h['ENV_PATH'])+' (ignore_errors=no)', str(new/target.EXTRA_ENV)+' (ignore_errors=no)']
    if change=='missing': files=files[1:]
    if change=='reversed': files.reverse()
    if change=='duplicate': files.append(files[-1])
    if change=='ignored': files[0]=files[0].replace('=no','=yes')
    if change=='foreign': files.append('/foreign (ignore_errors=no)')
    state['EnvironmentFiles']=tuple(files)
    with pytest.raises(target.Blocked,match='ENVIRONMENT_FILES_DRIFT'):
        target.validate_stopped(state,h,new,True)


@pytest.mark.parametrize('suffix,guard', [
    ('MainPID=0\n','SERVICE_PROPERTY_DUPLICATE'),
    ('garbage\n','SERVICE_PROPERTY_INVALID'),
    ('Unknown=value\n','SERVICE_PROPERTY_INVALID'),
])
def test_systemd_parser_rejects_ambiguous_properties(monkeypatch,suffix,guard):
    from types import SimpleNamespace
    output=''.join(key+'=value\n' for key in target.SERVICE_KEYS)+suffix
    monkeypatch.setattr(target.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout=output))
    with pytest.raises(target.Blocked,match=guard):
        target.service_state('unit')
