"""Versioned Native control sources and create-only, credential-free history.

This stages code only. It does not install a unit, accept a manifest/window,
start a process, touch an existing release, or grant ISSUE/RUN authority. A
qualified digest is supplied independently; a source label alone is not enough.
All paths are fixed. Historical code stays separate from mutable service data.
"""
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess


ROOT = Path('/opt/bridge-school/light-native-host')
RELEASES = ROOT/'releases'
HISTORY = ROOT/'history'
FILES = ('ops/light_native_service_authority.py',
         'ops/light_native_service_supervisor.py',
         'ops/light_native_service_unit.py',
         'ops/light_native_lane_controller.py',
         'ops/light_native_lane_cycle.py',
         'ops/light_native_lane_issuer.py',
         'ops/light_native_lane_execution.py',
         'ops/light_native_service_bootstrap.py',
         'ops/light_native_service_history.py',
         'ops/light_native_service_install.py',
         'ops/native_maintenance_run_guard.py',
         'ops/native_maintenance_budgets.py',
         'ops/native_maintenance_lifetime.py',
         'ops/native_maintenance_workflow_pause.py')
LIMIT = 2*1024*1024


class Refusal(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise Refusal(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def parse(raw):
    require(type(raw) is bytes and 0 < len(raw) <= LIMIT, 'NATIVE_HISTORY_SIZE')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'NATIVE_HISTORY_RECORD')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: require(False, 'NATIVE_HISTORY_RECORD'))
        require(encoded(value) == raw, 'NATIVE_HISTORY_RECORD')
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Refusal('NATIVE_HISTORY_RECORD') from None


def package(raw, accepted):
    require(type(accepted) is str and re.fullmatch('[0-9a-f]{64}', accepted)
            and type(raw) is bytes and sha(raw) == accepted, 'NATIVE_HISTORY_NOT_QUALIFIED')
    value = parse(raw)
    require(type(value) is dict and set(value) == {'version', 'kind', 'source', 'files'}
            and type(value['version']) is int and value['version'] == 1
            and value['kind'] == 'LIGHT_NATIVE_CONTROL_SOURCE'
            and type(value['source']) is str and re.fullmatch('[0-9a-f]{40}', value['source'])
            and type(value['files']) is dict and set(value['files']) == set(FILES),
            'NATIVE_HISTORY_PACKAGE')
    files = {}
    for name in FILES:
        item = value['files'][name]
        require(type(item) is dict and set(item) == {'sha256', 'base64'}
                and type(item['base64']) is str, 'NATIVE_HISTORY_PACKAGE')
        try:
            data = base64.b64decode(item['base64'], validate=True)
        except (ValueError, TypeError):
            raise Refusal('NATIVE_HISTORY_PACKAGE') from None
        require(0 < len(data) <= 262144 and sha(data) == item['sha256']
                and base64.b64encode(data).decode('ascii') == item['base64']
                and b'\x00' not in data, 'NATIVE_HISTORY_PACKAGE')
        files[name] = data
    return value['source'], files


def build(repository, source):
    """Read exact Git objects, never the worktree or a newest-candidate pointer."""
    require(type(source) is str and re.fullmatch('[0-9a-f]{40}', source), 'NATIVE_HISTORY_SOURCE')
    repository = Path(repository)
    actual = subprocess.check_output(['git', 'rev-parse', '--verify', source+'^{commit}'],
                                     cwd=repository, timeout=10).decode('ascii').strip()
    require(actual == source, 'NATIVE_HISTORY_SOURCE')
    files = {}
    for name in FILES:
        raw = subprocess.check_output(['git', 'show', source+':'+name], cwd=repository, timeout=10)
        files[name] = dict(sha256=sha(raw), base64=base64.b64encode(raw).decode('ascii'))
    raw = encoded(dict(version=1, kind='LIGHT_NATIVE_CONTROL_SOURCE', source=source, files=files))
    package(raw, sha(raw))
    return raw


def signature(row):
    return tuple(getattr(row, name) for name in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
        'st_gid', 'st_nlink', 'st_size', 'st_atime_ns', 'st_mtime_ns', 'st_ctime_ns'))


