"""Pure draft evidence validators; no I/O, authority or live readiness claims."""
import math
import datetime
import json
import re

INSTANCE = 'ocid1.instance.oc1.eu-frankfurt-1.antheljtruoejaica7hj5oubnh2cctnjr7ti7llcgo6ho6wdvgvui6td7saq'
ACTIVE = ('in_progress', 'queued', 'waiting', 'pending', 'requested')
REPO = 'olegmed1-art/bridge-video-free'
WITNESS_WORKFLOW = '.github/workflows/issue-881-server-witness-v2.yml'


def self_run_identity(run, run_id, exact_sha):
    """Validate GET run detail; never exclude by name, SHA or actor alone."""
    if type(run_id) is not int or run_id <= 0 or not re.fullmatch('[0-9a-f]{40}', exact_sha):
        raise ValueError()
    required = {'id': run_id, 'head_sha': exact_sha, 'run_attempt': 1,
                'event': 'workflow_dispatch', 'path': WITNESS_WORKFLOW,
                'status': 'in_progress'}
    if type(run) is not dict or any(run.get(k) != v or type(run.get(k)) is not type(v)
                                    for k, v in required.items()):
        raise ValueError()
    for field in ('repository', 'head_repository'):
        if type(run.get(field)) is not dict or run[field].get('full_name') != REPO:
            raise ValueError()
    for field in ('actor', 'triggering_actor'):
        if type(run.get(field)) is not dict or run[field].get('login') != 'olegmed1-art':
            raise ValueError()
    return required


def resident_summary(raw, started_at, finished_at):
    """Validate collector output and expose only fixed-schema observations.

    Collector verdicts are NOT trusted. Caller must complete external postchecks
    before using these observations. A valid receipt still cannot prove worker
    in-memory DSN or process idleness.
    """
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError()
            result[k] = v
        return result
    if type(raw) is not str or len(raw) > 16384:
        raise ValueError()
    e = json.loads(raw, object_pairs_hook=pairs,
                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if type(e) is not dict:
        raise ValueError()
    fixed = {'resident_target', 'inode_match', 'container_recreation_required',
             'PRECANARY_READY', 'production_queue_access', 'queue_idle',
             'conflicting_work', 'server_health', 'vm_state', 'data_mutated',
             'status', 'BLOCKER', 'NEXT_STEP', 'observed_at'}
    optional = {'cpu_count', 'load', 'root_free_bytes', 'memory_kib', 'service',
                'container', 'host_device_inode', 'resident_device_inode',
                'resident_mounted_credential_probe', 'observation_error', 'observation_stable'}
    if not fixed <= set(e) or not set(e) <= fixed | optional:
        raise ValueError()
    for k in ('resident_target', 'PRECANARY_READY', 'production_queue_access',
              'queue_idle', 'conflicting_work', 'server_health', 'vm_state'):
        if e[k] != 'UNKNOWN':
            raise ValueError()
    if e['data_mutated'] is not False or e['inode_match'] not in ('YES', 'NO', 'UNKNOWN'):
        raise ValueError()
    if e['container_recreation_required'] not in ('YES', 'UNKNOWN'):
        raise ValueError()
    if e['status'] not in ('BLOCKED_CAPABILITY', 'BLOCKED_RUNTIME', 'RECREATE_REQUIRED'):
        raise ValueError()
    if e['BLOCKER'] not in ('resident_not_running', 'credential_inode_mismatch',
                           'effective_worker_configuration_and_full_server_witness_unproved'):
        raise ValueError()
    if e['NEXT_STEP'] not in ('review_runtime_failure_without_lifecycle_action',
                             'independent_review_and_complete_missing_observers_before_any_GO'):
        raise ValueError()
    observed = datetime.datetime.fromisoformat(e['observed_at'])
    if observed.utcoffset() is None or not started_at <= observed <= finished_at:
        raise ValueError()
    for k in ('observation_stable', 'observation_error'):
        if k in e and type(e[k]) is not bool:
            raise ValueError()
    for k in ('cpu_count', 'root_free_bytes'):
        if k in e and (type(e[k]) is not int or e[k] < 0):
            raise ValueError()
    if 'load' in e and (type(e['load']) is not list or len(e['load']) != 3 or
                       any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in e['load'])):
        raise ValueError()
    if 'memory_kib' in e:
        m = e['memory_kib']
        if type(m) is not dict or set(m) != {'MemTotal', 'MemAvailable'} or any(
                type(v) is not int or v < 0 for v in m.values()):
            raise ValueError()
    if 'service' in e:
        s = e['service']
        if type(s) is not dict or set(s) != {'ActiveState', 'SubState', 'NRestarts', 'MainPID'}:
            raise ValueError()
        if s['ActiveState'] not in ('active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading', 'maintenance'):
            raise ValueError()
        if s['SubState'] not in ('running', 'dead', 'failed', 'start', 'start-pre', 'start-post', 'stop', 'stop-sigterm', 'stop-sigkill', 'stop-post', 'auto-restart', 'exited', 'reload'):
            raise ValueError()
        if any(type(s[k]) is not str or not re.fullmatch('[0-9]{1,20}', s[k]) for k in ('NRestarts', 'MainPID')):
            raise ValueError()
    if 'container' in e:
        c = e['container']
        if type(c) is not dict or set(c) != {'Running', 'Restarting', 'ExitCode', 'OOMKilled', 'Pid'}:
            raise ValueError()
        if any(type(c[k]) is not bool for k in ('Running', 'Restarting', 'OOMKilled')):
            raise ValueError()
        if any(type(c[k]) is not int or c[k] < 0 for k in ('ExitCode', 'Pid')):
            raise ValueError()
    for k in ('host_device_inode', 'resident_device_inode'):
        if k in e and (type(e[k]) is not list or len(e[k]) != 2 or
                       any(type(v) is not int or v < 0 for v in e[k])):
            raise ValueError()
    if e.get('observation_stable') is True:
        if e.get('observation_error') or not all(k in e for k in
                ('host_device_inode', 'resident_device_inode', 'container', 'service', 'resident_mounted_credential_probe')):
            raise ValueError()
        expected = 'YES' if e['host_device_inode'] == e['resident_device_inode'] else 'NO'
        if e['inode_match'] != expected:
            raise ValueError()
    elif e['inode_match'] != 'UNKNOWN':
        raise ValueError()
    if 'resident_mounted_credential_probe' in e and e['resident_mounted_credential_probe'] != 'UNKNOWN':
        from witness import sanitize_probe
        sanitize_probe(json.dumps(e['resident_mounted_credential_probe']))
    # Never pass through the remote decision or any arbitrary text.
    return {k: e[k] for k in optional if k in e} | {
        'observed_at': observed.isoformat(), 'inode_match': e['inode_match']}


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def oci_summary(payload):
    """OCI CLI instance-get response; retain no arbitrary metadata."""
    unknown = {'vm_state': 'UNKNOWN', 'ocpus': None, 'memory_gib': None}
    if type(payload) is not dict or type(payload.get('data')) is not dict:
        return unknown
    data = payload['data']
    shape = data.get('shape-config')
    if data.get('id') != INSTANCE or type(shape) is not dict:
        return unknown
    state = data.get('lifecycle-state')
    if state not in ('RUNNING', 'STOPPED', 'STARTING', 'STOPPING', 'TERMINATED', 'TERMINATING', 'PROVISIONING'):
        return unknown
    cpu, ram = shape.get('ocpus'), shape.get('memory-in-gbs')
    if not positive(cpu) or not positive(ram):
        return unknown
    return {'vm_state': state, 'ocpus': cpu, 'memory_gib': ram}


