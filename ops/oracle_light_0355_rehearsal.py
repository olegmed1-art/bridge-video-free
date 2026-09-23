"""Rehearse 0355 retirement and re-fence on an expiring Neon child only."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import urllib.request

PROJECT = 'misty-poetry-18012774'
PRODUCTION = 'br-cold-moon-a1sgkzfd'
API = f'https://console.neon.tech/api/v2/projects/{PROJECT}'
TASK = 'f05c605f-f664-4ff7-9927-a039f000a929'
FENCED_SHA = '655fa30ce165663fb0de1b98bb3bba85237fefc6900a507e4a88edced9b0b11e'


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def api(method, path, token, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method=method,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=25) as response:
        raw = response.read(1000000)
        return json.loads(raw) if raw else {}


def sql(uri, query=None, file=None):
    env = dict(os.environ, PGDATABASE=uri, PGCONNECT_TIMEOUT='10')
    env.pop('NEON_API_KEY', None)
    cmd = ['psql', '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1']
    cmd += ['-c', query] if query is not None else ['-f', str(file)]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=45)
    require(result.returncode == 0, 'CHILD_SQL_FAILED')
    return result.stdout.strip()


def snapshot(uri):
    return sql(uri, f"""SELECT
        encode(sha256(pg_get_functiondef('autopilot.claim_next_task(text,integer)'::regprocedure)::bytea),'hex'),
        (SELECT count(*) FROM autopilot.task WHERE status IN
         ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')),
        (SELECT status||':'||attempts||':'||lease_epoch FROM autopilot.task
         WHERE task_id='{TASK}'::uuid),
        (SELECT count(*) FROM autopilot.role_dispatch_outbox WHERE task_id='{TASK}'::uuid),
        (SELECT count(*) FROM public.schema_migration
         WHERE migration_key='0355_autopilot_light_v3_compatibility_fence'),
        (SELECT count(*) FROM autopilot.migration_0355_function_backup
         WHERE function_key='claim_next_task')""").split('|')


def main():
    token = os.environ['NEON_API_KEY']
    retire, refence = map(Path, sys.argv[1:])
    require(len(sys.argv) == 3 and retire.is_file() and refence.is_file(),
            'REVIEWED_SQL_MISSING')
    branches = api('GET', '/branches', token)['branches']
    defaults = [branch['id'] for branch in branches if branch.get('default')]
    require(defaults == [PRODUCTION], 'PRODUCTION_PARENT_DRIFT')
    name = f'light-0355-{os.environ["GITHUB_RUN_ID"]}-{os.environ["GITHUB_RUN_ATTEMPT"]}'
    expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M:%SZ')
    child = None
    try:
        result = api('POST', '/branches', token, {
            'branch': {'name': name, 'parent_id': PRODUCTION, 'expires_at': expires},
            'endpoints': [{'type': 'read_write'}]})
        child = result['branch']['id']
        require(re.fullmatch(r'br-[a-z0-9-]+', child) and child != PRODUCTION
                and result['branch']['parent_id'] == PRODUCTION, 'CHILD_IDENTITY_DRIFT')
        endpoint = [e['host'] for e in result['endpoints'] if e['type'] == 'read_write']
        require(len(endpoint) == 1 and re.fullmatch(r'ep-[a-z0-9-]+\..+\.neon\.tech', endpoint[0]),
                'CHILD_ENDPOINT_DRIFT')
        uri = api('GET', '/connection_uri?branch_id=' + child +
                  '&database_name=neondb&role_name=neondb_owner&pooled=false', token)['uri']
        parsed = urllib.parse.urlsplit(uri)
        require(parsed.scheme in ('postgres', 'postgresql')
                and parsed.hostname == endpoint[0] and parsed.username == 'neondb_owner'
                and parsed.path == '/neondb' and parsed.password,
                'CHILD_CONNECTION_DRIFT')
        print('::add-mask::' + uri, flush=True)
        for attempt in range(30):
            try:
                require(sql(uri, 'SELECT current_user||\':\'||current_database()')
                        == 'neondb_owner:neondb', 'CHILD_LOGIN_DRIFT')
                break
            except (RuntimeError, subprocess.TimeoutExpired):
                if attempt == 29:
                    raise
                import time
                time.sleep(3)
        before = snapshot(uri)
        require(len(before) == 6 and before[0] == FENCED_SHA
                and before[1:] == ['1','READY:0:0','0','1','1'],
                'CHILD_CANARY_DRIFT')
        sql(uri, file=retire)
        retired = snapshot(uri)
        require(retired[0] != before[0] and retired[1:] == before[1:],
                'CHILD_RETIRE_DRIFT')
        sql(uri, file=refence)
        restored = snapshot(uri)
        require(restored == before, 'CHILD_RECOVERY_DRIFT')
        print(json.dumps({'rehearsal':'PASS','production_mutated':False,
                          'child_branch':child,'retire_changed_function_only':True,
                          'refence_restored_exact_hash':True}), flush=True)
    finally:
        if child and re.fullmatch(r'br-[a-z0-9-]+', child) and child != PRODUCTION:
            api('DELETE', '/branches/' + child, token)
            print('DISPOSABLE_BRANCH_DELETED=PASS', flush=True)


if __name__ == '__main__':
    main()
