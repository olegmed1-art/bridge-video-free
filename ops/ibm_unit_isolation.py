#!/usr/bin/env python3
"""Bounded, guest-only IBM unit isolation. Importing this module has no effects.

The reviewed controller owns provider identity, freshness, strict pinned SSH,
noninteractive privilege escalation, the Start-relative deadline and instance
Stop in all outcomes. This module neither proves provider identity nor has any
network, credential, workload, environment-file or log access. main() is the
only production entrypoint; dependency injection exists for offline tests.

Configuration bytes are retained ONLY inside the root-private boot-root archive.
The archive is a bounded JSON manifest with base64 bytes/xattrs plus an actual
private staging restore. Only additive, exclusive files are written in systemd.
No automatic rollback is performed. A stopped service may have already written
application data; configuration restoration cannot undo those writes.
"""
from __future__ import annotations

import time
_SCRIPT_STARTED = time.monotonic() if __name__ == "__main__" else None

import base64
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import PurePosixPath
import platform
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

REQUEST_SCHEMA = "bridge.ibm.unit-isolation.request.v1"
RECEIPT_SCHEMA = "bridge.ibm.unit-isolation.receipt.v6"
RESUME_SNAPSHOT_ID = "38123951485-e599b3b39325"
RESUME_SNAPSHOT_SHA256 = "1bd1490b28c4b184729bc31b7650f83dd372af1fe545e434c4695971f0626c92"
RESUME_ORIGINAL_HEAD = "e599b3b39325de4f05ec8dc7631295c1d6085984"
IDENTITY = {
    "instance_id": "02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2",
    "instance_name": "bridge-school-compute-ibm",
    "boot_volume_id": "r010-430d0d70-3cdf-4f0c-8709-53e0ee0c4d7e",
    "image_id": "r010-25f84546-413c-4476-83ee-ae9580f554e5",
    "image_name": "bridge-ibm-before-start-20261001",
    "profile": "bx3dc-8x40",
}
TRANSPORT = {
    "ibm_host": "161.156.86.34",
    "ibm_hostkey_sha256": "SHA256:o+7KPm2p4VSZSvFFYzIIzA8iiXfze2hhW9tUM58e00w",
    "oracle_host": "92.5.47.149",
    "oracle_hostkey_sha256": "SHA256:XBR1x74uJ41BxmDF7Y9P20GjIjNbrYXqieV4c2MC0Go",
    "strict_host_key_checking": "yes",
    "known_hosts_source": "sealed-pinned-record",
}
OBSERVER = "assistant-lab-observer.service"
CONTROL = "assistant-lab-control.service"
BRIDGE = "assistant-lab-control-bridge.service"
TIMER = "bridge-ben-healthcheck.timer"
HEALTH = "bridge-ben-healthcheck.service"
BEN = "bridge-ben.service"
TARGETS = (OBSERVER, CONTROL, BRIDGE, TIMER)
STOP_ORDER = (TIMER, BRIDGE, CONTROL, OBSERVER)
COMPANIONS = (HEALTH, BEN)
GATE_UNITS = TARGETS + (BEN,)
PENDING_GATE_SECONDS = 45
COMPANION_PHASES = ("PREFLIGHT", "AFTER_SNAPSHOT", "BEFORE_GATE", "BEFORE_RELOAD",
                    "AFTER_RELOAD", "BEFORE_STOP", "WAIT_STOP", "AFTER_STOP", "FINAL_VERIFY",
                    "ROLLBACK_PREFLIGHT", "ROLLBACK_BEFORE_REMOVE", "ROLLBACK_AFTER_RELOAD")
COMPANION_PREDICATES = ("ACTIVE_STATE", "SUB_STATE", "JOB_PENDING", "MAIN_PID_NONZERO", "CONTROL_PID_NONZERO")
COMPANION_ELAPSED_MAX_MS = 600000  # Export bound only; never a runtime deadline.
UNITS = TARGETS + COMPANIONS
PRIVATE_TMP_UNITS = frozenset((OBSERVER, CONTROL, BRIDGE, HEALTH))
# systemd v255 unit_add_exec_dependencies(), src/core/unit.c L1227-L1241.
# Only these exact forward edges are justified by an observed PrivateTmp=yes.
PRIVATE_TMP_EDGES = {"Wants": frozenset(("tmp.mount",)),
                     "After": frozenset(("tmp.mount", "systemd-tmpfiles-setup.service"))}
SYSTEM_DIRS = ("/etc/systemd/system", "/run/systemd/system", "/usr/lib/systemd/system")
RECOVERY_ROOT = "/var/lib/bridge-school-maintenance"
GUEST_SECONDS = 240
MAX_FILES = 32
MAX_BYTES = 256 * 1024
MAX_INPUT = 8192
MAX_OUTPUT = 32768
GRAPH_MAX_FAILURES = 16
GRAPH_MAX_UNEXPECTED = 8
GRAPH_MAX_BYTES = 8192
GRAPH_MAX_TOKENS = 8192  # parse_show's existing per-line bound is stricter.
UNIT_NAME_MAX = 255
UNIT_NAME_PATTERN = re.compile(
    r"(?:[A-Za-z0-9:_.-]|\\x(?!00)[0-9a-f]{2})+"
    r"(?:@(?:[A-Za-z0-9:_.-]|\\x(?!00)[0-9a-f]{2})*)?"
    r"\.(?:service|socket|device|mount|automount|swap|target|path|timer|slice|scope)", re.ASCII)
LIMITATIONS = ["EXPECTED_MACHINE_ID_UNAVAILABLE", "TRANSPORT_IDENTITY_ASSERTION_ONLY",
               "NO_REBOOT_VERIFICATION", "LOADED_GRAPH_ONLY", "DAEMON_RELOAD_GLOBAL_EFFECTS"]
EDGES = ("Requires", "Requisite", "Wants", "BindsTo", "PartOf", "Upholds",
         "RequiredBy", "RequisiteOf", "WantedBy", "BoundBy", "ConsistsOf", "UpheldBy",
         "Conflicts", "ConflictedBy", "Before", "After", "Triggers", "TriggeredBy",
         "PropagatesStopTo", "StopPropagatedFrom", "OnFailure", "OnSuccess")
COMMON = ("Id", "Names", "LoadState", "ActiveState", "SubState", "UnitFileState",
          "FragmentPath", "DropInPaths", "Job", "Following",
          "Transient", "NeedDaemonReload", "StopWhenUnneeded", "RefuseManualStop",
          "FailureAction", "SuccessAction", "JobTimeoutAction") + EDGES
SERVICE = ("ControlGroup", "MainPID", "ControlPID", "TimeoutStopUSec",
           "KillMode", "KillSignal", "SendSIGKILL", "FinalKillSignal",
           "TimeoutStopFailureMode", "Restart", "PrivateTmp")
SAFE_STATES = {"active", "inactive", "activating", "deactivating", "reloading", "failed"}
SAFE_SUB = {"running", "waiting", "dead", "exited", "start", "stop", "stop-sigterm",
            "stop-sigkill", "stop-post", "failed", "auto-restart"}
REASONS = {"OK", "REQUEST_INVALID", "HOST_MISMATCH", "ROOT_REQUIRED", "DEADLINE",
           "PARENT_LOST", "CANCELLED", "COMMAND_FAILED", "COMMAND_LIMIT", "METADATA_INVALID",
           "ALIAS_OR_UNIT_MISMATCH", "UNKNOWN_EDGE", "STOP_HANDLER", "STOP_POLICY",
           "COMPANION_BUSY", "UNIT_BUSY", "PATH_UNSAFE", "FILE_LIMIT", "FILE_DRIFT",
           "COLLISION", "ARCHIVE_FAILED", "RESTORE_MISMATCH", "GATE_NOT_EFFECTIVE",
           "CGROUP_NOT_EMPTY", "STOP_INCOMPLETE", "BUDGET_INSUFFICIENT", "INTERNAL_BLOCKED",
           "ROLLBACK_INVALID", "ROLLBACK_DRIFT", "CONFIG_ONLY_RESTORED",
           "PENDING_GATE_RACE", "PENDING_GATE_TIMEOUT", "PENDING_GATE_UNVERIFIED"}
REASON_CODES = frozenset(REASONS)


class Blocked(Exception):
    """A fixed, safe error code, never raw subprocess/file data."""


class EdgeBlocked(Blocked):
    """Only graph rejections may be collected across initial exact-unit reads."""


def need(value, reason):
    if not value:
        raise Blocked(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def is_canonical_unit_name(value):
    """A diagnostic-only syntax check; never expands the exact edge allowlist.

    No normalization: retain only the exact canonical ASCII spelling, including
    lower-case systemd hex escapes. Commands, paths, URLs and arbitrary strings
    cannot pass. This cannot infer whether a syntactically valid name is secret.
    """
    return (type(value) is str and len(value) <= UNIT_NAME_MAX
            and UNIT_NAME_PATTERN.fullmatch(value) is not None)


class GraphEvidence:
    """Bounded metadata-only failure evidence; no configuration or command text."""
    def __init__(self):
        self.failures = []
        self.truncated = False

    def value(self):
        return {"failures": self.failures, "truncated": self.truncated}

    def add(self, unit, field, violation, raw, candidates):
        if len(self.failures) >= GRAPH_MAX_FAILURES:
            self.truncated = True
            return
        tokens = raw.split()
        names = []
        seen = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if not is_canonical_unit_name(candidate):
                continue
            if len(names) >= GRAPH_MAX_UNEXPECTED:
                self.truncated = True
                continue
            names.append(candidate)
        failure = {"unit": unit, "property": field, "violation": violation,
                   "token_count": len(tokens), "unexpected": names,
                   "omitted_count": len(tokens) - len(names),
                   "value_sha256": digest(raw.encode("utf-8"))}
        self.failures.append(failure)
        # Bound the complete canonical object, including keys and escaping.
        # Prefer preserving another failure's identity and digest to its names.
        while len(canonical(self.value())) > GRAPH_MAX_BYTES:
            self.truncated = True
            donor = max(self.failures, key=lambda item: len(canonical(item["unexpected"])))
            if donor["unexpected"]:
                donor["unexpected"].pop()
                donor["omitted_count"] += 1
            else:
                self.failures.pop()
                break


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "REQUEST_INVALID")
            result[key] = value
        return result
    def invalid(_):
        raise Blocked("REQUEST_INVALID")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, TypeError, UnicodeError):
        raise Blocked("REQUEST_INVALID") from None


