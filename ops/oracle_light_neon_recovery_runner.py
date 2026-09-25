"""Protected Actions runner: Neon secret stays in memory and SSH stdin."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROJECT = 'misty-poetry-18012774'
BRANCH = 'br-aged-mud-b1i64914'
ROLE = 'autopilot_light_worker_login'
REPO = 'olegmed1-art/bridge-video-free'
HOST = '92.5.47.149'

class Blocked(Exception):
    pass

def require_main():
    expected = os.environ['EXPECTED_MAIN']
    if not re.fullmatch(r'[0-9a-f]{40}',expected):
        raise Blocked('SHA_INVALID')
    local = subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    remote = subprocess.run(['gh','api',f'repos/{REPO}/git/ref/heads/main','--jq','.object.sha'],
                            capture_output=True,text=True,check=True).stdout.strip()
    if local != expected or remote != expected:
        raise Blocked('MAIN_CHANGED')

def ssh_command(key,known):
    return ['ssh','-F','/dev/null','-i',key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
        '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+known,
        '-o','ConnectTimeout=15','-o','ConnectionAttempts=1',f'ubuntu@{HOST}']

def retrieve():
    endpoint=(f'https://console.neon.tech/api/v2/projects/{PROJECT}/branches/{BRANCH}/'
              f'roles/{ROLE}/reveal_password')
    request=Request(endpoint,headers={'Authorization':'Bearer '+os.environ['NEON_API_KEY'],
                                     'Accept':'application/json'})
    try:
        with urlopen(request,timeout=20) as response:
            if response.status != 200:
                raise Blocked('NEON_RESPONSE')
            body=response.read(8193)
    except HTTPError as exc:
        if exc.code==412:
            raise Blocked('PASSWORD_STORAGE_DISABLED') from None
        raise Blocked('NEON_HTTP_ERROR') from None
    if len(body)>8192:
        raise Blocked('NEON_RESPONSE')
    payload=json.loads(body)
    if set(payload) != {'password'} or not isinstance(payload['password'],str):
        raise Blocked('NEON_RESPONSE')
    return payload['password']

def main(key,known):
    require_main()
    command=ssh_command(key,known)
    password=retrieve()
    require_main()  # Last-second primary-source check immediately before host mutation.
    source=Path('ops/oracle_light_credential_recovery.py').read_bytes()
    loader=('import base64;exec(compile(base64.b64decode("'
            +base64.b64encode(source).decode()+'"),"recovery","exec"))')
    remote='sudo -n /usr/bin/python3 -c '+"'"+loader+"'"
    packet=json.dumps({'password':password,'mode':'stopped_hold'})
    result=subprocess.run(command+[remote],input=packet,text=True,capture_output=True,timeout=150)
    # No raw remote output, stderr, or connection exception ever reaches Actions logs.
    try:
        status=json.loads(result.stdout)
    except ValueError:
        raise Blocked('REMOTE_FAILED') from None
    if result.returncode or status != {'recovery':'PASS','admission':'HOLD','light_active':True,
                                       'database_login':'READ_ONLY_PASS','queue_nonterminal':0}:
        allowed={'HOST_IDENTITY','INPUT_INVALID','ROUTE_DRIFT','INVOCATION_DRIFT','BASE_DRIFT',
          'DROP_DRIFT','PIN_DRIFT','LIVE_ENV_DRIFT','PRE_WRITE_DRIFT','STOP_FAILED',
          'LOGIN_OR_QUEUE_FAILED','UNIT_DRIFT','UNIT_STATE_DRIFT','ENV_SOURCE_DRIFT',
          'ENV_DRIFT','ENV_SYNTAX','DSN_DRIFT','DSN_FORMAT_DRIFT','PASSWORD_INVALID',
          'PASSWORD_UNCHANGED','ENV_REWRITE_DRIFT','FILE_DRIFT','FILE_SYMLINK',
          'ENV_WRITE_FAILED','POST_START_DRIFT','POST_START_BOTH_DRIFT',
          'POST_START_DATABASE_DRIFT','POST_START_ADMISSION_DRIFT',
          'POST_START_CODE_PATH_DRIFT','POST_START_PROCESS_CHANGED','ROLLBACK_UNVERIFIED',
          'CONTAINMENT_UNVERIFIED',
          'DROP_CHANGED_DURING_RECOVERY'}
        code=status.get('code') if isinstance(status,dict) else None
        raise Blocked(code if code in allowed else 'REMOTE_FAILED')
    print(json.dumps(status,sort_keys=True))

if __name__=='__main__':
    try:
        main(*sys.argv[1:])
    except BaseException as exc:
        code=exc.args[0] if isinstance(exc,Blocked) else 'RUNNER_FAILED'
        print(json.dumps({'recovery':'BLOCKED','code':code}))
        sys.exit(2)
