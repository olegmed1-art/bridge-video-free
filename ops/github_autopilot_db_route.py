"""Run approved autopilot consumers under a server routing lease on trusted main."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

CA_SHA256 = '1ee37914846a8f85aff90523937ed6dde63517f239782e782190eb5038799212'
HOST = '92.5.47.149'
SOURCE = 'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech'
TARGETS = {
    'role-callback': ('AUTOPILOT_CALLBACK_DATABASE_URL','autopilot_callback_login','oracle_autopilot.github_role_callback',()),
    'codex-ack': ('AUTOPILOT_CALLBACK_DATABASE_URL','autopilot_callback_login','oracle_autopilot.github_codex_callback',('ack',)),
    'codex-terminal': ('AUTOPILOT_CALLBACK_DATABASE_URL','autopilot_callback_login','oracle_autopilot.github_codex_callback',('terminal',)),
    'codex-publication': ('AUTOPILOT_CALLBACK_DATABASE_URL','autopilot_callback_login','oracle_autopilot.github_codex_publication',()),
    'diagnostics': ('DATABASE_URL','bridge_school_worker_principal','oracle_autopilot.reconcile_diagnostics',()),
    'reconcile': ('DATABASE_URL','bridge_school_worker_principal','oracle_autopilot.paused_reconcile',()),
    'next-step': ('DATABASE_URL','bridge_school_worker_principal','oracle_autopilot.next_step_reconcile',()),
    'mailbox': ('DATABASE_URL','autopilot_callback_login','ops.github_autopilot_db_route',('--mailbox-read',)),
}


def mask(value):
    escaped = value.replace('%','%25').replace('\r','%0D').replace('\n','%0A')
    print('::add-mask::'+escaped,file=sys.stderr,flush=True)


def target_dsn(raw, principal, ca):
    assert raw and not any(ord(c)<32 or ord(c)==127 for c in raw)
    parsed = urlsplit(raw)
    query = parse_qs(parsed.query,strict_parsing=True,keep_blank_values=True)
    assert parsed.scheme in {'postgres','postgresql'} and parsed.hostname in {SOURCE,SOURCE.replace('.c-5.','-pooler.c-5.')}
    assert parsed.path == '/neondb' and unquote(parsed.username or '') == principal and parsed.password
    assert parsed.port in {None,5432} and not parsed.fragment
    assert set(query) <= {'sslmode','channel_binding','connect_timeout','application_name'}
    assert all(len(v)==1 for v in query.values())
    password = unquote(parsed.password)
    assert not any(ord(c)<32 or ord(c)==127 for c in password)
    suffix = urlencode({'sslmode':'verify-full','channel_binding':'require','sslrootcert':str(ca),'connect_timeout':'10'})
    return 'postgresql://' + quote(principal,safe='') + ':' + quote(password,safe='') + '@127.0.0.1:55432/autopilot?' + suffix


def parse_record(raw, minimum_epoch):
    assert len(raw) <= 16384
    value = json.loads(raw)
    if value == {'busy':True}:
        return None
    assert isinstance(value,dict) and set(value) in ({'route'},{'route','ca_pem'})
    route = value['route']
    assert isinstance(route,dict) and set(route)=={'version','backend','database','epoch'}
    assert route['version']==1 and route['database']=='autopilot'
    assert route['backend'] in {'neon','paused','postgresql'}
    assert type(route['epoch']) is int and minimum_epoch <= route['epoch'] <= 2**31-1
    if route['backend'] != 'paused':
        assert isinstance(value.get('ca_pem'),str)
        assert hashlib.sha256(value['ca_pem'].encode()).hexdigest()==CA_SHA256
    return value


def stop(process, group=False):
    if process.poll() is not None:
        if group:
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
        return
    try:
        if group:
            os.killpg(process.pid,signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=5)
        if group:
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
    except subprocess.TimeoutExpired:
        if group:
            os.killpg(process.pid,signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=5)


def header(process):
    data = bytearray()
    deadline = time.monotonic()+20
    while time.monotonic()<deadline and len(data)<=16384:
        if select.select([process.stdout],[],[],1)[0]:
            value = os.read(process.stdout.fileno(),1)
            if value == b'\n':
                return bytes(data)
            if not value:
                raise RuntimeError('route_eof')
            data.extend(value)
        elif process.poll() is not None:
            raise RuntimeError('route_process_exited')
    raise RuntimeError('route_header_timeout_or_size')


def execute_under_lease(ssh, selection, environment, work):
    minimum_epoch = 0
    deadline = time.monotonic()+600
    while time.monotonic()<deadline:
        lease = subprocess.Popen(ssh+['-T','-L','127.0.0.1:55432:127.0.0.1:55432',
            'autopilot-db-tunnel@'+HOST,'route-v1'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,bufsize=0)
        child = None
        try:
            record = parse_record(header(lease),minimum_epoch)
            if record is None or record['route']['backend']=='paused':
                if record:
                    minimum_epoch = record['route']['epoch']
                stop(lease)
                time.sleep(2)
                continue
            route = record['route']
            assert lease.poll() is None
            ca = work/'ca.crt'
            ca.write_text(record['ca_pem'])
            ca.chmod(0o600)
            env_name,principal,module,args = TARGETS[selection]
            child_env = environment.copy()
            child_env.pop('SSH_PRIVATE_KEY',None)
            child_env['AUTOPILOT_DB_BACKEND'] = route['backend']
            if route['backend']=='postgresql':
                value = target_dsn(child_env[env_name],principal,ca)
                mask(unquote(urlsplit(child_env[env_name]).password))
                mask(value)
                child_env[env_name] = value
                child_env.update(AUTOPILOT_PG_HOST='127.0.0.1',AUTOPILOT_PG_PORT='55432',AUTOPILOT_PG_DATABASE='autopilot')
            lease.stdin.write(b'.')
            child = subprocess.Popen([sys.executable,'-m',module,*args],env=child_env,start_new_session=True)
            start = heartbeat = time.monotonic()
            while child.poll() is None:
                if lease.poll() is not None or time.monotonic()-start>240:
                    raise RuntimeError('route_lease_lost_or_consumer_timeout')
                if time.monotonic()-heartbeat>3:
                    lease.stdin.write(b'.')
                    heartbeat = time.monotonic()
                time.sleep(0.1)
            assert lease.poll() is None, 'lease_lost_at_completion'
            return child.returncode
        finally:
            if child is not None:
                stop(child,group=True)
            stop(lease)
    raise RuntimeError('route_paused_timeout')


def main(selection):
    assert selection in TARGETS
    assert os.environ.get('GITHUB_ACTIONS')=='true'
    assert os.environ.get('GITHUB_REPOSITORY')=='olegmed1-art/bridge-video-free'
    assert os.environ.get('GITHUB_REF')=='refs/heads/main', 'trusted_main_required'
    def cancelled(signum, frame):
        raise SystemExit(128+signum)
    signal.signal(signal.SIGTERM,cancelled)
    signal.signal(signal.SIGINT,cancelled)
    environment = os.environ.copy()
    private = environment.pop('SSH_PRIVATE_KEY')
    os.environ.pop('SSH_PRIVATE_KEY',None)
    assert private.strip()
    with tempfile.TemporaryDirectory(prefix='autopilot-route-') as temporary:
        work = Path(temporary)
        key = work/'key'
        key.write_text(private.replace('\r','')+'\n')
        key.chmod(0o600)
        del private
        subprocess.run(['ssh-keygen','-y','-P','','-f',str(key)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
        scanner = Path(__file__).resolve().parents[1]/'ops/oracle_known_hosts_from_scan.sh'
        subprocess.run(['bash',str(scanner),HOST,'SHA256:XBR1x74uJ41BxmDF7Y9P20GjIjNbrYXqieV4c2MC0Go',str(work/'known_hosts')],
                       check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
        ssh = ['ssh','-F','/dev/null','-i',str(key),'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
               '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(work/'known_hosts'),
               '-o','ConnectTimeout=15','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2',
               '-o','ExitOnForwardFailure=yes']
        return execute_under_lease(ssh,selection,environment,work)


def mailbox_read():
    import psycopg
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute('SET statement_timeout = 10000')
            cur.execute('SELECT mailbox_pr,used_dispatches,max_dispatches,readiness FROM autopilot.mailbox_rotation_readiness()')
            rows = cur.fetchall()
            assert len(rows)==1 and len(rows[0])==4
            assert all('\n' not in str(v) and '|' not in str(v) for v in rows[0])
            print('|'.join(str(v) for v in rows[0]))


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--mailbox-read']:
            mailbox_read()
        else:
            assert len(sys.argv)==2
            sys.exit(main(sys.argv[1]))
    except Exception:
        print('AUTOPILOT_DATABASE_ROUTING_FAILED',file=sys.stderr)
        sys.exit(1)
