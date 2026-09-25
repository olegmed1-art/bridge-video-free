"""Read-only root inspection after failed credential recovery; never print secrets."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import stat
import subprocess
from urllib.parse import unquote, urlsplit

UNIT = 'school-autopilot-production-light.service'
BASE = Path('/etc/systemd/system') / UNIT
DROP = Path('/etc/systemd/system/school-autopilot-production-light.service.d/40-reviewed-runtime-hold.conf')
ENV = Path('/etc/school-autopilot-production-light.env')
BASE_SHA = 'e8d22d57089b9c43b1d65d78de7e022c2b2124357cf40e98728128d2980ee2b4'
HOST = 'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech'
ROLE = 'autopilot_light_worker_login'


def safe_read(path, mode, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                stat.S_IMODE(info.st_mode) == mode and info.st_size <= limit):
            raise RuntimeError('FILE_METADATA')
        value = stream.read(limit + 1)
        if len(value) > limit:
            raise RuntimeError('FILE_TOO_LARGE')
        return value


def get_state():
    keys = ('ActiveState', 'SubState', 'MainPID', 'InvocationID', 'WorkingDirectory',
            'User', 'Group', 'Environment', 'EnvironmentFiles', 'DropInPaths',
            'FragmentPath', 'NeedDaemonReload')
    output = subprocess.run(['systemctl','show',UNIT,*['-p'+key for key in keys]],
                            capture_output=True,text=True,timeout=15,check=True).stdout
    state = {'EnvironmentFiles': []}
    for line in output.splitlines():
        key,sep,value = line.partition('=')
        if not sep or key not in keys:
            raise RuntimeError('UNIT_PROPERTIES')
        if key == 'EnvironmentFiles':
            state[key].append(value)
        else:
            if key in state: raise RuntimeError('UNIT_PROPERTIES')
            state[key] = value
    if set(state) != set(keys):
        raise RuntimeError('UNIT_PROPERTIES')
    return state


def read_dsn(raw):
    result = {}
    for line in raw.decode().splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        key,sep,value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*',key) or key in result:
            raise RuntimeError('ENV_SYNTAX')
        tokens = shlex.split(value, posix=True,comments=False)
        if len(tokens)!=1: raise RuntimeError('ENV_SYNTAX')
        result[key]=tokens[0]
    if set(k for k in result if k=='AUTOPILOT_DATABASE_URL') != {'AUTOPILOT_DATABASE_URL'}:
        raise RuntimeError('ENV_SOURCE')
    return result['AUTOPILOT_DATABASE_URL']

# Child reads its DSN only from env; output is restricted to fixed status codes.
CHILD = '''import json,os,sys
try:
 import psycopg
 with psycopg.connect(os.environ['AUDIT_DATABASE_URL'],autocommit=True,connect_timeout=8,
   options='-c statement_timeout=5000 -c default_transaction_read_only=on') as conn:
  row=conn.execute("SELECT current_user,current_database(),current_setting('transaction_read_only')").fetchone()
 print(json.dumps({'status':'VALID' if row==('autopilot_light_worker_login','neondb','on') else 'IDENTITY_DRIFT'}))
except Exception as exc:
 print(json.dumps({'status':'AUTH_REJECTED' if getattr(exc,'sqlstate',None)=='28P01' else 'UNAVAILABLE'}))
 sys.exit(2)
'''


def auth_status(dsn):
    account=pwd.getpwnam('school-autopilot')
    def identity():
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    child=subprocess.run(['/opt/bridge-school/school-autopilot/.venv/bin/python','-c',CHILD],
        env={'AUDIT_DATABASE_URL':dsn,'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},
        cwd='/',preexec_fn=identity,capture_output=True,text=True,timeout=25)
    try:
        proof=json.loads(child.stdout) if len(child.stdout)<128 else {}
    except ValueError:
        proof={}
    status=proof.get('status')
    return status if status in ('VALID','AUTH_REJECTED','IDENTITY_DRIFT','UNAVAILABLE') else 'UNAVAILABLE'


def main():
    if os.geteuid()!=0 or os.uname().nodename!='autopilot-lite-vnic':
        raise RuntimeError('HOST_IDENTITY')
    state=get_state()
    release=state['WorkingDirectory']
    release_ok=bool(re.fullmatch(r'/opt/bridge-school/school-autopilot-production-light/releases/[a-f0-9]{40}',release))
    hold=('[Service]\nWorkingDirectory='+release+'\nEnvironment=AUTOPILOT_ADMISSION_MODE=HOLD\n'
          'EnvironmentFile='+release+'/ops/autopilot/broker-hold.env\n').encode()
    drop_ok=(release_ok and safe_read(DROP,0o644,4096)==hold)
    base_ok=hashlib.sha256(safe_read(BASE,0o644,65536)).hexdigest()==BASE_SHA
    raw=safe_read(ENV,0o600,262144)
    dsn=read_dsn(raw)
    parsed=urlsplit(dsn)
    dsn_target_ok=(parsed.scheme in ('postgres','postgresql') and
        unquote(parsed.username or '')==ROLE and parsed.hostname==HOST and
        unquote(parsed.path.lstrip('/'))=='neondb')
    source_ok=(release_ok and state['EnvironmentFiles']==[str(ENV)+' (ignore_errors=no)',
            release+'/ops/autopilot/broker-hold.env (ignore_errors=no)'])
    broker_keys={'AUTOPILOT_TOKEN_BROKER_URL','AUTOPILOT_TOKEN_BROKER_EXPECTED_SOURCE_SHA',
        'AUTOPILOT_TOKEN_BROKER_EXPECTED_ARTIFACT_SHA256',
        'AUTOPILOT_TOKEN_BROKER_EXPECTED_POLICY_SHA256',
        'AUTOPILOT_TOKEN_BROKER_EXPECTED_PROVENANCE_SHA256'}
    pin_path=Path(release)/'ops/autopilot/broker-hold.env' if release_ok else None
    broker_ok=bool(pin_path and set(line.partition('=')[0] for line in
        safe_read(pin_path,0o444,4096).decode().splitlines())==broker_keys)
    stopped_hold=(state['ActiveState']=='inactive' and state['SubState']=='dead' and
        state['MainPID']=='0' and state['NeedDaemonReload']=='no' and
        state['Environment'].split().count('AUTOPILOT_ADMISSION_MODE=HOLD')==1 and
        not any(t.startswith('AUTOPILOT_ADMISSION_MODE=') and t!='AUTOPILOT_ADMISSION_MODE=HOLD'
                for t in state['Environment'].split()))
    identity_ok=(state['User']==state['Group']=='school-autopilot' and
        state['DropInPaths']==str(DROP) and state['FragmentPath']==str(BASE))
    result={'audit':'POST_RECOVERY_READ_ONLY','service_state':state['ActiveState']
        if state['ActiveState'] in ('active','inactive','failed') else 'OTHER',
        'main_pid_zero':state['MainPID']=='0','admission_hold_stopped':stopped_hold,
        'drop_exact_hold':drop_ok,'base_unit_pinned':base_ok,'environment_sources_pinned':source_ok,
        'service_identity_pinned':identity_ok,'broker_pin_file_pinned':broker_ok,
        'primary_env_root_0600':True,
        'dsn_target_pinned':dsn_target_ok,'disk_login':auth_status(dsn)}
    print(json.dumps(result,sort_keys=True))
    if not (stopped_hold and drop_ok and base_ok and source_ok and broker_ok and identity_ok and dsn_target_ok):
        raise RuntimeError('CONTAINMENT_NOT_ATTESTED')

if __name__=='__main__':
    try: main()
    except BaseException as exc:
        code=exc.args[0] if isinstance(exc,RuntimeError) else type(exc).__name__
        print(json.dumps({'audit':'INCOMPLETE','code':code if code in {'HOST_IDENTITY',
           'FILE_METADATA','FILE_TOO_LARGE','UNIT_PROPERTIES','ENV_SYNTAX','ENV_SOURCE',
           'CONTAINMENT_NOT_ATTESTED'} else 'UNCLASSIFIED'}))
        raise SystemExit(2)
