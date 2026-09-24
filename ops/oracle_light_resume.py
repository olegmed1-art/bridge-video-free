"""Guarded activation of the unchanged installed Light after completed canary."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import pwd
import signal
import time

INSTALLED = '5eb0e1bb2c2932bd8d02ff187b9cf24f6bc09c7c'
BUNDLE_SHA = '3f7736a4fa7a6796eb9fa7be10e1d01e6cd04cb803d426991c942a91f7eb1c07'


def attest(packet):
    loaded = {}
    for name, source in packet['helpers'].items():
        scope = {'__name__': 'reviewed_' + name}
        exec(compile(source, name, 'exec'), scope)
        loaded[name] = scope
    u, h, s = loaded['upgrade'], loaded['held'], loaded['stage']
    require = u['require']
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'HOST_IDENTITY')
    bundle = packet['bundle']
    require(bundle['revision'] == INSTALLED and bundle['sha256'] == BUNDLE_SHA,
            'INSTALLED_BUNDLE_IDENTITY')
    read = h['read_owned']
    release = s['RELEASES'] / INSTALLED
    route = h['ROUTE']
    read(route / 'route.lock', 0o644)
    with os.fdopen(os.open(route / 'route.lock', os.O_RDONLY | os.O_NOFOLLOW), 'rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(lock.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and stat.S_IMODE(info.st_mode) == 0o644, 'LOCK_METADATA')
        def stable():
            now = (route / 'route.lock').lstat()
            require((now.st_dev, now.st_ino) == (info.st_dev, info.st_ino)
                and json.loads(read(route / 'lock-identity.json', 0o644)) ==
                    {'device': info.st_dev, 'inode': info.st_ino}, 'ROUTE_LOCK_CHANGED')
            require(json.loads(read(route / 'route.json', 0o644)) ==
                {'version': 1, 'backend': 'neon', 'database': 'autopilot', 'epoch': 0}, 'ROUTE_CHANGED')
            s['require_current_main'](packet['admin_revision'])
            s['verify_release'](release, bundle)
        stable()
        before = u['service_state'](h['UNIT'])
        u['validate_stopped'](before, h, release, True)
        unit, raw_env = read(h['UNIT_PATH'], 0o644), read(h['ENV_PATH'], 0o600)
        require(hashlib.sha256(unit).hexdigest() == u['BASE_SHA'], 'BASE_UNIT_DRIFT')
        require(read(h['DROP'], 0o644) == u['desired_drop'](release), 'DROP_DRIFT')
        env = h['environment'](raw_env)
        require(not any(k in env for k in ('PYTHONPATH', 'PYTHONHOME')), 'IMPORT_OVERRIDE')
        require(env.get('AUTOPILOT_ADMISSION_MODE', 'HOLD') == 'HOLD', 'ENV_ADMISSION_DRIFT')
        pins = u['release_pins'](json.loads(bundle['files']['ops/autopilot/broker-release.json']), loaded['verifier'])
        override = h['environment'](read(release / u['EXTRA_ENV'], 0o444))
        require(override == pins, 'BROKER_OVERRIDE_DRIFT')
        env.update(override)
        env['AUTOPILOT_ADMISSION_MODE'] = 'HOLD'
        proof = probe(packet, h, release, env)
        stable()
        require(u['service_state'](h['UNIT']) == before
            and read(h['ENV_PATH'], 0o600) == raw_env and read(h['UNIT_PATH'], 0o644) == unit
            and read(h['DROP'], 0o644) == u['desired_drop'](release), 'POST_PROBE_DRIFT')
        require(packet['operation'] in ('preflight', 'activate'), 'OPERATION_INVALID')
        if packet['operation'] == 'activate':
            def unchanged():
                stable()
                require(read(h['ENV_PATH'], 0o600) == raw_env
                    and read(h['UNIT_PATH'], 0o644) == unit, 'BASE_CONFIG_DRIFT')
            result = activate(u, h, s, release, env, before, unchanged,
                              lambda: probe(packet, h, release, env))
        else:
            result = {'main_pid': 0, 'admission': 'HOLD', 'service_started': False}
        return {'light_resume': 'PASS', 'installed_revision': INSTALLED,
            'bundle_sha256': BUNDLE_SHA, 'admin_revision': packet['admin_revision'],
            'mailbox_pr': proof['mailbox_pr'], 'route': 'neon_epoch_0', **result}


def probe(packet, h, release, env):
    account = pwd.getpwnam('school-autopilot')
    child_env = {k: v for k, v in env.items() if k.startswith('AUTOPILOT_')}
    child_env.update(PATH='/usr/bin:/bin', PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
    def identity():
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    child = subprocess.run([h['PYTHON'], '-c', packet['probe_source']], cwd=release,
        env=child_env, preexec_fn=identity, capture_output=True, text=True, timeout=90)
    if child.returncode or len(child.stdout) > 4096:
        raise RuntimeError('POST_TERMINAL_PROBE_FAILED')
    result = json.loads(child.stdout)
    if not (result.get('status') == 'PASS' and result.get('stage') == 'complete'
        and result.get('mailbox_pr') == 1703
        and result.get('worker_id') == 'oracle-autopilot-light-1'
        and result.get('database_user') == 'autopilot_light_worker_login'
        and result.get('fence_sha256') == '44722551c605e775fefde91ad82f15fd08d713ad8903f65265435ead5567c36e'):
        raise RuntimeError('POST_TERMINAL_PROBE_INVALID')
    return result


def validate_active(u, h, release, state, before):
    # Reuse the complete loaded configuration validator, changing only the
    # three lifecycle fields and the one explicitly promoted admission value.
    checked = dict(state)
    checked.update(ActiveState='inactive', SubState='dead', MainPID='0')
    checked['Environment'] = ' '.join('AUTOPILOT_ADMISSION_MODE=HOLD'
        if x == 'AUTOPILOT_ADMISSION_MODE=ACTIVE' else x for x in state['Environment'].split())
    u['validate_stopped'](checked, h, release, True)
    u['require'](state['ActiveState'] == 'active' and state['SubState'] == 'running'
        and int(state['MainPID']) > 0 and state['InvocationID'] != before['InvocationID']
        and state['NRestarts'] == before['NRestarts']
        and state['Environment'].split().count('AUTOPILOT_ADMISSION_MODE=ACTIVE') == 1
        and 'AUTOPILOT_ADMISSION_MODE=HOLD' not in state['Environment'].split(), 'ACTIVE_STATE_DRIFT')


def activate(u, h, s, release, env, before, stable, fresh_probe):
    require, read = u['require'], h['read_owned']
    hold = u['desired_drop'](release)
    active = hold.replace(b'ADMISSION_MODE=HOLD', b'ADMISSION_MODE=ACTIVE')
    changed = False
    try:
        baseline = fresh_probe()
        stable()
        require(u['service_state'](h['UNIT']) == before and read(h['DROP'], 0o644) == hold,
                'PRE_START_DRIFT')
        changed = True
        u['replace_drop'](h['DROP'], hold, active, read, s['fsync_directory'])
        s['run']('systemctl', 'daemon-reload')
        s['run']('systemctl', 'start', h['UNIT'])
        initial = u['service_state'](h['UNIT'])
        validate_active(u, h, release, initial, before)
        # Observe a bounded 40-second startup window.
        for _ in range(20):
            time.sleep(2)
            require(u['service_state'](h['UNIT']) == initial, 'PROCESS_NOT_STABLE')
        pid = int(initial['MainPID'])
        process_env = s['process_environment'](pid)
        require(process_env.get('AUTOPILOT_ADMISSION_MODE') == 'ACTIVE'
            and process_env.get('AUTOPILOT_WORKER_ID') == 'oracle-autopilot-light-1'
            and not any(k in process_env for k in ('PYTHONPATH', 'PYTHONHOME'))
            and Path('/proc/' + str(pid) + '/cwd').resolve() == release, 'PROCESS_IDENTITY_DRIFT')
        require(all(process_env.get(k) == v for k, v in env.items()
                    if k.startswith('AUTOPILOT_') and k != 'AUTOPILOT_ADMISSION_MODE'), 'PROCESS_ENV_DRIFT')
        journal = s['run']('journalctl', '--no-pager', '-o', 'cat', '-u', h['UNIT'],
            '_SYSTEMD_INVOCATION_ID=' + initial['InvocationID'], '-n', '100')
        require('worker_started worker_id=oracle-autopilot-light-1' in journal
            and 'parallel_work_manifest_reconciled ' in journal
            and 'sha256=' + baseline['manifest_sha256'] in journal
            and 'replayed=True' in journal
            and not any(x in journal for x in ('ERROR', 'WARNING', 'Traceback', 'task_claimed ',
                                               'role_dispatch_published', 'project_work_probe_claimed')),
            'STARTUP_NOT_CLEAN')
        require(fresh_probe() == baseline, 'POST_PROBE_CHANGED')
        stable()
        require(u['service_state'](h['UNIT']) == initial and read(h['DROP'], 0o644) == active,
                'POST_START_DRIFT')
        return {'main_pid': pid, 'invocation_id': initial['InvocationID'], 'admission': 'ACTIVE',
                'service_started': True, 'restarts_delta': 0, 'soak_seconds': 40}
    except BaseException:
        if changed:
            # Stop this service even when verification failed; preserve all DB history.
            # Never overwrite a drop-in changed by another actor.
            s['run']('systemctl', 'stop', h['UNIT'])
            state = u['service_state'](h['UNIT'])
            require(state['MainPID'] == '0' and state['ActiveState'] == 'inactive', 'ROLLBACK_STOP_FAILED')
            present = read(h['DROP'], 0o644)
            require(present in (hold, active), 'ROLLBACK_DROP_DRIFT')
            if present == active:
                u['replace_drop'](h['DROP'], active, hold, read, s['fsync_directory'])
            s['run']('systemctl', 'daemon-reload')
            u['validate_stopped'](u['service_state'](h['UNIT']), h, release, True)
            print(json.dumps({'rollback': 'STOPPED_HOLD', 'main_pid': 0}))
        raise


def packet(revision, operation):
    root = Path(__file__).parent
    names = {'upgrade': 'oracle_light_stopped_hold_upgrade.py',
        'held': 'oracle_light_held_runtime_preflight.py', 'stage': 'oracle_light_runtime_hold_install.py',
        'verifier': 'verify_light_broker_1703_release.py'}
    helpers = {k: (root / v).read_text() for k, v in names.items()}
    def git(*args):
        return subprocess.run(['git', *args], check=True, capture_output=True, text=True).stdout
    paths = git('ls-tree', '-r', '--name-only', INSTALLED, 'oracle_autopilot', 'autopilot_phase3b').splitlines()
    paths = [p for p in paths if p.count('/') == 1 and p.endswith('.py')]
    paths.append('ops/autopilot/broker-release.json')
    files = {p: git('show', INSTALLED + ':' + p) for p in paths}
    from oracle_light_stopped_hold_upgrade import augment_bundle, PIN_KEYS
    from oracle_light_held_runtime_preflight import HOLD
    manifest = json.loads(files['ops/autopilot/broker-release.json'])
    bundle = augment_bundle({'revision': INSTALLED, 'files': files},
        {k: manifest[v] for k, v in PIN_KEYS.items()}, HOLD.encode())
    if bundle['sha256'] != BUNDLE_SHA:
        raise ValueError('INSTALLED_SOURCE_MISMATCH')
    return {'admin_revision': revision, 'helpers': helpers, 'bundle': bundle,
            'operation': operation, 'probe_source': (root / 'oracle_light_resume_probe.py').read_text()}


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'bundle' and sys.argv[3] in ('preflight', 'activate'):
        print('PACKET=' + repr(base64.b64encode(json.dumps(packet(sys.argv[2], sys.argv[3])).encode()).decode()))
        print(Path(__file__).read_text())
    else:
        def interrupted(signum, frame):
            raise RuntimeError('INTERRUPTED')
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGHUP, interrupted)
        try:
            print(json.dumps(attest(json.loads(base64.b64decode(PACKET)))))
        except Exception as exc:
            print(json.dumps({'light_resume': 'NOT_CONFIRMED',
                              'error_type': type(exc).__name__}))
            raise SystemExit(2) from None
