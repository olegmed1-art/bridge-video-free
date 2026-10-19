"""Credential-free installed HOST entry; no ISSUE or RUN is admitted here.

The first installation is deliberately dormant. A future finite-window runtime
must add explicit driver/entry pins and its dual HOST/GHA integration; root
files or a matching self-computed hash alone cannot turn this into authority.
All non-stdlib bytes are checked before import. Nothing is installed by import.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import types


ROOT = Path('/var/lib/bridge-light-native-service')
MODULES = ('light_native_service_authority', 'light_native_service_supervisor',
           'light_native_service_unit')


class Refusal(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise Refusal(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def parse(raw, limit):
    require(type(raw) is bytes and 0 < len(raw) <= limit, 'NATIVE_BOOTSTRAP_RECORD')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'NATIVE_BOOTSTRAP_RECORD')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: require(False, 'NATIVE_BOOTSTRAP_RECORD'))
        require(canonical(value) == raw, 'NATIVE_BOOTSTRAP_RECORD')
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Refusal('NATIVE_BOOTSTRAP_RECORD') from None


def signature(row):
    return tuple(getattr(row, name) for name in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
        'st_gid', 'st_nlink', 'st_size', 'st_atime_ns', 'st_mtime_ns', 'st_ctime_ns'))


def private(path):
    require(os.name == 'posix' and os.geteuid() == 0 and path.is_absolute(), 'NATIVE_BOOTSTRAP_ROOT')
    for item in (path, *path.parents):
        row = item.lstat()
        require(stat.S_ISDIR(row.st_mode) and row.st_uid == 0 and not row.st_mode & 0o022,
                'NATIVE_BOOTSTRAP_PARENT')
    row = path.lstat()
    require(row.st_gid == 0 and stat.S_IMODE(row.st_mode) == 0o700, 'NATIVE_BOOTSTRAP_PRIVATE')


def read(path, limit):
    private(path.parent)
    flag = getattr(os, 'O_NOATIME', None)
    require(type(flag) is int and flag > 0, 'NATIVE_BOOTSTRAP_NOATIME')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | flag)
    try:
        row = os.fstat(fd)
        require(stat.S_ISREG(row.st_mode) and row.st_uid == row.st_gid == 0
                and row.st_nlink == 1 and stat.S_IMODE(row.st_mode) == 0o600
                and 0 < row.st_size <= limit, 'NATIVE_BOOTSTRAP_FILE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit+1)
        require(len(raw) <= limit and signature(row) == signature(os.fstat(fd))
                == signature(path.stat(follow_symlinks=False)), 'NATIVE_BOOTSTRAP_CHANGED')
        return raw
    finally:
        os.close(fd)


def qualified_sources():
    """Verify dormant source receipt and every installed source before import.

    This is code integrity, never an accepted task policy or finite authority.
    Credential/window/live authority files are deliberately not inferred here.
    """
    private(ROOT)
    receipt_raw = read(ROOT/'dormant-installation.json', 32768)
    value = parse(receipt_raw, 32768)
    require(type(value) is dict and set(value) == {'version', 'kind', 'source', 'package_sha256',
            'qualification_sha256', 'installed_at', 'history_sha256', 'unit_sha256', 'files'}
            and type(value['version']) is int and value['version'] == 1
            and value['kind'] == 'LIGHT_NATIVE_DORMANT_INSTALLATION'
            and type(value['source']) is str and re.fullmatch('[0-9a-f]{40}', value['source'])
            and all(type(value[k]) is str and re.fullmatch('[0-9a-f]{64}', value[k]) for k in
                    ('package_sha256', 'qualification_sha256', 'history_sha256', 'unit_sha256'))
            and type(value['installed_at']) is str
            and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value['installed_at'])
            and type(value['files']) is dict
            and set(value['files']) == {'launcher.py', *(name+'.py' for name in MODULES)},
            'NATIVE_BOOTSTRAP_INSTALLATION')
    blobs = {}
    for name, expected in value['files'].items():
        require(type(expected) is str and re.fullmatch('[0-9a-f]{64}', expected),
                'NATIVE_BOOTSTRAP_SOURCE_PIN')
        raw = read(ROOT/name, 262144)
        require(sha(raw) == expected, 'NATIVE_BOOTSTRAP_SOURCE_PIN')
        blobs[name] = raw
    require(Path(__file__) == ROOT/'launcher.py' and blobs['launcher.py'] == read(Path(__file__), 262144),
            'NATIVE_BOOTSTRAP_SELF')
    require(read(ROOT/'dormant-installation.json', 32768) == receipt_raw, 'NATIVE_BOOTSTRAP_CHANGED')
    return value, blobs


def load_modules(blobs):
    """Only a verified closed module map is executed, in a fresh isolated process."""
    require(type(blobs) is dict and set(blobs) == {'launcher.py', *(name+'.py' for name in MODULES)}
            and all(type(raw) is bytes for raw in blobs.values())
            and not any(name == 'ops' or name.startswith('ops.') for name in sys.modules),
            'NATIVE_BOOTSTRAP_IMPORT_STATE')
    package = types.ModuleType('ops')
    package.__path__ = []
    package.__file__ = str(ROOT/'__init__.py')
    sys.modules['ops'] = package
    loaded = {}
    try:
        # Supervisor imports the authority from this exact private package.
        for name in MODULES:
            module_name = 'ops.'+name
            module = types.ModuleType(module_name)
            module.__file__ = str(ROOT/(name+'.py'))
            module.__package__ = 'ops'
            sys.modules[module_name] = module
            setattr(package, name, module)
            exec(compile(blobs[name+'.py'], module.__file__, 'exec'), module.__dict__)
            loaded[name] = module
        return loaded
    except BaseException:
        for name in ('ops', *('ops.'+item for item in MODULES)):
            sys.modules.pop(name, None)
        raise Refusal('NATIVE_BOOTSTRAP_IMPORT_REFUSED') from None


def main():
    """Dormant installation check only. No credential, network, DB or issue port."""
    try:
        value, blobs = qualified_sources()
        # Checking/executing these stdlib service definitions proves only the
        # installed entry/module closure. No HostAuthority/Supervisor is created.
        load_modules(blobs)
        again, same = qualified_sources()
        require(again == value and same == blobs, 'NATIVE_BOOTSTRAP_CHANGED')
        result = dict(audit='LIGHT_NATIVE_HOST_DORMANT', state='RUNTIME_INTEGRATION_REQUIRED',
            source=value['source'], package_sha256=value['package_sha256'],
            software_installed=True, issue_authorized=False, native_admission_authorized=False,
            credentials_loaded=False, end_to_end_accepted=False)
    except BaseException:
        # No traceback, exception text, environment, file content or private path.
        print('{"audit":"LIGHT_NATIVE_HOST_DORMANT_REFUSED","issue_authorized":false}', flush=True)
        return 2
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
