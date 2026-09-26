"""Bounded source packaging, not a production maintenance executor.

The caller supplies an independently trusted source commit AND bundle digest.
The decoder and Python runtime must themselves be trusted.
"""
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

FILES = (
    'database/native_cli_permission_engine.py',
    'database/native_cli_write_fence.py',
    'database/native_cli_maintenance_session.py',
    'database/fixtures/native_cli_commit_rehearsal.py',
    'database/fixtures/native_route_drain_rehearsal.py',
    'database/fixtures/native_maintenance_session_rehearsal.py',
    'database/fixtures/native_maintenance_executor_rehearsal.py',
    'ops/oracle_light_route_lease.py',
    'ops/github_autopilot_db_route.py',
    'ops/native_permission_route_fence.py',
    'ops/oracle_light_active_hold_attest.py',
    'ops/native_permission_hold_guard.py',
    'ops/native_maintenance_executor.py',
    'ops/native_maintenance_workflow_api.py',
    'ops/native_maintenance_workflow_pause.py',
    'ops/native_maintenance_run_guard.py',
    'ops/native_maintenance_store.py',
    'ops/native_maintenance_snapshot.py',
    'ops/native_maintenance_checkpoint.py',
    'ops/native_maintenance_checkpoint_oci.py',
    'ops/native_maintenance_checkpoint_transport.py',
    'ops/native_maintenance_checkpoint_host_probe.py',
    'ops/native_maintenance_driver.py',
    'ops/native_maintenance_owner_attest.py',
    'ops/oracle_autopilot_source_preflight.py',
    'ops/native_maintenance_owner_host.py',
)
PACKAGE_MARKERS = ('database/__init__.py', 'database/fixtures/__init__.py', 'ops/__init__.py')
MAX_FILE = 256 * 1024
MAX_TOTAL = 1024 * 1024
MAX_WIRE = 2 * 1024 * 1024


class BundleError(RuntimeError):
    pass


def check(condition, code):
    if not condition:
        raise BundleError(code)


def identifier(value, size):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{%d}' % size, value) is not None


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def git(repo, *args):
    # Ignore inherited Git directory/object overrides and replacement refs.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null')
    result = subprocess.run(['git', '--no-replace-objects', '-C', str(repo), *args],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=10, check=False)
    check(result.returncode == 0, 'GIT_SOURCE_REFUSED')
    return result.stdout


def build(repo, source_sha):
    check(identifier(source_sha, 40), 'SOURCE_SHA_INVALID')
    check(git(repo, 'cat-file', '-t', source_sha) == b'commit\n', 'SOURCE_NOT_COMMIT')
    files = {}
    total = 0
    for path in FILES:
        record = git(repo, 'ls-tree', source_sha, '--', path).decode('ascii').strip()
        fields = record.split()
        check(len(fields) == 4 and fields[0] in ('100644', '100755')
              and fields[1] == 'blob' and fields[3] == path
              and identifier(fields[2], 40), 'SOURCE_FILE_INVALID')
        size = int(git(repo, 'cat-file', '-s', fields[2]))
        check(0 < size <= MAX_FILE, 'SOURCE_FILE_SIZE')
        total += size
        check(total <= MAX_TOTAL, 'SOURCE_TOTAL_SIZE')
        data = git(repo, 'cat-file', 'blob', fields[2])
        check(len(data) == size, 'SOURCE_SIZE_CHANGED')
        files[path] = base64.b64encode(data).decode('ascii')
    payload = canonical(dict(version=1, source_sha=source_sha, files=files))
    check(len(payload) <= MAX_WIRE, 'WIRE_SIZE')
    return payload


def unique(pairs):
    result = {}
    for key, value in pairs:
        check(key not in result, 'DUPLICATE_KEY')
        result[key] = value
    return result


def decode(payload, expected_source, expected_digest):
    check(type(payload) is bytes and 0 < len(payload) <= MAX_WIRE, 'WIRE_SIZE')
    check(identifier(expected_source, 40) and identifier(expected_digest, 64), 'EXPECTED_ID_INVALID')
    check(digest(payload) == expected_digest, 'BUNDLE_DIGEST_MISMATCH')
    try:
        obj = json.loads(payload, object_pairs_hook=unique)
        check(type(obj) is dict and set(obj) == {'version', 'source_sha', 'files'}, 'BUNDLE_SCHEMA')
        check(type(obj['version']) is int and obj['version'] == 1, 'BUNDLE_VERSION')
        check(obj['source_sha'] == expected_source, 'SOURCE_MISMATCH')
        check(type(obj['files']) is dict and set(obj['files']) == set(FILES), 'FILE_SET_MISMATCH')
        result = {}
        total = 0
        for path in FILES:
            encoded = obj['files'][path]
            check(type(encoded) is str and 0 < len(encoded) <= 4 * ((MAX_FILE + 2) // 3), 'ENCODED_SIZE')
            data = base64.b64decode(encoded, validate=True)
            check(base64.b64encode(data).decode('ascii') == encoded, 'BASE64_NONCANONICAL')
            check(0 < len(data) <= MAX_FILE, 'DECODED_SIZE')
            total += len(data)
            check(total <= MAX_TOTAL, 'DECODED_TOTAL_SIZE')
            result[path] = data
        check(canonical(obj) == payload, 'JSON_NONCANONICAL')
        return result
    except BundleError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise BundleError('BUNDLE_ENCODING_INVALID') from exc


@contextmanager
def extracted(payload, expected_source, expected_digest):
    files = decode(payload, expected_source, expected_digest)
    # Fixed /tmp, unpredictable exclusive mkdir0700; only this UID/root trusted.
    with tempfile.TemporaryDirectory(prefix='native-maintenance-source-', dir='/tmp') as directory:
        root = Path(directory)
        for subdirectory in ('database', 'database/fixtures', 'ops'):
            (root / subdirectory).mkdir(mode=0o700)
        for path, data in {**files, **dict.fromkeys(PACKAGE_MARKERS, b'')}.items():
            fd = os.open(root / path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
        yield root
