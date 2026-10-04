"""Linux create-only evidence writer. No CLI, network, DB or admission adapter.

The caller MUST be an independently reviewed action-time adapter supplying an
approved request digest and read-only observation function under writer exclusion.
An arbitrary request/observer supplied together is NOT an authorization mechanism.
"""
import hashlib
import json
import os
import re
import stat
import sys
from contextlib import contextmanager

NAME = 'reconciliation-v1.json'
REQUIRED_PROOFS = frozenset(('local_bindings', 'db_closure', 'provider_readback',
    'failed_plan_no_execution', 'writer_exclusion', 'source_review'))


class Refused(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_KEY')
            result[key] = value
        return result
    require(type(raw) is bytes and len(raw) <= 262144, 'REQUEST_SIZE')
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: require(False, 'NONFINITE'))


def hex64(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def parts(path):
    require(type(path) is str and 0 < len(path) <= 512, 'PATH')
    result = path.split('/')
    require(all(re.fullmatch('[A-Za-z0-9_.-]+', p) and p not in ('.', '..')
                for p in result), 'PATH')
    return result


def validate(raw, approved):
    require(hex64(approved) and digest(raw) == approved, 'REQUEST_NOT_APPROVED')
    r = decode(raw)
    require(type(r) is dict and set(r) == {'version', 'operation', 'policy_sha256',
        'plan_sha256', 'predecessor', 'files', 'directories', 'proofs', 'observation',
        'not_before', 'expires_at', 'root_identity', 'snapshot_sha256'}, 'REQUEST_SCHEMA')
    require(type(r['version']) is int and r['version'] == 1 and
            r['operation'] == 'RETAIN_RECONCILIATION_EVIDENCE_ONLY', 'OPERATION')
    require(all(hex64(r[k]) for k in ('policy_sha256', 'plan_sha256')), 'IDENTITY')
    p = r['predecessor']
    require(type(p) is dict and set(p) == {'sequence', 'plan_sha256', 'terminal_sha256'}
        and type(p['sequence']) is int and p['sequence'] >= 0
        and hex64(p['plan_sha256']) and hex64(p['terminal_sha256'])
        and p['plan_sha256'] != r['plan_sha256'], 'PREDECESSOR')
    require(type(r['proofs']) is dict and set(r['proofs']) == REQUIRED_PROOFS and
            all(hex64(v) for v in r['proofs'].values()), 'PROOFS_REQUIRED')
    require(hex64(r['observation']), 'OBSERVATION_REQUIRED')
    require(hex64(r['snapshot_sha256']), 'SNAPSHOT_REQUIRED')
    require(type(r['not_before']) is int and type(r['expires_at']) is int and
            0 < r['expires_at'] - r['not_before'] <= 300, 'WINDOW')
    require(type(r['root_identity']) is list and len(r['root_identity']) == 2 and
            all(type(v) is int and v >= 0 for v in r['root_identity']), 'ROOT_IDENTITY')
    require(type(r['files']) is dict and type(r['directories']) is dict and
            1 <= len(r['files']) <= 256 and 1 <= len(r['directories']) <= 64, 'MANIFEST')
    destination = 'cycles/' + r['plan_sha256']
    require(destination in r['directories'], 'DESTINATION_NOT_GUARDED')
    for path, pin in r['files'].items():
        parts(path)
        require(not path.endswith('/' + NAME), 'OUTPUT_IN_INPUTS')
        require(pin is None or hex64(pin), 'FILE_PIN')
    for path, names in r['directories'].items():
        parts(path)
        require(type(names) is list and len(names) <= 256 and
                len(set(names)) == len(names), 'INVENTORY')
        for name in names:
            require(len(parts(name)) == 1 and name != NAME, 'INVENTORY_NAME')
    return r


def record(r, request_sha256):
    # This records reviewed evidence; it is deliberately NOT COMPLETE/ACK/retired.
    return encoded(dict(version=1, kind='RECONCILIATION_EVIDENCE_ONLY',
        assessment='PREPARE_ROLLBACK_SUPPORTED', policy_sha256=r['policy_sha256'],
        plan_sha256=r['plan_sha256'], predecessor=r['predecessor'],
        request_sha256=request_sha256, proofs=r['proofs'], observation=r['observation'],
        original_failure='UNKNOWN', failed_plan_execution='NO_EXECUTION_PROOF_REFERENCED',
        issue_allowed=False, replay_allowed=False, acknowledgement_allowed=False,
        retirement_allowed=False, incident_closed=False))


def identity(st):
    return st.st_dev, st.st_ino


def file_meta(st):
    require(stat.S_ISREG(st.st_mode) and st.st_uid == st.st_gid == 0 and
        stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1, 'FILE_METADATA')


@contextmanager
def directory(parent, path):
    fd = os.dup(parent)
    try:
        for part in parts(path):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME,
                            dir_fd=fd)
            os.close(fd)
            fd = child
            st = os.fstat(fd)
            require(st.st_uid == st.st_gid == 0 and stat.S_IMODE(st.st_mode) == 0o700,
                    'DIRECTORY_METADATA')
        yield fd
    finally:
        os.close(fd)


