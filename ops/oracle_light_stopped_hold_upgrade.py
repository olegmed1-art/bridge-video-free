"""Upgrade the exact stopped Light HOLD baseline; never start or claim work.

An immutable release-local EnvironmentFile overrides only five non-secret
broker pins. One atomic drop-in switch binds code and pins together. The
original service EnvironmentFile (including credentials) is never modified.
"""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import subprocess
import sys

PIN_KEYS = {
    'AUTOPILOT_TOKEN_BROKER_URL': 'broker_url',
    'AUTOPILOT_TOKEN_BROKER_EXPECTED_SOURCE_SHA': 'broker_source_sha',
    'AUTOPILOT_TOKEN_BROKER_EXPECTED_ARTIFACT_SHA256': 'broker_artifact_sha256',
    'AUTOPILOT_TOKEN_BROKER_EXPECTED_POLICY_SHA256': 'broker_policy_sha256',
    'AUTOPILOT_TOKEN_BROKER_EXPECTED_PROVENANCE_SHA256': 'broker_provenance_sha256',
}
BASE_SHA = 'e8d22d57089b9c43b1d65d78de7e022c2b2124357cf40e98728128d2980ee2b4'
BROKER_URL = 'https://bridge-school-autopilot-c7a2oah04-olegmed1-4368s-projects.vercel.app/v1/github/draft-repair'
FENCE_SHA = '44722551c605e775fefde91ad82f15fd08d713ad8903f65265435ead5567c36e'
EXTRA_ENV = 'ops/autopilot/broker-hold.env'
BACKUP = 'ops/autopilot/previous-hold.conf'


