"""Read-only candidate attestation on Light; never start a worker or claim work."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import stat
import subprocess
import sys
import tempfile

UNIT = 'school-autopilot-production-light.service'
UNIT_PATH = Path('/etc/systemd/system') / UNIT
ENV_PATH = Path('/etc/school-autopilot-production-light.env')
ROUTE = Path('/var/lib/bridge-autopilot-tunnel')
PYTHON = '/opt/bridge-school/school-autopilot/.venv/bin/python'
OLD_REVISION = '962903f3f2ee7ca9b63147b28cd889bf03838314'
OLD_DIRECTORY = '/opt/bridge-school/school-autopilot-production-light/releases/' + OLD_REVISION
CANARY = 'f05c605f-f664-4ff7-9927-a039f000a929'


class PreflightBlocked(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise PreflightBlocked(code)


def read_owned(path, mode, limit=262144):
    # Parent ownership is checked too: no mutable directory or symlink traversal.
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'UNTRUSTED_PARENT')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as file:
        info = os.fstat(file.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                stat.S_IMODE(info.st_mode) == mode and info.st_size <= limit, 'UNTRUSTED_FILE')
        data = file.read(limit + 1)
        require(len(data) <= limit, 'FILE_TOO_LARGE')
        return data


def parse_environment(raw):
    result = {}
    for line in raw.decode().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, sep, value = line.partition('=')
        require(sep and re.fullmatch('[A-Z][A-Z0-9_]*', key) and key not in result,
                'ENV_SYNTAX')
        tokens = shlex.split(value, comments=False, posix=True)
        require(len(tokens) == 1, 'ENV_SYNTAX')
        result[key] = tokens[0]
    return result


def service():
    keys = ('ActiveState', 'SubState', 'MainPID', 'NRestarts', 'InvocationID',
            'WorkingDirectory', 'User', 'Group', 'DropInPaths', 'FragmentPath', 'ExecStart', 'EnvironmentFiles')
    result = subprocess.run(['systemctl','show',UNIT,*['--property='+k for k in keys]],
                            check=True,capture_output=True,text=True,timeout=10)
    return dict(line.split('=',1) for line in result.stdout.splitlines())


def validate_service(value):
    require(value['ActiveState'] == 'active' and value['SubState'] == 'running' and
            int(value['MainPID']) > 0 and re.fullmatch('[a-f0-9]{32}',value['InvocationID']),
            'SERVICE_NOT_STABLE')
    require(value['WorkingDirectory'] == OLD_DIRECTORY and value['User'] == 'school-autopilot'
            and value['Group'] == 'school-autopilot' and not value['DropInPaths'] and
            value['FragmentPath'] == str(UNIT_PATH), 'SERVICE_DRIFT')
    require('path='+PYTHON+' ;' in value['ExecStart'] and
            'argv[]='+PYTHON+' -m oracle_autopilot.worker_v17 ;' in value['ExecStart'] and
            value['EnvironmentFiles'] == str(ENV_PATH)+' (ignore_errors=no)', 'SERVICE_EXEC_DRIFT')


def validate_bundle(bundle):
    require(set(bundle) == {'revision','files','sha256'},'BUNDLE_FIELDS')
    require(re.fullmatch('[a-f0-9]{40}',bundle['revision']), 'SOURCE_REVISION')
    require(1 <= len(bundle['files']) <= 80, 'BUNDLE_SIZE')
    digest = hashlib.sha256(json.dumps({'revision':bundle['revision'],'files':bundle['files']},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    require(digest == bundle['sha256'], 'BUNDLE_DIGEST')
    required = {'oracle_autopilot/worker.py','oracle_autopilot/worker_v17.py',
                'oracle_autopilot/contract.py','ops/autopilot/broker-release.json',
                'oracle_autopilot/light_runtime_probe.py'}
    require(required <= bundle['files'].keys(),'BUNDLE_INCOMPLETE')
    for name, data in bundle['files'].items():
        require(re.fullmatch(r'(?:oracle_autopilot|autopilot_phase3b)/[a-z0-9_]+\.py',name)
                or name == 'ops/autopilot/broker-release.json','BUNDLE_PATH')
        require(isinstance(data,str) and len(data) <= 600000,'BUNDLE_FILE_SIZE')


def main(bundle):
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic','HOST_IDENTITY')
    validate_bundle(bundle)
    # Same permanent lock inode as the reviewed migration coordinator. No route writes.
    read_owned(ROUTE/'route.lock',0o644)
    with os.fdopen(os.open(ROUTE/'route.lock',os.O_RDONLY|os.O_NOFOLLOW),'rb') as lock:
        info = os.fstat(lock.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o644,'LOCK_METADATA')
        identity = json.loads(read_owned(ROUTE/'lock-identity.json',0o644))
        require(identity == {'device':info.st_dev,'inode':info.st_ino},'LOCK_IDENTITY')
        fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
        path_info = (ROUTE/'route.lock').lstat()
        require((path_info.st_dev,path_info.st_ino) == (info.st_dev,info.st_ino),'LOCK_REPLACED')
        route = read_owned(ROUTE/'route.json',0o644)
        require(json.loads(route) == {'version':1,'backend':'neon','database':'autopilot','epoch':0},
                'ROUTE_CHANGED')
        before = service()
        validate_service(before)
        unit = read_owned(UNIT_PATH,0o644)
        raw_env = read_owned(ENV_PATH,0o600)
        expected_env = parse_environment(raw_env)
        pid = int(before['MainPID'])
        actual_env = dict(x.decode().split('=',1) for x in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if b'=' in x)
        require(all(actual_env.get(k) == v for k,v in expected_env.items()),'LIVE_ENV_DRIFT')
        require(actual_env.get('AUTOPILOT_WORKER_ID') == 'oracle-autopilot-light-1' and
                actual_env.get('AUTOPILOT_DB_BACKEND','neon') == 'neon','WORKER_IDENTITY')
        account = pwd.getpwnam('school-autopilot')
        require(Path(f'/proc/{pid}').stat().st_uid == account.pw_uid,'LIVE_UID')
        # Only existing service settings; never inherit root's Python/DB configuration.
        env = {k:v for k,v in actual_env.items() if k.startswith('AUTOPILOT_')}
        env.update(PATH='/usr/bin:/bin',PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1')
        with tempfile.TemporaryDirectory(prefix='autopilot-readonly-preflight-',dir='/var/tmp') as temp:
            root = Path(temp)
            root.chmod(0o755)
            for name, value in bundle['files'].items():
                path = root/name
                path.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
                # umask may be 077; the restricted service must be able to read its code.
                for parent in path.parents:
                    if parent == root:
                        break
                    parent.chmod(0o755)
                path.write_text(value)
                path.chmod(0o444)
            def service_identity():
                os.setgroups([])
                os.setgid(account.pw_gid)
                os.setuid(account.pw_uid)
            child = subprocess.run([PYTHON,'-c',
                'import sys; sys.path.insert(0,sys.argv[1]); from oracle_autopilot.light_runtime_probe import main; main()',temp],
                cwd=temp,env=env,preexec_fn=service_identity,capture_output=True,text=True,timeout=90)
            # The probe prints only an allowlisted JSON record. Never relay traceback/DSNs.
            require(len(child.stdout) <= 4096,'PROBE_OUTPUT_SIZE')
            try:
                proof = json.loads(child.stdout)
            except ValueError:
                proof = {'status':'FAIL','stage':'probe_process','error_type':'InvalidOutput'}
            require(isinstance(proof,dict) and set(proof) <= {'status','stage','error_type','mailbox_pr','worker_id','database_user','fence_sha256','manifest_sha256'},'PROBE_OUTPUT_FIELDS')
            for key,value in proof.items():
                require(isinstance(value,(str,int)) and re.fullmatch('[a-zA-Z0-9_-]{1,128}',str(value)), 'PROBE_OUTPUT_VALUE')
            final_lock = (ROUTE/'route.lock').lstat()
            require((final_lock.st_dev,final_lock.st_ino) == (info.st_dev,info.st_ino) and
                    json.loads(read_owned(ROUTE/'lock-identity.json',0o644)) == identity,'LOCK_REPLACED')
            unchanged = service() == before and read_owned(UNIT_PATH,0o644) == unit and read_owned(ENV_PATH,0o600) == raw_env and read_owned(ROUTE/'route.json',0o644) == route
            require(unchanged,'RUNTIME_CHANGED_DURING_PREFLIGHT')
            print(json.dumps({'preflight':proof,'source_revision':bundle['revision'],
                'bundle_sha256':bundle['sha256'],'live_revision':OLD_REVISION,
                'invocation_id':before['InvocationID'],'unit_unchanged':True,
                'route':'neon_epoch_0','service_restarted':False,'database_writes':False}))
            require(child.returncode == 0 and proof.get('status') == 'PASS','PREFLIGHT_NOT_PASSED')


def bundle(revision):
    root = Path(__file__).resolve().parents[1]
    files = {p.relative_to(root).as_posix():p.read_text() for package in ('oracle_autopilot','autopilot_phase3b')
             for p in sorted((root/package).glob('*.py'))}
    files['ops/autopilot/broker-release.json'] = (root/'ops/autopilot/broker-release.json').read_text()
    value = {'revision':revision,'files':files,'sha256':hashlib.sha256(json.dumps({'revision':revision,'files':files},sort_keys=True,separators=(',',':')).encode()).hexdigest()}
    validate_bundle(value)
    return value


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'bundle':
        encoded = base64.b64encode(json.dumps(bundle(sys.argv[2])).encode()).decode()
        print('BUNDLE = ' + repr(encoded))
        print(Path(__file__).read_text())
    else:
        try:
            main(json.loads(base64.b64decode(BUNDLE)))
        except Exception as exc:
            # Fixed exception class only: no raw remote error text.
            print(json.dumps({'runtime_preflight':'NOT_CONFIRMED','error_type':type(exc).__name__,
                'guard':str(exc) if isinstance(exc,PreflightBlocked) else 'SYSTEM_ERROR'}))
            raise SystemExit(2) from None
