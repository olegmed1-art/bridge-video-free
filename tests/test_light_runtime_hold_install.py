"""Fault injection for the production service switch and its constrained rollback."""
import json
import os
from pathlib import Path
import subprocess
import sys
import types
from unittest.mock import Mock

import pytest
from ops import oracle_light_runtime_hold_install as target


def test_only_source_defined_assertions_publish_codes():
    with pytest.raises(target.InstallBlocked) as caught:
        target.check(False,'LIVE_CODE_PATH')
    assert target.failure_record(caught.value)['error_code']=='LIVE_CODE_PATH'
    for error in (RuntimeError('private-dsn'),OSError('private-token')):
        record=target.failure_record(error)
        assert 'error_code' not in record
        assert 'private' not in json.dumps(record)


EXEC_START='{ path=/worker/python ; argv[]=/worker/python -m worker ; ignore_errors=no ; start_time=[Tue 2026-09-22 18:40:23 UTC] ; stop_time=[n/a] ; pid=42 ; code=(null) ; status=0/0 }'


def test_reload_execution_accounting_is_not_command_drift(capsys):
    before={'ExecStart':EXEC_START,'MainPID':'42','InvocationID':'same'}
    after={**before,'WorkingDirectory':'/release','DropInPaths':str(target.DROP),
        'ExecStart':EXEC_START.replace('Tue 2026-09-22 18:40:23 UTC','n/a').replace('pid=42','pid=0')}
    target.attest_loaded_config(before,after,Path('/release'))
    assert json.loads(capsys.readouterr().out)=={'pre_stop_mismatched_fields':['ExecStart']}


@pytest.mark.parametrize('field,value',[
    ('MainPID','43'),('InvocationID','different'),('NRestarts','1'),
    ('WorkingDirectory','/wrong'),('DropInPaths','/extra'),
    ('ExecStart',EXEC_START.replace(' -m worker',' -m wrong')),
    ('ExecStart',EXEC_START+' { path=/extra ; }'),
    ('ExecStart',EXEC_START.replace('ignore_errors=no','ignore_errors=yes')),
])
def test_reload_still_rejects_process_and_execution_drift(field,value):
    before={'ExecStart':EXEC_START,'MainPID':'42','InvocationID':'same','NRestarts':'0'}
    after={**before,'WorkingDirectory':'/release','DropInPaths':str(target.DROP),field:value}
    with pytest.raises(target.InstallBlocked):
        target.attest_loaded_config(before,after,Path('/release'))


