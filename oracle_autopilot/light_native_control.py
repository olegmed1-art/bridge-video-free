"""Root-owned admission files; stdlib-only so the launch gate has no effects."""
import os
from pathlib import Path
import stat


def require(value, code):
    if not value:
        raise RuntimeError(code)


def root_bytes(path, limit, *, private=True):
    """Open every parent without symlink traversal; accept only root control."""
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'PILOT_CONTROL_PATH')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            row = os.fstat(fd)
            require(row.st_uid == 0 and not row.st_mode & 0o022, 'PILOT_CONTROL_PARENT')
        leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(leaf, 'rb') as stream:
            row = os.fstat(stream.fileno())
            require(stat.S_ISREG(row.st_mode) and row.st_uid == 0 and row.st_nlink == 1
                    and (stat.S_IMODE(row.st_mode) == 0o640 and row.st_gid == os.getgid()
                         if private else stat.S_IMODE(row.st_mode) in (0o444, 0o600, 0o640, 0o644))
                    and row.st_size <= limit, 'PILOT_CONTROL_FILE')
            data = stream.read(limit + 1)
            require(len(data) <= limit, 'PILOT_CONTROL_SIZE')
            return data
    finally:
        os.close(fd)
