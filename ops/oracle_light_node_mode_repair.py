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
TARGETS = tuple(reversed((PATH, *tuple(PATH.parents)[:3])))


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
    def __init__(self, path, uid, gid, targets=None):
        self.fds = []
        self.names = path.parts[1:]
        self.uid, self.gid = uid, gid
        targets = (path,) if targets is None else tuple(targets)
        chain_paths = (Path('/'), *reversed(path.parents[:-1]), path)
        require(bool(targets) and len(set(targets)) == len(targets) and
                all(p in chain_paths for p in targets), 'TARGET_SCOPE')
        self.indices = [i for i, p in enumerate(chain_paths) if p in targets]
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
            if i not in self.indices:
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


def transaction(path, uid, gid, expected_mode, preflight, postflight, emit,
                targets=None):
    chain = DirectoryChain(path, uid, gid, targets)
    old = {}
    attempted = []
    new_mode = 0o755
    try:
        preflight()
        chain.validate()
        require(expected_mode == 0o775, 'MODE_UNEXPECTED')
        old = {i: os.fstat(chain.fds[i]) for i in chain.indices}
        require(all(stat.S_IMODE(info.st_mode) == expected_mode for info in old.values()),
                'MODE_UNEXPECTED')
        emit({'audit': 'NVM_MODE_REPAIR', 'action': 'PREPARED',
              'directory_count':len(old), 'old_mode':'0775', 'new_mode':'0755'})
        expected = dict(old)
        for i in chain.indices:
            chain.validate()
            require(all(metadata(os.fstat(chain.fds[j])) == metadata(info)
                        for j, info in expected.items()), 'PRE_WRITE_DRIFT')
            attempted.append(i)  # Include a syscall that changes mode then raises.
            os.fchmod(chain.fds[i], new_mode)
            changed = os.fstat(chain.fds[i])
            require(identity(changed) == identity(old[i]) and
                    stat.S_IMODE(changed.st_mode) == new_mode, 'POST_MODE_DRIFT')
            expected[i] = changed
        chain.validate()
        postflight()
        chain.validate()
        require(all(metadata(os.fstat(chain.fds[j])) == metadata(info)
                    for j, info in expected.items()), 'POST_WRITE_DRIFT')
        emit({'audit': 'NVM_MODE_REPAIR', 'action': 'APPLIED_VERIFIED',
              'directory_count':len(old), 'old_mode':'0775', 'new_mode':'0755'})
    except BaseException:
        rollback = 'NOT_NEEDED'
        if attempted:
            rollback = 'ROLLBACK_UNCERTAIN'
            for i in reversed(attempted):
                try:
                    fd = chain.fds[i]
                    now = os.fstat(fd)
                    no_acl(fd)
                    require(identity(now) == identity(old[i]), 'ROLLBACK_IDENTITY_DRIFT')
                    current = stat.S_IMODE(now.st_mode)
                    require(current in (0o775, new_mode), 'ROLLBACK_MODE_DRIFT')
                    if current == new_mode:
                        os.fchmod(fd, 0o775)
                except BaseException:
                    # Continue restoring the other held inodes.
                    pass
            try:
                for i, info in old.items():
                    no_acl(chain.fds[i])
                    now = os.fstat(chain.fds[i])
                    require(identity(now) == identity(info) and
                            stat.S_IMODE(now.st_mode) == 0o775, 'ROLLBACK_FAILED')
                rollback = 'ROLLBACK_DONE'
            except BaseException:
                pass
        emit({'audit': 'BLOCKED', 'rollback': rollback,
              'old_mode': '0775' if old else None})
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
    def checked_preflight():
        preflight()
        current = pwd.getpwnam('ubuntu')
        group = grp.getgrgid(current.pw_gid)
        accounts = {u.pw_name for u in pwd.getpwall() if u.pw_gid == current.pw_gid}
        require((current.pw_uid, current.pw_gid) == (user.pw_uid, user.pw_gid) and
                not (accounts | set(group.gr_mem)) - {'ubuntu'}, 'GROUP_NOT_PRIVATE')
    transaction(PATH, user.pw_uid, user.pw_gid, expected_mode,
                checked_preflight, postflight, emit, targets=TARGETS)


if __name__ == '__main__':
    raise SystemExit('USE_REVIEWED_RUNNER')