def harness(tmp_path,monkeypatch,failure=None):
    route=tmp_path/'route'
    route.mkdir()
    lock=route/'route.lock'
    lock.write_text('')
    lock.chmod(0o644)
    info=lock.stat()
    (route/'lock-identity.json').write_text(json.dumps({'device':info.st_dev,'inode':info.st_ino}))
    (route/'route.json').write_text(json.dumps({'version':1,'backend':'neon','database':'autopilot','epoch':0}))
    unit=tmp_path/'service.unit'; unit.write_bytes(b'original unit')
    env=tmp_path/'service.env'; env.write_bytes(b'original environment')
    dropdir=tmp_path/'dropin'; drop=dropdir/'hold.conf'
    release=tmp_path/'release'; release.mkdir()
    old={'ActiveState':'active','SubState':'running','MainPID':'999999991','NRestarts':'0',
         'InvocationID':'a'*32,'WorkingDirectory':'/old','User':'school-autopilot',
         'Group':'school-autopilot','DropInPaths':'','FragmentPath':str(unit)}
    state=old.copy(); commands=[]; probe_count=0; started_new=False
    old_env={'AUTOPILOT_WORKER_ID':'oracle-autopilot-light-1','AUTOPILOT_DATABASE_URL':'private-dsn'}
    h=types.ModuleType('_held_install_fixture')
    h.ROUTE=route;h.UNIT_PATH=unit;h.ENV_PATH=env;h.OLD_REVISION='old';h.UNIT='unit'
    h.validate_bundle=lambda b:None;h.main=lambda b:None;h.validate_service=lambda b:None
    h.parse_environment=lambda b:{};h.service=lambda:state.copy()
    h.read_owned=lambda path,mode:path.read_bytes()
    monkeypatch.setitem(sys.modules,h.__name__,h)
    monkeypatch.setattr(target,'DROP_DIR',dropdir);monkeypatch.setattr(target,'DROP',drop)
    monkeypatch.setattr(target,'TEMP_DROP',dropdir/'.hold.conf.tmp')
    monkeypatch.setattr(target,'stage',lambda bundle:release)
    monkeypatch.setattr(target,'require_current_main',lambda revision:None)
    monkeypatch.setattr(target.os,'geteuid',lambda:0)
    monkeypatch.setattr(target.os,'uname',lambda:types.SimpleNamespace(nodename='autopilot-lite-vnic'))
    real_fstat=os.fstat
    def root_stat(fd):
        values=list(real_fstat(fd));values[4]=0
        return os.stat_result(values)
    monkeypatch.setattr(target.os,'fstat',root_stat)
    real_lstat=Path.lstat
    def owned_lstat(path):
        values=list(real_lstat(path));values[4]=0
        return os.stat_result(values)
    monkeypatch.setattr(Path,'lstat',owned_lstat)
    monkeypatch.setattr(target.time,'sleep',lambda _:None)
    if failure=='atomic_rename':
        monkeypatch.setattr(target.os,'rename',Mock(side_effect=OSError('rename failed')))
    real_resolve=Path.resolve
    def resolve(path,*a,**kw):
        if str(path)=='/proc/999999991/cwd':return Path('/old')
        return release if str(path)=='/proc/999999992/cwd' else real_resolve(path,*a,**kw)
    monkeypatch.setattr(Path,'resolve',resolve)
    real_sync=target.fsync_directory
    sync_failed=False
    def sync(path):
        nonlocal sync_failed
        commands.append(('fsync',str(path)))
        if failure=='after_rename' and drop.exists() and not sync_failed:
            sync_failed=True
            raise OSError('directory flush failed')
        return real_sync(path)
    monkeypatch.setattr(target,'fsync_directory',sync)
    def environment(pid):
        return {**old_env,**({'AUTOPILOT_ADMISSION_MODE':'HOLD'} if pid==999999992 else {})}
    monkeypatch.setattr(target,'process_environment',environment)
    def probe(*args):
        nonlocal probe_count
        probe_count+=1
        if failure=='uncertain_queue' and probe_count>=3:
            raise RuntimeError('private-dsn must not be logged')
        return {'status':'PASS','worker_id':'oracle-autopilot-light-1','mailbox_pr':1703,'fence_sha256':'f'*64}
    monkeypatch.setattr(target,'probe',probe)
    def run(*args,**kw):
        nonlocal started_new
        commands.append(args)
        if args[:2]==('systemctl','daemon-reload'):
            state.update(WorkingDirectory=str(release) if drop.exists() else '/old',
                         DropInPaths=str(drop) if drop.exists() else '')
            if failure=='before_stop' and drop.exists():
                raise RuntimeError('interrupted after reload')
        if args[:2]==('systemctl','stop'):
            state.update(ActiveState='inactive',SubState='dead',MainPID='0')
        if args[:2]==('systemctl','start'):
            if drop.exists():
                started_new=True
                if failure=='new_start':
                    raise RuntimeError('start failed')
                state.update(ActiveState='active',SubState='running',MainPID='999999992',
                    InvocationID='b'*32,WorkingDirectory=str(release),DropInPaths=str(drop))
            else:
                state.update(old)
                if failure=='rollback_start':
                    # A start may succeed while reporting an ambiguous failure.
                    raise RuntimeError('uncertain old start')
        if args[:2]==('systemctl','show'):
            return 'PYTHONUNBUFFERED=1 AUTOPILOT_ADMISSION_MODE=HOLD'
        if args[0]=='journalctl':
            if failure in {'no_connected','rollback_start'}:
                return ''
            if failure=='drop_drift':
                drop.write_text('unexpected replacement')
            return 'worker_hold_connected worker_id=oracle-autopilot-light-1'
        return ''
    monkeypatch.setattr(target,'run',run)
    bundle={'revision':'c'*40,'sha256':'d'*64}
    return lambda:target.install(bundle,'from _held_install_fixture import *'),state,commands,drop,release


def test_success_stays_held_and_preserves_previous_release(tmp_path,monkeypatch,capsys):
    action,state,commands,drop,release=harness(tmp_path,monkeypatch)
    action()
    assert state['MainPID']=='999999992' and state['WorkingDirectory']==str(release)
    assert drop.read_text()=='[Service]\nWorkingDirectory='+str(release)+'\nEnvironment=AUTOPILOT_ADMISSION_MODE=HOLD\n'
    assert commands.count(('systemctl','stop','unit'))==1
    assert commands.count(('systemctl','start','unit'))==1
    result=json.loads(capsys.readouterr().out)
    assert result['runtime_hold']=='PASS' and result['admission']=='HOLD'
    assert result['database_writes'] is False


