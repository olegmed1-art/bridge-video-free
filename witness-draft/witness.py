"""Draft fixed-scope read-only observer. Never execute during local tests.

No raw inspect/env/cmdline/exception output. Missing evidence fails closed.
Queue identity is a resident-mounted-credential connection witness, NOT proof
of the already-running worker's effective configuration. That stays UNKNOWN.
"""
import datetime
import json
import os
import subprocess


def configuration_mode(env):
    """Pure mirror of current-main precedence; never return credential values.

    Caller must not substitute Docker Config.Env for effective worker state.
    No environment acquisition is implemented or authorized by this helper.
    """
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()):
        return 'UNKNOWN'
    if env.get('BRIDGE_VIDEO_QUEUE_DATABASE_URL', '').strip():
        return 'DIRECT_OVERRIDE'
    filename = env.get('BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE', '').strip()
    if filename:
        return 'EXPECTED_FILE' if filename == RESIDENT else 'OTHER_FILE'
    if env.get('BRIDGE_APP_DATABASE_URL', '').strip():
        return 'APP_FALLBACK'
    if env.get('BRIDGE_WORKER_DATABASE_URL', '').strip():
        return 'WORKER_FALLBACK'
    return 'UNCONFIGURED'


def sanitize_probe(raw):
    """Reject malformed/extra fields, including potential secret payloads."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    if len(raw) > 1024:
        raise ValueError()
    result = json.loads(raw, object_pairs_hook=unique)
    if type(result) is not dict:
        raise ValueError()
    base = {'production', 'schema', 'function'}
    if set(result) not in (base, base | {'claimable', 'leased'}):
        raise ValueError()
    if any(type(result[k]) is not bool for k in base):
        raise ValueError()
    if 'claimable' in result:
        if not all(result[k] for k in base):
            raise ValueError()
        if any(type(result[k]) is not int or result[k] < 0
               for k in ('claimable', 'leased')):
            raise ValueError()
    elif all(result[k] for k in base):
        raise ValueError()
    return result

CONTAINER = 'universal-video-container'
HOST = '/opt/bridge-school/universal-video/secrets/video-queue-dsn'
RESIDENT = '/run/secrets/video-queue-dsn'


def run(argv, stdin=None):
    return subprocess.run(argv, input=stdin, text=True, capture_output=True,
                          check=True, timeout=25).stdout


def decision(e):
    if e.get('observation_stable') is True and e.get('inode_match') == 'NO':
        return 'RECREATE_REQUIRED'
    # No YES until effective worker configuration, OCI state and conflicts
    # are independently bound to this same observation.
    return 'BLOCKED_CAPABILITY'


def collect():
    e = dict(observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
             resident_target='UNKNOWN', inode_match='UNKNOWN',
             container_recreation_required='UNKNOWN', PRECANARY_READY='UNKNOWN',
             production_queue_access='UNKNOWN', queue_idle='UNKNOWN',
             conflicting_work='UNKNOWN', server_health='UNKNOWN',
             vm_state='UNKNOWN', data_mutated=False)
    try:
        e['cpu_count'] = os.cpu_count()
        e['load'] = list(os.getloadavg())
        e['root_free_bytes'] = os.statvfs('/').f_bavail * os.statvfs('/').f_frsize
        with open('/proc/meminfo') as f:
            e['memory_kib'] = {k: int(v.split()[0]) for k, v in
                               (line.split(':', 1) for line in f)
                               if k in ('MemTotal', 'MemAvailable')}
        fields = ['ActiveState', 'SubState', 'NRestarts', 'MainPID']
        raw = run(['systemctl', 'show', 'universal-video-container.service',
                   '--no-pager'] + ['--property=' + x for x in fields])
        e['service'] = dict(line.split('=', 1) for line in raw.splitlines()
                            if line.split('=', 1)[0] in fields)
        fmt = '{{json .State}}'
        state = json.loads(run(['docker', 'inspect', '--type', 'container',
                                '--format', fmt, CONTAINER]))
        e['container'] = {k: state[k] for k in
                          ('Running', 'Restarting', 'ExitCode', 'OOMKilled', 'Pid')}
        pid = state['Pid']
        if type(pid) is not int or pid <= 0 or not state['Running']:
            e['status'] = 'BLOCKED_RUNTIME'
            e['BLOCKER'] = 'resident_not_running'
            e['NEXT_STEP'] = 'review_runtime_failure_without_lifecycle_action'
            return e
        a = os.stat(HOST)
        b = os.stat('/proc/' + str(pid) + '/root' + RESIDENT)
        e['host_device_inode'] = [a.st_dev, a.st_ino]
        e['resident_device_inode'] = [b.st_dev, b.st_ino]
        e['inode_match'] = 'YES' if (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino) else 'NO'
        # Probe uses the resident filesystem and installed interpreter only.
        # It never imports application/worker code or performs inference.
        probe = '''import json
from pathlib import Path
import psycopg
with psycopg.connect(Path('/run/secrets/video-queue-dsn').read_text().strip(), connect_timeout=8) as c:
 c.read_only = True
 with c.cursor() as q:
  q.execute("SET LOCAL statement_timeout = '5s'")
  q.execute("SET LOCAL lock_timeout = '1s'")
  q.execute("SELECT current_database(), current_setting('neon.branch_id',true), current_setting('neon.project_id',true), to_regnamespace('video_queue') IS NOT NULL")
  db,branch,project,schema=q.fetchone()
  # Only known labels and booleans leave the container.
  production=(db=='neondb' and branch=='br-wispy-lab-b1rq54of' and project=='misty-poetry-18012774')
  result={'production':production,'schema':schema}
  q.execute("SELECT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='video_queue' AND p.proname='precanary_idle_snapshot' AND p.pronargs=0)")
  result['function']=q.fetchone()[0]
  if production and schema and result['function']:
   q.execute('SELECT * FROM video_queue.precanary_idle_snapshot()')
   row=q.fetchone()
   if len(row)!=2 or any(type(x) is not int or x<0 for x in row): raise ValueError()
   result.update(claimable=row[0],leased=row[1])
 c.rollback()
print(json.dumps(result))
'''
        raw = run(['docker', 'exec', '-i', CONTAINER, 'python', '-B', '-'], probe)
        e['resident_mounted_credential_probe'] = sanitize_probe(raw)
        # Detect process replacement or atomic credential changes during probe.
        after = json.loads(run(['docker', 'inspect', '--type', 'container',
                                 '--format', fmt, CONTAINER]))
        a2, b2 = os.stat(HOST), os.stat('/proc/' + str(pid) + '/root' + RESIDENT)
        if (after['Pid'], after['StartedAt'], a2.st_dev, a2.st_ino, b2.st_dev, b2.st_ino) != (
                pid, state['StartedAt'], a.st_dev, a.st_ino, b.st_dev, b.st_ino):
            e['inode_match'] = 'UNKNOWN'
            e['resident_mounted_credential_probe'] = 'UNKNOWN'
        else:
            e['observation_stable'] = True
    except Exception:
        # Never serialize exceptions: SQL/SSH errors can include credentials.
        e['observation_error'] = True
        e['inode_match'] = 'UNKNOWN'
        e['resident_mounted_credential_probe'] = 'UNKNOWN'
        e['observation_stable'] = False
    e['status'] = decision(e)
    if e['status'] == 'RECREATE_REQUIRED':
        e['container_recreation_required'] = 'YES'
        e['BLOCKER'] = 'credential_inode_mismatch'
    else:
        e['BLOCKER'] = 'effective_worker_configuration_and_full_server_witness_unproved'
    e['NEXT_STEP'] = 'independent_review_and_complete_missing_observers_before_any_GO'
    return e


if __name__ == '__main__':
    # Importable for mocked tests; no runnable server transport in this draft.
    raise SystemExit('DRAFT_DISABLED: independent review and separate GO required')
