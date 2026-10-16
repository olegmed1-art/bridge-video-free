"""Fixed root-owned journal storage preparation; never a permission executor."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid

from ops.native_maintenance_workflow_pause import Journal, require

PARENT = Path('/var/lib')
NAME = 'bridge-native-maintenance'
VERSION = b'bridge-native-maintenance-store-v1\n'
EVENT = {'kind': 'STORAGE_PROBE', 'version': 1}


def trusted_parent(path):
    for item in (path, *path.parents):
        info = item.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                and not stat.S_IMODE(info.st_mode) & 0o022, 'STORE_PARENT_UNTRUSTED')


def persistent_mount(path):
    result = subprocess.run(['/usr/bin/findmnt', '--noheadings', '--output',
                             'FSTYPE,OPTIONS', '--target', str(path)],
                            capture_output=True, text=True, timeout=5,
                            env={'PATH': '/usr/bin:/bin'})
    fields = result.stdout.strip().split()
    require(result.returncode == 0 and len(fields) == 2
            and fields[0] in ('ext4', 'xfs', 'btrfs')
            and 'rw' in fields[1].split(',') and 'ro' not in fields[1].split(','),
            'STORE_FILESYSTEM_REFUSED')
    return fields[0]


def private_directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o700, 'STORE_DIRECTORY_REFUSED')
    return info


def open_file(directory_fd, name, flags):
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
                'STORE_FILE_REFUSED')
        return fd
    except BaseException:
        os.close(fd)
        raise


def prepare():
    require(os.getuid() == 0, 'STORE_ROOT_REQUIRED')
    trusted_parent(PARENT)
    filesystem = persistent_mount(PARENT)
    root = PARENT / NAME
    parent_fd = os.open(PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = lock = None
    try:
        try:
            os.mkdir(NAME, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        info = private_directory(root)
        require(info.st_dev == os.fstat(parent_fd).st_dev, 'STORE_MOUNT_CHANGED')
        fd = os.open(NAME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        require((os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (info.st_dev, info.st_ino),
                'STORE_DIRECTORY_CHANGED')
        # Do not create even a lock in an unrelated or interrupted store.
        require(all(n in ('lock', 'VERSION') or re.fullmatch('[0-9a-f]{64}', n)
                    for n in os.listdir(fd)), 'STORE_RECONCILIATION_REQUIRED')
        lock = open_file(fd, 'lock', os.O_RDWR | os.O_CREAT)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        names = os.listdir(fd)
        require(all(n in ('lock', 'VERSION') or re.fullmatch('[0-9a-f]{64}', n)
                    for n in names), 'STORE_RECONCILIATION_REQUIRED')
        if 'VERSION' not in names:
            require(names == ['lock'], 'STORE_VERSION_MISSING')
            marker = open_file(fd, 'VERSION', os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(marker, 'wb') as stream:
                stream.write(VERSION)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(fd)
        marker = open_file(fd, 'VERSION', os.O_RDONLY)
        try:
            require(os.read(marker, 256) == VERSION, 'STORE_VERSION_DRIFT')
        finally:
            os.close(marker)
        for name in names:
            if re.fullmatch('[0-9a-f]{64}', name):
                private_directory(root / name)
        probe(root, fd)
        require(private_directory(root).st_ino == info.st_ino
                and root.stat().st_dev == info.st_dev, 'STORE_DIRECTORY_CHANGED')
        os.fsync(fd)
        return filesystem
    finally:
        if lock is not None: os.close(lock)
        if fd is not None: os.close(fd)
        os.close(parent_fd)


def probe(root, directory_fd):
    # Keep uncertain remnants for operator reconciliation. Never sweep old probes.
    name = 'probe-' + uuid.uuid4().hex
    base = root / name
    os.mkdir(name, 0o700, dir_fd=directory_fd)
    os.fsync(directory_fd)
    original, restored = base / 'original', base / 'restored'
    original.mkdir(mode=0o700)
    restored.mkdir(mode=0o700)
    source = Path(__file__).resolve().parents[1]
    code = ("import sys;sys.path.insert(0,sys.argv[1]);"
            "from ops.native_maintenance_workflow_pause import Journal;"
            "j=Journal(sys.argv[2]);j.append({'kind':'STORAGE_PROBE','version':1});j.close()")
    subprocess.run([sys.executable, '-I', '-B', '-S', '-c', code, str(source), str(original)],
                   cwd='/', env={'PATH': '/usr/bin:/bin'}, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
    with Journal(original) as journal:
        require(len(journal.records) == 1 and journal.records[0]['event'] == EVENT,
                'STORE_REOPEN_FAILED')
    data = (original / '000000.json').read_bytes()
    target = os.open(restored / '000000.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(target, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    restore_fd = os.open(restored, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(restore_fd)
    finally: os.close(restore_fd)
    with Journal(restored) as journal:
        require(len(journal.records) == 1 and journal.records[0]['event'] == EVENT,
                'STORE_RESTORE_FAILED')
        journal.append({'kind': 'RESTORE_PROBE', 'version': 1})
    require((original / '000000.json').read_bytes() == data
            and not (original / '000001.json').exists(), 'STORE_COPY_NOT_INDEPENDENT')
    # Only this random, successfully verified synthetic probe is removed.
    shutil.rmtree(base)
    os.fsync(directory_fd)


def main():
    from ops.oracle_light_active_hold_attest import attest
    require(os.getuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'STORE_HOST_REQUIRED')
    before = attest()
    filesystem = prepare()
    require(attest() == before, 'STORE_HOLD_CHANGED')
    print(json.dumps({'audit': 'NATIVE_STORE_PREPARED', 'filesystem': filesystem,
                      'journal_reopen': True, 'copy_restore': True,
                      'hold_unchanged': True, 'production_sql_mutations': False}, sort_keys=True))
