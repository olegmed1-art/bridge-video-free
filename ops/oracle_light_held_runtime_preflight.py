"""Attest the installed Light HOLD release without changing its unit or queue."""
import base64
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

UNIT = 'school-autopilot-production-light.service'
UNIT_PATH = Path('/etc/systemd/system') / UNIT
DROP = Path('/etc/systemd/system/school-autopilot-production-light.service.d/40-reviewed-runtime-hold.conf')
ENV_PATH = Path('/etc/school-autopilot-production-light.env')
ROUTE = Path('/var/lib/bridge-autopilot-tunnel')
PYTHON = '/opt/bridge-school/school-autopilot/.venv/bin/python'
REVISION = '3244f4d4b17ce99e58c342f01e4715436a09622b'
BUNDLE_SHA256 = 'b15ca186c56dcd9c4ff5fbf05df6a9d294d9ddbf1787683e36c90305f4175c5a'
FENCE_SHA256 = '655fa30ce165663fb0de1b98bb3bba85237fefc6900a507e4a88edced9b0b11e'
RELEASE = Path('/opt/bridge-school/school-autopilot-production-light/releases') / REVISION
HOLD = '[Service]\nWorkingDirectory=' + str(RELEASE) + '\nEnvironment=AUTOPILOT_ADMISSION_MODE=HOLD\n'


class Blocked(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise Blocked(code)


def read_owned(path, mode, limit=1048576):
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'UNTRUSTED_PARENT')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                stat.S_IMODE(info.st_mode) == mode and info.st_size <= limit, 'UNTRUSTED_FILE')
        content = stream.read(limit + 1)
        require(len(content) <= limit, 'FILE_TOO_LARGE')
        return content


def environment(raw):
    result = {}
    for line in raw.decode().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        require(separator and re.fullmatch('[A-Z][A-Z0-9_]*', key) and key not in result,
                'ENV_SYNTAX')
        tokens = shlex.split(value, comments=False, posix=True)
        require(len(tokens) == 1, 'ENV_SYNTAX')
        result[key] = tokens[0]
    return result


def service():
    keys = ('ActiveState', 'SubState', 'MainPID', 'NRestarts', 'InvocationID',
            'WorkingDirectory', 'User', 'Group', 'DropInPaths', 'FragmentPath',
            'ExecStart', 'EnvironmentFiles', 'Environment', 'NeedDaemonReload')
    output = subprocess.run(['systemctl', 'show', UNIT, *['--property=' + key for key in keys]],
                            check=True, capture_output=True, text=True, timeout=10).stdout
    return dict(line.split('=', 1) for line in output.splitlines())


def validate_service(value):
    require(value['ActiveState'] == 'active' and value['SubState'] == 'running' and
            int(value['MainPID']) > 0 and re.fullmatch('[a-f0-9]{32}', value['InvocationID']),
            'SERVICE_NOT_STABLE')
    require(value['WorkingDirectory'] == str(RELEASE) and value['User'] == 'school-autopilot'
            and value['Group'] == 'school-autopilot' and value['DropInPaths'] == str(DROP)
            and value['FragmentPath'] == str(UNIT_PATH), 'SERVICE_DRIFT')
    require('path=' + PYTHON + ' ;' in value['ExecStart'] and
            'argv[]=' + PYTHON + ' -m oracle_autopilot.worker_v17 ;' in value['ExecStart']
            and value['EnvironmentFiles'] == str(ENV_PATH) + ' (ignore_errors=no)',
            'SERVICE_EXEC_DRIFT')
    require('AUTOPILOT_ADMISSION_MODE=HOLD' in value['Environment'].split(), 'HOLD_NOT_LOADED')


