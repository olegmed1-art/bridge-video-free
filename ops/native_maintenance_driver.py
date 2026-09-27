"""Fixed private ARM64 driver installation; no owner credential loading or DB writes."""
import base64
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import sys
import zipfile

from ops.native_maintenance_workflow_pause import encoded, require, unique
from ops.native_maintenance_store import trusted_parent, private_directory, open_file, persistent_mount

PARENT = Path('/opt')
NAME = 'bridge-native-maintenance-python'
MAX_WIRE = 10 * 1024 * 1024
MAX_EXPANDED = 32 * 1024 * 1024
WHEELS = {
    'psycopg-3.3.4-py3-none-any.whl':
        ('b6bbc25ccf05c8fad3b061d9db2ef0909a555171b84b07f29458a447253d679a', 213001),
    'psycopg_binary-3.3.4-cp312-cp312-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl':
        ('77df19583501ea288eaf15ac0fe7ad01e6d8091a91d5c41df5c718f307d8e31b', 6738180),
    'typing_extensions-4.15.0-py3-none-any.whl':
        ('f0fa19c6845758ab08074a0cfa8b7aecb71c999ca73d62883bc25cc018c4e548', 44614),
}
TOP = {'psycopg', 'psycopg-3.3.4.dist-info', 'psycopg_binary', 'psycopg_binary.libs',
       'psycopg_binary-3.3.4.dist-info', 'typing_extensions.py', 'typing_extensions-4.15.0.dist-info'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def decode(data):
    require(type(data) is bytes and 0 < len(data) <= MAX_WIRE, 'DRIVER_WIRE_SIZE')
    value = json.loads(data, object_pairs_hook=unique)
    require(type(value) is dict and set(value) == {'version', 'wheels'}
            and type(value['version']) is int and value['version'] == 1
            and type(value['wheels']) is dict and set(value['wheels']) == set(WHEELS)
            and encoded(value) == data, 'DRIVER_WIRE_SHAPE')
    files = {}
    total = 0
    for name, (digest, size) in WHEELS.items():
        text = value['wheels'][name]
        require(type(text) is str and len(text) == 4 * ((size + 2) // 3), 'DRIVER_ENCODED_SIZE')
        raw = base64.b64decode(text, validate=True)
        require(len(raw) == size and sha(raw) == digest
                and base64.b64encode(raw).decode('ascii') == text, 'DRIVER_WHEEL_DIGEST')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            require(len(entries) <= 512, 'DRIVER_ZIP_COUNT')
            seen = set()
            for entry in entries:
                path = PurePosixPath(entry.filename.rstrip('/'))
                require(not path.is_absolute() and path.parts and path.parts[0] in TOP
                        and str(path) == entry.filename.rstrip('/')
                        and all(p not in ('', '.', '..') for p in path.parts)
                        and '\\' not in entry.filename and '\x00' not in entry.filename
                        and entry.filename not in seen, 'DRIVER_ZIP_PATH')
                seen.add(entry.filename)
                mode = entry.external_attr >> 16
                require(not stat.S_ISLNK(mode) and stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR)
                        and not entry.flag_bits & 1, 'DRIVER_ZIP_TYPE')
                if entry.is_dir():
                    continue
                total += entry.file_size
                require(0 <= entry.file_size <= MAX_EXPANDED and total <= MAX_EXPANDED
                        and str(path) not in files and not str(path).endswith('.pth'), 'DRIVER_ZIP_SIZE_OR_DUPLICATE')
                contents = archive.read(entry)
                require(len(contents) == entry.file_size, 'DRIVER_ZIP_TRUNCATED')
                files[str(path)] = contents
    require(len(files) <= 512 and all(x in files for x in
            ('psycopg/__init__.py', 'psycopg_binary/__init__.py', 'typing_extensions.py')), 'DRIVER_IMPORTS_MISSING')
    return files


def build(directory):
    directory = Path(directory)
    require(set(p.name for p in directory.iterdir()) == set(WHEELS), 'DRIVER_DOWNLOAD_SET')
    wheels = {}
    for name, (_, size) in WHEELS.items():
        path = directory / name
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_size == size, 'DRIVER_DOWNLOAD_SIZE')
        with path.open('rb') as stream:
            raw = stream.read(size + 1)
        require(len(raw) == size, 'DRIVER_DOWNLOAD_CHANGED')
        wheels[name] = base64.b64encode(raw).decode('ascii')
    payload = encoded(dict(version=1, wheels=wheels))
    decode(payload)
    return payload


def python_identity():
    require(os.getuid() == 0 and platform.machine() == 'aarch64'
            and sys.version_info[:2] == (3, 12), 'DRIVER_PLATFORM')
    binary = Path('/usr/bin/python3').resolve(strict=True)
    require(binary == Path('/usr/bin/python3.12'), 'DRIVER_PYTHON_TARGET')
    trusted_parent(binary.parent)
    info = binary.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
            and info.st_size <= 32 * 1024 * 1024, 'DRIVER_PYTHON_UNTRUSTED')
    return dict(python=platform.python_version(), binary_sha256=sha(binary.read_bytes()),
                architecture='aarch64', wheels_digest=sha(encoded(WHEELS)))


