"""One bounded Light upgrade into HOLD; no ACTIVE path and no database mutation."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import ssl
import urllib.request

DROP_DIR = Path('/etc/systemd/system/school-autopilot-production-light.service.d')
DROP = DROP_DIR/'40-reviewed-runtime-hold.conf'
TEMP_DROP = DROP_DIR/'.40-reviewed-runtime-hold.conf.tmp'
RELEASES = Path('/opt/bridge-school/school-autopilot-production-light/releases')


class InstallBlocked(RuntimeError):
    """A source-defined assertion code, never a provider error or credential."""


def failure_record(exc):
    result={'runtime_hold':'NOT_CONFIRMED','error_type':type(exc).__name__}
    if type(exc) is InstallBlocked:
        result['error_code']=exc.args[0]
    return result


def run(*args,timeout=45):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=timeout).stdout.strip()


def check(condition,code):
    if not condition:
        raise InstallBlocked(code)


def fsync_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def process_environment(pid):
    return dict(x.decode().split('=',1) for x in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if b'=' in x)


def execution_contract(value):
    # systemctl's ExecStart includes execution accounting as well as config.
    # Keep the complete executable/argv/ignore-errors contract, accepting only
    # the observed single-command shape and named volatile accounting fields.
    match=re.fullmatch(r'\{ (path=[^;{}]+ ; argv\[\]=[^;{}]+ ; ignore_errors=(?:yes|no)) ; '
        r'start_time=\[[^\]\n]*\] ; stop_time=\[[^\]\n]*\] ; pid=[0-9]+ ; '
        r'code=[^;{}\n]+ ; status=[^;{}\n]+ \}',value)
    check(match is not None,'EXEC_START_FORMAT_DRIFT')
    return match.group(1)


def attest_loaded_config(before,configured,release):
    expected={**before,'WorkingDirectory':str(release),'DropInPaths':str(DROP)}
    allowed_fields={'ActiveState','SubState','MainPID','NRestarts','InvocationID',
        'WorkingDirectory','User','Group','DropInPaths','FragmentPath','ExecStart','EnvironmentFiles'}
    check(set(expected)==set(configured) and set(expected)<=allowed_fields,'SERVICE_FIELD_DRIFT')
    mismatches=sorted(k for k in expected if configured[k]!=expected[k])
    if mismatches:
        print(json.dumps({'pre_stop_mismatched_fields':mismatches}))
    if 'ExecStart' in expected:
        expected['ExecStart']=execution_contract(expected['ExecStart'])
        configured={**configured,'ExecStart':execution_contract(configured['ExecStart'])}
    check(configured==expected,'PRE_STOP_LOADED_CONFIG_DRIFT')


def probe(h,release,old_env):
    account=pwd.getpwnam('school-autopilot')
    env={k:v for k,v in old_env.items() if k.startswith('AUTOPILOT_')}
    env.update(PATH='/usr/bin:/bin',PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1')
    def identity():
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    result=subprocess.run([h['PYTHON'],'-c',
        'import sys;sys.path.insert(0,sys.argv[1]);from oracle_autopilot.light_runtime_probe import main;main()',str(release)],
        cwd=release,env=env,preexec_fn=identity,capture_output=True,text=True,timeout=90)
    check(len(result.stdout)<=4096,'PROBE_OUTPUT_SIZE')
    record=json.loads(result.stdout)
    check(result.returncode==0 and record.get('status')=='PASS','RESTRICTED_PROBE_FAILED')
    check(record.get('worker_id')=='oracle-autopilot-light-1' and record.get('mailbox_pr')==1703,'PROBE_IDENTITY')
    return record


def require_current_main(revision):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):
            raise RuntimeError('MAIN_REDIRECT_REJECTED')
    url='https://api.github.com/repos/olegmed1-art/bridge-video-free/git/ref/heads/main'
    context=ssl.create_default_context(cafile='/etc/ssl/certs/ca-certificates.crt')
    opener=urllib.request.build_opener(NoRedirect(),urllib.request.HTTPSHandler(context=context))
    request=urllib.request.Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'bridge-autopilot-held-rollout'})
    with opener.open(request,timeout=15) as response:
        check(response.status==200 and response.geturl()==url,'MAIN_READ_FAILED')
        content=response.read(65537)
    check(len(content)<=65536,'MAIN_RESPONSE_SIZE')
    check(json.loads(content)['object']['sha']==revision,'CURRENT_MAIN_CHANGED')


def verify_release(target,bundle):
    expected={**bundle['files'],'SOURCE_REVISION':bundle['revision']+'\n',
              'RUNTIME_BUNDLE_SHA256':bundle['sha256']+'\n'}
    directories={'.'}
    for name in expected:
        directories.update(str(p) for p in Path(name).parents)
    paths={p.relative_to(target).as_posix():p for p in target.rglob('*')}
    check(set(paths)==set(expected)|(directories-{'.'}),'RELEASE_INVENTORY_DRIFT')
    for name,path in {'.':target,**paths}.items():
        info=path.lstat()
        check(info.st_uid==0 and not stat.S_ISLNK(info.st_mode),'RELEASE_OWNER_OR_LINK')
        if name in directories:
            check(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o755,'RELEASE_DIRECTORY_MODE')
        else:
            check(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode)==0o444 and
                  path.read_text()==expected[name],'RELEASE_FILE_DRIFT')


def stage(bundle):
    # Immutable retained release. Never overwrite/reuse an existing directory.
    for parent in [RELEASES,*RELEASES.parents]:
        info=parent.lstat()
        check(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022,'RELEASE_PARENT')
    target=RELEASES/bundle['revision']
    check(not target.is_symlink(),'RELEASE_LINK')
    if target.exists():
        verify_release(target,bundle)
        persist_release(target)
        return target
    with tempfile.TemporaryDirectory(prefix='.hold-stage-',dir=RELEASES) as temporary:
        root=Path(temporary)
        root.chmod(0o755)
        for name,content in bundle['files'].items():
            path=root/name
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
            for parent in path.parents:
                if parent==root:
                    break
                parent.chmod(0o755)
            path.write_text(content)
            path.chmod(0o444)
        (root/'SOURCE_REVISION').write_text(bundle['revision']+'\n')
        (root/'SOURCE_REVISION').chmod(0o444)
        (root/'RUNTIME_BUNDLE_SHA256').write_text(bundle['sha256']+'\n')
        (root/'RUNTIME_BUNDLE_SHA256').chmod(0o444)
        os.rename(root,target)
    verify_release(target,bundle)
    persist_release(target)
    return target


def persist_release(target):
    # The boot-visible HOLD config must never reference an unflushed release.
    paths=list(target.rglob('*'))
    for path in paths:
        if path.is_file():
            with path.open('rb') as file:
                os.fsync(file.fileno())
    for path in sorted((p for p in paths if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
        fsync_directory(path)
    fsync_directory(target)
    fsync_directory(target.parent)


def install(bundle,helper_source):
    h={'__name__':'reviewed_preflight_helpers'}
    exec(compile(helper_source,'reviewed_preflight_helpers','exec'),h)
    check(os.geteuid()==0 and os.uname().nodename=='autopilot-lite-vnic','HOST_IDENTITY')
    h['validate_bundle'](bundle)
    read=h['read_owned']
    route_root=h['ROUTE']
    read(route_root/'route.lock',0o644)
    with os.fdopen(os.open(route_root/'route.lock',os.O_RDONLY|os.O_NOFOLLOW),'rb') as lock:
        info=os.fstat(lock.fileno())
        check(stat.S_ISREG(info.st_mode) and info.st_uid==0 and stat.S_IMODE(info.st_mode)==0o644,'LOCK_METADATA')
        identity=json.loads(read(route_root/'lock-identity.json',0o644))
        check(identity=={'device':info.st_dev,'inode':info.st_ino},'LOCK_IDENTITY')
        fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
        def same_route():
            now=(route_root/'route.lock').lstat()
            check((now.st_dev,now.st_ino)==(info.st_dev,info.st_ino) and
                  json.loads(read(route_root/'lock-identity.json',0o644))==identity,'LOCK_REPLACED')
            check(json.loads(read(route_root/'route.json',0o644))==
                  {'version':1,'backend':'neon','database':'autopilot','epoch':0},'ROUTE_CHANGED')
        same_route()
        h['main'](bundle)  # Full read-only preflight on the still-running original process.
        before=h['service']()
        h['validate_service'](before)
        unit=read(h['UNIT_PATH'],0o644)
        env_bytes=read(h['ENV_PATH'],0o600)
        old_env=process_environment(int(before['MainPID']))
        check('AUTOPILOT_ADMISSION_MODE' not in old_env and
              'AUTOPILOT_ADMISSION_MODE' not in h['parse_environment'](env_bytes),'ADMISSION_ALREADY_CONFIGURED')
        check(not DROP_DIR.exists() and not DROP_DIR.is_symlink(),'DROP_IN_ALREADY_EXISTS')
        check(not any(k in old_env for k in ('PYTHONPATH','PYTHONHOME')),'PYTHON_IMPORT_OVERRIDE')
        require_current_main(bundle['revision'])
        release=stage(bundle)
        baseline=probe(h,release,old_env)
        content='[Service]\nWorkingDirectory='+str(release)+'\nEnvironment=AUTOPILOT_ADMISSION_MODE=HOLD\n'
        changed=False
        created_dir=False
        temporary_created=False
        stopped=False
        try:
            # Last-second local CAS before stopping only this unit.
            same_route()
            require_current_main(bundle['revision'])
            check(h['service']()==before and read(h['UNIT_PATH'],0o644)==unit and
                  read(h['ENV_PATH'],0o600)==env_bytes,'PRE_STOP_DRIFT')
            # Persist the complete compatible HOLD configuration before stopping.
            # A reboot after this point enters HOLD even if this installer dies.
            DROP_DIR.mkdir(mode=0o755)
            DROP_DIR.chmod(0o755)
            created_dir=True
            fsync_directory(DROP_DIR.parent)
            fd=os.open(TEMP_DROP,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
            temporary_created=True
            with os.fdopen(fd,'w') as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            TEMP_DROP.chmod(0o644)
            os.rename(TEMP_DROP,DROP)
            changed=True
            temporary_created=False
            fsync_directory(DROP_DIR)
            fsync_directory(DROP_DIR.parent)
            run('systemctl','daemon-reload')
            configured=h['service']()
            attest_loaded_config(before,configured,release)
            environment=run('systemctl','show',h['UNIT'],'--property=Environment','--value')
            check('AUTOPILOT_ADMISSION_MODE=HOLD' in environment.split(),'PRE_STOP_HOLD_NOT_EFFECTIVE')
            check(process_environment(int(before['MainPID']))==old_env and
                  Path('/proc/'+before['MainPID']+'/cwd').resolve()==Path(before['WorkingDirectory']),
                  'OLD_PROCESS_CHANGED_BEFORE_STOP')
            same_route()
            require_current_main(bundle['revision'])
            check(read(h['UNIT_PATH'],0o644)==unit and read(h['ENV_PATH'],0o600)==env_bytes and
                  read(DROP,0o644).decode()==content,'PRE_STOP_CONFIG_CHANGED')
            stopped=True
            run('systemctl','stop',h['UNIT'])
            after_stop=h['service']()
            check(after_stop['ActiveState']=='inactive' and after_stop['MainPID']=='0' and
                  not Path('/proc/'+before['MainPID']).exists(),'OLD_PROCESS_NOT_QUIESCENT')
            check(probe(h,release,old_env)==baseline,'QUEUE_CHANGED_DURING_STOP')
            run('systemctl','start',h['UNIT'])
            initial=h['service']()
            check(initial['ActiveState']=='active' and int(initial['MainPID'])>0 and
                  initial['InvocationID']!=before['InvocationID'] and
                  initial['MainPID']!=before['MainPID'],'NEW_PROCESS_MISSING')
            check(initial['NRestarts']==before['NRestarts'],'NEW_PROCESS_RESTARTED')
            check(initial['WorkingDirectory']==str(release) and initial['User']==before['User'] and
                  initial['Group']==before['Group'] and initial['DropInPaths']==str(DROP),'NEW_UNIT_DRIFT')
            environment=run('systemctl','show',h['UNIT'],'--property=Environment','--value')
            check('AUTOPILOT_ADMISSION_MODE=HOLD' in environment.split(),'HOLD_NOT_EFFECTIVE')
            new_env=process_environment(int(initial['MainPID']))
            check(new_env.get('AUTOPILOT_ADMISSION_MODE')=='HOLD' and
                  all(new_env.get(k)==v for k,v in old_env.items() if k.startswith('AUTOPILOT_')),'LIVE_ENV_CHANGED')
            check(Path('/proc/'+initial['MainPID']+'/cwd').resolve()==release,'LIVE_CODE_PATH')
            # Bounded soak: no need for a new task or publication to prove HOLD.
            for _ in range(10):
                time.sleep(2)
                check(h['service']()==initial,'NEW_PROCESS_NOT_STABLE')
            journal=run('journalctl','--no-pager','-o','cat','-u',h['UNIT'],
                '_SYSTEMD_INVOCATION_ID='+initial['InvocationID'],'-n','40')
            check('worker_hold_connected worker_id=oracle-autopilot-light-1' in journal,'HOLD_CONNECTION_NOT_ATTESTED')
            check('worker_started ' not in journal,'ACTIVE_LOOP_SEEN')
            check(probe(h,release,new_env)==baseline,'POST_START_QUEUE_OR_FENCE_DRIFT')
            same_route()
            check(read(h['UNIT_PATH'],0o644)==unit and read(h['ENV_PATH'],0o600)==env_bytes and
                  read(DROP,0o644).decode()==content,'PROTECTED_CONFIG_CHANGED')
            print(json.dumps({'runtime_hold':'PASS','source_revision':bundle['revision'],
                'bundle_sha256':bundle['sha256'],'previous_revision':h['OLD_REVISION'],
                'invocation_id':initial['InvocationID'],'main_pid':int(initial['MainPID']),
                'restarts_delta':int(initial['NRestarts'])-int(before['NRestarts']),'fence_sha256':baseline['fence_sha256'],
                'route':'neon_epoch_0','admission':'HOLD','database_writes':False}))
        except BaseException:
            if stopped or changed or created_dir:
                # Never resume the incompatible old worker if queue/fence/route evidence is uncertain.
                handlers={sig:signal.signal(sig,signal.SIG_IGN) for sig in (signal.SIGTERM,signal.SIGHUP)}
                try:
                    run('systemctl','stop',h['UNIT'])
                    check(h['service']()['MainPID']=='0','ROLLBACK_STOP_NOT_CONFIRMED')
                    same_route()
                    check(probe(h,release,old_env)==baseline,'ROLLBACK_QUEUE_UNCERTAIN')
                    check(read(h['UNIT_PATH'],0o644)==unit and read(h['ENV_PATH'],0o600)==env_bytes,'ROLLBACK_CONFIG_DRIFT')
                    if changed:
                        check(read(DROP,0o644).decode()==content,'ROLLBACK_DROP_IN_DRIFT')
                        DROP.unlink()
                        fsync_directory(DROP_DIR)
                        DROP_DIR.rmdir()
                        fsync_directory(DROP_DIR.parent)
                    elif created_dir:
                        check(not DROP.exists() and temporary_created and TEMP_DROP.exists() and
                              set(DROP_DIR.iterdir())=={TEMP_DROP},'ROLLBACK_PARTIAL_DROP_IN')
                        info=TEMP_DROP.lstat()
                        check(stat.S_ISREG(info.st_mode) and info.st_uid==0 and
                              stat.S_IMODE(info.st_mode)==0o644,'ROLLBACK_TEMP_METADATA')
                        TEMP_DROP.unlink()
                        fsync_directory(DROP_DIR)
                        DROP_DIR.rmdir()
                        fsync_directory(DROP_DIR.parent)
                    else:
                        check(not DROP_DIR.exists(),'ROLLBACK_PARTIAL_DROP_IN')
                    run('systemctl','daemon-reload')
                    run('systemctl','start',h['UNIT'])
                    restored=h['service']()
                    h['validate_service'](restored)
                    check(restored['ActiveState']=='active' and restored['WorkingDirectory']==before['WorkingDirectory']
                          and not restored['DropInPaths'],'ROLLBACK_NOT_CONFIRMED')
                    for _ in range(3):
                        time.sleep(2)
                        check(h['service']()==restored,'ROLLBACK_NOT_STABLE')
                    check(probe(h,release,old_env)==baseline,'ROLLBACK_QUEUE_DRIFT')
                    print(json.dumps({'rollback':'PREVIOUS_RELEASE_RUNNING','database_writes':False}))
                except BaseException:
                    try:
                        run('systemctl','stop',h['UNIT'])
                        check(h['service']()['MainPID']=='0','STOP_NOT_CONFIRMED')
                        state='LEFT_STOPPED_EVIDENCE_UNCERTAIN'
                    except BaseException:
                        state='SERVICE_STATE_UNCERTAIN'
                    print(json.dumps({'rollback':state,'manual_review_required':True}))
                finally:
                    for sig,handler in handlers.items():
                        signal.signal(sig,handler)
            raise


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='bundle':
        from oracle_light_runtime_preflight import bundle
        helper=Path(__file__).with_name('oracle_light_runtime_preflight.py').read_text()
        print('BUNDLE_DATA='+repr(base64.b64encode(json.dumps(bundle(sys.argv[2])).encode()).decode()))
        print('HELPER_DATA='+repr(base64.b64encode(helper.encode()).decode()))
        print(Path(__file__).read_text())
    else:
        def interrupted(signum,frame):
            raise RuntimeError('INSTALL_INTERRUPTED')
        signal.signal(signal.SIGTERM,interrupted)
        signal.signal(signal.SIGHUP,interrupted)
        try:
            install(json.loads(base64.b64decode(BUNDLE_DATA)),base64.b64decode(HELPER_DATA).decode())
        except BaseException as exc:
            print(json.dumps(failure_record(exc)))
            sys.exit(2)
