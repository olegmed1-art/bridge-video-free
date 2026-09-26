"""Read-only attestation of the recovered Light worker under HOLD."""
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
from urllib.parse import unquote, urlsplit

UNIT = 'school-autopilot-production-light.service'
BASE = Path('/etc/systemd/system') / UNIT
DROP = Path('/etc/systemd/system/school-autopilot-production-light.service.d/40-reviewed-runtime-hold.conf')
ENV = Path('/etc/school-autopilot-production-light.env')
ROUTE = Path('/var/lib/bridge-autopilot-tunnel')
BASE_SHA = 'e8d22d57089b9c43b1d65d78de7e022c2b2124357cf40e98728128d2980ee2b4'
HOST = 'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech'
ROLE = 'autopilot_light_worker_login'
PIN_KEYS = {'AUTOPILOT_TOKEN_BROKER_URL','AUTOPILOT_TOKEN_BROKER_EXPECTED_SOURCE_SHA',
    'AUTOPILOT_TOKEN_BROKER_EXPECTED_ARTIFACT_SHA256',
    'AUTOPILOT_TOKEN_BROKER_EXPECTED_POLICY_SHA256',
    'AUTOPILOT_TOKEN_BROKER_EXPECTED_PROVENANCE_SHA256'}

class Blocked(Exception):
    pass

def require(ok, code):
    if not ok:
        raise Blocked(code)

def read(path, mode, limit):
    fd=os.open(path,os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and
                stat.S_IMODE(info.st_mode)==mode and info.st_size<=limit,'FILE_DRIFT')
        value=stream.read(limit+1)
        require(len(value)<=limit,'FILE_DRIFT')
        return value

def env(raw):
    values={}
    for line in raw.decode().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key,sep,value=line.partition('=')
        require(sep and re.fullmatch(r'[A-Z][A-Z0-9_]*',key) and key not in values,'ENV_SYNTAX')
        try:
            words=shlex.split(value,posix=True,comments=False)
        except ValueError:
            raise Blocked('ENV_SYNTAX') from None
        require(len(words)==1,'ENV_SYNTAX')
        values[key]=words[0]
    return values

def service():
    keys=('ActiveState','SubState','MainPID','NRestarts','InvocationID','WorkingDirectory',
        'User','Group','Environment','EnvironmentFiles','ExecStart','FragmentPath','DropInPaths',
        'NeedDaemonReload')
    output=subprocess.run(['systemctl','show',UNIT,*['-p'+key for key in keys]],
                          check=True,capture_output=True,text=True,timeout=15).stdout
    result={'EnvironmentFiles':[]}
    for line in output.splitlines():
        key,sep,value=line.partition('=')
        require(sep and key in keys,'UNIT_DRIFT')
        if key=='EnvironmentFiles': result[key].append(value)
        else:
            require(key not in result,'UNIT_DRIFT')
            result[key]=value
    require(set(result)==set(keys),'UNIT_DRIFT')
    return result

CHILD='''import json,os,sys
try:
 import psycopg
 with psycopg.connect(os.environ['AUDIT_DATABASE_URL'],autocommit=True,connect_timeout=10,
   options='-c statement_timeout=5000 -c default_transaction_read_only=on',
   sslmode='verify-full',sslrootcert='system',gssencmode='disable') as conn:
  row=conn.execute("SELECT current_user,current_database(),current_setting('transaction_read_only')").fetchone()
  if row!=('autopilot_light_worker_login','neondb','on'):
   raise RuntimeError('DATABASE_SESSION_MISMATCH')
  expected={
   'neon.project_id':('misty-poetry-18012774','postmaster'),
   'neon.branch_id':('br-aged-mud-b1i64914','postmaster'),
   'neon.endpoint_id':('ep-noisy-pine-b1pe30sf','superuser')}
  tags=conn.execute("SELECT name,setting,context,source,reset_val,pending_restart FROM pg_catalog.pg_settings WHERE name IN ('neon.project_id','neon.branch_id','neon.endpoint_id')").fetchall()
  binding=(len(tags)==3 and {t[0] for t in tags}==set(expected) and all(
   (setting,context)==expected[name] and source=='configuration file'
   and reset==setting and not pending
   for name,setting,context,source,reset,pending in tags)
   and conn.info.host=='ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech'
   and conn.info.port==5432)
  if not binding:
   raise RuntimeError('DATABASE_BINDING_MISMATCH')
  count=conn.execute("SELECT count(*) FROM autopilot.task_status WHERE status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')").fetchone()[0]
 print(json.dumps({'ok':row==('autopilot_light_worker_login','neondb','on') and count==0}))
except BaseException:
 print(json.dumps({'ok':False}))
 sys.exit(2)
'''