def read(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME,
                 dir_fd=parent)
    try:
        before = os.fstat(fd)
        file_meta(before)
        require(before.st_size <= 16 * 1024 * 1024, 'FILE_TOO_LARGE')
        chunks = []
        left = before.st_size + 1
        while left:
            chunk = os.read(fd, min(left, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        raw = b''.join(chunks)
        after = os.fstat(fd)
        require(before == after and len(raw) == before.st_size and
                identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity(before),
                'READ_DRIFT')
        return raw, (before.st_dev, before.st_ino, before.st_size,
                     before.st_mtime_ns, before.st_ctime_ns)
    finally:
        os.close(fd)


def snapshot(root, r):
    result = {}
    for path, expected in r['files'].items():
        ps = parts(path)
        with directory(root, '/'.join(ps[:-1])) as parent:
            try:
                raw, metadata = read(parent, ps[-1])
            except FileNotFoundError:
                require(expected is None, 'MISSING_FILE')
                result[path] = None
            else:
                require(expected is not None and digest(raw) == expected, 'FILE_DRIFT')
                result[path] = metadata
    output_parent = 'cycles/' + r['plan_sha256']
    for path, expected in r['directories'].items():
        with directory(root, path) as parent:
            names = set(os.listdir(parent))
            if path == output_parent:
                names.discard(NAME)
            require(names == set(expected), 'INVENTORY_DRIFT')
            st = os.fstat(parent)
            result[path + '/'] = (identity(st) if path == output_parent else
                (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns))
    return result


@contextmanager
def root_handle(path):
    require(sys.platform == 'linux' and os.geteuid() == os.getuid() ==
            os.getegid() == os.getgid() == 0, 'LINUX_ROOT_REQUIRED')
    require(type(path) is str and path.startswith('/') and path != '/', 'ROOT_PATH')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOATIME)
    try:
        for part in parts(path[1:]):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME, dir_fd=fd)
            os.close(fd)
            fd = child
            st = os.fstat(fd)
            require(st.st_uid == 0 and (not st.st_mode & 0o022 or
                    bool(st.st_mode & stat.S_ISVTX)), 'UNTRUSTED_ANCESTOR')
        require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700 and
                os.fstat(fd).st_gid == 0, 'ROOT_METADATA')
        yield fd
    finally:
        os.close(fd)


def commit(root_path, request_raw, approved_request_sha256, observe):
    """Retain one fixed record. observe() returns (unix_seconds, evidence_digest).

    observe is trusted reviewed code, not request-controlled code. It must refuse
    if any required proof, provider freshness or all-writer exclusion is missing.
    No caller callback is imported or executed from the JSON.
    """
    r = validate(request_raw, approved_request_sha256)
    payload = record(r, approved_request_sha256)
    import fcntl
    locks = []
    with root_handle(root_path) as root:
        require(list(identity(os.fstat(root))) == r['root_identity'], 'ROOT_REPLACED')
        try:
            for path in ('issuers', 'cycles'):
                with directory(root, path) as parent:
                    fd = os.open('cycle.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=parent)
                    locks.append(fd)
                    file_meta(os.fstat(fd))
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    require(identity(os.stat('cycle.lock', dir_fd=parent,
                            follow_symlinks=False)) == identity(os.fstat(fd)), 'LOCK_REPLACED')
            baseline = snapshot(root, r)
            require(digest(encoded(baseline)) == r['snapshot_sha256'], 'APPROVED_SNAPSHOT_DRIFT')
            def guard():
                now, observed = observe()
                require(type(now) is int and r['not_before'] <= now <= r['expires_at']
                        and observed == r['observation'], 'FRESH_EVIDENCE_REQUIRED')
                with root_handle(root_path) as current:
                    require(identity(os.fstat(current)) == identity(os.fstat(root)), 'ROOT_REPLACED')
                require(snapshot(root, r) == baseline, 'CAS_DRIFT')
                for path, fd in zip(('issuers', 'cycles'), locks):
                    with directory(root, path) as parent:
                        require(identity(os.stat('cycle.lock', dir_fd=parent,
                            follow_symlinks=False)) == identity(os.fstat(fd)), 'LOCK_REPLACED')
            guard()
            with directory(root, 'cycles/' + r['plan_sha256']) as parent:
                try:
                    existing, _ = read(parent, NAME)
                except FileNotFoundError:
                    existing = None
                if existing is not None:
                    require(existing == payload, 'EXISTING_CONFLICT_OR_PARTIAL')
                    guard()
                    retained_identity = identity(os.stat(NAME, dir_fd=parent, follow_symlinks=False))
                    fd = os.open(NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME, dir_fd=parent)
                    try:
                        file_meta(os.fstat(fd))
                        require(identity(os.fstat(fd)) == retained_identity, 'OUTPUT_REPLACED')
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    os.fsync(parent)
                    require(read(parent, NAME)[0] == payload and
                        identity(os.stat(NAME, dir_fd=parent, follow_symlinks=False)) == retained_identity,
                        'OUTPUT_REPLACED')
                    guard()
                    final_raw, final_meta = read(parent, NAME)
                    require(final_raw == payload and tuple(final_meta[:2]) == retained_identity,
                            'OUTPUT_REPLACED')
                    return 'ALREADY_RECORDED'
                guard()
                fd = os.open(NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
                try:
                    file_meta(os.fstat(fd))
                    written_identity = identity(os.fstat(fd))
                    remaining = memoryview(payload)
                    while remaining:
                        count = os.write(fd, remaining)
                        require(count > 0, 'SHORT_WRITE')
                        remaining = remaining[count:]
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.fsync(parent)
                require(read(parent, NAME)[0] == payload, 'WRITE_READBACK')
                guard()
                final_raw, final_meta = read(parent, NAME)
                require(final_raw == payload and tuple(final_meta[:2]) == written_identity,
                        'OUTPUT_REPLACED')
                return 'RECORDED_EVIDENCE_ONLY'
        finally:
            for fd in reversed(locks):
                os.close(fd)