def digest(bundle):
    canonical = json.dumps({'revision': bundle['revision'], 'files': bundle['files']},
                           sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(canonical).hexdigest()


def validate_bundle(bundle):
    require(set(bundle) == {'revision', 'files', 'sha256'} and bundle['revision'] == REVISION,
            'SOURCE_REVISION')
    require(bundle['sha256'] == BUNDLE_SHA256 and digest(bundle) == BUNDLE_SHA256,
            'BUNDLE_DIGEST')
    files = bundle['files']
    require(1 <= len(files) <= 80 and
            {'oracle_autopilot/worker.py', 'oracle_autopilot/worker_v17.py',
             'oracle_autopilot/contract.py', 'oracle_autopilot/light_runtime_probe.py',
             'ops/autopilot/broker-release.json'} <= files.keys(), 'BUNDLE_INCOMPLETE')
    for name, content in files.items():
        require(re.fullmatch(r'(?:oracle_autopilot|autopilot_phase3b)/[a-z0-9_]+\.py', name)
                or name == 'ops/autopilot/broker-release.json', 'BUNDLE_PATH')
        require(isinstance(content, str) and len(content) <= 600000, 'BUNDLE_FILE_SIZE')


def source_bundle():
    """Use immutable Git tree contents, never files from a changed checkout."""
    names = subprocess.run(['git', 'ls-tree', '-r', '--name-only', REVISION,
                            'oracle_autopilot', 'autopilot_phase3b'],
                           check=True, capture_output=True, text=True, timeout=15).stdout.splitlines()
    names = [name for name in names if name.endswith('.py')]
    names.append('ops/autopilot/broker-release.json')
    files = {}
    for name in names:
        require(re.fullmatch(r'(?:oracle_autopilot|autopilot_phase3b)/[a-z0-9_]+\.py', name)
                or name == 'ops/autopilot/broker-release.json', 'BUNDLE_PATH')
        files[name] = subprocess.run(['git', 'show', REVISION + ':' + name],
                                     check=True, capture_output=True, timeout=15).stdout.decode()
    bundle = {'revision': REVISION, 'files': files}
    bundle['sha256'] = digest(bundle)
    validate_bundle(bundle)
    return bundle


def verify_release(bundle):
    for parent in RELEASE.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
                not info.st_mode & 0o022, 'RELEASE_PARENT')
    expected = {**bundle['files'], 'SOURCE_REVISION': REVISION + '\n',
                'RUNTIME_BUNDLE_SHA256': BUNDLE_SHA256 + '\n'}
    directories = {'.'}
    for name in expected:
        directories.update(str(parent) for parent in Path(name).parents)
    entries = {item.relative_to(RELEASE).as_posix(): item for item in RELEASE.rglob('*')}
    require(set(entries) == set(expected) | (directories - {'.'}), 'RELEASE_INVENTORY_DRIFT')
    for name, path in {'.': RELEASE, **entries}.items():
        info = path.lstat()
        require(info.st_uid == 0 and not stat.S_ISLNK(info.st_mode), 'RELEASE_OWNER_OR_LINK')
        if name in directories:
            require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o755,
                    'RELEASE_DIRECTORY_MODE')
        else:
            require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o444
                    and path.read_text() == expected[name], 'RELEASE_FILE_DRIFT')


def process_environment(pid):
    return dict(part.decode().split('=', 1) for part in
                Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if b'=' in part)


def validate_live_environment(live, expected):
    require(not any(key in live or key in expected for key in
                    ('PYTHONPATH', 'PYTHONHOME')), 'PYTHON_IMPORT_OVERRIDE')
    require(all(live.get(key) == value for key, value in expected.items())
            and live.get('AUTOPILOT_ADMISSION_MODE') == 'HOLD', 'LIVE_ENV_DRIFT')
    require(live.get('AUTOPILOT_WORKER_ID') == 'oracle-autopilot-light-1' and
            live.get('AUTOPILOT_DB_BACKEND', 'neon') == 'neon', 'WORKER_IDENTITY')


def validate_proof(proof, exit_code):
    require(exit_code == 0 and proof.get('status') == 'PASS' and
            proof.get('worker_id') == 'oracle-autopilot-light-1' and
            proof.get('mailbox_pr') == 1703 and
            proof.get('fence_sha256') == FENCE_SHA256, 'PREFLIGHT_NOT_PASSED')


