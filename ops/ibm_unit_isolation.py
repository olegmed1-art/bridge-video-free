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
RECEIPT_SCHEMA = "bridge.ibm.unit-isolation.receipt.v1"
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
UNITS = TARGETS + COMPANIONS
SYSTEM_DIRS = ("/etc/systemd/system", "/run/systemd/system", "/usr/lib/systemd/system")
RECOVERY_ROOT = "/var/lib/bridge-school-maintenance"
GUEST_SECONDS = 90
MAX_FILES = 32
MAX_BYTES = 256 * 1024
MAX_INPUT = 8192
MAX_OUTPUT = 32768
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
           "TimeoutStopFailureMode", "Restart")
SAFE_STATES = {"active", "inactive", "activating", "deactivating", "reloading", "failed"}
SAFE_SUB = {"running", "waiting", "dead", "exited", "start", "stop", "stop-sigterm",
            "stop-sigkill", "stop-post", "failed", "auto-restart"}
REASONS = {"OK", "REQUEST_INVALID", "HOST_MISMATCH", "ROOT_REQUIRED", "DEADLINE",
           "PARENT_LOST", "CANCELLED", "COMMAND_FAILED", "COMMAND_LIMIT", "METADATA_INVALID",
           "ALIAS_OR_UNIT_MISMATCH", "UNKNOWN_EDGE", "STOP_HANDLER", "STOP_POLICY",
           "COMPANION_BUSY", "UNIT_BUSY", "PATH_UNSAFE", "FILE_LIMIT", "FILE_DRIFT",
           "COLLISION", "ARCHIVE_FAILED", "RESTORE_MISMATCH", "GATE_NOT_EFFECTIVE",
           "CGROUP_NOT_EMPTY", "STOP_INCOMPLETE", "BUDGET_INSUFFICIENT", "INTERNAL_BLOCKED",
           "ROLLBACK_INVALID", "ROLLBACK_DRIFT", "CONFIG_ONLY_RESTORED"}
REASON_CODES = frozenset(REASONS)


class Blocked(Exception):
    """A fixed, safe error code, never raw subprocess/file data."""


