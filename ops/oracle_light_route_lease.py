"""Forced SSH command: expose only routing metadata and hold a bounded lease."""
import fcntl
import json
import os
from pathlib import Path
import select
import stat
import sys
import time

ROOT = Path('/var/lib/bridge-autopilot-tunnel')
MAX_LEASE_SECONDS = 300
HEARTBEAT_SECONDS = 15


def validate_route(value):
    assert isinstance(value, dict) and set(value) == {'version', 'backend', 'database', 'epoch'}
    assert value['version'] == 1 and value['backend'] in {'neon', 'paused', 'postgresql'}
    assert value['database'] == 'autopilot'
    assert type(value['epoch']) is int and 0 <= value['epoch'] <= 2**31 - 1
    return value


def read_owned(path, limit=16384):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as file:
        info = os.fstat(file.fileno())
        assert stat.S_ISREG(info.st_mode) and info.st_uid == 0
        assert stat.S_IMODE(info.st_mode) == 0o644 and info.st_size <= limit
        value = file.read(limit + 1)
        assert len(value) <= limit
        return value.decode('utf-8')


def route():
    return validate_route(json.loads(read_owned(ROOT / 'route.json')))


def main():
    if os.environ.get('SSH_ORIGINAL_COMMAND') != 'route-v1':
        return 1
    root = ROOT.stat()
    assert not ROOT.is_symlink() and root.st_uid == 0 and stat.S_IMODE(root.st_mode) == 0o755
    descriptor = os.open(ROOT / 'route.lock', os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as lock:
        info = os.fstat(lock.fileno())
        assert stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o644
        identity = json.loads(read_owned(ROOT / 'lock-identity.json'))
        assert identity == {'device': info.st_dev, 'inode': info.st_ino}
        current_path = (ROOT / 'route.lock').lstat()
        assert current_path.st_dev == info.st_dev and current_path.st_ino == info.st_ino
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'busy': True}), flush=True)
            return 0
        current = route()
        if current['backend'] == 'paused':
            print(json.dumps({'route': current}), flush=True)
            return 0
        print(json.dumps({'route': current, 'ca_pem': read_owned(ROOT / 'ca.crt')}), flush=True)
        start = last_heartbeat = time.monotonic()
        while time.monotonic() - start < MAX_LEASE_SECONDS:
            if not select.select([sys.stdin.buffer], [], [], 1)[0]:
                if time.monotonic() - last_heartbeat > HEARTBEAT_SECONDS:
                    return 1
                continue
            data = os.read(sys.stdin.fileno(), 1024)
            if not data:
                return 0
            if data.strip(b'.'):
                return 1
            last_heartbeat = time.monotonic()
        return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        print('AUTOPILOT_ROUTE_UNAVAILABLE', file=sys.stderr)
        sys.exit(1)
