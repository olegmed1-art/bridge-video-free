"""Scoped Neon credential repair for the installed Light worker. stdin is secret JSON.

Run only as root over the protected, pinned SSH workflow. All output is a fixed
status code; neither the password nor connection errors enter logs or argv.
"""
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
from urllib.parse import quote, unquote, urlsplit

UNIT = 'school-autopilot-production-light.service'
BASE = Path('/etc/systemd/system') / UNIT
DROP = Path('/etc/systemd/system/school-autopilot-production-light.service.d/40-reviewed-runtime-hold.conf')
ENV = Path('/etc/school-autopilot-production-light.env')
ROUTE = Path('/var/lib/bridge-autopilot-tunnel')
BASE_SHA = 'e8d22d57089b9c43b1d65d78de7e022c2b2124357cf40e98728128d2980ee2b4'
ROLE = 'autopilot_light_worker_login'
HOST = 'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech'
RELEASE_PATTERN = r'/opt/bridge-school/school-autopilot-production-light/releases/[a-f0-9]{40}'
PIN_KEYS = frozenset({'AUTOPILOT_TOKEN_BROKER_URL', 'AUTOPILOT_TOKEN_BROKER_EXPECTED_SOURCE_SHA',
    'AUTOPILOT_TOKEN_BROKER_EXPECTED_ARTIFACT_SHA256', 'AUTOPILOT_TOKEN_BROKER_EXPECTED_POLICY_SHA256',
    'AUTOPILOT_TOKEN_BROKER_EXPECTED_PROVENANCE_SHA256'})

class Blocked(Exception):
    pass

def require(condition, code):
    if not condition:
        raise Blocked(code)

def read_file(path, mode, limit=262144):
    require(not path.is_symlink(), 'FILE_SYMLINK')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                stat.S_IMODE(info.st_mode) == mode and info.st_size <= limit, 'FILE_DRIFT')
        raw = stream.read(limit + 1)
        require(len(raw) <= limit, 'FILE_DRIFT')
        return raw

def service():
    keys = ('ActiveState', 'SubState', 'MainPID', 'InvocationID', 'WorkingDirectory',
            'User', 'Group', 'Environment', 'EnvironmentFiles', 'ExecStart',
            'FragmentPath', 'DropInPaths', 'NeedDaemonReload')
    raw = subprocess.run(['systemctl', 'show', UNIT, *['-p'+key for key in keys]],
                         capture_output=True, check=True, text=True, timeout=15).stdout
    result = {'EnvironmentFiles': []}
    for line in raw.splitlines():
        key, sep, value = line.partition('=')
        require(sep and key in keys, 'UNIT_DRIFT')
        if key == 'EnvironmentFiles':
            result[key].append(value)
        else:
            require(key not in result, 'UNIT_DRIFT')
            result[key] = value
    require(set(result) == set(keys), 'UNIT_DRIFT')
    return result

def validate_state(state, admission, active):
    release = state['WorkingDirectory']
    require(re.fullmatch(RELEASE_PATTERN, release) and
            state['User'] == state['Group'] == 'school-autopilot' and
            state['FragmentPath'] == str(BASE) and state['DropInPaths'] == str(DROP) and
            state['NeedDaemonReload'] == 'no' and
            state['Environment'].split().count('AUTOPILOT_ADMISSION_MODE='+admission) == 1 and
            not any(token.startswith('AUTOPILOT_ADMISSION_MODE=') and
                    token != 'AUTOPILOT_ADMISSION_MODE='+admission
                    for token in state['Environment'].split()) and
            ' -m oracle_autopilot.worker_v17 ;' in state['ExecStart'], 'UNIT_DRIFT')
    expected = [str(ENV)+' (ignore_errors=no)',
                release+'/ops/autopilot/broker-hold.env (ignore_errors=no)']
    require(state['EnvironmentFiles'] == expected, 'ENV_SOURCE_DRIFT')
    require((state['ActiveState'], state['SubState']) ==
            (('active','running') if active else ('inactive','dead')) and
            (int(state['MainPID']) > 0 if active else state['MainPID'] == '0'), 'UNIT_STATE_DRIFT')
    return release

