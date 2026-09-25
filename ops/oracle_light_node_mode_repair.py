"""One-bit mode repair with descriptor traversal and same-fd rollback.

Called only by the reviewed runner, with fresh live pre/post attestations.
An advisory lock serializes cooperating operators, not hostile same-UID/root
processes. Those principals are trusted; concurrent NVM maintenance is excluded
operationally. No claim of a chmod compare-and-swap is made.
"""
import errno
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import stat

PATH = Path('/home/ubuntu/.nvm/versions/node/v22.23.2')


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def identity(info):
    return info.st_dev, info.st_ino, info.st_uid, info.st_gid


def metadata(info):
    return identity(info) + (stat.S_IMODE(info.st_mode), info.st_ctime_ns)


def no_acl(fd):
    for kind in ('access', 'default'):
        try:
            os.getxattr(fd, 'system.posix_acl_' + kind)
        except OSError as exc:
            require(exc.errno == errno.ENODATA, 'ACL_UNKNOWN')
        else:
            raise RuntimeError('ACL_PRESENT')


class DirectoryChain:
    def __init__(self, path, uid, gid):
        self.fds = []
        self.names = path.parts[1:]
        self.uid, self.gid = uid, gid
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            self.fds.append(os.open('/', flags))
            for name in self.names:
                self.fds.append(os.open(name, flags, dir_fd=self.fds[-1]))
            self.initial = [os.fstat(fd) for fd in self.fds]
            self.validate()
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.close()
            raise

    @property
    def fd(self):
        return self.fds[-1]

    def validate(self):
        # Each directory entry must still refer to its held descriptor.
        for i, fd in enumerate(self.fds):
            info = os.fstat(fd)
            require(stat.S_ISDIR(info.st_mode), 'NOT_DIRECTORY')
            require(identity(info) == identity(self.initial[i]), 'IDENTITY_DRIFT')
            if i:
                entry = os.stat(self.names[i-1], dir_fd=self.fds[i-1], follow_symlinks=False)
                require(stat.S_ISDIR(entry.st_mode) and identity(entry) == identity(info), 'PATH_DRIFT')
            if i < len(self.fds)-1:
                require(info.st_uid in (0, self.uid) and not info.st_mode & 0o022,
                        'UNSAFE_ANCESTOR_' + str(i))
                no_acl(fd)
            else:
                require(info.st_uid == self.uid and info.st_gid == self.gid,
                        'OWNER_DRIFT')
                no_acl(fd)

    def close(self):
        for fd in reversed(self.fds):
            os.close(fd)
        self.fds = []


def transaction(path, uid, gid, expected_mode, preflight, postflight, emit):
    chain = DirectoryChain(path, uid, gid)
    old = None
    attempted = False
    try:
        preflight()
        chain.validate()
        old = os.fstat(chain.fd)
        mode = stat.S_IMODE(old.st_mode)
        require(mode == expected_mode and mode & 0o020 and not mode & 0o7002,
                'MODE_UNEXPECTED')
        new_mode = mode & ~0o020
        emit({'audit': 'NVM_MODE_REPAIR', 'action': 'PREPARED',
              'old_mode': format(mode, '04o'), 'new_mode': format(new_mode, '04o')})
        chain.validate()
        require(metadata(os.fstat(chain.fd)) == metadata(old), 'PRE_WRITE_DRIFT')
        attempted = True
        os.fchmod(chain.fd, new_mode)
        changed = os.fstat(chain.fd)
        require(identity(changed) == identity(old) and
                stat.S_IMODE(changed.st_mode) == new_mode, 'POST_MODE_DRIFT')
        chain.validate()
        postflight()
        chain.validate()
        require(metadata(os.fstat(chain.fd)) == metadata(changed), 'POST_WRITE_DRIFT')
        emit({'audit': 'NVM_MODE_REPAIR', 'action': 'APPLIED_VERIFIED',
              'old_mode': format(mode, '04o'), 'new_mode': format(new_mode, '04o')})
    except BaseException:
        rollback = 'NOT_NEEDED'
        if attempted:
            rollback = 'ROLLBACK_UNCERTAIN'
            try:
                now = os.fstat(chain.fd)
                no_acl(chain.fd)
                require(identity(now) == identity(old), 'ROLLBACK_IDENTITY_DRIFT')
                current = stat.S_IMODE(now.st_mode)
                require(current in (mode, new_mode), 'ROLLBACK_MODE_DRIFT')
                if current == new_mode:
                    # Restore the original held inode even if its path vanished.
                    os.fchmod(chain.fd, mode)
                require(stat.S_IMODE(os.fstat(chain.fd).st_mode) == mode, 'ROLLBACK_FAILED')
                rollback = 'ROLLBACK_DONE'
            except BaseException:
                pass
        emit({'audit': 'BLOCKED', 'rollback': rollback,
              'old_mode': format(stat.S_IMODE(old.st_mode), '04o') if old else None})
        raise
    finally:
        chain.close()


def run(expected_mode, preflight, postflight):
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic'
            and os.uname().machine == 'aarch64', 'HOST_IDENTITY')
    user = pwd.getpwnam('ubuntu')
    group = grp.getgrgid(user.pw_gid)
    accounts = {u.pw_name for u in pwd.getpwall() if u.pw_gid == user.pw_gid}
    require(not (accounts | set(group.gr_mem)) - {'ubuntu'}, 'GROUP_NOT_PRIVATE')
    def emit(value):
        print(json.dumps(value, sort_keys=True), flush=True)
    transaction(PATH, user.pw_uid, user.pw_gid, expected_mode, preflight, postflight, emit)


if __name__ == '__main__':
    raise SystemExit('USE_REVIEWED_RUNNER')
