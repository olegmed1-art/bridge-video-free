"""Own the existing route.lock exclusively during bounded maintenance.

Linux/root only. This blocks cooperating route-v1 clients, not direct database
users, catalog writers or surviving database transactions. No route is edited.
"""
import fcntl
import json
import os
from pathlib import Path
import stat
import time

from ops.oracle_light_route_lease import ROOT, validate_route


class RouteFenceError(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise RouteFenceError(code)


class RouteMaintenanceFence:
    def __init__(self, expected_route, *, root=ROOT, seconds=30):
        self.expected = validate_route(dict(expected_route))
        require(type(seconds) is int and 1 <= seconds <= 60, 'ROUTE_WINDOW_INVALID')
        self.root = Path(root)
        self.seconds = seconds
        self.directory = None
        self.lock = None
        self.active = False

    def read(self, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.directory)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                    and stat.S_IMODE(info.st_mode) == 0o644 and info.st_size <= 16384,
                    'ROUTE_FILE_UNTRUSTED')
            content = handle.read(16385)
            require(len(content) <= 16384, 'ROUTE_FILE_TOO_LARGE')
            return json.loads(content)

    def verify_files(self):
        root_info = os.fstat(self.directory)
        path_info = self.root.lstat()
        require(stat.S_ISDIR(path_info.st_mode) and path_info.st_uid == 0
                and stat.S_IMODE(path_info.st_mode) == 0o755
                and (path_info.st_dev, path_info.st_ino) == (root_info.st_dev, root_info.st_ino),
                'ROUTE_DIRECTORY_CHANGED')
        info = os.fstat(self.lock)
        path = os.stat('route.lock', dir_fd=self.directory, follow_symlinks=False)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and stat.S_IMODE(info.st_mode) == 0o644 and info.st_nlink == 1
                and stat.S_ISREG(path.st_mode)
                and (path.st_dev, path.st_ino) == (info.st_dev, info.st_ino),
                'ROUTE_LOCK_CHANGED')
        require(self.read('lock-identity.json') == {'device': info.st_dev, 'inode': info.st_ino},
                'ROUTE_LOCK_IDENTITY_CHANGED')
        require(validate_route(self.read('route.json')) == self.expected, 'ROUTE_VALUE_CHANGED')

    def __enter__(self):
        require(not self.active and self.directory is None and self.lock is None,
                'ROUTE_FENCE_ALREADY_OPEN')
        require(os.geteuid() == 0, 'ROUTE_ROOT_REQUIRED')
        try:
            self.directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.lock = os.open('route.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                dir_fd=self.directory)
            self.verify_files()
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RouteFenceError('ROUTE_CONSUMERS_ACTIVE') from exc
            self.pid = os.getpid()
            # /proc may be mounted from an enclosing PID namespace. fdinfo's
            # lock owner uses that namespace, not necessarily os.getpid().
            self.proc_pid = next(line.split()[1] for line in
                                 Path('/proc/self/status').read_text().splitlines()
                                 if line.startswith('Pid:'))
            self.deadline = time.monotonic() + self.seconds
            self.active = True
            self.assert_held()
            return self
        except BaseException:
            self.close()
            raise

    def assert_held(self):
        require(self.active and os.getpid() == self.pid, 'ROUTE_FENCE_LOST')
        require(time.monotonic() < self.deadline, 'ROUTE_WINDOW_EXPIRED')
        self.verify_files()
        # Inspect the actual open file description. Retrying flock here would
        # silently reacquire a lost lease and conceal an intervening admission.
        lines = Path(f'/proc/self/fdinfo/{self.lock}').read_text().splitlines()
        locks = [line.split() for line in lines if line.startswith('lock:')]
        info = os.fstat(self.lock)
        require(any(len(row) == 9 and row[2:5] == ['FLOCK', 'ADVISORY', 'WRITE']
                    and row[5] == self.proc_pid and row[6].split(':')[-1] == str(info.st_ino)
                    and row[7:] == ['0', 'EOF'] for row in locks), 'ROUTE_FENCE_LOST')

    def close(self):
        self.active = False
        for name in ('lock', 'directory'):
            fd = getattr(self, name)
            setattr(self, name, None)
            if fd is not None:
                os.close(fd)

    def __exit__(self, exc_type, exc, tb):
        self.close()