def validate_request(p, *, rollback=False, resume=False):
    keys = {"schema", "operation", "run_id", "attempt", "head", "identity", "transport"}
    need(not (rollback and resume), "REQUEST_INVALID")
    if rollback or resume:
        keys |= {"snapshot_id", "snapshot_sha256"}
    need(type(p) is dict and set(p) == keys, "REQUEST_INVALID")
    need(p["schema"] == REQUEST_SCHEMA and p["operation"] == ("rollback" if rollback else "resume" if resume else "isolate"), "REQUEST_INVALID")
    need(type(p["run_id"]) is str and re.fullmatch(r"[1-9][0-9]{0,19}", p["run_id"]), "REQUEST_INVALID")
    need(type(p["attempt"]) is int and p["attempt"] == 1, "REQUEST_INVALID")
    need(type(p["head"]) is str and re.fullmatch(r"[0-9a-f]{40}", p["head"]), "REQUEST_INVALID")
    need(p["identity"] == IDENTITY and p["transport"] == TRANSPORT, "REQUEST_INVALID")
    if rollback or resume:
        need(type(p["snapshot_id"]) is str and re.fullmatch(r"[1-9][0-9]{0,19}-[0-9a-f]{12}", p["snapshot_id"]), "ROLLBACK_INVALID")
        need(type(p["snapshot_sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", p["snapshot_sha256"]), "ROLLBACK_INVALID")
    if resume:
        need(p["snapshot_id"] == RESUME_SNAPSHOT_ID and p["snapshot_sha256"] == RESUME_SNAPSHOT_SHA256
             and p["run_id"] != RESUME_SNAPSHOT_ID.split("-")[0] and p["head"] != RESUME_ORIGINAL_HEAD,
             "ROLLBACK_INVALID")
    return p


class Budget:
    def __init__(self, clock=time.monotonic, started=None):
        self.clock = clock
        self.end = (clock() if started is None else started) + GUEST_SECONDS

    def left(self, reserve=0):
        left = self.end - self.clock()
        need(left > reserve, "DEADLINE" if not reserve else "BUDGET_INSUFFICIENT")
        return left


def bounded_command(argv, *, timeout, limit=MAX_OUTPUT):
    """No shell; bounded combined output; never export stderr. Own process group only.

    The Python exec shim installs PDEATHSIG before exec. start_new_session gives
    this command a distinct group; timeout cleanup cannot kill ssh/systemd/units.
    """
    need(timeout > 0 and timeout <= GUEST_SECONDS and limit <= MAX_OUTPUT, "DEADLINE")
    parent = os.getpid()
    boot = ("import ctypes,os,signal; p=" + str(parent) + ";"
            "assert ctypes.CDLL(None).prctl(1,signal.SIGKILL)==0 and os.getppid()==p;"
            "a=" + repr(list(argv)) + ";os.execv(a[0],a)")
    selector = selectors.DefaultSelector()
    try:
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", boot],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 start_new_session=True,
                                 env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                                      "SYSTEMD_PAGER": "", "SYSTEMD_COLORS": "0"})
    except BaseException:
        selector.close()
        raise
    output = bytearray()
    size = 0
    end = time.monotonic() + timeout
    try:
        for stream, label in ((child.stdout, "out"), (child.stderr, "err")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map():
            left = end - time.monotonic()
            need(left > 0, "DEADLINE")
            for key, _ in selector.select(min(left, .1)):
                part = os.read(key.fd, 4096)
                if not part:
                    selector.unregister(key.fileobj)
                else:
                    size += len(part)
                    need(size <= limit, "COMMAND_LIMIT")
                    if key.data == "out":
                        output.extend(part)
        # Observe exit without reaping: the leader's zombie keeps its PID/PGID
        # unavailable for reuse until all owned descendants have been killed.
        while True:
            need(time.monotonic() < end, "DEADLINE")
            status = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
            if status is not None:
                break
            time.sleep(min(.01, max(0, end - time.monotonic())))
        need(status.si_code == os.CLD_EXITED and status.si_status == 0, "COMMAND_FAILED")
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise Blocked("DEADLINE") from None
    finally:
        selector.close()
        # Always clean the anchored group, including a closed-stdio descendant
        # of a successfully exited leader. Never reap before this killpg.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=1)
        child.stdout.close()
        child.stderr.close()


class RootFS:
    """Descriptor-relative no-follow access; a different root is tests-only.

    Root-owned, non-group/world-writable directory chains protect all mutations.
    Linux permits symlink xattrs through /proc/self/fd/<held-parent>/<basename>;
    no final symlink is followed. Regular file reads and attributes use one fd.
    """
    def __init__(self, root="/"):
        self.root = os.fspath(root)
        self.uid = os.geteuid()
        self.device = os.stat(self.root).st_dev

    @staticmethod
    def parts(path):
        need(type(path) is str and path.startswith("/") and "\x00" not in path
             and str(PurePosixPath(path)) == path and all(x not in (".", "..") for x in path.split("/")[1:]), "PATH_UNSAFE")
        return path.split("/")[1:]

    @contextmanager
    def directory(self, path):
        parts = self.parts(path) if path != "/" else []
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            for name in parts:
                nxt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                os.close(fd)
                fd = nxt
                st = os.fstat(fd)
                need(st.st_uid == self.uid and not st.st_mode & 0o022, "PATH_UNSAFE")
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def parent(self, path):
        parts = self.parts(path)
        need(bool(parts) and all(parts), "PATH_UNSAFE")
        with self.directory("/" + "/".join(parts[:-1]) if len(parts) > 1 else "/") as fd:
            yield fd, parts[-1]

    def exists(self, path):
        try:
            with self.parent(path) as (fd, name):
                os.stat(name, dir_fd=fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def mkdir(self, path, *, mode=0o700, exclusive=True):
        with self.parent(path) as (fd, name):
            try:
                os.mkdir(name, mode, dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:
                need(not exclusive, "COLLISION")
        with self.directory(path) as fd:
            st = os.fstat(fd)
            need(st.st_dev == self.device, "PATH_UNSAFE")
            if mode == 0o700:
                need(stat.S_IMODE(st.st_mode) == mode, "PATH_UNSAFE")

    def listdir(self, path, limit=2048):
        with self.directory(path) as fd:
            with os.scandir(fd) as entries:
                result = []
                for entry in entries:
                    result.append((entry.name, entry.is_dir(follow_symlinks=False), entry.is_symlink()))
                    need(len(result) <= limit, "FILE_LIMIT")
                return sorted(result)

    def read(self, path, limit=MAX_BYTES):
        with self.parent(path) as (fd, name):
            filefd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd)
            try:
                st = os.fstat(filefd)
                need(stat.S_ISREG(st.st_mode) and st.st_size <= limit, "FILE_LIMIT")
                data = bytearray()
                while True:
                    part = os.read(filefd, min(65536, limit + 1 - len(data)))
                    if not part:
                        break
                    data.extend(part)
                    need(len(data) <= limit, "FILE_LIMIT")
                now = os.fstat(filefd)
                need((st.st_ino, st.st_size, st.st_mtime_ns) == (now.st_ino, now.st_size, now.st_mtime_ns), "FILE_DRIFT")
                return bytes(data)
            finally:
                os.close(filefd)

    def record(self, path):
        with self.parent(path) as (fd, name):
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            need(st.st_uid == self.uid and not st.st_mode & 0o022 or stat.S_ISLNK(st.st_mode), "PATH_UNSAFE")
            common = {"mode": stat.S_IMODE(st.st_mode), "uid": st.st_uid, "gid": st.st_gid,
                      "mtime_ns": st.st_mtime_ns}
            if stat.S_ISREG(st.st_mode):
                filefd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd)
                try:
                    before = os.fstat(filefd)
                    need(before.st_ino == st.st_ino and before.st_dev == st.st_dev and before.st_size <= MAX_BYTES, "FILE_DRIFT")
                    data = bytearray()
                    while True:
                        part = os.read(filefd, min(65536, MAX_BYTES + 1 - len(data)))
                        if not part:
                            break
                        data.extend(part)
                        need(len(data) <= MAX_BYTES, "FILE_LIMIT")
                    attrs = self.xattrs(filefd)
                    after = os.fstat(filefd)
                    need((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                         (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), "FILE_DRIFT")
                    common.update(kind="file", data=base64.b64encode(data).decode(), sha256=digest(data), xattrs=attrs)
                finally:
                    os.close(filefd)
            elif stat.S_ISLNK(st.st_mode):
                need(st.st_uid == self.uid, "PATH_UNSAFE")
                link = os.readlink(name, dir_fd=fd)
                need(len(link.encode()) <= 4096 and "\x00" not in link, "FILE_LIMIT")
                proxy = "/proc/self/fd/" + str(fd) + "/" + name
                common.update(kind="symlink", link=link, xattrs=self.xattrs(proxy, follow=False))
            else:
                raise Blocked("PATH_UNSAFE")
            after = os.stat(name, dir_fd=fd, follow_symlinks=False)
            need((st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns) ==
                 (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns), "FILE_DRIFT")
            common.update(path=path, dev=st.st_dev, ino=st.st_ino, ctime_ns=st.st_ctime_ns)
            return common

    @staticmethod
    def xattrs(target, follow=True):
        args = {} if type(target) is int else {"follow_symlinks": follow}
        names = os.listxattr(target, **args)
        need(len(names) <= 32, "FILE_LIMIT")
        result = {}
        for name in sorted(names):
            value = os.getxattr(target, name, **args)
            need(len(value) <= 16384, "FILE_LIMIT")
            result[name] = base64.b64encode(value).decode()
        need(len(canonical(result)) <= 32768, "FILE_LIMIT")
        return result

    def write_new(self, path, data, *, mode=0o400):
        need(len(data) <= 2 * MAX_BYTES, "FILE_LIMIT")
        with self.parent(path) as (fd, name):
            out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=fd)
            try:
                offset = 0
                while offset < len(data):
                    count = os.write(out, data[offset:])
                    need(count > 0, "ARCHIVE_FAILED")
                    offset += count
                os.fchmod(out, mode)
                os.fsync(out)
            finally:
                os.close(out)
            os.fsync(fd)

    def restore_record(self, record, path):
        with self.parent(path) as (fd, name):
            if record["kind"] == "file":
                self.write_new(path, base64.b64decode(record["data"], validate=True), mode=0o600)
                out = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                try:
                    os.fchown(out, record["uid"], record["gid"])
                    os.fchmod(out, record["mode"])
                    for key, value in record["xattrs"].items():
                        os.setxattr(out, key, base64.b64decode(value, validate=True))
                    os.utime(out, ns=(record["mtime_ns"], record["mtime_ns"]))
                    os.fsync(out)
                finally:
                    os.close(out)
            else:
                os.symlink(record["link"], name, dir_fd=fd)
                os.chown(name, record["uid"], record["gid"], dir_fd=fd, follow_symlinks=False)
                proxy = "/proc/self/fd/" + str(fd) + "/" + name
                for key, value in record["xattrs"].items():
                    os.setxattr(proxy, key, base64.b64decode(value, validate=True), follow_symlinks=False)
                os.utime(name, ns=(record["mtime_ns"], record["mtime_ns"]), dir_fd=fd, follow_symlinks=False)
            os.fsync(fd)

    def unlink_owned(self, path, record):
        need(self.record(path) == record, "ROLLBACK_DRIFT")
        with self.parent(path) as (fd, name):
            now = os.stat(name, dir_fd=fd, follow_symlinks=False)
            need((now.st_dev, now.st_ino, now.st_ctime_ns) == (record["dev"], record["ino"], record["ctime_ns"]), "ROLLBACK_DRIFT")
            os.unlink(name, dir_fd=fd)
            os.fsync(fd)


def restorable(record):
    return {k: v for k, v in record.items() if k not in ("path", "dev", "ino", "ctime_ns")}


def local_host_check(fs):
    need(os.geteuid() == 0, "ROOT_REQUIRED")
    need(platform.system() == "Linux" and platform.machine() == "x86_64", "HOST_MISMATCH")
    osrel = fs.read("/usr/lib/os-release", 8192).decode("utf-8", "strict")
    fields = dict(line.split("=", 1) for line in osrel.splitlines() if "=" in line and not line.startswith("#"))
    need(fields.get("ID", "").strip('"') == "ubuntu" and fields.get("VERSION_ID", "").strip('"') == "24.04", "HOST_MISMATCH")
    need(fs.exists("/sys/fs/cgroup/cgroup.controllers"), "HOST_MISMATCH")


def unit_path(path, unit, *, dropin=False):
    need(type(path) is str and not any(c.isspace() for c in path), "PATH_UNSAFE")
    RootFS.parts(path)
    parent = str(PurePosixPath(path).parent)
    if dropin:
        # Generic/template/truncated-name dropins may carry unexpected behavior;
        # this maintenance candidate deliberately refuses those compatibility cases.
        need(parent in {root + "/" + unit + ".d" for root in SYSTEM_DIRS}
             and re.fullmatch(r"[A-Za-z0-9_.-]+\.conf", PurePosixPath(path).name), "PATH_UNSAFE")
    else:
        need(path in {root + "/" + unit for root in SYSTEM_DIRS}, "PATH_UNSAFE")
    return path


def parse_show(raw, fields):
    need(len(raw) <= MAX_OUTPUT, "COMMAND_LIMIT")
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeError:
        raise Blocked("METADATA_INVALID") from None
    result = {}
    for line in text.splitlines():
        need("=" in line and len(line) <= 8192, "METADATA_INVALID")
        key, value = line.split("=", 1)
        need(key in fields and key not in result, "METADATA_INVALID")
        result[key] = value
    need(set(result) == set(fields), "METADATA_INVALID")
    return result


def parse_conditions(raw):
    value = strict_json(raw)
    need(type(value) is dict and set(value) == {"type", "data"} and value["type"] == "a(sbbsi)"
         and type(value["data"]) is list and len(value["data"]) <= 32, "METADATA_INVALID")
    for row in value["data"]:
        need(type(row) is list and len(row) == 5 and type(row[0]) is str
             and re.fullmatch(r"Condition[A-Za-z]+", row[0]) and type(row[1]) is bool
             and type(row[2]) is bool and type(row[3]) is str and len(row[3]) <= 4096
             and type(row[4]) is int and row[4] in (-1, 0, 1), "METADATA_INVALID")
    # Last result is cached and irrelevant. Comparison uses predicate only.
    return sorted([row[:4] for row in value["data"]], key=canonical)


def validate_edges(unit, row, *, evidence=None, systemd_255=False):
    evidence = GraphEvidence() if evidence is None else evidence
    failed = False
    ordering = {"basic.target", "sysinit.target", "system.slice", "shutdown.target",
                "network-online.target", "multi-user.target", "timers.target", "time-set.target",
                "time-sync.target", "systemd-journald.socket", "-.mount"} | set(UNITS)
    direct = {OBSERVER: set(), CONTROL: {OBSERVER}, BRIDGE: {OBSERVER, CONTROL}, TIMER: set(), HEALTH: set(), BEN: {"docker.service"}}
    if unit == BEN:
        ordering.add("docker.service")  # Known, read-only companion edge; no Docker operation.
    reverse = {OBSERVER: {CONTROL, BRIDGE}, CONTROL: {BRIDGE}, BRIDGE: set(), TIMER: set(), HEALTH: set(), BEN: set()}
    for field in EDGES:
        values = row[field].split()
        if len(values) > 32:
            evidence.add(unit, field, "CARDINALITY", row[field], values)
            failed = True
        seen, repeated = set(), set()
        for value in values:
            if value in seen:
                repeated.add(value)
            seen.add(value)
        if repeated:
            evidence.add(unit, field, "DUPLICATE_MEMBER", row[field],
                         [value for value in values if value in repeated])
            failed = True
        allowed = set()
        if field in ("Before", "After"):
            allowed = ordering
        elif field == "Requires":
            allowed = {"sysinit.target", "system.slice", "-.mount"} | direct[unit]
        elif field == "RequiredBy":
            allowed = reverse[unit]
        elif field == "Wants":
            allowed = {"network-online.target"}
        elif field == "WantedBy":
            allowed = {"multi-user.target"} if unit.endswith(".service") else {"timers.target"}
        elif field in ("Conflicts", "ConflictedBy"):
            allowed = {"shutdown.target"}
        elif field == "Triggers" and unit == TIMER:
            allowed = {HEALTH}
        elif field == "TriggeredBy" and unit == HEALTH:
            allowed = {TIMER}
        if systemd_255 is True and unit in PRIVATE_TMP_UNITS and row.get("PrivateTmp") == "yes":
            # Union creates a new set: never mutate shared ordering or admit a
            # reverse Before edge because an earlier After edge was permitted.
            allowed = allowed | PRIVATE_TMP_EDGES.get(field, frozenset())
        if not set(values) <= allowed:
            evidence.add(unit, field, "UNEXPECTED_MEMBER", row[field],
                         [value for value in values if value not in allowed])
            failed = True
    if not (row["Triggers"] == HEALTH if unit == TIMER else not row["Triggers"]):
        evidence.add(unit, "Triggers", "TRIGGER_MISMATCH", row["Triggers"], row["Triggers"].split())
        failed = True
    if failed:
        raise EdgeBlocked("UNKNOWN_EDGE")


def stop_seconds(value):
    # systemctl formats USec properties as timespans (systemd v255). Deliberately
    # accept only a single finite seconds term, sufficient for reviewed 15/20s.
    need(re.fullmatch(r"(?:[1-9][0-9]?)(?:\.\d{1,3})?s", value), "STOP_POLICY")
    duration = float(value[:-1])
    need(0 < duration <= 20, "STOP_POLICY")
    return duration


JOB_DIAG_SECONDS = 20
JOB_DIAG_BYTES = 12288
JOB_DIAG_INPUT_BYTES = 131072  # Includes bounded_command's final 4096-byte detection chunk.
JOB_DIAG_CALLS = 16
JOB_DIAG_CALL_BYTES = 4096
JOB_DIAG_UNITS = frozenset(UNITS) | frozenset((
    "docker.service", "docker.socket", "network-online.target", "network.target",
    "systemd-networkd-wait-online.service", "NetworkManager-wait-online.service",
    "basic.target", "sysinit.target", "system.slice", "shutdown.target", "multi-user.target",
    "timers.target", "time-set.target", "time-sync.target", "systemd-journald.socket",
    "-.mount", "tmp.mount", "systemd-tmpfiles-setup.service"))
JOB_DIAG_TYPES = frozenset(("start", "verify-active", "stop", "reload", "restart", "try-restart", "reload-or-start"))
JOB_DIAG_EXEC = ("ExecCondition", "ExecStartPre", "ExecStart", "ExecStartPost", "ExecStop", "ExecStopPost")
JOB_DIAG_GRAPH = ("Before", "After", "Requires", "Wants", "Triggers", "TriggeredBy")
JOB_DIAG_HANDLER_CODES = frozenset(("UNKNOWN", "DOCKER_STOP_BEN_10S", "DOCKER_REMOVE_BEN_FORCE",
                                  "DOCKER_RUN_BEN_PINNED", "HEALTHCHECK_EXECUTABLE_ONLY"))
JOB_DIAG_POLICY = (("Type", "s"), ("Restart", "s"), ("RestartUSec", "t"),
    ("TimeoutStartUSec", "t"), ("TimeoutStopUSec", "t"), ("TimeoutStopFailureMode", "s"),
    ("KillMode", "s"), ("KillSignal", "i"), ("SendSIGKILL", "b"), ("FinalKillSignal", "i"))
JOB_DIAG_STAGES = frozenset(("job", "before", "after", "policy_ben", "policy_health",
    "blocker_0", "blocker_1", "blocker_2", "blocker_3", "final_ben", "final_health", "final_timer"))
JOB_DIAG_ERRORS = frozenset(("READ_FAILED", "TIMEOUT", "LIMIT", "METADATA_INVALID"))


def job_unit_path(unit):
    need(unit in JOB_DIAG_UNITS, "METADATA_INVALID")
    return "/org/freedesktop/systemd1/unit/" + "".join(
        c if c.isalnum() else "_" + format(ord(c), "02x") for c in unit)


def job_id(value):
    need(type(value) is int and 0 <= value <= 4294967295, "METADATA_INVALID")
    return value


def job_enum(value, allowed):
    need(type(value) is str and len(value) <= 255, "METADATA_INVALID")
    return value if value in allowed else "UNKNOWN"


def job_handler(raw):
    # No free-form argv, credential path, shell text or environment is exported.
    need(type(raw) is list and len(raw) <= 8, "METADATA_INVALID")
    result = []
    for row in raw:
        need(type(row) is list and len(row) == 10 and type(row[0]) is str and type(row[1]) is list
             and len(row[1]) <= 64 and all(type(a) is str and len(a) <= 1024 for a in row[1])
             and type(row[2]) is bool and all(type(n) is int for n in row[3:]), "METADATA_INVALID")
        path, argv = row[:2]
        code = "UNKNOWN"
        if path == "/usr/bin/docker" and argv == [path, "stop", "-t", "10", BEN.removesuffix(".service")]:
            code = "DOCKER_STOP_BEN_10S"
        elif path == "/usr/bin/docker" and argv == [path, "rm", "-f", "bridge-ben"]:
            code = "DOCKER_REMOVE_BEN_FORCE"
        elif path == "/usr/local/sbin/bridge-ben-healthcheck" and argv == [path]:
            code = "HEALTHCHECK_EXECUTABLE_ONLY"
        prefix = ["/usr/bin/docker", "run", "--rm", "--name", "bridge-ben", "--pull=never", "--read-only",
                  "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--pids-limit=256", "--memory=6g",
                  "--memory-swap=6g", "--cpus=2.0", "--shm-size=512m", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=1g",
                  "--tmpfs", "/logs:rw,nosuid,nodev,noexec,size=64m", "--tmpfs", "/app/logs:rw,nosuid,nodev,noexec,size=64m",
                  "-p", "127.0.0.1:8085:8085"]
        if path == "/usr/bin/docker" and len(argv) == len(prefix) + 1 and argv[:-1] == prefix and re.fullmatch(
                r"ghcr\.io/lorserker/ben@sha256:[0-9a-f]{64}", argv[-1]):
            code = "DOCKER_RUN_BEN_PINNED"
        result.append({"action": code, "ignore_failure": row[2]})
    return result


class JobDiagnostic:
    """Observe one already refused BEN job. Never resumes maintenance."""
    def __init__(self, op, observed):
        self.op, self.clock = op, op.budget.clock
        self.end = min(op.budget.end, self.clock() + JOB_DIAG_SECONDS)
        self.calls = self.bytes = 0
        self.value = dict(status="PARTIAL", observed_job_id=job_id(observed), started_ms=self.elapsed(),
            finished_ms=self.elapsed(), job=None, before=None, after=None, policies=[], blockers=[],
            final_states=[], context=[], errors=[], race=None)

    def elapsed(self):
        return max(0, min(COMPANION_ELAPSED_MAX_MS, int((self.clock() - (self.op.budget.end - GUEST_SECONDS)) * 1000)))

    def command(self, args):
        left = self.end - self.clock()
        need(left > 0, "DEADLINE")
        need(self.calls < JOB_DIAG_CALLS and self.bytes + JOB_DIAG_CALL_BYTES + 4096 <= JOB_DIAG_INPUT_BYTES, "COMMAND_LIMIT")
        self.calls += 1
        # Reserve the whole combined stdout/stderr allowance before spawning;
        # failures, discarded stderr and the final detection read all count.
        allowance = JOB_DIAG_CALL_BYTES
        self.bytes += allowance + 4096
        raw = self.op.runner(["/usr/bin/busctl", "--system", "--no-pager", "--json=short"] + args,
            timeout=min(3, left), limit=allowance)
        need(type(raw) is bytes, "METADATA_INVALID")
        need(len(raw) <= allowance, "COMMAND_LIMIT")
        need(self.clock() < self.end, "DEADLINE")
        return raw

    def properties(self, path, interface, specs):
        raw = self.command(["get-property", "org.freedesktop.systemd1", path,
                            "org.freedesktop.systemd1." + interface] + [k for k, _ in specs])
        lines = raw.splitlines()
        need(len(lines) == len(specs), "METADATA_INVALID")
        values = {}
        for line, (name, signature) in zip(lines, specs):
            value = strict_json(line)
            need(type(value) is dict and set(value) == {"type", "data"} and value["type"] == signature, "METADATA_INVALID")
            data = value["data"]
            need((signature not in {"s", "u", "t", "i", "b"}) or
                 type(data) is {"s": str, "u": int, "t": int, "i": int, "b": bool}[signature], "METADATA_INVALID")
            values[name] = data
        return values

    def job(self, identifier):
        row = self.properties("/org/freedesktop/systemd1/job/" + str(identifier), "Job",
                              (("Id", "u"), ("Unit", "(so)"), ("JobType", "s"), ("State", "s")))
        need(row["Id"] == identifier and row["Unit"] == [BEN, job_unit_path(BEN)], "METADATA_INVALID")
        return {"id": identifier, "unit": BEN, "type": job_enum(row["JobType"], JOB_DIAG_TYPES),
                "state": job_enum(row["State"], {"waiting", "running"})}

    def dependencies(self, identifier, method):
        value = strict_json(self.command(["call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1/job/" + str(identifier),
                                         "org.freedesktop.systemd1.Job", method]))
        need(type(value) is dict and set(value) == {"type", "data"} and value["type"] == "a(usssoo)"
             and type(value["data"]) is list and len(value["data"]) == 1 and type(value["data"][0]) is list, "METADATA_INVALID")
        rows = value["data"][0]  # busctl call returns a tuple of output arguments.
        need(len(rows) <= 16, "COMMAND_LIMIT")
        result = []
        for row in rows:
            need(type(row) is list and len(row) == 6, "METADATA_INVALID")
            jid = job_id(row[0]); need(jid > 0 and row[4] == "/org/freedesktop/systemd1/job/" + str(jid), "METADATA_INVALID")
            unit = job_enum(row[1], JOB_DIAG_UNITS)
            need(type(row[5]) is str and len(row[5]) <= 1024, "METADATA_INVALID")
            if unit != "UNKNOWN":
                need(row[5] == job_unit_path(unit), "METADATA_INVALID")
            result.append(dict(id=jid, unit=unit, type=job_enum(row[2], JOB_DIAG_TYPES),
                               state=job_enum(row[3], {"waiting", "running"})))
        need(len({r["id"] for r in result}) == len(result), "METADATA_INVALID")
        return result

    def policy(self, unit):
        row = self.properties(job_unit_path(unit), "Service", JOB_DIAG_POLICY + tuple((n, "a(sasbttttuii)") for n in JOB_DIAG_EXEC))
        durations = {}
        for name in ("RestartUSec", "TimeoutStartUSec", "TimeoutStopUSec"):
            need(0 <= row[name] <= 18446744073709551615, "METADATA_INVALID")
            durations[name] = row[name] if row[name] <= 86400000000 else "LONG_OR_INFINITE"
        need(all(-2147483648 <= row[k] <= 2147483647 for k in ("KillSignal", "FinalKillSignal")), "METADATA_INVALID")
        return dict(unit=unit, type=job_enum(row["Type"], {"simple", "exec", "forking", "oneshot", "dbus", "notify", "notify-reload", "idle"}),
            restart=job_enum(row["Restart"], {"no", "always", "on-success", "on-failure", "on-abnormal", "on-watchdog", "on-abort"}),
            durations=durations, kill_mode=job_enum(row["KillMode"], {"control-group", "mixed", "process", "none"}),
            kill_signal=row["KillSignal"], final_kill_signal=row["FinalKillSignal"], send_sigkill=row["SendSIGKILL"],
            stop_failure_mode=job_enum(row["TimeoutStopFailureMode"], {"terminate", "abort", "kill"}),
            handlers={name: job_handler(row[name]) for name in JOB_DIAG_EXEC})

    def state(self, unit, *, pids=False):
        row = self.properties(job_unit_path(unit), "Unit", (("Id", "s"), ("ActiveState", "s"), ("SubState", "s"), ("Job", "(uo)")))
        need(row["Id"] == unit and type(row["Job"]) is list and len(row["Job"]) == 2, "METADATA_INVALID")
        jid = job_id(row["Job"][0])
        need(row["Job"][1] == ("/org/freedesktop/systemd1/job/" + str(jid) if jid else "/"), "METADATA_INVALID")
        result = dict(unit=unit, active=job_enum(row["ActiveState"], SAFE_STATES), sub=job_enum(row["SubState"], SAFE_SUB),
                      job_id=jid, main_pid_nonzero=None, control_pid_nonzero=None)
        if pids:
            data = self.properties(job_unit_path(unit), "Service", (("MainPID", "u"), ("ControlPID", "u")))
            result.update(main_pid_nonzero=job_id(data["MainPID"]) != 0, control_pid_nonzero=job_id(data["ControlPID"]) != 0)
        return result

    def step(self, stage, key, call, *, append=False):
        try:
            value = call()
            target = self.value[key] + [value] if append else value
            trial = dict(self.value, **{key: target})
            need(len(canonical(trial)) <= JOB_DIAG_BYTES - 2048, "COMMAND_LIMIT")
            self.value[key] = target
        except Blocked as exc:
            if str(exc) in {"CANCELLED", "PARENT_LOST"}:
                raise
            code = {"DEADLINE": "TIMEOUT", "COMMAND_LIMIT": "LIMIT", "COMMAND_FAILED": "READ_FAILED"}.get(str(exc), "METADATA_INVALID")
            self.value["errors"].append(dict(stage=stage, code=code))
        except Exception:
            self.value["errors"].append(dict(stage=stage, code="READ_FAILED"))

    def collect(self):
        identifier = self.value["observed_job_id"]
        for unit in (BEN, HEALTH, TIMER):
            if unit not in self.op.rows:
                continue
            row = self.op.rows[unit]
            self.value["context"].append(dict(unit=unit, active=row["ActiveState"], sub=row["SubState"],
                job_pending=bool(row["Job"]), graph={field: [job_enum(n, JOB_DIAG_UNITS) for n in row[field].split()] for field in JOB_DIAG_GRAPH}))
        self.step("job", "job", lambda: self.job(identifier))
        self.step("before", "before", lambda: self.dependencies(identifier, "GetBefore"))
        self.step("after", "after", lambda: self.dependencies(identifier, "GetAfter"))
        for unit, stage in ((BEN, "policy_ben"), (HEALTH, "policy_health")):
            self.step(stage, "policies", lambda u=unit: self.policy(u), append=True)
        seen = set()
        for dependency in self.value["before"] or []:
            unit = dependency["unit"]
            if unit == "UNKNOWN" or unit in seen:
                continue
            if len(seen) == 4:
                self.value["errors"].append(dict(stage="before", code="LIMIT")); break
            self.step("blocker_" + str(len(seen)), "blockers", lambda u=unit: self.state(u), append=True)
            seen.add(unit)
        for unit, stage in ((BEN, "final_ben"), (HEALTH, "final_health"), (TIMER, "final_timer")):
            self.step(stage, "final_states", lambda u=unit: self.state(u, pids=u != TIMER), append=True)
        final = next((row for row in self.value["final_states"] if row["unit"] == BEN), None)
        self.value["race"] = final["job_id"] != identifier if final else None
        self.value["finished_ms"] = self.elapsed()
        self.value["status"] = "PARTIAL" if self.value["errors"] else "COMPLETE"
        return self.value


class Isolation:
    def __init__(self, payload, *, root="/", runner=None, clock=time.monotonic,
                 sleep=time.sleep, host_check=None, started=None):
        self.p = payload
        self.fs = RootFS(root)
        self.runner = runner or bounded_command
        self.budget = Budget(clock, started)
        self.sleep = sleep
        self.host_check = host_check or local_host_check
        self.rows = {}
        self.graph_evidence = GraphEvidence()
        self.companion_evidence = None
        self.job_evidence = None
        self.ben_gate = None
        self.ben_gate_journaled = False
        self.pending_end = None
        self.baseline = {}
        self.conditions = {}
        self.records = []
        self.changes = []
        self.snapshot_id = None
        self.snapshot_sha = None
        self.restore_sha = None
        self.journal_index = 0
        self.journal_hash = None
        self.own_records = {}
        self.original_paths = set()
        self.gate_paths = {}
        self.created_dirs = []
        self.systemd_255 = None
        self.continuation = None
        self.loaded_events = []
        self.drift_checks = 0
        self.drift_order = {}
        self.drift_failure = None

    def command(self, args, timeout=3):
        left = self.budget.left()
        if self.pending_end is not None:
            pending_left = self.pending_end - self.budget.clock()
            need(pending_left > 0, "PENDING_GATE_TIMEOUT")
            left = min(left, pending_left)
        return self.runner(args, timeout=min(timeout, left), limit=MAX_OUTPUT)

    def private_tmp_version(self):
        if self.systemd_255 is None:
            # Read exactly the running manager's Version. systemctl show of the
            # manager internally reads all properties, including Environment.
            # v255 busctl's single string property is typed JSON, not an array.
            raw = self.command(["/usr/bin/busctl", "--system", "--json=short", "get-property",
                "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                "org.freedesktop.systemd1.Manager", "Version"])
            value = strict_json(raw)
            need(type(value) is dict and set(value) == {"type", "data"} and value["type"] == "s"
                 and type(value["data"]) is str and len(value["data"]) <= 96, "METADATA_INVALID")
            # Cache only a boolean, never raw version text or arbitrary suffixes.
            self.systemd_255 = re.fullmatch(r"255(?:\.[0-9A-Za-z.+:~_-]+)?", value["data"], re.ASCII) is not None
        return self.systemd_255

    def show(self, unit):
        need(unit in UNITS, "ALIAS_OR_UNIT_MISMATCH")
        fields = COMMON + (SERVICE if unit.endswith(".service") else ())
        raw = self.command(["/usr/bin/systemctl", "show", "--no-pager", "--all", "--property=" + ",".join(fields), unit])
        row = parse_show(raw, fields)
        if unit == TIMER:
            # Timer units have no cgroup interface in systemd 255; do not invent
            # a queried property or a cgroup. Only services own cgroups here.
            row["ControlGroup"] = ""
        need(row["Id"] == unit and row["Names"] == unit and not row["Following"] and row["Transient"] == "no", "ALIAS_OR_UNIT_MISMATCH")
        need(row["LoadState"] == "loaded" and row["ActiveState"] in SAFE_STATES and row["SubState"] in SAFE_SUB
             and row["UnitFileState"] in {"enabled", "disabled", "static", "indirect"}, "METADATA_INVALID")
        need(row["Job"] == "" or re.fullmatch(r"[1-9][0-9]*", row["Job"]), "METADATA_INVALID")
        need(row["NeedDaemonReload"] in {"yes", "no"}, "METADATA_INVALID")
        for field in ("StopWhenUnneeded", "RefuseManualStop"):
            need(row[field] == "no", "STOP_POLICY")
        for field in ("FailureAction", "SuccessAction", "JobTimeoutAction"):
            need(row[field] == "none", "STOP_POLICY")
        unit_path(row["FragmentPath"], unit)
        drops = row["DropInPaths"].split()
        need(len(drops) <= MAX_FILES and len(drops) == len(set(drops)), "FILE_LIMIT")
        for path in drops:
            unit_path(path, unit, dropin=True)
        group = row["ControlGroup"]
        need(group in ("", "/system.slice/" + unit), "PATH_UNSAFE")
        if unit.endswith(".service"):
            need(row["PrivateTmp"] in {"yes", "no"}, "METADATA_INVALID")
            need(row["MainPID"].isdigit() and row["ControlPID"].isdigit(), "METADATA_INVALID")
            if unit in TARGETS:
                stop_seconds(row["TimeoutStopUSec"])
                need(row["KillMode"] == "control-group" and row["KillSignal"] == "15"
                     and row["SendSIGKILL"] == "yes" and row["FinalKillSignal"] == "9"
                     and row["TimeoutStopFailureMode"] == "terminate"
                     and row["Restart"] in {"always", "on-failure", "no"}, "STOP_POLICY")
        # Read Version only when a documented candidate edge needs the extra
        # exception. Unrelated graph failures retain their diagnostic path.
        private_tmp_candidate = (unit in PRIVATE_TMP_UNITS and row["PrivateTmp"] == "yes"
                                 and any(set(row[field].split()) & peers
                                         for field, peers in PRIVATE_TMP_EDGES.items()))
        systemd_255 = self.private_tmp_version() if private_tmp_candidate else False
        validate_edges(unit, row, evidence=self.graph_evidence, systemd_255=systemd_255)
        self.rows[unit] = row
        return row

    def property(self, unit, interface, name):
        need(unit in UNITS, "ALIAS_OR_UNIT_MISMATCH")
        obj = "/org/freedesktop/systemd1/unit/" + "".join(c if c.isalnum() else "_" + format(ord(c), "02x") for c in unit)
        return self.command(["/usr/bin/busctl", "--system", "--json=short", "get-property",
            "org.freedesktop.systemd1", obj, "org.freedesktop.systemd1." + interface, name])

    def effective_conditions(self, unit):
        return parse_conditions(self.property(unit, "Unit", "Conditions"))

    def stop_handlers(self, unit):
        if unit == TIMER:
            return
        need(unit in TARGETS, "ALIAS_OR_UNIT_MISMATCH")
        # systemctl show omits empty Exec arrays even with --all on systemd255.
        # Typed, explicit empty arrays prove absence without interpreting or
        # emitting command text. Companion handlers are never executed.
        for name in ("ExecStop", "ExecStopPost"):
            value = strict_json(self.property(unit, "Service", name))
            need(type(value) is dict and set(value) == {"type", "data"}
                 and value["type"] == "a(sasbttttuii)" and type(value["data"]) is list, "METADATA_INVALID")
            need(value["data"] == [], "STOP_HANDLER")

    def companions(self, phase="PREFLIGHT"):
        for unit in COMPANIONS:
            row = self.show(unit)
            try:
                need(row["ActiveState"] == "inactive" and row["SubState"] == "dead" and not row["Job"]
                     and row["MainPID"] == "0" and row["ControlPID"] == "0", "COMPANION_BUSY")
            except Blocked as exc:
                if str(exc) != "COMPANION_BUSY":
                    raise
                # Only retain the already validated row at the original refusal.
                # No further host command, cgroup read, wait or maintenance retry.
                failed = (row["ActiveState"] != "inactive", row["SubState"] != "dead",
                          bool(row["Job"]), row["MainPID"] != "0", row["ControlPID"] != "0")
                self.companion_evidence = {
                    "unit": unit, "phase": phase,
                    "elapsed_ms": int((self.budget.clock() - (self.budget.end - GUEST_SECONDS)) * 1000),
                    "active": row["ActiveState"], "sub": row["SubState"],
                    "job_pending": failed[2], "main_pid_nonzero": failed[3], "control_pid_nonzero": failed[4],
                    "failed_predicates": [name for name, value in zip(COMPANION_PREDICATES, failed) if value]}
                if (unit == BEN and phase == "PREFLIGHT" and self.p.get("operation") == "isolate"
                        and failed == (False, False, True, False, False)
                        and len(row["Job"]) <= 10 and int(row["Job"]) <= 4294967295):
                    # Observe only this exact refusal. Never resume maintenance,
                    # cancel a systemd job, or treat a later disappearance as safe.
                    self.job_evidence = JobDiagnostic(self, int(row["Job"])).collect()
                raise
            need(self.cgroup_empty(row["ControlGroup"]), "COMPANION_BUSY")

    def cgroup_empty(self, group):
        if not group:
            return True
        path = "/sys/fs/cgroup" + group
        if not self.fs.exists(path):
            return True  # Removed recorded cgroup is empty; no broad process scan.
        raw = self.fs.read(path + "/cgroup.events", 1024)
        need(b"populated 0\n" in raw.splitlines(keepends=True), "CGROUP_NOT_EMPTY")
        need(not self.fs.read(path + "/cgroup.procs", 4096).strip(), "CGROUP_NOT_EMPTY")
        return True

    def expected_gate_paths(self):
        units = set(self.own_records) | {c["unit"] for c in self.changes if c["gate"] == "REMOVED"}
        return {self.gate_paths[u] for u in units}

    def inventory(self):
        paths = set()
        for unit, row in self.rows.items():
            paths.add(row["FragmentPath"])
            paths.update(row["DropInPaths"].split())
        # Include exact on-disk fragments/dropins, even not yet reflected by
        # systemd's cached properties. A newly added reset/override must be drift.
        for root in SYSTEM_DIRS:
            for unit in UNITS:
                fragment = root + "/" + unit
                if self.fs.exists(fragment):
                    paths.add(fragment)
                dropdir = fragment + ".d"
                if self.fs.exists(dropdir):
                    for name, directory, link in self.fs.listdir(dropdir, limit=MAX_FILES):
                        if name.endswith(".conf"):
                            need(not directory and not link, "PATH_UNSAFE")
                            paths.add(unit_path(dropdir + "/" + name, unit, dropin=True))
        # Exact unit-named enablement links only. Bound traversal of the three
        # load roots; unrelated file contents, envfiles and logs are never read.
        for root in SYSTEM_DIRS:
            if not self.fs.exists(root):
                continue
            for name, directory, link in self.fs.listdir(root):
                if name in UNITS and link:
                    paths.add(root + "/" + name)
                if name.endswith((".wants", ".requires", ".upholds")):
                    need(directory and not link, "PATH_UNSAFE")
                    for unit in UNITS:
                        path = root + "/" + name + "/" + unit
                        if self.fs.exists(path):
                            paths.add(path)
        need(len(paths) <= MAX_FILES, "FILE_LIMIT")
        return paths - self.expected_gate_paths()

    def capture(self, paths):
        records = []
        size = 0
        for path in sorted(paths):
            self.budget.left()
            record = self.fs.record(path)
            base = PurePosixPath(path).name
            parent = str(PurePosixPath(path).parent)
            if parent in SYSTEM_DIRS or any(parent == r + "/" + u + ".d" for r in SYSTEM_DIRS for u in UNITS):
                need(record["kind"] == "file", "PATH_UNSAFE")
            else:
                need(record["kind"] == "symlink" and base in UNITS, "PATH_UNSAFE")
                resolved = os.path.normpath(os.path.join(parent, record["link"]))
                need(resolved in {r + "/" + base for r in SYSTEM_DIRS}, "PATH_UNSAFE")
            size += len(base64.b64decode(record.get("data", ""))) + len(record.get("link", "").encode())
            size += sum(len(base64.b64decode(v)) for v in record["xattrs"].values())
            need(size <= MAX_BYTES and len(records) < MAX_FILES, "FILE_LIMIT")
            records.append(record)
        return records

    def journal(self, event, **fields):
        self.budget.left()
        item = {"seq": self.journal_index, "previous_sha256": self.journal_hash, "event": event, **fields}
        raw = canonical(item)
        self.fs.write_new(self.archive + "/journal-" + format(self.journal_index, "03d") + ".json", raw)
        self.journal_index += 1
        self.journal_hash = digest(raw)

    def snapshot(self):
        self.fs.mkdir(RECOVERY_ROOT, exclusive=False)
        self.snapshot_id = self.change_id
        self.archive = RECOVERY_ROOT + "/" + self.snapshot_id
        self.fs.mkdir(self.archive)
        self.fs.mkdir(self.archive + "/restore-check")
        self.records = self.capture(self.original_paths)
        self.manifest = {"schema": "bridge.ibm.unit-isolation.snapshot.v1", "request": self.p,
                         "records": self.records, "metadata": self.baseline, "conditions": self.conditions}
        raw = canonical(self.manifest)
        need(len(raw) <= 2 * MAX_BYTES, "FILE_LIMIT")
        self.fs.write_new(self.archive + "/snapshot.json", raw)
        self.snapshot_sha = digest(raw)
        self.verify_archive(self.manifest, self.archive + "/restore-check")
        self.restore_sha = self.snapshot_sha
        self.journal("SNAPSHOT_RESTORED", snapshot_sha256=self.snapshot_sha,
                     restore_sha256=self.restore_sha, intended_gates=self.gate_paths,
                     gate_sha256=digest(self.gate_data))

    def verify_archive(self, manifest, staging):
        raw = self.fs.read(self.archive + "/snapshot.json", 2 * MAX_BYTES)
        need(digest(raw) == self.snapshot_sha and strict_json(raw) == manifest, "ARCHIVE_FAILED")
        restored = []
        for index, record in enumerate(manifest["records"]):
            self.budget.left()
            path = staging + "/" + str(index)
            self.fs.restore_record(record, path)
            actual = self.fs.record(path)
            need(restorable(record) == restorable(actual), "RESTORE_MISMATCH")
            restored.append({**actual, **{k: record[k] for k in ("path", "dev", "ino", "ctime_ns")}})
        proof = {**manifest, "records": restored}
        need(digest(canonical(proof)) == self.snapshot_sha, "RESTORE_MISMATCH")

    def drift_compare(self, expected, actual, kind, unit=None, field=None):
        if expected == actual:
            return
        if self.drift_failure is None:
            self.drift_failure = dict(kind=kind, unit=unit, property=field,
                baseline_sha256=digest(canonical(expected)), current_sha256=digest(canonical(actual)))
        raise Blocked("FILE_DRIFT")

    def drift_evidence(self):
        if not self.drift_checks:
            return None
        rows = list(self.drift_order.values())
        return dict(checks=self.drift_checks, order_only=rows[:8], omitted_count=max(0, len(rows)-8),
                    first_failure=self.drift_failure)

    def drift(self):
        self.drift_checks += 1
        need(self.drift_checks <= 100000, "FILE_LIMIT")
        need(not self.fs.exists(self.allow), "COLLISION")
        self.drift_compare(sorted(self.original_paths), sorted(self.inventory()), "INVENTORY")
        try:
            current = self.capture(self.original_paths)
        except Blocked as exc:
            if str(exc) == "FILE_DRIFT" and self.drift_failure is None:
                self.drift_failure = dict(kind="CONFIG_READ", unit=None, property=None,
                    baseline_sha256=digest(canonical(self.records)), current_sha256=None)
            raise
        except OSError:
            self.drift_failure = self.drift_failure or dict(kind="CONFIG_READ", unit=None, property=None,
                baseline_sha256=digest(canonical(self.records)), current_sha256=None)
            raise Blocked("FILE_DRIFT") from None
        for before, after in zip(self.records, current):
            if before != after:
                path = before["path"]
                unit = next((u for u in UNITS if path.endswith("/" + u) or "/" + u + ".d/" in path), None)
                self.drift_compare(before, after, "CONFIG_RECORDS", unit)
        self.drift_compare(self.records, current, "CONFIG_RECORDS")
        for unit, base in self.baseline.items():
            row = self.rows[unit]
            fields = ("Id", "Names", "FragmentPath", "UnitFileState") + EDGES
            if unit.endswith(".service"):
                fields += ("PrivateTmp",)
            for field in fields:
                need(field in base, "FILE_DRIFT")
                if field in EDGES:
                    # v255 dbus-unit.c property_get_dependencies iterates a
                    # Hashmap. Only these validated dependency sets lack order.
                    before, after = base[field].split(), row[field].split()
                    need(len(before) <= 32 and len(before) == len(set(before)), "METADATA_INVALID")
                    self.drift_compare(sorted(before), sorted(after), "UNIT_PROPERTY", unit, field)
                    if base[field] != row[field]:
                        self.drift_order.setdefault((unit, field), dict(unit=unit, property=field,
                            baseline_sha256=digest(canonical(base[field])), current_sha256=digest(canonical(row[field]))))
                else:
                    self.drift_compare(base[field], row[field], "UNIT_PROPERTY", unit, field)
            # Own additive files are expected. Preserve the relative order of
            # every pre-existing drop-in; Exec arrays are never normalized.
            drops = [p for p in row["DropInPaths"].split() if p not in self.expected_gate_paths()]
            self.drift_compare(base["DropInPaths"].split(), drops, "DROPIN_ORDER", unit, "DropInPaths")
        for unit, record in self.own_records.items():
            try:
                current = self.fs.record(self.gate_paths[unit])
            except (Blocked, OSError) as exc:
                if isinstance(exc, OSError) or str(exc) == "FILE_DRIFT":
                    self.drift_failure = self.drift_failure or dict(kind="OWNED_GATE", unit=unit, property=None,
                        baseline_sha256=digest(canonical(record)), current_sha256=None)
                    raise Blocked("FILE_DRIFT") from None
                raise
            self.drift_compare(record, current, "OWNED_GATE", unit)

    def remaining_stop_budget(self):
        return sum(stop_seconds(self.baseline[u]["TimeoutStopUSec"])
                   for u in STOP_ORDER if u.endswith(".service") and self.rows[u]["ActiveState"] != "inactive") + 12

    def check_gates(self):
        need(not self.fs.exists(self.allow), "GATE_NOT_EFFECTIVE")
        expected = ["ConditionPathExists", False, False, self.allow]
        for unit in self.own_records:
            if unit in TARGETS:
                self.stop_handlers(unit)
            current = self.effective_conditions(unit)
            need(current == sorted(self.conditions[unit] + [expected], key=canonical), "GATE_NOT_EFFECTIVE")
            need(self.gate_paths[unit] in self.rows[unit]["DropInPaths"].split(), "GATE_NOT_EFFECTIVE")
            need(self.rows[unit]["NeedDaemonReload"] == "no", "GATE_NOT_EFFECTIVE")
            need(self.fs.record(self.gate_paths[unit]) == self.own_records[unit], "FILE_DRIFT")

    def create_gate(self, unit):
        need(unit in self.gate_paths and unit not in self.own_records, "COLLISION")
        path = self.gate_paths[unit]
        change = {"unit": unit, "gate": "INTENDED", "stop": "NOT_REQUESTED"}
        self.changes.append(change)
        directory = str(PurePosixPath(path).parent)
        self.journal("GATE_INTENT", unit=unit, path=path, sha256=digest(self.gate_data),
                     directory_was_absent=not self.fs.exists(directory))
        if not self.fs.exists(directory):
            self.fs.mkdir(directory, mode=0o755)
            self.created_dirs.append(directory)
        self.fs.write_new(path, self.gate_data, mode=0o644)
        change["gate"] = "CREATED"
        record = self.fs.record(path)
        need(record["sha256"] == digest(self.gate_data), "FILE_DRIFT")
        self.own_records[unit] = record
        self.journal("GATE_CREATED", unit=unit, record=record)
        if unit == BEN:
            self.ben_gate_journaled = True

    def pending_marker(self):
        raw = strict_json(self.property(BEN, "Service", "ExecStartPre"))
        need(type(raw) is dict and set(raw) == {"type", "data"} and raw["type"] == "a(sasbttttuii)", "PENDING_GATE_UNVERIFIED")
        need(job_handler(raw["data"]) == [{"action": "DOCKER_REMOVE_BEN_FORCE", "ignore_failure": True}], "PENDING_GATE_UNVERIFIED")
        markers = []
        for unit in (BEN, HEALTH):
            value = strict_json(self.property(unit, "Unit", "InactiveExitTimestampMonotonic"))
            need(type(value) is dict and set(value) == {"type", "data"} and value["type"] == "t"
                 and type(value["data"]) is int and 0 <= value["data"] <= 18446744073709551615,
                 "PENDING_GATE_UNVERIFIED")
            markers.append(value["data"])
        # v255 serializes these unit timestamps across daemon-reload. Unlike
        # ExecStartPre status, they cannot reset when command arrays reload.
        # Leaving inactive (even a short HEALTH/ExecStartPre cycle) changes them;
        # a skipped Condition leaves them unchanged. Values stay internal.
        return tuple(markers)

    def pending_quiet_check(self, identifier, marker, *, allow_skipped=False):
        health = self.show(HEALTH)
        need(health["ActiveState"] == "inactive" and health["SubState"] == "dead" and not health["Job"]
             and health["MainPID"] == "0" and health["ControlPID"] == "0", "PENDING_GATE_RACE")
        self.cgroup_empty(health["ControlGroup"])
        row = self.show(BEN)
        need(row["ActiveState"] == "inactive" and row["SubState"] == "dead" and row["Job"] in (("", str(identifier)) if allow_skipped else (str(identifier),))
             and row["MainPID"] == "0" and row["ControlPID"] == "0", "PENDING_GATE_RACE")
        need(self.pending_marker() == marker, "PENDING_GATE_RACE")
        self.cgroup_empty(row["ControlGroup"])
        return row

    def prepare_pending_ben(self):
        row = self.rows[BEN]
        if not (row["ActiveState"] == "inactive" and row["SubState"] == "dead" and row["MainPID"] == "0"
                and row["ControlPID"] == "0" and row["Job"] and len(row["Job"]) <= 10 and int(row["Job"]) <= 4294967295):
            return
        identifier = int(row["Job"])
        self.ben_gate = dict(unit=BEN, boot_job_id=identifier, status="ADMISSION_BLOCKED", timer_stopped=False,
                             execution_unchanged=None, condition_skipped=None, production_release_required=False)
        try:
            need(self.private_tmp_version(), "PENDING_GATE_UNVERIFIED")
            reader = JobDiagnostic(self, identifier)
            job = reader.job(identifier)
            before = reader.dependencies(identifier, "GetBefore")
            need(job["type"] == "start" and job["state"] == "waiting"
                 and len(before) == 1 and before[0]["unit"] == "docker.service"
                 and before[0]["type"] == "start" and before[0]["state"] == "running", "PENDING_GATE_UNVERIFIED")
            marker = self.pending_marker()
            self.pending_quiet_check(identifier, marker)
        except Blocked as exc:
            if str(exc) in {"CANCELLED", "PARENT_LOST", "DEADLINE", "PENDING_GATE_RACE", "CGROUP_NOT_EMPTY"}:
                raise
            raise Blocked("PENDING_GATE_UNVERIFIED") from None
        except Exception:
            raise Blocked("PENDING_GATE_UNVERIFIED") from None
        self.gate_paths[BEN] = "/etc/systemd/system/" + BEN + ".d/90-bridge-ibm-isolation-" + self.change_id + ".conf"
        need(not self.fs.exists(self.gate_paths[BEN]), "COLLISION")
        self.baseline = {u: dict(r) for u, r in self.rows.items()}
        self.conditions = {u: self.effective_conditions(u) for u in GATE_UNITS}
        self.original_paths = self.inventory()
        # Explicit preparation budget plus original AL stop reserve; never reset
        # guest/controller time. This is a condition gate, not BEN Stop/Cancel.
        self.budget.left(PENDING_GATE_SECONDS + self.remaining_stop_budget() + 20)
        self.snapshot()
        end = min(self.budget.end - self.remaining_stop_budget() - 10, self.budget.clock() + PENDING_GATE_SECONDS)
        self.pending_end = end
        def check_time():
            need(self.budget.clock() < end, "PENDING_GATE_TIMEOUT")
        self.ben_gate["status"] = "PREPARING"
        for unit in (BEN, TIMER):
            check_time(); self.pending_quiet_check(identifier, marker); self.drift()
            if unit == BEN:
                # Conservative before write: a completed write followed by a
                # failed fsync/record/journal still needs reviewed recovery.
                self.ben_gate["production_release_required"] = True
            self.create_gate(unit)
        check_time(); self.pending_quiet_check(identifier, marker)
        self.journal("PENDING_BEN_RELOAD_INTENT", unit=BEN, boot_job_id=identifier)
        self.command(["/usr/bin/systemctl", "daemon-reload"], timeout=5)
        for unit in UNITS:
            self.show(unit)
        self.pending_quiet_check(identifier, marker, allow_skipped=True); self.drift(); self.check_gates()
        timer = self.show(TIMER)
        need(not timer["Job"] and timer["ActiveState"] in {"active", "inactive"}, "PENDING_GATE_RACE")
        change = next(c for c in self.changes if c["unit"] == TIMER)
        if timer["ActiveState"] == "active":
            check_time(); self.pending_quiet_check(identifier, marker, allow_skipped=True)
            self.journal("STOP_INTENT", unit=TIMER); change["stop"] = "REQUESTED"
            self.command(["/usr/bin/systemctl", "--job-mode=fail", "--no-block", "stop", TIMER])
        while True:
            check_time(); row = self.pending_quiet_check(identifier, marker, allow_skipped=True)
            timer = self.show(TIMER)
            need(timer["ActiveState"] in {"active", "inactive", "deactivating"}, "PENDING_GATE_RACE")
            if timer["ActiveState"] == "inactive" and timer["SubState"] == "dead" and not timer["Job"]:
                change["stop"] = "OBSERVED_INACTIVE"; self.ben_gate["timer_stopped"] = True
            if self.ben_gate["timer_stopped"] and not row["Job"]:
                break
            self.sleep(min(.2, max(0, end - self.budget.clock())))
        raw = strict_json(self.property(BEN, "Unit", "Conditions"))
        parse_conditions(canonical(raw))
        own = ["ConditionPathExists", False, False, self.allow, -1]
        need(own in raw["data"], "PENDING_GATE_UNVERIFIED")
        self.pending_quiet_check(identifier, marker, allow_skipped=True); self.drift(); self.check_gates()
        self.ben_gate.update(status="GATED_QUIET", execution_unchanged=True, condition_skipped=True)
        for change in self.changes:
            change["gate"] = "VERIFIED"
        self.journal("PENDING_BEN_GATED_QUIET", unit=BEN, boot_job_id=identifier,
                     production_release_required=True, ben_stop_requested=False)
        self.pending_end = None

    def resume(self):
        """Continue only the pinned partial pass; preserve original archive/gates."""
        validate_request(self.p, resume=True)
        self.host_check(self.fs)
        self.snapshot_id = self.change_id = RESUME_SNAPSHOT_ID
        self.snapshot_sha = RESUME_SNAPSHOT_SHA256
        self.continuation = dict(snapshot_id=RESUME_SNAPSHOT_ID, original_head=RESUME_ORIGINAL_HEAD,
                                 prior_journal_event=None, retained_gates=[], stage="REQUEST_BOUND")
        self.changes = [dict(unit=u, gate="INHERITED_UNVERIFIED", stop="NOT_REQUESTED") for u in (BEN, TIMER)]
        self.ben_gate = dict(unit=BEN, boot_job_id=211, status="PREPARING", timer_stopped=False,
                            execution_unchanged=None, condition_skipped=None, production_release_required=True)
        load_recovery_snapshot(self, self.p, prefix="resume")
        need(self.original_request["head"] == RESUME_ORIGINAL_HEAD
             and self.original_request["run_id"] == RESUME_SNAPSHOT_ID.split("-")[0]
             and set(self.conditions) == set(GATE_UNITS), "ROLLBACK_INVALID")
        names = [e["event"] for e in self.loaded_events]
        prefix = ["SNAPSHOT_RESTORED", "GATE_INTENT", "GATE_CREATED", "GATE_INTENT", "GATE_CREATED"]
        need(names in (prefix, prefix + ["PENDING_BEN_RELOAD_INTENT"])
             and set(self.own_records) == {BEN, TIMER}, "ROLLBACK_INVALID")
        need([self.loaded_events[i].get("unit") for i in range(1, 5)] == [BEN, BEN, TIMER, TIMER], "ROLLBACK_INVALID")
        first = self.loaded_events[0]
        need(first.get("snapshot_sha256") == self.snapshot_sha and first.get("restore_sha256") == self.snapshot_sha
             and first.get("intended_gates") == self.gate_paths and first.get("gate_sha256") == digest(self.gate_data), "ROLLBACK_INVALID")
        old_job = self.baseline[BEN]["Job"]
        need(type(old_job) is str and re.fullmatch(r"[1-9][0-9]{0,9}", old_job)
             and int(old_job) <= 4294967295, "ROLLBACK_INVALID")
        if names[-1] == "PENDING_BEN_RELOAD_INTENT":
            last = self.loaded_events[-1]
            need(last.get("unit") == BEN and last.get("boot_job_id") == int(old_job), "ROLLBACK_INVALID")
        self.ben_gate["boot_job_id"] = int(old_job)  # Historical ID only; never queried on this boot.
        self.ben_gate_journaled = True
        self.continuation["prior_journal_event"] = names[-1]
        for unit, path in self.gate_paths.items():
            if unit not in self.own_records:
                need(not self.fs.exists(path), "COLLISION")
        for unit in UNITS:
            row = self.show(unit)
            need(row["NeedDaemonReload"] == "no", "FILE_DRIFT")
            if unit in TARGETS:
                self.stop_handlers(unit)
                need(not row["Job"] and (row["ActiveState"], row["SubState"]) in
                     {("inactive", "dead"), ("active", "waiting" if unit == TIMER else "running")}, "UNIT_BUSY")
        need(self.private_tmp_version(), "PENDING_GATE_UNVERIFIED")
        self.drift(); self.check_gates()
        self.continuation.update(retained_gates=[BEN, TIMER], stage="VERIFIED")
        for change in self.changes:
            change["gate"] = "CREATED"
        # This boot has its own job and monotonic timestamps. Prior-boot values
        # establish provenance only and are never reused as live observations.
        row = self.rows[BEN]
        current_job = int(row["Job"] or "0")
        if current_job:
            need(current_job <= 4294967295, "PENDING_GATE_UNVERIFIED")
            reader = JobDiagnostic(self, current_job)
            job = reader.job(current_job); before = reader.dependencies(current_job, "GetBefore")
            need(job["type"] == "start" and job["state"] == "waiting" and len(before) == 1
                 and before[0]["unit"] == "docker.service" and before[0]["type"] == "start"
                 and before[0]["state"] == "running", "PENDING_GATE_UNVERIFIED")
        marker = self.pending_marker()
        self.pending_quiet_check(current_job, marker, allow_skipped=True)
        self.budget.left(PENDING_GATE_SECONDS + self.remaining_stop_budget() + 20)
        self.pending_end = min(self.budget.end - self.remaining_stop_budget() - 10,
                               self.budget.clock() + PENDING_GATE_SECONDS)
        self.journal("CONTINUATION_BOUND", run_id=self.p["run_id"], head=self.p["head"],
                     snapshot_sha256=self.snapshot_sha, drift_evidence=self.drift_evidence())
        timer = self.show(TIMER)
        need(not timer["Job"] and timer["ActiveState"] in {"active", "inactive"}, "PENDING_GATE_RACE")
        change = next(c for c in self.changes if c["unit"] == TIMER)
        if timer["ActiveState"] == "active":
            self.pending_quiet_check(current_job, marker, allow_skipped=True)
            self.drift(); self.check_gates(); self.budget.left(self.remaining_stop_budget() + 10)
            self.journal("STOP_INTENT", unit=TIMER); change["stop"] = "REQUESTED"
            self.command(["/usr/bin/systemctl", "--job-mode=fail", "--no-block", "stop", TIMER])
        while True:
            need(self.budget.clock() < self.pending_end, "PENDING_GATE_TIMEOUT")
            row = self.pending_quiet_check(current_job, marker, allow_skipped=True)
            timer = self.show(TIMER)
            need(timer["ActiveState"] in {"active", "inactive", "deactivating"}, "PENDING_GATE_RACE")
            if timer["ActiveState"] == "inactive" and timer["SubState"] == "dead" and not timer["Job"]:
                change["stop"] = "OBSERVED_INACTIVE"; self.ben_gate["timer_stopped"] = True
            if self.ben_gate["timer_stopped"] and not row["Job"]:
                break
            self.sleep(min(.2, max(0, self.pending_end - self.budget.clock())))
        raw = strict_json(self.property(BEN, "Unit", "Conditions")); parse_conditions(canonical(raw))
        need(["ConditionPathExists", False, False, self.allow, -1] in raw["data"], "PENDING_GATE_UNVERIFIED")
        self.pending_quiet_check(current_job, marker, allow_skipped=True)
        self.drift(); self.check_gates()
        self.ben_gate.update(status="GATED_QUIET", execution_unchanged=True, condition_skipped=True)
        for change in self.changes:
            change["gate"] = "VERIFIED"
        self.pending_end = None
        self.companions("PREFLIGHT"); self.budget.left(self.remaining_stop_budget() + 10)
        self.continuation["stage"] = "RESUMING"
        self.journal("CONTINUATION_PREPARED", run_id=self.p["run_id"], head=self.p["head"],
                     retained_gates=[BEN, TIMER], ben_stop_requested=False)
        return self.finish_isolation()

    def preflight(self):
        validate_request(self.p)
        self.host_check(self.fs)
        self.change_id = self.p["run_id"] + "-" + self.p["head"][:12]
        self.allow = "/etc/systemd/system/.bridge-ibm-isolation-" + self.change_id + ".allow"
        self.gate_data = ("[Unit]\nConditionPathExists=" + self.allow + "\n").encode()
        self.gate_paths = {u: "/etc/systemd/system/" + u + ".d/90-bridge-ibm-isolation-" + self.change_id + ".conf" for u in TARGETS}
        need(not self.fs.exists(self.allow), "COLLISION")
        for path in self.gate_paths.values():
            need(not self.fs.exists(path), "COLLISION")
        for unit in UNITS:
            try:
                self.show(unit)
            except EdgeBlocked:
                # Only this fixed graph failure may continue to the remaining
                # exact units. show() never promotes a rejected row to rows.
                continue
        need(not self.graph_evidence.failures, "UNKNOWN_EDGE")
        for unit in UNITS:
            row = self.rows[unit]
            need(row["NeedDaemonReload"] == "no", "FILE_DRIFT")
            if unit in TARGETS:
                self.stop_handlers(unit)
                need(not row["Job"] and row["ActiveState"] in {"active", "inactive"}, "UNIT_BUSY")
                need((row["ActiveState"], row["SubState"]) in {("active", "waiting" if unit == TIMER else "running"), ("inactive", "dead")}, "UNIT_BUSY")
        self.prepare_pending_ben()
        self.companions("PREFLIGHT")
        if self.snapshot_id is None:
            self.baseline = {u: dict(r) for u, r in self.rows.items()}
            self.conditions = {u: self.effective_conditions(u) for u in TARGETS}
            self.original_paths = self.inventory()
        self.budget.left(self.remaining_stop_budget())

    def isolate(self):
        self.preflight()
        if self.snapshot_id is None:
            self.snapshot()
        return self.finish_isolation()

    def finish_isolation(self):
        self.companions("AFTER_SNAPSHOT")
        self.drift()
        self.budget.left(self.remaining_stop_budget())
        for unit in TARGETS:
            self.companions("BEFORE_GATE")
            self.drift()
            self.budget.left(self.remaining_stop_budget() + 10)
            if unit not in self.own_records:
                self.create_gate(unit)
        self.companions("BEFORE_RELOAD")
        self.journal("RELOAD_INTENT")
        self.command(["/usr/bin/systemctl", "daemon-reload"], timeout=5)
        for unit in UNITS:
            self.show(unit)
        self.companions("AFTER_RELOAD")
        self.drift()
        self.check_gates()
        for change in self.changes:
            change["gate"] = "VERIFIED"
        self.journal("GATES_EFFECTIVE")
        self.budget.left(self.remaining_stop_budget())
        for unit in STOP_ORDER:
            self.companions("BEFORE_STOP")
            row = self.show(unit)
            self.drift()
            self.check_gates()
            self.budget.left(self.remaining_stop_budget())
            change = next(c for c in self.changes if c["unit"] == unit)
            recorded_group = self.baseline[unit]["ControlGroup"]
            need(not row["Job"] and row["ActiveState"] in {"active", "inactive"}, "UNIT_BUSY")
            if row["ActiveState"] != "inactive":
                self.journal("STOP_INTENT", unit=unit)
                change["stop"] = "REQUESTED"
                self.command(["/usr/bin/systemctl", "--no-block", "stop", unit])
                stop_end = min(self.budget.end, self.budget.clock() + (stop_seconds(row["TimeoutStopUSec"]) + 2 if unit.endswith(".service") else 3))
                while True:
                    self.budget.left()
                    self.companions("WAIT_STOP")
                    row = self.show(unit)
                    if row["ActiveState"] == "inactive" and row["SubState"] == "dead" and not row["Job"]:
                        break
                    need(self.budget.clock() < stop_end, "STOP_INCOMPLETE")
                    self.sleep(min(.2, max(0, stop_end - self.budget.clock())))
            need(row["ActiveState"] == "inactive" and row["SubState"] == "dead" and not row["Job"], "STOP_INCOMPLETE")
            need(self.cgroup_empty(recorded_group) and self.cgroup_empty(row["ControlGroup"]), "CGROUP_NOT_EMPTY")
            if unit.endswith(".service"):
                need(row["MainPID"] == "0" and row["ControlPID"] == "0", "STOP_INCOMPLETE")
            change["stop"] = "OBSERVED_INACTIVE"
            self.journal("STOP_OBSERVED", unit=unit)
            self.companions("AFTER_STOP")
        for _ in range(2):
            self.companions("FINAL_VERIFY")
            for unit in TARGETS:
                row = self.show(unit)
                need(row["ActiveState"] == "inactive" and row["SubState"] == "dead" and not row["Job"], "STOP_INCOMPLETE")
                need(self.cgroup_empty(self.baseline[unit]["ControlGroup"]) and self.cgroup_empty(row["ControlGroup"]), "CGROUP_NOT_EMPTY")
            self.drift()
            self.check_gates()
            self.sleep(.1)
        self.journal("ISOLATED_TARGET_SCOPE")
        if self.continuation is not None:
            self.continuation["stage"] = "COMPLETE"
        return self.receipt("ISOLATED_TARGET_SCOPE", "OK")

    def ben_gate_receipt(self):
        if self.ben_gate is None:
            return None
        value = dict(self.ben_gate)
        path = "/etc/systemd/system/" + BEN + ".d/90-bridge-ibm-isolation-" + self.change_id + ".conf"
        value.update(gate_path=path, gate_presence="UNKNOWN", recovery="NOT_APPLICABLE")
        if value["production_release_required"]:
            value["recovery"] = "REVIEW_REQUIRED"
            try:
                value["gate_presence"] = "PRESENT" if self.fs.exists(path) else "ABSENT"
                if (value["gate_presence"] == "PRESENT" and self.ben_gate_journaled
                        and BEN in self.own_records and self.fs.record(path) == self.own_records[BEN]):
                    value["recovery"] = "SNAPSHOT_BOUND_CONFIG_ONLY"
            except (Exception, KeyboardInterrupt):
                # A deadline/read error never converts uncertain ownership into
                # absence or recoverability. No server/helper command is run.
                value["gate_presence"] = "UNKNOWN"
        return value

    def receipt(self, result, reason):
        ben_gate = self.ben_gate_receipt()
        if result == "ISOLATED_TARGET_SCOPE" and ben_gate is not None and ben_gate["recovery"] != "SNAPSHOT_BOUND_CONFIG_ONLY":
            result, reason = "PARTIAL_BLOCKED", "FILE_DRIFT"
        states = []
        for unit in TARGETS:
            row = self.rows.get(unit, {})
            states.append({"unit": unit, "active": row.get("ActiveState", "unknown"),
                           "sub": row.get("SubState", "unknown"),
                           "job_pending": bool(row["Job"]) if "Job" in row else None,
                           "cgroup_empty": True if row.get("ActiveState") == "inactive" and not row.get("Job") and any(c["unit"] == unit and c["stop"] == "OBSERVED_INACTIVE" for c in self.changes) else None})
        p = self.p if type(self.p) is dict else {}
        # Invalid untrusted request values are never reflected.
        valid_run = p.get("run_id") if type(p.get("run_id")) is str and re.fullmatch(r"[1-9][0-9]{0,19}", p["run_id"]) else None
        valid_head = p.get("head") if type(p.get("head")) is str and re.fullmatch(r"[0-9a-f]{40}", p["head"]) else None
        return {"schema": RECEIPT_SCHEMA, "operation": p.get("operation") if p.get("operation") in ("rollback", "resume") else "isolate",
                "run_id": valid_run, "head": valid_head, "attempt": 1,
                "result": result, "reason": reason if reason in REASONS else "INTERNAL_BLOCKED",
                "snapshot_id": self.snapshot_id, "snapshot_sha256": self.snapshot_sha,
                "snapshot_restore_sha256": self.restore_sha,
                "changed_units": [c["unit"] for c in self.changes], "changes": self.changes,
                "target_states": states, "host_identity": "TRANSPORT_ASSERTION_ONLY",
                "graph_evidence": self.graph_evidence.value() if self.graph_evidence.failures else None,
                "companion_evidence": self.companion_evidence if reason == "COMPANION_BUSY" else None,
                "job_evidence": self.job_evidence if reason == "COMPANION_BUSY" else None,
                "ben_gate": ben_gate,
                "drift_evidence": self.drift_evidence(), "continuation": self.continuation,
                "limitations": LIMITATIONS}


def execute(payload, **kwargs):
    """Execute one bounded isolation; injectable root/runner are offline-test seams."""
    operation = Isolation(payload, **kwargs)
    try:
        return operation.resume() if type(payload) is dict and payload.get("operation") == "resume" else operation.isolate()
    except Blocked as exc:
        return operation.receipt("PARTIAL_BLOCKED" if operation.changes else "BLOCKED", str(exc))
    except (Exception, KeyboardInterrupt):
        return operation.receipt("PARTIAL_BLOCKED" if operation.changes else "BLOCKED", "INTERNAL_BLOCKED")


def load_recovery_snapshot(op, payload, prefix="rollback"):
    need(prefix in {"rollback", "resume"}, "ROLLBACK_INVALID")
    op.snapshot_id = payload["snapshot_id"]
    op.archive = RECOVERY_ROOT + "/" + op.snapshot_id
    op.snapshot_sha = payload["snapshot_sha256"]
    raw = op.fs.read(op.archive + "/snapshot.json", 2 * MAX_BYTES)
    need(digest(raw) == op.snapshot_sha, "ROLLBACK_INVALID")
    manifest = strict_json(raw)
    need(type(manifest) is dict and set(manifest) == {"schema", "request", "records", "metadata", "conditions"}
         and manifest["schema"] == "bridge.ibm.unit-isolation.snapshot.v1", "ROLLBACK_INVALID")
    original = validate_request(manifest["request"])
    op.original_request = original
    need(op.snapshot_id == original["run_id"] + "-" + original["head"][:12], "ROLLBACK_INVALID")
    op.change_id = op.snapshot_id
    op.allow = "/etc/systemd/system/.bridge-ibm-isolation-" + op.change_id + ".allow"
    op.gate_data = ("[Unit]\nConditionPathExists=" + op.allow + "\n").encode()
    op.gate_paths = {u: "/etc/systemd/system/" + u + ".d/90-bridge-ibm-isolation-" + op.change_id + ".conf" for u in GATE_UNITS}
    op.records = manifest["records"]
    need(type(op.records) is list and 0 < len(op.records) <= MAX_FILES, "ROLLBACK_INVALID")
    op.baseline = manifest["metadata"]
    need(type(op.baseline) is dict and set(op.baseline) == set(UNITS), "ROLLBACK_INVALID")
    op.conditions = manifest["conditions"]
    need(type(op.conditions) is dict and set(op.conditions) in (set(TARGETS), set(GATE_UNITS)), "ROLLBACK_INVALID")
    op.original_paths = {r["path"] for r in op.records}
    need(len(op.original_paths) == len(op.records), "ROLLBACK_INVALID")
    # Prove archive and restoration again, with an exclusive new staging dir.
    stage = op.archive + "/" + prefix + "-restore-" + payload["run_id"] + "-" + payload["head"][:12]
    op.fs.mkdir(stage)
    op.verify_archive(manifest, stage)
    op.restore_sha = op.snapshot_sha
    previous = None
    intents = set()
    for name, directory, link in op.fs.listdir(op.archive, limit=128):
        if not re.fullmatch(r"journal-[0-9]{3}\.json", name):
            continue
        need(not directory and not link, "ROLLBACK_INVALID")
        event_raw = op.fs.read(op.archive + "/" + name, MAX_OUTPUT)
        event = strict_json(event_raw)
        op.loaded_events.append(event)
        need(event["seq"] == op.journal_index and event["previous_sha256"] == previous, "ROLLBACK_INVALID")
        if event["event"] == "GATE_INTENT":
            need(event["unit"] in GATE_UNITS and event["path"] == op.gate_paths[event["unit"]]
                 and event["sha256"] == digest(op.gate_data), "ROLLBACK_INVALID")
            intents.add(event["unit"])
        if event["event"] == "GATE_CREATED":
            unit = event["unit"]
            need(unit in intents and unit not in op.own_records and event["record"]["path"] == op.gate_paths[unit]
                 and event["record"]["sha256"] == digest(op.gate_data), "ROLLBACK_INVALID")
            op.own_records[unit] = event["record"]
        if event["event"] == "ROLLBACK_REMOVED":
            removed = event["unit"]
            need(removed in op.own_records, "ROLLBACK_INVALID")
            del op.own_records[removed]; intents.remove(removed)
        previous = digest(event_raw)
        op.journal_index += 1
    op.journal_hash = previous
    need(intents == set(op.own_records) and bool(intents), "ROLLBACK_INVALID")

def rollback(payload, *, release_ben_only=False, **kwargs):
    """Separately authorized future window only; remove own verified gates, never start.

    A fresh controller run must supply the prior snapshot ID and digest. Unknown
    or partial files without a completed immutable GATE_CREATED record block.
    Empty directories and the full recovery archive are deliberately retained.
    """
    op = Isolation(payload, **kwargs)
    try:
        need(type(release_ben_only) is bool, "ROLLBACK_INVALID")
        validate_request(payload, rollback=True)
        op.host_check(op.fs)
        load_recovery_snapshot(op, payload)
        for unit in UNITS:
            row = op.show(unit)
            need(row["ActiveState"] == "inactive" and row["SubState"] == "dead" and not row["Job"], "UNIT_BUSY")
            need(op.cgroup_empty(row["ControlGroup"]), "CGROUP_NOT_EMPTY")
        op.companions("ROLLBACK_PREFLIGHT")
        op.drift()
        if release_ben_only:
            need(BEN in op.own_records, "ROLLBACK_INVALID")
        restored_units = []
        for unit, record in list(op.own_records.items()):
            if release_ben_only and unit != BEN:
                continue
            restored_units.append(unit)
            op.companions("ROLLBACK_BEFORE_REMOVE")
            op.drift()
            op.journal("ROLLBACK_INTENT", unit=unit, path=op.gate_paths[unit], sha256=record["sha256"])
            op.changes.append({"unit": unit, "gate": "INTENDED", "stop": "NOT_REQUESTED"})
            op.fs.unlink_owned(op.gate_paths[unit], record)
            del op.own_records[unit]
            op.changes[-1]["gate"] = "REMOVED"
            op.journal("ROLLBACK_REMOVED", unit=unit)
        op.journal("ROLLBACK_RELOAD_INTENT")
        op.command(["/usr/bin/systemctl", "daemon-reload"], timeout=5)
        for unit in UNITS:
            row = op.show(unit)
            need(row["ActiveState"] == "inactive" and row["SubState"] == "dead" and not row["Job"], "UNIT_BUSY")
        op.companions("ROLLBACK_AFTER_RELOAD")
        op.drift()
        for unit in restored_units:
            need(op.effective_conditions(unit) == op.conditions[unit], "ROLLBACK_DRIFT")
        op.journal("CONFIG_ONLY_RESTORED")
        return op.receipt("ROLLED_BACK_CONFIG_ONLY", "CONFIG_ONLY_RESTORED")
    except Blocked as exc:
        return op.receipt("PARTIAL_BLOCKED" if op.changes else "BLOCKED", str(exc))
    except (Exception, KeyboardInterrupt):
        return op.receipt("PARTIAL_BLOCKED" if op.changes else "BLOCKED", "INTERNAL_BLOCKED")


def release_ben_gate(payload, **kwargs):
    """Explicit future production-release: restore BEN config only, never Start.

    Requires the original snapshot/hash and all units freshly quiet. Current
    production main/transport does not invoke this separately reviewed action.
    Other owned Assistant Lab/timer gates remain in place.
    """
    return rollback(payload, release_ben_only=True, **kwargs)


def main(payload=None):
    """Production entrypoint: 240s from entry, parent death and signals fail closed.

    The transport embeds this reviewed source using python -I -B -c, then calls
    main with __name__='__main__'. Input JSON is bounded stdin; no CLI path input.
    Production transport only invokes the exact snapshot-bound continuation;
    rollback/release require a separately reviewed future controller window.
    """
    started = _SCRIPT_STARTED if _SCRIPT_STARTED is not None else time.monotonic()
    original_handlers = {}
    timer_owned = False
    receipt = None
    def stop(signum, _frame):
        raise Blocked("DEADLINE" if signum == signal.SIGALRM else "CANCELLED")
    try:
        need(platform.system() == "Linux" and os.geteuid() == 0, "ROOT_REQUIRED")
        need(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "HOST_MISMATCH")
        for sig in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            original_handlers[sig] = signal.signal(sig, stop)
        # Reserve one second for own-helper group cleanup and the small receipt.
        signal.setitimer(signal.ITIMER_REAL, max(.001, GUEST_SECONDS - 1 - (time.monotonic() - started)))
        timer_owned = True
        parent = os.getppid()
        need(parent > 1 and ctypes.CDLL(None).prctl(1, signal.SIGTERM) == 0 and os.getppid() == parent, "PARENT_LOST")
        if payload is None:
            raw = sys.stdin.buffer.read(MAX_INPUT + 1)
            need(len(raw) <= MAX_INPUT, "REQUEST_INVALID")
            payload = strict_json(raw)
        # This publication continues only the exact preserved pass. Initial
        # isolation/rollback/release are not exposed by its production route.
        validate_request(payload, resume=True)
        receipt = execute(payload, started=started)
    except Blocked as exc:
        receipt = Isolation(payload).receipt("BLOCKED", str(exc))
    except (Exception, KeyboardInterrupt):
        receipt = Isolation(payload).receipt("BLOCKED", "INTERNAL_BLOCKED")
    finally:
        if timer_owned:
            signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in original_handlers.items():
            signal.signal(sig, handler)
    raw = canonical(receipt)
    need(len(raw) <= MAX_OUTPUT, "COMMAND_LIMIT")
    print(raw.decode(), flush=True)
    return 0 if receipt["result"] == "ISOLATED_TARGET_SCOPE" else 3


if __name__ == "__main__":
    sys.exit(main())