@pytest.mark.parametrize('failure',['new_start','no_connected','atomic_rename','after_rename','before_stop'])
def test_pre_outcome_failure_restores_old_service(tmp_path,monkeypatch,capsys,failure):
    action,state,commands,drop,release=harness(tmp_path,monkeypatch,failure)
    expected=OSError if failure in {'atomic_rename','after_rename'} else RuntimeError
    with pytest.raises(expected):action()
    assert state['ActiveState']=='active' and state['WorkingDirectory']=='/old'
    assert not drop.exists() and not drop.parent.exists() and release.exists()
    assert json.loads(capsys.readouterr().out)['rollback']=='PREVIOUS_RELEASE_RUNNING'


def test_durable_boot_hold_precedes_first_stop(tmp_path,monkeypatch):
    action,state,commands,drop,release=harness(tmp_path,monkeypatch)
    original_run=target.run
    def run(*args,**kw):
        if args[:2]==('systemctl','stop'):
            assert drop.read_text()=='[Service]\nWorkingDirectory='+str(release)+'\nEnvironment=AUTOPILOT_ADMISSION_MODE=HOLD\n'
            assert ('fsync',str(drop.parent)) in commands
            assert ('fsync',str(drop.parent.parent)) in commands
            assert ('systemctl','daemon-reload') in commands
            assert ('systemctl','show','unit','--property=Environment','--value') in commands
            assert state['MainPID']=='999999991' and state['WorkingDirectory']==str(release)
        return original_run(*args,**kw)
    monkeypatch.setattr(target,'run',run)
    action()


@pytest.mark.parametrize('failure',['uncertain_queue','drop_drift','rollback_start'])
def test_uncertainty_never_leaves_old_worker_active(tmp_path,monkeypatch,capsys,failure):
    action,state,commands,drop,release=harness(tmp_path,monkeypatch,failure)
    with pytest.raises(RuntimeError):action()
    assert state['MainPID']=='0' and state['ActiveState']=='inactive'
    text=capsys.readouterr().out
    assert 'private-dsn' not in text
    assert json.loads(text)['rollback']=='LEFT_STOPPED_EVIDENCE_UNCERTAIN'
    assert release.exists()


def test_bundle_script_compiles_and_workflow_is_explicit_only():
    root=Path(__file__).resolve().parents[1]
    program=subprocess.check_output([sys.executable,str(root/'ops/oracle_light_runtime_hold_install.py'),'bundle','a'*40],text=True)
    compile(program,'held-rollout','exec')
    workflow=(root/'.github/workflows/oracle-light-runtime-hold-rollout.yml').read_text()
    assert '\n  push:' not in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.expected_main_sha == github.sha" in workflow
    assert 'git diff --exit-code HEAD --' in workflow
    assert 'git/ref/heads/main --jq .object.sha' in workflow
    assert 'group: oracle-light-backup-mutation' in workflow
    assert 'contents: write' not in workflow


def test_main_drift_aborts_before_service_stop(tmp_path,monkeypatch):
    action,state,commands,drop,release=harness(tmp_path,monkeypatch)
    def drift(revision):
        raise RuntimeError('CURRENT_MAIN_CHANGED')
    monkeypatch.setattr(target,'require_current_main',drift)
    with pytest.raises(RuntimeError,match='CURRENT_MAIN_CHANGED'):
        action()
    assert state['ActiveState']=='active' and state['WorkingDirectory']=='/old'
    assert not commands and not drop.exists()


@pytest.mark.parametrize('damage',['extra','changed','writable','symlink'])
def test_retained_release_rejects_drift(tmp_path,monkeypatch,damage):
    root=tmp_path/'release';root.mkdir(mode=0o755)
    bundle={'revision':'a'*40,'sha256':'b'*64,'files':{'oracle_autopilot/worker.py':'pass\n'}}
    (root/'oracle_autopilot').mkdir(mode=0o755)
    for name,text in {**bundle['files'],'SOURCE_REVISION':bundle['revision']+'\n',
        'RUNTIME_BUNDLE_SHA256':bundle['sha256']+'\n'}.items():
        p=root/name;p.write_text(text);p.chmod(0o444)
    real_lstat=Path.lstat
    def root_stat(path):
        values=list(real_lstat(path));values[4]=0
        return os.stat_result(values)
    monkeypatch.setattr(Path,'lstat',root_stat)
    target.verify_release(root,bundle)
    file=root/'oracle_autopilot/worker.py'
    if damage=='extra':(root/'unexpected').write_text('x')
    elif damage=='changed':file.chmod(0o644);file.write_text('different');file.chmod(0o444)
    elif damage=='writable':file.chmod(0o644)
    elif damage=='symlink':file.unlink();file.symlink_to(root/'SOURCE_REVISION')
    with pytest.raises(RuntimeError):target.verify_release(root,bundle)