def run(bundle):
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic',
            'HOST_IDENTITY')
    validate_bundle(bundle)
    verify_release(bundle)
    lock_path = ROUTE / 'route.lock'
    read_owned(lock_path, 0o644)
    with os.fdopen(os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as lock:
        info = os.fstat(lock.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                stat.S_IMODE(info.st_mode) == 0o644, 'LOCK_METADATA')
        identity = json.loads(read_owned(ROUTE / 'lock-identity.json', 0o644))
        require(identity == {'device': info.st_dev, 'inode': info.st_ino}, 'LOCK_IDENTITY')
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        route = read_owned(ROUTE / 'route.json', 0o644)
        require(json.loads(route) == {'version': 1, 'backend': 'neon',
                                      'database': 'autopilot', 'epoch': 0}, 'ROUTE_CHANGED')
        before = service()
        validate_service(before)
        unit = read_owned(UNIT_PATH, 0o644)
        drop = read_owned(DROP, 0o644)
        require(drop.decode() == HOLD, 'HOLD_DROPIN_DRIFT')
        raw_env = read_owned(ENV_PATH, 0o600)
        expected_env = environment(raw_env)
        pid = int(before['MainPID'])
        live = process_environment(pid)
        validate_live_environment(live, expected_env)
        account = pwd.getpwnam('school-autopilot')
        require(Path(f'/proc/{pid}').stat().st_uid == account.pw_uid and
                Path(f'/proc/{pid}/cwd').resolve() == RELEASE, 'LIVE_CODE_OR_UID_DRIFT')
        env = {key: value for key, value in live.items() if key.startswith('AUTOPILOT_')}
        env.update(PATH='/usr/bin:/bin', PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')

        def identity_only():
            os.setgroups([])
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)

        child = subprocess.run([PYTHON, '-m', 'oracle_autopilot.light_runtime_probe'],
                               cwd=RELEASE, env=env, preexec_fn=identity_only,
                               capture_output=True, text=True, timeout=90)
        require(len(child.stdout) <= 4096, 'PROBE_OUTPUT_SIZE')
        try:
            proof = json.loads(child.stdout)
        except ValueError:
            proof = {'status': 'FAIL', 'stage': 'probe_process', 'error_type': 'InvalidOutput'}
        allowed = {'status', 'stage', 'error_type', 'mailbox_pr', 'worker_id',
                   'database_user', 'fence_sha256', 'manifest_sha256'}
        require(isinstance(proof, dict) and set(proof) <= allowed and
                all(isinstance(value, (str, int)) and
                    re.fullmatch('[a-zA-Z0-9_-]{1,128}', str(value))
                    for value in proof.values()), 'PROBE_OUTPUT_FIELDS')
        require(service() == before and read_owned(UNIT_PATH, 0o644) == unit and
                read_owned(DROP, 0o644) == drop and read_owned(ENV_PATH, 0o600) == raw_env and
                read_owned(ROUTE / 'route.json', 0o644) == route and
                json.loads(read_owned(ROUTE / 'lock-identity.json', 0o644)) == identity and
                (lock_path.lstat().st_dev, lock_path.lstat().st_ino) ==
                (info.st_dev, info.st_ino), 'RUNTIME_CHANGED_DURING_PREFLIGHT')
        verify_release(bundle)
        validate_proof(proof, child.returncode)
        result = {'preflight': proof, 'source_revision': REVISION,
                  'bundle_sha256': BUNDLE_SHA256, 'invocation_id': before['InvocationID'],
                  'need_daemon_reload': before['NeedDaemonReload'],
                  'unit_unchanged': True, 'route': 'neon_epoch_0',
                  'service_restarted': False, 'database_writes': False}
        print(json.dumps(result))


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == 'bundle':
        print('BUNDLE_DATA=' + repr(base64.b64encode(json.dumps(source_bundle()).encode()).decode()))
        print(Path(__file__).read_text())
    else:
        try:
            run(json.loads(base64.b64decode(BUNDLE_DATA)))
        except Exception as exc:
            print(json.dumps({'held_runtime_preflight': 'NOT_CONFIRMED',
                              'error_type': type(exc).__name__,
                              'guard': str(exc) if type(exc) is Blocked else 'SYSTEM_ERROR'}))
            raise SystemExit(2) from None