def directory(path, *, private=False):
    require(os.name == 'posix' and os.geteuid() == 0 and path.is_absolute(), 'NATIVE_HISTORY_ROOT')
    for item in (path, *path.parents):
        row = item.lstat()
        require(stat.S_ISDIR(row.st_mode) and row.st_uid == 0 and not row.st_mode & 0o022,
                'NATIVE_HISTORY_PARENT')
    if private:
        require(path.lstat().st_gid == 0 and stat.S_IMODE(path.lstat().st_mode) == 0o700,
                'NATIVE_HISTORY_PRIVATE')


def read(path, *, limit=LIMIT):
    directory(path.parent, private=True)
    flag = getattr(os, 'O_NOATIME', None)
    require(type(flag) is int and flag > 0, 'NATIVE_HISTORY_NOATIME')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | flag)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
                and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600
                and 0 < before.st_size <= limit, 'NATIVE_HISTORY_FILE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit+1)
        require(len(raw) <= limit and signature(before) == signature(os.fstat(fd))
                == signature(path.stat(follow_symlinks=False)), 'NATIVE_HISTORY_DRIFT')
        return raw
    finally:
        os.close(fd)


def fsync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def new_directory(path):
    directory(path.parent, private=path.parent in (ROOT, RELEASES, HISTORY))
    os.mkdir(path, mode=0o700)
    fsync(path.parent)
    directory(path, private=True)


