"""CI-only real loaded-unit/authority boundary; no installation or issue authority."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace


ROOT = Path('/var/lib/bridge-light-native-service')
UNIT = 'bridge-light-native-supervisor.service'
UNIT_FILE = Path('/etc/systemd/system')/UNIT
DROP = Path(str(UNIT_FILE)+'.d')
STAGE = ROOT/'fixture-code/ops'
WANTS = UNIT_FILE.parent/'multi-user.target.wants'/UNIT
CREATED = {}
WORKER = b'import time\nend=time.monotonic()+45\nwhile time.monotonic()<end: time.sleep(0.1)\n'


def need(value, code):
    if not value: raise RuntimeError('HOST_FIXTURE_'+code)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def command(*argv, timeout=10, allow_failure=False):
    result = subprocess.run(argv, capture_output=True, timeout=timeout,
        env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
    need(len(result.stdout)<=65536 and len(result.stderr)<=65536, 'OUTPUT_BOUND')
    need(allow_failure or result.returncode==0, 'COMMAND')
    return result


def create(path, raw, mode):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as stream:
        row=os.fstat(fd);CREATED[path]=(row.st_dev,row.st_ino)
        os.fchmod(stream.fileno(),mode);stream.write(raw);stream.flush();os.fsync(stream.fileno())


def replace(path, raw, mode):
    temporary=path.with_name(path.name+'.fixture-new')
    create(temporary,raw,mode)
    os.replace(temporary,path)
    CREATED[path]=CREATED.pop(temporary)


def module(path, name):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);sys.modules[name]=value
    spec.loader.exec_module(value)
    return value


def show(*keys):
    raw=command('/usr/bin/systemctl','show',UNIT,'--no-pager',*['--property='+key for key in keys]).stdout
    rows=dict(line.split('=',1) for line in raw.decode().splitlines())
    need(set(rows)==set(keys),'SHOW_FIELDS')
    return rows


def running():
    for _ in range(100):
        row=show('ActiveState','SubState','MainPID')
        if row['ActiveState']=='active' and row['SubState']=='running' and int(row['MainPID'])>1:
            return int(row['MainPID'])
        time.sleep(.02)
    raise RuntimeError('HOST_FIXTURE_START')


def rejected(call, refusal, reason=None):
    try: call()
    except refusal as error:
        need(reason is None or str(error)==reason,'WRONG_REFUSAL_REASON')
        return
    raise RuntimeError('HOST_FIXTURE_NEGATIVE_ACCEPTED')


def mark(name): print(json.dumps(dict(case=name,result='PASS',real_systemd=True)),flush=True)


def isolated_change(actual, baseline, key, expected, case):
    changed=sorted(name for name in baseline if actual[name]!=baseline[name])
    valid=set(actual)==set(baseline) and changed==[key] and actual[key]==expected
    if not valid:
        print(json.dumps(dict(audit='HOST_FIXTURE_ISOLATION_REFUSED',case=case,
            changed_properties=changed,expected_value_matched=actual[key]==expected,
            need_daemon_reload=actual['NeedDaemonReload'],drop_in_empty=actual['DropInPaths']=='')),flush=True)
    need(valid,case+'_NEGATIVE_NOT_ISOLATED')


def cleanup():
    row=ROOT.stat(follow_symlinks=False)
    need(ROOT.resolve()==ROOT and not ROOT.is_symlink() and
         (row.st_dev,row.st_ino)==CREATED.get(ROOT),'CLEANUP_ROOT')
    marker=ROOT/'fixture-purpose'
    need(not marker.exists() or marker.read_bytes()==b'CI_ONLY_NO_PRODUCTION_AUTHORITY\n','CLEANUP_PURPOSE')
    if UNIT_FILE in CREATED:
        row=UNIT_FILE.stat(follow_symlinks=False)
        need((row.st_dev,row.st_ino)==CREATED[UNIT_FILE],'CLEANUP_UNIT_OWNERSHIP')
        command('/usr/bin/systemctl','stop',UNIT,timeout=35,allow_failure=True)
        states=show('MainPID','ControlPID')
        need(states['MainPID']==states['ControlPID']=='0','CLEANUP_NOT_EMPTY')
        if WANTS.exists() or WANTS.is_symlink():
            need(WANTS.is_symlink() and WANTS.resolve()==UNIT_FILE,'CLEANUP_ENABLE_LINK')
            command('/usr/bin/systemctl','disable',UNIT,allow_failure=True)
        command('/usr/bin/systemctl','reset-failed',UNIT,allow_failure=True)
        if DROP.exists():
            need(not DROP.is_symlink() and set(p.name for p in DROP.iterdir())<= {'99-host-fixture.conf'},'CLEANUP_FOREIGN_DROP')
            for path in DROP.iterdir(): path.unlink()
            DROP.rmdir()
        UNIT_FILE.unlink();command('/usr/bin/systemctl','daemon-reload')
    shutil.rmtree(ROOT)


def exercise(repo):
    create(ROOT/'fixture-purpose',b'CI_ONLY_NO_PRODUCTION_AUTHORITY\n',0o600)
    STAGE.mkdir(parents=True,mode=0o700)
    (ROOT/'fixture-code').chmod(0o700)
    for name in ('light_native_service_unit.py','light_native_service_authority.py'):
        create(STAGE/name,(repo/'ops'/name).read_bytes(),0o600)
    create(ROOT/'launcher.py',WORKER,0o600)
    unit=module(STAGE/'light_native_service_unit.py','fixture_unit_contract')
    authority=module(STAGE/'light_native_service_authority.py','fixture_host_authority')
    raw=unit.render();create(UNIT_FILE,raw,0o644)
    command('/usr/bin/systemctl','daemon-reload')
    command('/usr/bin/systemctl','enable',UNIT)
    command('/usr/bin/systemctl','start',UNIT)
    pid=running()
    python_sha=sha(Path('/proc',str(pid),'exe').read_bytes())
    manifest=SimpleNamespace(version=2,service_unit_sha256=sha((STAGE/'light_native_service_unit.py').read_bytes()),
        unit_sha256=sha(raw),python_sha256=python_sha,argv=unit.ARGV)
    def collect(): return unit.collect_loaded(running(),expected_python_sha256=python_sha,expected_unit_sha256=sha(raw))
    initial=collect()
    actual=authority.host_identity(pid,manifest)
    need(actual.pid==pid and actual.invocation_id==initial.process.invocation_id and
         initial.process.no_new_privs==1,'AUTHORITY_NOT_WIRED')
    mark('actual-loaded-authority')
    rejected(lambda: authority.host_identity(os.getpid(),manifest),authority.Refusal)
    mark('foreign-process')
    rejected(lambda: unit.collect_loaded(pid,expected_python_sha256='0'*64,expected_unit_sha256=sha(raw)),unit.Refusal)
    mark('wrong-python-pin')
    # Creating a drop-in did not prove a stale loaded state in the first run.
    # Change only the known fragment mtime, keeping its accepted bytes.
    baseline=unit.parse_show(unit._show())
    prior_mtime=UNIT_FILE.stat().st_mtime_ns
    deadline=time.monotonic()+2
    while time.time_ns()//1_000_000_000 <= prior_mtime//1_000_000_000:
        need(time.monotonic()<deadline,'MTIME_BOUND')
        time.sleep(.02)
    os.utime(UNIT_FILE,None,follow_symlinks=False)
    need(UNIT_FILE.stat().st_mtime_ns>prior_mtime and UNIT_FILE.read_bytes()==raw,'STALE_BYTES_CHANGED')
    stale=unit.parse_show(unit._show())
    isolated_change(stale,baseline,'NeedDaemonReload',True,'STALE')
    rejected(collect,unit.Refusal,'NATIVE_UNIT_PROFILE');mark('stale-reload')
    command('/usr/bin/systemctl','daemon-reload');collect()
    baseline=unit.parse_show(unit._show())
    DROP.mkdir(mode=0o755)
    drop=DROP/'99-host-fixture.conf'
    create(drop,b'[Service]\nNice=10\n',0o644)
    command('/usr/bin/systemctl','daemon-reload')
    actual=unit.parse_show(unit._show())
    isolated_change(actual,baseline,'DropInPaths',str(drop),'DROP')
    rejected(collect,unit.Refusal,'NATIVE_UNIT_PROFILE');mark('loaded-drop-in')
    drop.unlink();DROP.rmdir();command('/usr/bin/systemctl','daemon-reload');collect()
    # A reload can advertise NNP=yes while the old process still has NNP=0.
    replace(UNIT_FILE,raw.replace(b'NoNewPrivileges=yes\n',b'NoNewPrivileges=no\n'),0o644)
    command('/usr/bin/systemctl','daemon-reload');command('/usr/bin/systemctl','restart',UNIT)
    pid=running()
    status=dict(line.split(':',1) for line in Path('/proc',str(pid),'status').read_text().splitlines() if ':' in line)
    need(status['NoNewPrivs'].strip()=='0','NNP_NEGATIVE_NOT_EXERCISED')
    replace(UNIT_FILE,raw,0o644);command('/usr/bin/systemctl','daemon-reload')
    need(show('NoNewPrivileges')['NoNewPrivileges']=='yes','NNP_LOADED')
    rejected(collect,unit.Refusal,'NATIVE_UNIT_PROCESS');mark('actual-nnp-differs-from-loaded')
    command('/usr/bin/systemctl','restart',UNIT);pid=running();collect()
    helper=STAGE/'light_native_service_unit.py';original=helper.read_bytes()
    sentinel=ROOT/'unverified-helper-executed'
    payload=('from pathlib import Path\nPath('+repr(str(sentinel))+').write_bytes(b\"UNVERIFIED\")\n').encode()
    replace(helper,payload+original,0o600)
    try: rejected(lambda: authority.host_identity(pid,manifest),authority.Refusal)
    finally: replace(helper,original,0o600)
    need(not sentinel.exists() and not sentinel.is_symlink(),'UNVERIFIED_HELPER_EXECUTED')
    mark('helper-pin-before-execution')
    authority.host_identity(pid,manifest)
    print(json.dumps(dict(audit='HOST_LOADED_BOUNDARY_REHEARSAL',passed=7,
        real_systemd=True,production_accepted=False,installation_accepted=False,
        database_connected=False,provider_connected=False)),flush=True)


def run(repo):
    need(sys.platform=='linux' and os.geteuid()==0 and Path('/proc/1/comm').read_text().strip()=='systemd','REAL_PID1_REQUIRED')
    need(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('GITHUB_REPOSITORY')=='olegmed1-art/bridge-video-free','CI_ONLY')
    need(not ROOT.exists() and not ROOT.is_symlink() and not UNIT_FILE.exists() and not UNIT_FILE.is_symlink()
         and not DROP.exists() and not DROP.is_symlink() and not WANTS.exists() and not WANTS.is_symlink()
         and show('LoadState')['LoadState']=='not-found','PREEXISTING_UNIT')
    created=False
    try:
        ROOT.mkdir(mode=0o700);created=True
        row=ROOT.stat(follow_symlinks=False);CREATED[ROOT]=(row.st_dev,row.st_ino)
        exercise(repo)
    finally:
        if created: cleanup()


if __name__=='__main__':
    need(len(sys.argv)==2,'ARGUMENTS')
    run(Path(sys.argv[1]).resolve())