class Blocked(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise Blocked(code)


def helpers(sources):
    loaded = {}
    for name, source in sources.items():
        namespace = {'__name__': 'reviewed_' + name}
        exec(compile(source, name, 'exec'), namespace)
        loaded[name] = namespace
    return loaded


def release_pins(release, verifier):
    # Reuse the independently tested health contract's constants, not arbitrary
    # endpoint metadata from a caller. Live health is separately probed below.
    require(release == {'schema_version': 1, 'broker_url': BROKER_URL,
        'broker_source_sha': verifier['SOURCE'], 'broker_artifact_sha256': verifier['ARTIFACT'],
        'broker_policy_sha256': verifier['POLICY'], 'broker_policy_version': 'physical-no-merge-v2',
        'broker_provenance_sha256': verifier['PROVENANCE']}, 'BROKER_RELEASE_DRIFT')
    return {key: release[value] for key, value in PIN_KEYS.items()}


def augment_bundle(bundle, pins, old_drop):
    value = {'revision': bundle['revision'], 'files': dict(bundle['files'])}
    value['files'][EXTRA_ENV] = ''.join(key + '=' + pins[key] + '\n' for key in sorted(pins))
    value['files'][BACKUP] = old_drop.decode()
    value['sha256'] = hashlib.sha256(json.dumps(value, sort_keys=True,
                                               separators=(',', ':')).encode()).hexdigest()
    return value


def desired_drop(release):
    return ('[Service]\nWorkingDirectory=' + str(release) +
            '\nEnvironment=AUTOPILOT_ADMISSION_MODE=HOLD\nEnvironmentFile=' +
            str(release / EXTRA_ENV) + '\n').encode()


def validate_stopped(state, h, release, overridden=False):
    require(state['ActiveState'] == 'inactive' and state['SubState'] == 'dead'
            and state['MainPID'] == '0' and state['NeedDaemonReload'] == 'no', 'NOT_STOPPED')
    require(state['WorkingDirectory'] == str(release)
            and state['User'] == state['Group'] == 'school-autopilot'
            and state['FragmentPath'] == str(h['UNIT_PATH'])
            and state['DropInPaths'] == str(h['DROP']), 'UNIT_DRIFT')
    require(state['Environment'].split().count('AUTOPILOT_ADMISSION_MODE=HOLD') == 1
            and not any(v.startswith('AUTOPILOT_ADMISSION_MODE=') and v != 'AUTOPILOT_ADMISSION_MODE=HOLD'
                        for v in state['Environment'].split()), 'HOLD_DRIFT')
    expected_env = str(h['ENV_PATH']) + ' (ignore_errors=no)'
    if overridden:
        expected_env += ' ' + str(release / EXTRA_ENV) + ' (ignore_errors=no)'
    require(state['EnvironmentFiles'] == expected_env, 'ENVIRONMENT_FILES_DRIFT')
    require('path=' + h['PYTHON'] + ' ;' in state['ExecStart'] and
            'argv[]=' + h['PYTHON'] + ' -m oracle_autopilot.worker_v17 ;' in state['ExecStart'],
            'EXEC_START_DRIFT')


def probe(h, release, env):
    account = pwd.getpwnam('school-autopilot')
    child_env = {key: value for key, value in env.items() if key.startswith('AUTOPILOT_')}
    child_env.update(PATH='/usr/bin:/bin', PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
    def identity():
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    child = subprocess.run([h['PYTHON'], '-m', 'oracle_autopilot.light_recovery_probe'],
        cwd=release, env=child_env, preexec_fn=identity, capture_output=True, text=True, timeout=90)
    require(len(child.stdout) <= 4096, 'PROBE_OUTPUT_SIZE')
    try:
        result = json.loads(child.stdout)
    except ValueError:
        raise Blocked('PROBE_OUTPUT_INVALID') from None
    require(child.returncode == 0 and result.get('status') == 'PASS'
            and result.get('stage') == 'complete' and result.get('mailbox_pr') == 1703
            and result.get('worker_id') == 'oracle-autopilot-light-1'
            and result.get('database_user') == 'autopilot_light_worker_login'
            and result.get('fence_sha256') == FENCE_SHA, 'COMPATIBILITY_NOT_CONFIRMED')
    return result


def replace_drop(path, expected, new, read, sync):
    require(read(path, 0o644) == expected, 'DROP_COMPARE_FAILED')
    temp = path.parent / '.stopped-hold-upgrade.tmp'
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(new)
            stream.flush()
            os.fsync(stream.fileno())
        require(read(path, 0o644) == expected, 'DROP_COMPARE_FAILED')
        os.replace(temp, path)
        sync(path.parent)
    finally:
        if temp.exists():
            temp.unlink()


def restore_stopped(h, staging, release, old_drop):
    state = h['service']()
    require(state['MainPID'] == '0' and state['ActiveState'] == 'inactive', 'ROLLBACK_NOT_STOPPED')
    present = h['read_owned'](h['DROP'], 0o644)
    new_drop = desired_drop(release)
    require(present in (old_drop, new_drop), 'ROLLBACK_DROP_DRIFT')
    if present == new_drop:
        replace_drop(h['DROP'], new_drop, old_drop, h['read_owned'], staging['fsync_directory'])
    staging['run']('systemctl', 'daemon-reload')
    validate_stopped(h['service'](), h, h['RELEASE'])
    print(json.dumps({'rollback': 'PREVIOUS_STOPPED_HOLD', 'main_pid': 0}))


def switch(h, staging, release, env, before, old_drop, stable):
    """The only mutating boundary, separately fault-injected in tests."""
    new_drop = desired_drop(release)
    read = h['read_owned']
    changed = False
    try:
        stable()
        require(h['service']() == before, 'PRE_SWITCH_SERVICE_DRIFT')
        changed = True  # replacement may succeed before a later fsync raises
        replace_drop(h['DROP'], old_drop, new_drop, read, staging['fsync_directory'])
        staging['run']('systemctl', 'daemon-reload')
        after = h['service']()
        validate_stopped(after, h, release, True)
        require(after['NRestarts'] == before['NRestarts'] and
                after['InvocationID'] == before['InvocationID'], 'INVOCATION_CHANGED')
        require(staging['execution_contract'](after['ExecStart']) ==
                staging['execution_contract'](before['ExecStart']), 'EXECUTION_CHANGED')
        stable()
        proof = probe(h, release, env)
        require(h['service']() == after and read(h['DROP'], 0o644) == new_drop, 'POST_SWITCH_DRIFT')
        stable()
        return proof
    except BaseException:
        if changed:
            # Never start the old or new worker. Do not overwrite unrelated drift.
            restore_stopped(h, staging, release, old_drop)
        raise


def install(packet):
    loaded = helpers(packet['helpers'])
    h, staging, candidate = loaded['held'], loaded['stage'], loaded['candidate']
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'HOST_IDENTITY')
    h['validate_bundle'](packet['old_bundle'])
    h['verify_release'](packet['old_bundle'])
    candidate['validate_bundle'](packet['bundle'])
    release_meta = json.loads(packet['bundle']['files']['ops/autopilot/broker-release.json'])
    pins = release_pins(release_meta, loaded['verifier'])
    read = h['read_owned']
    route = h['ROUTE']
    lock_bytes = read(route / 'route.lock', 0o644)
    require(len(lock_bytes) <= 1048576, 'LOCK_SIZE')
    with os.fdopen(os.open(route / 'route.lock', os.O_RDONLY | os.O_NOFOLLOW), 'rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(lock.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and stat.S_IMODE(info.st_mode) == 0o644, 'LOCK_METADATA')
        identity = {'device': info.st_dev, 'inode': info.st_ino}
        def same_route():
            current = (route / 'route.lock').lstat()
            require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino)
                    and json.loads(read(route / 'lock-identity.json', 0o644)) == identity,
                    'ROUTE_LOCK_CHANGED')
            require(json.loads(read(route / 'route.json', 0o644)) ==
                    {'version': 1, 'backend': 'neon', 'database': 'autopilot', 'epoch': 0}, 'ROUTE_CHANGED')
        same_route()
        require(packet.get('operation') in ('upgrade', 'rollback'), 'OPERATION_INVALID')
        before = h['service']()
        revision = packet['bundle']['revision']
        release = staging['RELEASES'] / revision
        validate_stopped(before, h, release if packet['operation'] == 'rollback' else h['RELEASE'],
                         packet['operation'] == 'rollback')
        unit, raw_env = read(h['UNIT_PATH'], 0o644), read(h['ENV_PATH'], 0o600)
        require(hashlib.sha256(unit).hexdigest() == BASE_SHA, 'BASE_UNIT_DRIFT')
        old_drop = h['HOLD'].encode()
        require(read(h['DROP'], 0o644) ==
                (desired_drop(release) if packet['operation'] == 'rollback' else old_drop), 'BASE_DROP_DRIFT')
        environment = h['environment'](raw_env)
        old_manifest = json.loads(packet['old_bundle']['files']['ops/autopilot/broker-release.json'])
        require(all(environment.get(key) == old_manifest[value] for key, value in PIN_KEYS.items()), 'OLD_BROKER_ENV_DRIFT')
        require(not any(key in environment for key in ('PYTHONPATH', 'PYTHONHOME')), 'IMPORT_OVERRIDE')
        require(environment.get('AUTOPILOT_ADMISSION_MODE', 'HOLD') == 'HOLD', 'ENV_ADMISSION_DRIFT')
        environment.update(pins)
        environment['AUTOPILOT_ADMISSION_MODE'] = 'HOLD'
        staging['require_current_main'](revision)
        bundle = augment_bundle(packet['bundle'], pins, old_drop)
        if packet['operation'] == 'rollback':
            staging['verify_release'](release, bundle)
            same_route()
            require(h['service']() == before and read(h['UNIT_PATH'], 0o644) == unit
                    and read(h['ENV_PATH'], 0o600) == raw_env, 'ROLLBACK_BASE_CHANGED')
            restore_stopped(h, staging, release, old_drop)
            same_route()
            require(read(h['ENV_PATH'], 0o600) == raw_env and read(h['UNIT_PATH'], 0o644) == unit,
                    'ROLLBACK_BASE_CHANGED')
            return
        release = staging['stage'](bundle)
        def stable():
            same_route()
            staging['require_current_main'](revision)
            require(read(h['UNIT_PATH'], 0o644) == unit and read(h['ENV_PATH'], 0o600) == raw_env,
                    'BASE_CONFIG_CHANGED')
            staging['verify_release'](release, bundle)
        stable()
        probe(h, release, environment)
        proof = switch(h, staging, release, environment, before, old_drop, stable)
        print(json.dumps({'stopped_hold_upgrade': 'PASS', 'source_revision': revision,
            'bundle_sha256': bundle['sha256'], 'previous_revision': h['REVISION'],
            'mailbox_pr': proof['mailbox_pr'], 'main_pid': 0, 'admission': 'HOLD',
            'service_started': False, 'database_writes': False, 'route': 'neon_epoch_0'}))


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'bundle' and sys.argv[3] in ('upgrade', 'rollback'):
        import oracle_light_runtime_preflight as candidate
        import oracle_light_held_runtime_preflight as held
        root = Path(__file__).parent
        sources = {name: (root / filename).read_text() for name, filename in {
            'held': 'oracle_light_held_runtime_preflight.py',
            'stage': 'oracle_light_runtime_hold_install.py',
            'candidate': 'oracle_light_runtime_preflight.py',
            'verifier': 'verify_light_broker_1703_release.py'}.items()}
        packet = {'helpers': sources, 'old_bundle': held.source_bundle(), 'operation': sys.argv[3],
                  'bundle': candidate.bundle(sys.argv[2])}
        print('PACKET=' + repr(base64.b64encode(json.dumps(packet).encode()).decode()))
        print(Path(__file__).read_text())
    else:
        def interrupted(signum, frame):
            raise Blocked('INTERRUPTED')
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGHUP, interrupted)
        try:
            install(json.loads(base64.b64decode(PACKET)))
        except BaseException as exc:
            print(json.dumps({'stopped_hold_upgrade': 'NOT_CONFIRMED',
                'error_type': type(exc).__name__,
                'guard': str(exc) if type(exc) is Blocked else 'SYSTEM_ERROR'}))
            raise SystemExit(2) from None