def env_values(raw):
    result = {}
    for line in raw.decode('utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, sep, value = line.partition('=')
        require(sep and re.fullmatch(r'[A-Z][A-Z0-9_]*',key) and key not in result, 'ENV_SYNTAX')
        try:
            tokens = shlex.split(value, posix=True, comments=False)
        except ValueError:
            raise Blocked('ENV_SYNTAX') from None
        require(len(tokens) == 1, 'ENV_SYNTAX')
        result[key] = tokens[0]
    return result

def replace_dsn(raw, password):
    values = env_values(raw)
    require('AUTOPILOT_DATABASE_URL' in values and 'AUTOPILOT_ADMISSION_MODE' not in values,
            'ENV_DRIFT')
    old = values['AUTOPILOT_DATABASE_URL']
    parsed = urlsplit(old)
    require(parsed.scheme in ('postgresql', 'postgres') and
            unquote(parsed.username or '') == ROLE and parsed.hostname == HOST and
            (parsed.port is None or parsed.port == 5432) and
            unquote(parsed.path.lstrip('/')) == 'neondb' and parsed.password and
            parsed.fragment == '', 'DSN_DRIFT')
    require(isinstance(password,str) and 1 <= len(password) <= 1024 and
            all(32 < ord(ch) < 127 for ch in password), 'PASSWORD_INVALID')
    userinfo, sep, address = parsed.netloc.rpartition('@')
    require(sep and ':' in userinfo and address in (HOST, HOST+':5432'), 'DSN_DRIFT')
    new = old.replace(parsed.netloc, quote(ROLE,safe='')+':'+quote(password,safe='')+'@'+address, 1)
    require(new != old, 'PASSWORD_UNCHANGED')
    lines = raw.decode('utf-8').splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.startswith('AUTOPILOT_DATABASE_URL=')]
    require(len(matches) == 1, 'DSN_FORMAT_DRIFT')
    ending = '\r\n' if lines[matches[0]].endswith('\r\n') else '\n'
    lines[matches[0]] = 'AUTOPILOT_DATABASE_URL='+shlex.quote(new)+ending
    updated = ''.join(lines).encode()
    require({k:v for k,v in env_values(updated).items() if k != 'AUTOPILOT_DATABASE_URL'} ==
            {k:v for k,v in values.items() if k != 'AUTOPILOT_DATABASE_URL'}, 'ENV_REWRITE_DRIFT')
    return old, new, updated

# Only a fixed-shape read-only SELECT is executed under the worker account.
CHILD = '''import json,os,sys
try:
 import psycopg
 with psycopg.connect(os.environ['AUDIT_DATABASE_URL'],autocommit=True,connect_timeout=10,
   options='-c statement_timeout=5000 -c default_transaction_read_only=on') as conn:
  row=conn.execute("SELECT current_user,current_database(),current_setting('transaction_read_only')").fetchone()
  count=conn.execute("SELECT count(*) FROM autopilot.task_status WHERE status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')").fetchone()[0]
 print(json.dumps({'ok':row==('autopilot_light_worker_login','neondb','on') and count==0}))
except BaseException:
 print(json.dumps({'ok':False}))
 sys.exit(2)
'''

