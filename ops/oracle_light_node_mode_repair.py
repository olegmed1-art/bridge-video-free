"""One-inode, one-bit Light NVM permission repair. No CLI install or service change.

Run only after separate fresh active HOLD/zero-queue attestation. This program
does not replace that attestation or authorize a pilot.
"""
import errno
import grp
import json
import os
from pathlib import Path
import pwd
import stat
import sys

PATH = Path("/home/ubuntu/.nvm/versions/node/v22.23.2")
TARGET = Path("/home/ubuntu/.local/share/slavik-codex")
UNIT = "school-autopilot-production-light.service"


def require(ok):
    if not ok:
        raise RuntimeError("PRECONDITION_FAILED")


def audit():
    require(os.uname().nodename == "autopilot-lite-vnic" and os.uname().machine == "aarch64")
    ubuntu = pwd.getpwnam("ubuntu")
    require(os.geteuid() == ubuntu.pw_uid)
    require(not TARGET.exists() and not TARGET.is_symlink())
    require(not os.path.lexists(TARGET))
    # Every component is examined without following a symlink.
    for part in (Path("/home"), Path("/home/ubuntu"), Path("/home/ubuntu/.nvm"),
                 Path("/home/ubuntu/.nvm/versions"), Path("/home/ubuntu/.nvm/versions/node")):
        info = part.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, ubuntu.pw_uid)
                and not info.st_mode & 0o022)
    fd = os.open(PATH, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == ubuntu.pw_uid
                and info.st_gid == ubuntu.pw_gid and info.st_mode & 0o020
                and not info.st_mode & 0o002)
        group = grp.getgrgid(info.st_gid)
        accounts = {u.pw_name for u in pwd.getpwall() if u.pw_gid == info.st_gid}
        require(not (accounts | set(group.gr_mem)) - {"ubuntu"})
        for kind in ("access", "default"):
            try:
                os.getxattr(fd, "system.posix_acl_" + kind)
            except OSError as exc:
                require(exc.errno == errno.ENODATA)
            else:
                require(False)
        current = PATH.lstat()
        require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino))
        return fd, info
    except BaseException:
        os.close(fd)
        raise


def main():
    require(sys.argv[1:] in (["--check"], ["--apply"]))
    fd, old = audit()
    old_mode = stat.S_IMODE(old.st_mode)
    new_mode = old_mode & ~0o020
    try:
        if sys.argv[1:] == ["--apply"]:
            os.fchmod(fd, new_mode)
            after = os.fstat(fd)
            path = PATH.lstat()
            require((after.st_dev, after.st_ino, after.st_uid, after.st_gid)
                    == (old.st_dev, old.st_ino, old.st_uid, old.st_gid))
            require((path.st_dev, path.st_ino) == (old.st_dev, old.st_ino))
            require(stat.S_IMODE(after.st_mode) == new_mode)
        print(json.dumps({"audit": "NVM_MODE_REPAIR",
                          "action": "APPLIED" if sys.argv[1:] == ["--apply"] else "CHECK_ONLY",
                          "old_mode": format(old_mode, "04o"),
                          "new_mode": format(new_mode, "04o"),
                          "hold_change": False, "cli_install": False}, sort_keys=True))
    except BaseException:
        if sys.argv[1:] == ["--apply"]:
            now = os.fstat(fd)
            path = PATH.lstat()
            if ((now.st_dev, now.st_ino, now.st_uid, now.st_gid)
                    == (old.st_dev, old.st_ino, old.st_uid, old.st_gid)
                    and (path.st_dev, path.st_ino) == (old.st_dev, old.st_ino)
                    and stat.S_IMODE(now.st_mode) == new_mode):
                os.fchmod(fd, old_mode)
        raise
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        print(json.dumps({"audit": "BLOCKED", "code": "NVM_MODE_REPAIR_FAILED"}))
        sys.exit(2)
