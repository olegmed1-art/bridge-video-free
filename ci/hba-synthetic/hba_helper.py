"""Linux-only SYNTHETIC helper. No live adapter, reload, SQL or service calls.

Atomic replacement is not kernel compare-and-swap. Every authorized editor must
hold the same cooperative lock; noncooperating root writers remain a blocker.
"""
import base64
import array
import fcntl
import hashlib
import json
import os
import stat
import sys
import threading
import time
import uuid
from pathlib import Path

PREFIX = (b"host fixturedb all 0.0.0.0/0 reject\n"
          b"host fixturedb all ::/0 reject\n"
          b"local fixturedb postgres peer\nlocal fixturedb all reject\n")


class Refused(RuntimeError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()



# Only zero and the kernel-managed extents bit are accepted. User policy bits
# nodump/noatime/sync and every unknown bit fail closed; never SETFLAGS here.
KERNEL_MANAGED_EXTENTS = 0x00080000
REFUSED_POLICY_FLAGS = 0x00000040 | 0x00000080 | 0x00000008


def read_inode_flags(fd):
    values = array.array("L", [0])
    if values.itemsize not in (4, 8):
        raise Refused("Unsupported ioctl word size")
    request = 0x80006601 | (values.itemsize << 16)  # FS_IOC_GETFLAGS
    try:
        fcntl.ioctl(fd, request, values, True)
    except OSError as exc:
        raise Refused("Inode flags inspection unavailable; never infer zero") from exc
    flags = int(values[0])
    if flags & ~KERNEL_MANAGED_EXTENTS:
        raise Refused("Unsupported inode flags (including nodump/noatime/sync)")
    return flags


def require_new_private_file(fd):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise Refused("New private file identity/permissions mismatch")


def snapshot(dfd, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME, dir_fd=dfd)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= 524288:
            raise Refused("Unsafe file type/link/size")
        inode_flags = read_inode_flags(fd)
        chunks = []
        while True:
            part = os.read(fd, 65536)
            if not part:
                break
            chunks.append(part)
            if sum(map(len, chunks)) > 524288:
                raise Refused("Read budget exceeded")
        data = b"".join(chunks)
        attrs = {k: os.getxattr(fd, k) for k in os.listxattr(fd)}
        after_flags = read_inode_flags(fd)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_gid, s.st_mode,
                              s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if (identity(before) != identity(after) or len(data) != before.st_size or
                inode_flags != after_flags):
            raise Refused("File changed during snapshot")
        return {"data": data, "sha256": sha(data), "uid": before.st_uid,
                "gid": before.st_gid, "mode": stat.S_IMODE(before.st_mode),
                "dev": before.st_dev, "ino": before.st_ino,
                "atime_ns": before.st_atime_ns, "mtime_ns": before.st_mtime_ns,
                "ctime_ns": before.st_ctime_ns, "attrs": attrs, "inode_flags": inode_flags}
    finally:
        os.close(fd)


def same(actual, expected):
    return all(actual[k] == expected[k] for k in (
        "sha256", "uid", "gid", "mode", "dev", "ino", "mtime_ns", "ctime_ns", "attrs", "inode_flags"))


def write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise Refused("Short write")
        view = view[count:]


def exclusive_file(dfd, name, data, original=None):
    fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
    try:
        # This descriptor was created with O_EXCL by us, never a foreign file.
        os.fchmod(fd, 0o600)
        require_new_private_file(fd)
        write_all(fd, data)
        if original is not None:
            os.fchown(fd, original["uid"], original["gid"])
            os.fchmod(fd, original["mode"])
            # Copy every readable xattr, including POSIX ACL and SELinux labels.
            # Failure is a refusal, never a reason to drop an attribute silently.
            for key in os.listxattr(fd):
                if key not in original["attrs"]:
                    os.removexattr(fd, key)
            for key, value in original["attrs"].items():
                os.setxattr(fd, key, value)
            os.utime(fd, ns=(original["atime_ns"], original["mtime_ns"]))
        if original is None:
            require_new_private_file(fd)
        os.fsync(fd)
    finally:
        os.close(fd)


class SyntheticHelper:
    def __init__(self, root, lease_seconds=30):
        self.owner_thread = threading.current_thread()
        self.closed = True
        self.fd = None
        self.lock = None
        if sys.platform != "linux":
            raise Refused("Linux-only; native execution NOT_RUN on other platforms")
        root = Path(root)
        if root.parent != Path("/tmp") or not root.name.startswith("hba-synthetic-"):
            raise Refused("Only explicit /tmp synthetic fixtures are supported")
        if type(lease_seconds) is not int or not 0 < lease_seconds <= 120:
            raise Refused("Invalid bounded lease")
        self.fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.closed = False
        try:
            info = os.fstat(self.fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise Refused("Unsafe fixture directory")
            marker = snapshot(self.fd, ".synthetic-hba")
            if marker["data"] != b"HBA_SYNTHETIC_ONLY_V1\n":
                raise Refused("Missing synthetic marker")
            self.lock = os.open(".coordination.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                                0o600, dir_fd=self.fd)
            lock_info = os.fstat(self.lock)
            if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1 or
                    lock_info.st_uid != os.geteuid() or stat.S_IMODE(lock_info.st_mode) != 0o600):
                raise Refused("Unsafe coordination lock")
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_identity = (lock_info.st_dev, lock_info.st_ino)
            # No implicit recovery/replay of an existing attempt.
            try:
                os.stat(".attempt.json", dir_fd=self.fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise Refused("Existing attempt: recovery-only, never replay")
            self.expires = time.monotonic() + lease_seconds
            self.original = snapshot(self.fd, "pg_hba.conf")
            self.state = "PREPARED"
            self.poisoned = False
            self.expected = self.original
            self._journal(self.state)
            exclusive_file(self.fd, ".original.bytes", self.original["data"])
            meta = {k: self.original[k] for k in ("sha256", "uid", "gid", "mode", "dev", "ino",
                                                 "atime_ns", "mtime_ns", "ctime_ns", "inode_flags")}
            meta["attrs"] = {k: base64.b64encode(v).decode() for k, v in self.original["attrs"].items()}
            exclusive_file(self.fd, ".original.metadata.json", json.dumps(meta).encode())
            os.fsync(self.fd)
        except BaseException:
            self.close()
            raise

    def close(self):
        self._thread_guard()
        if self.closed:
            return
        # Revoke the lifecycle before closing descriptors, even if close raises.
        self.closed = True
        lock, directory = self.lock, self.fd
        self.lock = None
        self.fd = None
        try:
            if lock is not None:
                os.close(lock)
        finally:
            if directory is not None:
                os.close(directory)

    def _lifecycle_guard(self):
        self._thread_guard()
        if (self.closed or type(self.fd) is not int or self.fd < 0 or
                type(self.lock) is not int or self.lock < 0):
            raise Refused("Closed/uninitialized helper: no filesystem operation")

    def _thread_guard(self):
        if threading.current_thread() is not self.owner_thread:
            raise Refused("Single-owner-thread helper; concurrent calls refused")

    def _journal(self, state):
        self._lifecycle_guard()
        temporary = ".journal-" + uuid.uuid4().hex
        data = json.dumps({"state": state, "original_sha256": self.original["sha256"],
                           "expected_sha256": self.expected["sha256"],
                           "expected_dev": self.expected["dev"], "expected_ino": self.expected["ino"],
                           "live": False}).encode()
        exclusive_file(self.fd, temporary, data)
        os.replace(temporary, ".attempt.json", src_dir_fd=self.fd, dst_dir_fd=self.fd)
        os.fsync(self.fd)
        self.state = state

    def _lock_valid(self):
        self._lifecycle_guard()
        try:
            info = os.fstat(self.lock)
            linked = os.stat(".coordination.lock", dir_fd=self.fd, follow_symlinks=False)
            return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                    (info.st_dev, info.st_ino) == self.lock_identity == (linked.st_dev, linked.st_ino))
        except (OSError, TypeError):
            return False

    def _guard(self):
        self._lifecycle_guard()
        if not self._lock_valid() or time.monotonic() >= self.expires:
            raise Refused("Coordination expired/lost")
        if not same(snapshot(self.fd, "pg_hba.conf"), self.expected):
            raise Refused("Concurrent file edit: never overwrite")

    def _replace(self, data, final_state, before_guard=None):
        self._lifecycle_guard()
        # Sticky refusal BEFORE any operation; only verified success clears it.
        self.poisoned = True
        self.state = "UNKNOWN_BLOCKED"
        temporary = ".staged-" + uuid.uuid4().hex
        exclusive_file(self.fd, temporary, data, self.original)
        candidate = snapshot(self.fd, temporary)
        if candidate["data"] != data or candidate["sha256"] != sha(data):
            raise Refused("Staged bytes differ from intended payload; never publish")
        for key in ("uid", "gid", "mode", "attrs", "mtime_ns", "inode_flags"):
            if candidate[key] != self.original[key]:
                raise Refused("Staged metadata mismatch")
        if before_guard is not None:
            before_guard()  # Synthetic adversarial injection, not a live hook.
        self._guard()
        self._journal("REPLACE_INTENT_UNKNOWN")
        self._guard()  # Repeat after durable journal latency.
        os.replace(temporary, "pg_hba.conf", src_dir_fd=self.fd, dst_dir_fd=self.fd)
        os.fsync(self.fd)
        actual = snapshot(self.fd, "pg_hba.conf")
        # Rename may change ctime; compare byte/attribute/inode identity instead.
        if any(actual[k] != candidate[k] for k in ("sha256", "uid", "gid", "mode", "attrs", "dev", "ino", "mtime_ns", "inode_flags")):
            raise Refused("Published object mismatch; retain unknown state")
        self.expected = actual
        self._journal(final_state)
        self.poisoned = False

    def install(self, before_guard=None):
        self._lifecycle_guard()
        if self.poisoned or self.state != "PREPARED":
            raise Refused("No repeated install")
        self._replace(PREFIX + self.original["data"], "FENCED", before_guard)

    def restore(self, *, outcome, target_closed, sessions_zero, prepared_zero,
                worker_disconnected, exact_catalog, before_guard=None):
        self._lifecycle_guard()
        if self.poisoned or self.state != "FENCED" or outcome not in ("COMMITTED", "ROLLED_BACK"):
            raise Refused("Unknown state/outcome: no automatic restore")
        if not all(x is True for x in (target_closed, sessions_zero, prepared_zero,
                                       worker_disconnected, exact_catalog)):
            raise Refused("Incomplete synthetic database acceptance")
        backup = snapshot(self.fd, ".original.bytes")
        if backup["sha256"] != self.original["sha256"]:
            raise Refused("Private backup mismatch")
        self._replace(backup["data"], "RESTORED", before_guard)

    def watchdog(self, outcome):
        self._lifecycle_guard()
        # Deliberately observe-only: no restore/reload/DB opening in any branch.
        try:
            intact = same(snapshot(self.fd, "pg_hba.conf"), self.expected)
        except Exception:
            intact = False
        return {"action": "NONE_KEEP_CLOSED_REQUIRE_REVIEW", "file_matches": intact,
                "outcome_known": outcome in ("COMMITTED", "ROLLED_BACK"),
                "coordination_valid": self._lock_valid() and time.monotonic() < self.expires,
                "state": self.state, "live": False}


def execute_live(*args, **kwargs):
    raise Refused("LIVE DISABLED: synthetic helper only; no production/reload adapter")