def actions_summary(pages, self_identity=None):
    """Conservative all-repository active-run snapshot, no workflow exclusions.

    Each status must be fetched separately with per_page=100. A nonempty,
    fully enumerated response is BUSY, even if unrelated: never bypass a fence
    using an incomplete workflow classifier. Counts changing across requests
    do not constitute an atomic server-idleness proof.
    """
    if type(pages) is not dict or set(pages) != set(ACTIVE):
        return 'UNKNOWN'
    busy = False
    self_seen = 0
    for status in ACTIVE:
        page = pages[status]
        if type(page) is not dict:
            return 'UNKNOWN'
        count, runs = page.get('total_count'), page.get('workflow_runs')
        if type(count) is not int or count < 0 or type(runs) is not list:
            return 'UNKNOWN'
        if count != len(runs) or count >= 100:
            return 'UNKNOWN'
        ids = set()
        for run in runs:
            if type(run) is not dict or type(run.get('id')) is not int or run['id'] <= 0:
                return 'UNKNOWN'
            if run.get('status') != status or run['id'] in ids:
                return 'UNKNOWN'
            ids.add(run['id'])
            if self_identity is not None and run['id'] == self_identity['id']:
                if any(run.get(k) != v or type(run.get(k)) is not type(v)
                       for k, v in self_identity.items()):
                    return 'UNKNOWN'
                self_seen += 1
            else:
                busy = True
    if self_identity is not None and self_seen != 1:
        return 'UNKNOWN'
    return 'BUSY' if busy else 'IDLE'


def host_process_summary(records):
    """Known conflicts only, based on comm names, not cmdline/environment.

    Absence NEVER proves idleness: python workers and custom processes cannot
    safely be classified from comm alone. Full process inventory must come
    from an independently reviewed existing server interface.
    """
    if type(records) is not list:
        return 'UNKNOWN'
    known = {'ffmpeg', 'ffprobe', 'tesseract', 'dds', 'dds3'}
    for row in records:
        if type(row) is not dict or type(row.get('pid')) is not int or row['pid'] <= 0:
            return 'UNKNOWN'
        if type(row.get('comm')) is not str:
            return 'UNKNOWN'
        if row['comm'].lower() in known:
            return 'BUSY'
    return 'UNKNOWN'


def draft_report():
    """Never combine synthetic tests into a real operational PASS."""
    return dict(status='BLOCKED_CAPABILITY', resident_target='UNKNOWN',
                inode_match='UNKNOWN', container_recreation_required='UNKNOWN',
                PRECANARY_READY='UNKNOWN',
                BLOCKER='no_authorized_complete_resident_observation_interface',
                NEXT_STEP='review_complete_read_only_transport_contract',
                lifecycle_changes=False, media_runs=False, neon_changes=False)
