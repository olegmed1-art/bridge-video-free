"""Read-only attestation of the installed 5eb0e1bb stopped HOLD baseline."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

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
        proof = u['probe'](h, release, env)
        stable()
        require(u['service_state'](h['UNIT']) == before
            and read(h['ENV_PATH'], 0o600) == raw_env and read(h['UNIT_PATH'], 0o644) == unit
            and read(h['DROP'], 0o644) == u['desired_drop'](release), 'POST_PROBE_DRIFT')
        return {'installed_recovery_attestation': 'PASS', 'installed_revision': INSTALLED,
            'bundle_sha256': BUNDLE_SHA, 'admin_revision': packet['admin_revision'],
            'mailbox_pr': proof['mailbox_pr'], 'main_pid': 0, 'admission': 'HOLD',
            'route': 'neon_epoch_0', 'database_writes': False, 'service_started': False}


def packet(revision):
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
    return {'admin_revision': revision, 'helpers': helpers, 'bundle': bundle}


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'bundle':
        print('PACKET=' + repr(base64.b64encode(json.dumps(packet(sys.argv[2])).encode()).decode()))
        print(Path(__file__).read_text())
    else:
        try:
            print(json.dumps(attest(json.loads(base64.b64decode(PACKET)))))
        except Exception as exc:
            print(json.dumps({'installed_recovery_attestation': 'NOT_CONFIRMED',
                              'error_type': type(exc).__name__}))
            raise SystemExit(2) from None