def check_login(dsn):
    user = pwd.getpwnam('school-autopilot')
    def identity():
        os.setgroups([])
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
    child = subprocess.run(['/opt/bridge-school/school-autopilot/.venv/bin/python','-c',CHILD],
        cwd='/', env={'AUDIT_DATABASE_URL':dsn,'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},
        preexec_fn=identity,capture_output=True,text=True,timeout=30)
    require(child.returncode == 0 and len(child.stdout) < 128 and
            json.loads(child.stdout) == {'ok':True}, 'LOGIN_OR_QUEUE_FAILED')

def atomic(path, content, mode, suffix):
    temp = path.parent / ('.'+path.name+'.recovery-'+suffix)
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd,'wb') as stream:
            os.fchmod(stream.fileno(),mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp,path)
        directory = os.open(path.parent,os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temp.unlink(missing_ok=True)

def run(*args):
    subprocess.run(['systemctl',*args],check=True,capture_output=True,timeout=45)


def transition(old_drop, hold_drop, original, replacement, new):
    # Every mutation is covered. A failure must prove stopped/HOLD or
    # report containment unverified; never print a secret-bearing error.
    try:
        run('stop',UNIT)
        stopped = service()
        require(stopped['ActiveState']=='inactive' and stopped['MainPID']=='0','STOP_FAILED')
        atomic(DROP,hold_drop,0o644,'hold')
        run('daemon-reload')
        validate_state(service(),'HOLD',False)
        atomic(ENV,replacement,0o600,'env')
        require(read_file(ENV,0o600)==replacement,'ENV_WRITE_FAILED')
        check_login(env_values(replacement)['AUTOPILOT_DATABASE_URL'])
        run('start',UNIT)
        after=service()
        validate_state(after,'HOLD',True)
        observed=Path('/proc/'+after['MainPID']+'/environ').read_bytes()
        actual=dict(value.split(b'=',1) for value in observed.split(b'\0') if b'=' in value)
        verify_process_environment(actual,new)
        check_login(new)
    except BaseException:
        try:
            run('stop',UNIT)
            current_drop=read_file(DROP,0o644,4096)
            require(current_drop in (old_drop,hold_drop),'DROP_CHANGED_DURING_RECOVERY')
            if current_drop==old_drop:
                atomic(DROP,hold_drop,0o644,'contain')
            run('daemon-reload')
            validate_state(service(),'HOLD',False)
            if read_file(ENV,0o600) == replacement:
                atomic(ENV,original,0o600,'rollback')
        except BaseException:
            raise Blocked('CONTAINMENT_UNVERIFIED') from None
        raise

def verify_process_environment(actual,new):
    # Return only fixed codes; process environment and candidate never enter logs.
    database_ok = actual.get(b'AUTOPILOT_DATABASE_URL') == new.encode()
    admission_ok = actual.get(b'AUTOPILOT_ADMISSION_MODE') == b'HOLD'
    require(database_ok or admission_ok, 'POST_START_BOTH_DRIFT')
    require(database_ok, 'POST_START_DATABASE_DRIFT')
    require(admission_ok, 'POST_START_ADMISSION_DRIFT')

def transition_stopped(hold_drop, original, replacement, new):
    try:
        atomic(ENV,replacement,0o600,'env')
        require(read_file(ENV,0o600)==replacement,'ENV_WRITE_FAILED')
        check_login(env_values(replacement)['AUTOPILOT_DATABASE_URL'])
        run('start',UNIT)
        after=service()
        validate_state(after,'HOLD',True)
        observed=Path('/proc/'+after['MainPID']+'/environ').read_bytes()
        actual=dict(value.split(b'=',1) for value in observed.split(b'\0') if b'=' in value)
        verify_process_environment(actual,new)
        check_login(new)
    except BaseException:
        try:
            run('stop',UNIT)
            require(read_file(DROP,0o644,4096)==hold_drop,'DROP_CHANGED_DURING_RECOVERY')
            run('daemon-reload')
            validate_state(service(),'HOLD',False)
            if read_file(ENV,0o600) == replacement:
                atomic(ENV,original,0o600,'rollback')
            require(read_file(ENV,0o600)==original,'ROLLBACK_UNVERIFIED')
        except BaseException:
            raise Blocked('CONTAINMENT_UNVERIFIED') from None
        raise

def main(packet):
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic','HOST_IDENTITY')
    require(set(packet) == {'password','mode'} and packet['mode']=='stopped_hold', 'INPUT_INVALID')
    lock_path = ROUTE / 'route.lock'
    read_file(lock_path,0o644,1048576)
    with open(lock_path,'rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(json.loads(read_file(ROUTE/'route.json',0o644,4096)) ==
                {'version':1,'backend':'neon','database':'autopilot','epoch':0},'ROUTE_DRIFT')
        before = service()
        release = validate_state(before,'HOLD',False)
        require(hashlib.sha256(read_file(BASE,0o644)).hexdigest()==BASE_SHA,'BASE_DRIFT')
        hold_drop = ('[Service]\nWorkingDirectory='+release+'\nEnvironment=AUTOPILOT_ADMISSION_MODE=HOLD\n'
                    'EnvironmentFile='+release+'/ops/autopilot/broker-hold.env\n').encode()
        require(read_file(DROP,0o644,4096)==hold_drop,'DROP_DRIFT')
        pins = env_values(read_file(Path(release)/'ops/autopilot/broker-hold.env',0o444,4096))
        require(set(pins)==PIN_KEYS,'PIN_DRIFT')
        original = read_file(ENV,0o600)
        old,new,replacement = replace_dsn(original,packet['password'])
        # Invalid candidate aborts before any service or file mutation.
        check_login(new)
        require(service()==before and read_file(ENV,0o600)==original and
                read_file(DROP,0o644,4096)==hold_drop, 'PRE_WRITE_DRIFT')
        transition_stopped(hold_drop,original,replacement,new)
        print(json.dumps({'recovery':'PASS','admission':'HOLD','light_active':True,
                          'database_login':'READ_ONLY_PASS','queue_nonterminal':0}))

if __name__=='__main__':
    try:
        main(json.load(sys.stdin))
    except BaseException as exc:
        code=exc.args[0] if isinstance(exc,Blocked) else type(exc).__name__
        print(json.dumps({'recovery':'BLOCKED','code':code if code in {
          'HOST_IDENTITY','INPUT_INVALID','ROUTE_DRIFT','INVOCATION_DRIFT','BASE_DRIFT',
          'DROP_DRIFT','PIN_DRIFT','LIVE_ENV_DRIFT','PRE_WRITE_DRIFT','STOP_FAILED',
          'LOGIN_OR_QUEUE_FAILED','UNIT_DRIFT','UNIT_STATE_DRIFT','ENV_SOURCE_DRIFT',
          'ENV_DRIFT','ENV_SYNTAX','DSN_DRIFT','DSN_FORMAT_DRIFT','PASSWORD_INVALID',
          'PASSWORD_UNCHANGED','ENV_REWRITE_DRIFT','FILE_DRIFT','FILE_SYMLINK',
          'ENV_WRITE_FAILED','POST_START_DRIFT','POST_START_BOTH_DRIFT',
          'POST_START_DATABASE_DRIFT','POST_START_ADMISSION_DRIFT','ROLLBACK_UNVERIFIED',
          'CONTAINMENT_UNVERIFIED',
          'DROP_CHANGED_DURING_RECOVERY'} else 'UNCLASSIFIED'}))
        sys.exit(2)