def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def verify_tree(root, files, identity):
    private_directory(root)
    expected = {'site/' + name: data for name, data in files.items()}
    expected['READY.json'] = encoded(identity)
    expected_dirs = {str(parent) for name in expected for parent in PurePosixPath(name).parents if str(parent) != '.'}
    found = set()
    for directory, dirs, names in os.walk(root, followlinks=False):
        private_directory(Path(directory))
        for name in dirs:
            child = Path(directory) / name
            require(child.relative_to(root).as_posix() in expected_dirs, 'DRIVER_EXTRA_DIRECTORY')
            private_directory(child)
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            require(relative in expected and relative not in found, 'DRIVER_EXTRA_FILE')
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600
                    and info.st_nlink == 1 and info.st_size == len(expected[relative]), 'DRIVER_FILE_IDENTITY')
            require(path.read_bytes() == expected[relative], 'DRIVER_FILE_CHANGED')
            found.add(relative)
    require(found == set(expected), 'DRIVER_INCOMPLETE')


def smoke(root):
    site = str(root / 'site')
    code = ("import sys,json,pathlib;sys.path.insert(0,sys.argv[1]);"
            "import psycopg,psycopg_binary,typing_extensions;"
            "assert all(pathlib.Path(m.__file__).resolve().is_relative_to(sys.argv[1]) "
            "for m in (psycopg,psycopg_binary,typing_extensions));"
            "print(json.dumps(dict(driver=psycopg.__version__,impl=psycopg.pq.__impl__,libpq=psycopg.pq.version())))")
    result = subprocess.run(['/usr/bin/python3', '-I', '-B', '-S', '-c', code, site], cwd='/',
                            env={'PATH': '/usr/bin:/bin', 'PSYCOPG_IMPL': 'binary'}, stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=15)
    require(result.returncode == 0 and len(result.stdout) <= 512, 'DRIVER_IMPORT_REFUSED')
    value = json.loads(result.stdout, object_pairs_hook=unique)
    require(type(value) is dict and set(value) == {'driver', 'impl', 'libpq'}
            and value['driver'] == '3.3.4' and value['impl'] == 'binary'
            and type(value['libpq']) is int and value['libpq'] >= 170000, 'DRIVER_IMPORT_IDENTITY')
    return value


def prepare(payload):
    identity = python_identity()
    files = decode(payload)
    identifier = sha(encoded(identity))
    trusted_parent(PARENT)
    filesystem = persistent_mount(PARENT)
    require(shutil.disk_usage(PARENT).free >= 512 * 1024 * 1024, 'DRIVER_DISK_HEADROOM')
    parent = PARENT / NAME
    try:
        parent.mkdir(mode=0o700)
    except FileExistsError:
        pass
    parent_info = private_directory(parent)
    ancestor_fd = os.open(PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(ancestor_fd)
    finally: os.close(ancestor_fd)
    descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock = None
    try:
        require((os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) ==
                (parent_info.st_dev, parent_info.st_ino), 'DRIVER_PARENT_CHANGED')
        require(all(n == 'lock' or (len(n) == 64 and all(c in '0123456789abcdef' for c in n))
                    for n in os.listdir(descriptor)), 'DRIVER_PARENT_RECONCILIATION')
        lock = open_file(descriptor, 'lock', os.O_RDWR | os.O_CREAT)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        root = parent / identifier
        reused = root.exists()
        if not reused:
            root.mkdir(mode=0o700)
            os.fsync(descriptor)
            (root / 'site').mkdir(mode=0o700)
            for name, data in files.items():
                path = root / 'site' / name
                current = root / 'site'
                for part in PurePosixPath(name).parts[:-1]:
                    current = current / part
                    current.mkdir(mode=0o700, exist_ok=True)
                    private_directory(current)
                write_private(path, data)
            write_private(root / 'READY.json', encoded(identity))
            for directory, _, _ in os.walk(root, topdown=False):
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try: os.fsync(fd)
                finally: os.close(fd)
            os.fsync(descriptor)
        verify_tree(root, files, identity)
        result = smoke(root)
        verify_tree(root, files, identity)
        require(python_identity() == identity, 'DRIVER_PYTHON_CHANGED')
        after = private_directory(parent)
        require((after.st_dev, after.st_ino) == (parent_info.st_dev, parent_info.st_ino), 'DRIVER_PARENT_CHANGED')
        return dict(runtime_id=identifier, filesystem=filesystem, reused=reused,
                    installed_bytes=sum(map(len, files.values())), file_count=len(files), **result)
    finally:
        if lock is not None: os.close(lock)
        os.close(descriptor)


def main(payload):
    from ops.oracle_light_active_hold_attest import attest
    require(os.getuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'DRIVER_HOST')
    before = attest()
    result = prepare(payload)
    require(attest() == before, 'DRIVER_HOLD_CHANGED')
    print(json.dumps(dict(audit='NATIVE_DRIVER_PREPARED', hold_unchanged=True,
                         production_sql_mutations=False, **result), sort_keys=True))