def retain(path, raw):
    directory(path.parent, private=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fsync(path.parent)
    require(read(path) == raw, 'NATIVE_HISTORY_READBACK')


def entries():
    directory(RELEASES, private=True)
    names = sorted(path.name for path in RELEASES.iterdir())
    require(len(names) <= 128 and all(re.fullmatch('[0-9a-f]{40}', name) for name in names),
            'NATIVE_HISTORY_RELEASES')
    result = []
    for name in names:
        path = RELEASES/name
        directory(path, private=True)
        require({item.name for item in path.iterdir()} == {'package.json', 'staged.json'},
                'NATIVE_HISTORY_PARTIAL_STAGING')
        raw = read(path/'package.json')
        record = parse(read(path/'staged.json', limit=32768))
        require(type(record) is dict and set(record) == {'version', 'kind', 'source',
                'package_sha256', 'staged_at', 'history_sha256'}
                and type(record['version']) is int and record['version'] == 1
                and record['kind'] == 'LIGHT_NATIVE_CONTROL_SOURCE_STAGED'
                and record['source'] == name and package(raw, record['package_sha256'])[0] == name
                and re.fullmatch('[0-9a-f]{64}', record['history_sha256'])
                and timestamp(record['staged_at']), 'NATIVE_HISTORY_STAGING')
        result.append(dict(source=name, package_sha256=sha(raw),
                           staged_at=record['staged_at'], history_sha256=record['history_sha256']))
    return result


def timestamp(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value):
        return False
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp() > 0
    except ValueError:
        return False


def history_gate(versions):
    """No partial or unacknowledged historical write can be bypassed by a new ID."""
    directory(HISTORY, private=True)
    names = sorted(path.name for path in HISTORY.iterdir())
    require(len(names) <= 128 and all(re.fullmatch('[0-9a-f]{64}', name) for name in names)
            and set(names) == {item['history_sha256'] for item in versions},
            'NATIVE_HISTORY_RECONCILIATION_REQUIRED')
    for name in names:
        path = HISTORY/name
        directory(path, private=True)
        raw = read(path/'manifest.json')
        value = parse(raw)
        require(sha(raw) == name and type(value) is dict and set(value) == {
            'version', 'kind', 'observed_at', 'previous_versions', 'previous_state',
            'selected_source', 'selected_package_sha256', 'rollback'}
            and type(value['version']) is int and value['version'] == 1
            and value['kind'] == 'LIGHT_NATIVE_CONTROL_SOURCE_HISTORY'
            and timestamp(value['observed_at']) and type(value['previous_versions']) is list,
            'NATIVE_HISTORY_RECONCILIATION_REQUIRED')
        previous = value['previous_versions']
        expected = encoded(dict(version=1, manifest_sha256=name,
                                previous_versions_sha256=sha(encoded(previous))))
        require(read(path/'complete.json') == expected and
            {item.name for item in path.iterdir()} ==
            {'manifest.json', 'complete.json', *(item['source']+'.json' for item in previous)},
            'NATIVE_HISTORY_RECONCILIATION_REQUIRED')
        selected = [item for item in versions if item['history_sha256'] == name]
        require(len(selected) == 1 and selected[0]['source'] == value['selected_source']
                and selected[0]['package_sha256'] == value['selected_package_sha256']
                and selected[0]['staged_at'] == value['observed_at']
                and value['previous_state'] == ('PRESENT' if previous else 'ABSENT')
                and value['rollback'] == dict(action='KEEP_PREVIOUS_VERSION',
                    previous_versions=[item['source'] for item in previous],
                    service_changed=False, authority_granted=False),
                'NATIVE_HISTORY_RECONCILIATION_REQUIRED')
        for item in previous:
            require(item in versions, 'NATIVE_HISTORY_RECONCILIATION_REQUIRED')
            historical = read(path/(item['source']+'.json'))
            require(package(historical, item['package_sha256'])[0] == item['source'],
                    'NATIVE_HISTORY_RECONCILIATION_REQUIRED')


def stage(raw, accepted):
    """Append one qualified code package after a complete historical snapshot.

    There is no current pointer and no replacement/deletion path. Existing
    versions and mutable production directories are never modified. A partial
    record is a reconciliation barrier, including after an acknowledgement loss.
    """
    source, files = package(raw, accepted)
    directory(ROOT.parent)
    if not ROOT.exists() and not ROOT.is_symlink():
        new_directory(ROOT)
    directory(ROOT, private=True)
    for path in (RELEASES, HISTORY):
        if not path.exists() and not path.is_symlink():
            new_directory(path)
        directory(path, private=True)
    require({path.name for path in ROOT.iterdir()} <= {'releases', 'history', 'staging.lock'},
            'NATIVE_HISTORY_NAMESPACE')
    import fcntl
    lock_path = ROOT/'staging.lock'
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        row = os.fstat(fd)
        require(stat.S_ISREG(row.st_mode) and row.st_uid == row.st_gid == 0
                and row.st_nlink == 1 and stat.S_IMODE(row.st_mode) == 0o600, 'NATIVE_HISTORY_LOCK')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(signature(os.fstat(fd)) == signature(lock_path.stat(follow_symlinks=False)),
                'NATIVE_HISTORY_LOCK')
        before = entries()
        history_gate(before)
        require(source not in {entry['source'] for entry in before}, 'NATIVE_HISTORY_ALREADY_STAGED')
        # History is code-only; credentials, environment, live journals, database
        # state and student data are outside this namespace and this allowlist.
        observed = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        snapshot = encoded(dict(version=1, kind='LIGHT_NATIVE_CONTROL_SOURCE_HISTORY',
            observed_at=observed, previous_versions=before,
            previous_state='PRESENT' if before else 'ABSENT',
            selected_source=source, selected_package_sha256=accepted,
            rollback=dict(action='KEEP_PREVIOUS_VERSION', previous_versions=[x['source'] for x in before],
                          service_changed=False, authority_granted=False)))
        history_id = sha(snapshot)
        destination = HISTORY/history_id
        new_directory(destination)
        retain(destination/'manifest.json', snapshot)
        for old in before:
            old_raw = read(RELEASES/old['source']/'package.json')
            require(sha(old_raw) == old['package_sha256'], 'NATIVE_HISTORY_DRIFT')
            retain(destination/(old['source']+'.json'), old_raw)
        completed = encoded(dict(version=1, manifest_sha256=history_id,
                                 previous_versions_sha256=sha(encoded(before))))
        retain(destination/'complete.json', completed)
        require(entries() == before and read(destination/'manifest.json') == snapshot
                and read(destination/'complete.json') == completed, 'NATIVE_HISTORY_DRIFT')
        target = RELEASES/source
        new_directory(target)
        retain(target/'package.json', raw)
        staged = encoded(dict(version=1, kind='LIGHT_NATIVE_CONTROL_SOURCE_STAGED',
            source=source, package_sha256=accepted, staged_at=observed, history_sha256=history_id))
        retain(target/'staged.json', staged)
        after = entries()
        require(after == sorted([*before, dict(source=source, package_sha256=accepted,
                staged_at=observed, history_sha256=history_id)], key=lambda item: item['source']),
                'NATIVE_HISTORY_READBACK')
        history_gate(after)
        require(signature(os.fstat(fd)) == signature(lock_path.stat(follow_symlinks=False)),
                'NATIVE_HISTORY_LOCK')
        return dict(audit='LIGHT_NATIVE_CONTROL_SOURCE_STAGED', source=source,
            package_sha256=accepted, history_sha256=history_id, files=len(files),
            service_changed=False, software_installed=False,
            authority_granted=False, end_to_end_accepted=False)
    finally:
        os.close(fd)