def need(value, reason):
    if not value:
        raise Blocked(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


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


def validate_request(p, *, rollback=False):
    keys = {"schema", "operation", "run_id", "attempt", "head", "identity", "transport"}
    if rollback:
        keys |= {"snapshot_id", "snapshot_sha256"}
    need(type(p) is dict and set(p) == keys, "REQUEST_INVALID")
    need(p["schema"] == REQUEST_SCHEMA and p["operation"] == ("rollback" if rollback else "isolate"), "REQUEST_INVALID")
    need(type(p["run_id"]) is str and re.fullmatch(r"[1-9][0-9]{0,19}", p["run_id"]), "REQUEST_INVALID")
    need(type(p["attempt"]) is int and p["attempt"] == 1, "REQUEST_INVALID")
    need(type(p["head"]) is str and re.fullmatch(r"[0-9a-f]{40}", p["head"]), "REQUEST_INVALID")
    need(p["identity"] == IDENTITY and p["transport"] == TRANSPORT, "REQUEST_INVALID")
    if rollback:
        need(type(p["snapshot_id"]) is str and re.fullmatch(r"[1-9][0-9]{0,19}-[0-9a-f]{12}", p["snapshot_id"]), "ROLLBACK_INVALID")
        need(type(p["snapshot_sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", p["snapshot_sha256"]), "ROLLBACK_INVALID")
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


def validate_edges(unit, row):
    ordering = {"basic.target", "sysinit.target", "system.slice", "shutdown.target",
                "network-online.target", "multi-user.target", "timers.target", "time-set.target",
                "time-sync.target", "systemd-journald.socket", "-.mount"} | set(UNITS)
    direct = {OBSERVER: set(), CONTROL: {OBSERVER}, BRIDGE: {OBSERVER, CONTROL}, TIMER: set(), HEALTH: set(), BEN: {"docker.service"}}
    if unit == BEN:
        ordering.add("docker.service")  # Known, read-only companion edge; no Docker operation.
    reverse = {OBSERVER: {CONTROL, BRIDGE}, CONTROL: {BRIDGE}, BRIDGE: set(), TIMER: set(), HEALTH: set(), BEN: set()}
    for field in EDGES:
        values = row[field].split()
        need(len(values) <= 32 and len(values) == len(set(values)), "UNKNOWN_EDGE")
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
        need(set(values) <= allowed, "UNKNOWN_EDGE")
    need(row["Triggers"] == HEALTH if unit == TIMER else not row["Triggers"], "UNKNOWN_EDGE")


def stop_seconds(value):
    # systemctl formats USec properties as timespans (systemd v255). Deliberately
    # accept only a single finite seconds term, sufficient for reviewed 15/20s.
    need(re.fullmatch(r"(?:[1-9][0-9]?)(?:\.\d{1,3})?s", value), "STOP_POLICY")
    duration = float(value[:-1])
    need(0 < duration <= 20, "STOP_POLICY")
    return duration


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

    def command(self, args, timeout=3):
        return self.runner(args, timeout=min(timeout, self.budget.left()), limit=MAX_OUTPUT)

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
        validate_edges(unit, row)
        group = row["ControlGroup"]
        need(group in ("", "/system.slice/" + unit), "PATH_UNSAFE")
        if unit.endswith(".service"):
            need(row["MainPID"].isdigit() and row["ControlPID"].isdigit(), "METADATA_INVALID")
            if unit in TARGETS:
                stop_seconds(row["TimeoutStopUSec"])
                need(row["KillMode"] == "control-group" and row["KillSignal"] == "15"
                     and row["SendSIGKILL"] == "yes" and row["FinalKillSignal"] == "9"
                     and row["TimeoutStopFailureMode"] == "terminate"
                     and row["Restart"] in {"always", "on-failure", "no"}, "STOP_POLICY")
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

    def companions(self):
        for unit in COMPANIONS:
            row = self.show(unit)
            need(row["ActiveState"] == "inactive" and row["SubState"] == "dead" and not row["Job"]
                 and row["MainPID"] == "0" and row["ControlPID"] == "0", "COMPANION_BUSY")
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
        return paths - set(self.gate_paths.values())

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

    def drift(self):
        need(not self.fs.exists(self.allow), "COLLISION")
        need(self.inventory() == self.original_paths, "FILE_DRIFT")
        need(self.capture(self.original_paths) == self.records, "FILE_DRIFT")
        for unit, base in self.baseline.items():
            row = self.rows[unit]
            for field in ("Id", "Names", "FragmentPath", "UnitFileState") + EDGES:
                need(row[field] == base[field], "FILE_DRIFT")
            drops = set(row["DropInPaths"].split()) - set(self.gate_paths.values())
            need(drops == set(base["DropInPaths"].split()), "FILE_DRIFT")
        for unit, record in self.own_records.items():
            need(self.fs.record(self.gate_paths[unit]) == record, "FILE_DRIFT")

    def remaining_stop_budget(self):
        return sum(stop_seconds(self.baseline[u]["TimeoutStopUSec"])
                   for u in STOP_ORDER if u.endswith(".service") and self.rows[u]["ActiveState"] != "inactive") + 12

    def check_gates(self):
        need(not self.fs.exists(self.allow), "GATE_NOT_EFFECTIVE")
        expected = ["ConditionPathExists", False, False, self.allow]
        for unit in TARGETS:
            self.stop_handlers(unit)
            current = self.effective_conditions(unit)
            need(current == sorted(self.conditions[unit] + [expected], key=canonical), "GATE_NOT_EFFECTIVE")
            need(self.gate_paths[unit] in self.rows[unit]["DropInPaths"].split(), "GATE_NOT_EFFECTIVE")
            need(self.rows[unit]["NeedDaemonReload"] == "no", "GATE_NOT_EFFECTIVE")
            need(self.fs.record(self.gate_paths[unit]) == self.own_records[unit], "FILE_DRIFT")

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
            row = self.show(unit)
            need(row["NeedDaemonReload"] == "no", "FILE_DRIFT")
            if unit in TARGETS:
                self.stop_handlers(unit)
                need(not row["Job"] and row["ActiveState"] in {"active", "inactive"}, "UNIT_BUSY")
                need((row["ActiveState"], row["SubState"]) in {("active", "waiting" if unit == TIMER else "running"), ("inactive", "dead")}, "UNIT_BUSY")
        self.companions()
        self.baseline = {u: dict(r) for u, r in self.rows.items()}
        self.conditions = {u: self.effective_conditions(u) for u in TARGETS}
        self.original_paths = self.inventory()
        self.budget.left(self.remaining_stop_budget())

    def isolate(self):
        self.preflight()
        self.snapshot()
        self.companions()
        self.drift()
        self.budget.left(self.remaining_stop_budget())
        for unit in TARGETS:
            self.companions()
            self.drift()
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
        self.companions()
        self.journal("RELOAD_INTENT")
        self.command(["/usr/bin/systemctl", "daemon-reload"], timeout=5)
        for unit in UNITS:
            self.show(unit)
        self.companions()
        self.drift()
        self.check_gates()
        for change in self.changes:
            change["gate"] = "VERIFIED"
        self.journal("GATES_EFFECTIVE")
        self.budget.left(self.remaining_stop_budget())
        for unit in STOP_ORDER:
            self.companions()
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
                    self.companions()
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
            self.companions()
        for _ in range(2):
            self.companions()
            for unit in TARGETS:
                row = self.show(unit)
                need(row["ActiveState"] == "inactive" and row["SubState"] == "dead" and not row["Job"], "STOP_INCOMPLETE")
                need(self.cgroup_empty(self.baseline[unit]["ControlGroup"]) and self.cgroup_empty(row["ControlGroup"]), "CGROUP_NOT_EMPTY")
            self.drift()
            self.check_gates()
            self.sleep(.1)
        self.journal("ISOLATED_TARGET_SCOPE")
        return self.receipt("ISOLATED_TARGET_SCOPE", "OK")

    def receipt(self, result, reason):
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
        return {"schema": RECEIPT_SCHEMA, "operation": "rollback" if p.get("operation") == "rollback" else "isolate",
                "run_id": valid_run, "head": valid_head, "attempt": 1,
                "result": result, "reason": reason if reason in REASONS else "INTERNAL_BLOCKED",
                "snapshot_id": self.snapshot_id, "snapshot_sha256": self.snapshot_sha,
                "snapshot_restore_sha256": self.restore_sha,
                "changed_units": [c["unit"] for c in self.changes], "changes": self.changes,
                "target_states": states, "host_identity": "TRANSPORT_ASSERTION_ONLY",
                "limitations": LIMITATIONS}


def execute(payload, **kwargs):
    """Execute one bounded isolation; injectable root/runner are offline-test seams."""
    operation = Isolation(payload, **kwargs)
    try:
        return operation.isolate()
    except Blocked as exc:
        return operation.receipt("PARTIAL_BLOCKED" if operation.changes else "BLOCKED", str(exc))
    except (Exception, KeyboardInterrupt):
        return operation.receipt("PARTIAL_BLOCKED" if operation.changes else "BLOCKED", "INTERNAL_BLOCKED")


def rollback(payload, **kwargs):
    """Separately authorized future window only; remove own verified gates, never start.

    A fresh controller run must supply the prior snapshot ID and digest. Unknown
    or partial files without a completed immutable GATE_CREATED record block.
    Empty directories and the full recovery archive are deliberately retained.
    """
    op = Isolation(payload, **kwargs)
    try:
        validate_request(payload, rollback=True)
        op.host_check(op.fs)
        op.snapshot_id = payload["snapshot_id"]
        op.archive = RECOVERY_ROOT + "/" + op.snapshot_id
        op.snapshot_sha = payload["snapshot_sha256"]
        raw = op.fs.read(op.archive + "/snapshot.json", 2 * MAX_BYTES)
        need(digest(raw) == op.snapshot_sha, "ROLLBACK_INVALID")
        manifest = strict_json(raw)
        need(type(manifest) is dict and set(manifest) == {"schema", "request", "records", "metadata", "conditions"}
             and manifest["schema"] == "bridge.ibm.unit-isolation.snapshot.v1", "ROLLBACK_INVALID")
        original = validate_request(manifest["request"])
        need(op.snapshot_id == original["run_id"] + "-" + original["head"][:12], "ROLLBACK_INVALID")
        op.change_id = op.snapshot_id
        op.allow = "/etc/systemd/system/.bridge-ibm-isolation-" + op.change_id + ".allow"
        op.gate_data = ("[Unit]\nConditionPathExists=" + op.allow + "\n").encode()
        op.gate_paths = {u: "/etc/systemd/system/" + u + ".d/90-bridge-ibm-isolation-" + op.change_id + ".conf" for u in TARGETS}
        op.records = manifest["records"]
        need(type(op.records) is list and 0 < len(op.records) <= MAX_FILES, "ROLLBACK_INVALID")
        op.baseline = manifest["metadata"]
        need(type(op.baseline) is dict and set(op.baseline) == set(UNITS), "ROLLBACK_INVALID")
        op.conditions = manifest["conditions"]
        op.original_paths = {r["path"] for r in op.records}
        need(len(op.original_paths) == len(op.records), "ROLLBACK_INVALID")
        # Prove archive and restoration again, with an exclusive new staging dir.
        stage = op.archive + "/rollback-restore-" + payload["run_id"] + "-" + payload["head"][:12]
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
            need(event["seq"] == op.journal_index and event["previous_sha256"] == previous, "ROLLBACK_INVALID")
            if event["event"] == "GATE_INTENT":
                need(event["unit"] in TARGETS and event["path"] == op.gate_paths[event["unit"]]
                     and event["sha256"] == digest(op.gate_data), "ROLLBACK_INVALID")
                intents.add(event["unit"])
            if event["event"] == "GATE_CREATED":
                unit = event["unit"]
                need(unit in intents and unit not in op.own_records and event["record"]["path"] == op.gate_paths[unit]
                     and event["record"]["sha256"] == digest(op.gate_data), "ROLLBACK_INVALID")
                op.own_records[unit] = event["record"]
            previous = digest(event_raw)
            op.journal_index += 1
        op.journal_hash = previous
        need(intents == set(op.own_records) and bool(intents), "ROLLBACK_INVALID")
        for unit in UNITS:
            row = op.show(unit)
            need(row["ActiveState"] == "inactive" and row["SubState"] == "dead" and not row["Job"], "UNIT_BUSY")
            need(op.cgroup_empty(row["ControlGroup"]), "CGROUP_NOT_EMPTY")
        op.companions()
        op.drift()
        for unit, record in list(op.own_records.items()):
            op.companions()
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
        op.companions()
        op.drift()
        for unit in TARGETS:
            need(op.effective_conditions(unit) == op.conditions[unit], "ROLLBACK_DRIFT")
        op.journal("CONFIG_ONLY_RESTORED")
        return op.receipt("ROLLED_BACK_CONFIG_ONLY", "CONFIG_ONLY_RESTORED")
    except Blocked as exc:
        return op.receipt("PARTIAL_BLOCKED" if op.changes else "BLOCKED", str(exc))
    except (Exception, KeyboardInterrupt):
        return op.receipt("PARTIAL_BLOCKED" if op.changes else "BLOCKED", "INTERNAL_BLOCKED")


def main(payload=None):
    """Production entrypoint: 90s from entry, parent death and signals fail closed.

    The transport embeds this reviewed source using python -I -B -c, then calls
    main with __name__='__main__'. Input JSON is bounded stdin; no CLI path input.
    Production transport only invokes isolate; rollback requires a separately
    reviewed, freshly bound future controller window.
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
        # Rollback is intentionally unavailable through the current main path.
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