def login(dsn):
    user=pwd.getpwnam('school-autopilot')
    def identity():
        os.setgroups([])
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
    child=subprocess.run(['/opt/bridge-school/school-autopilot/.venv/bin/python','-c',CHILD],
        cwd='/',env={'AUDIT_DATABASE_URL':dsn,'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},
        preexec_fn=identity,capture_output=True,text=True,timeout=30)
    try: result=json.loads(child.stdout) if len(child.stdout)<128 else {}
    except ValueError: result={}
    require(child.returncode==0 and result=={'ok':True},'LOGIN_OR_QUEUE_FAILED')

def main():
    require(os.geteuid()==0 and os.uname().nodename=='autopilot-lite-vnic','HOST_IDENTITY')
    before=service()
    release=before['WorkingDirectory']
    require(re.fullmatch(r'/opt/bridge-school/school-autopilot-production-light/releases/[a-f0-9]{40}',release)
        and before['ActiveState']=='active' and before['SubState']=='running'
        and int(before['MainPID'])>0 and re.fullmatch(r'[a-f0-9]{32}',before['InvocationID'])
        and before['User']==before['Group']=='school-autopilot'
        and before['FragmentPath']==str(BASE) and before['DropInPaths']==str(DROP)
        and before['NeedDaemonReload']=='no'
        and before['Environment'].split().count('AUTOPILOT_ADMISSION_MODE=HOLD')==1
        and not any(token.startswith('AUTOPILOT_ADMISSION_MODE=') and
            token!='AUTOPILOT_ADMISSION_MODE=HOLD' for token in before['Environment'].split())
        and ' -m oracle_autopilot.worker_v17 ;' in before['ExecStart']
        and before['EnvironmentFiles']==[str(ENV)+' (ignore_errors=no)',
            release+'/ops/autopilot/broker-hold.env (ignore_errors=no)'], 'UNIT_DRIFT')
    require(hashlib.sha256(read(BASE,0o644,65536)).hexdigest()==BASE_SHA,'BASE_DRIFT')
    hold=('[Service]\nWorkingDirectory='+release+'\nEnvironment=AUTOPILOT_ADMISSION_MODE=HOLD\n'
          'EnvironmentFile='+release+'/ops/autopilot/broker-hold.env\n').encode()
    require(read(DROP,0o644,4096)==hold,'DROP_DRIFT')
    require(json.loads(read(ROUTE/'route.json',0o644,4096))==
            {'version':1,'backend':'neon','database':'autopilot','epoch':0},'ROUTE_DRIFT')
    pins=env(read(Path(release)/'ops/autopilot/broker-hold.env',0o444,4096))
    require(set(pins)==PIN_KEYS,'PIN_DRIFT')
    disk=read(ENV,0o600,262144)
    values=env(disk)
    require('AUTOPILOT_ADMISSION_MODE' not in values and 'AUTOPILOT_DATABASE_URL' in values,
            'ENV_DRIFT')
    dsn=values['AUTOPILOT_DATABASE_URL']
    target=urlsplit(dsn)
    require(target.scheme in ('postgres','postgresql') and unquote(target.username or '')==ROLE
        and target.hostname==HOST and unquote(target.path.lstrip('/'))=='neondb'
        and target.password and target.fragment=='','DSN_DRIFT')
    pid=int(before['MainPID'])
    observed=Path(f'/proc/{pid}/environ').read_bytes()
    live=dict(part.split(b'=',1) for part in observed.split(b'\0') if b'=' in part)
    require(live.get(b'AUTOPILOT_DATABASE_URL')==dsn.encode() and
            live.get(b'AUTOPILOT_ADMISSION_MODE')==b'HOLD','LIVE_ENV_DRIFT')
    require(Path(f'/proc/{pid}/cwd').resolve()==Path(release) and
            Path(f'/proc/{pid}').stat().st_uid==pwd.getpwnam('school-autopilot').pw_uid,
            'LIVE_PROCESS_DRIFT')
    login(dsn)
    require(service()==before and read(ENV,0o600,262144)==disk and
            read(DROP,0o644,4096)==hold and
            Path(f'/proc/{pid}/cwd').resolve()==Path(release),'POST_CHECK_DRIFT')
    print(json.dumps({'audit':'ACTIVE_HOLD_PASS','light_active':True,'admission':'HOLD',
                      'live_dsn_matches_root_file':True,'database_login':'READ_ONLY_PASS',
                      'database_binding':'NEON_PROJECT_BRANCH_ENDPOINT_PASS',
                      'queue_nonterminal':0,'same_invocation':True},sort_keys=True))

if __name__=='__main__':
    try: main()
    except BaseException as exc:
        code=exc.args[0] if isinstance(exc,Blocked) else type(exc).__name__
        print(json.dumps({'audit':'BLOCKED','code':code if code in {
            'HOST_IDENTITY','FILE_DRIFT','ENV_SYNTAX','UNIT_DRIFT','BASE_DRIFT','DROP_DRIFT',
            'PIN_DRIFT','ROUTE_DRIFT','ENV_DRIFT','DSN_DRIFT','LIVE_ENV_DRIFT','LIVE_PROCESS_DRIFT',
            'LOGIN_OR_QUEUE_FAILED','POST_CHECK_DRIFT'} else 'UNCLASSIFIED'}))
        sys.exit(2)
