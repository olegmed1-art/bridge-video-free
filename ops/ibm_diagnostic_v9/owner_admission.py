"""Owner-side retained admission, never a power/Runtime capability.

Only the byte-pinned authenticated owner flow supplies transports and an already
validated handoff. This module neither gathers evidence nor authenticates JSON.
An intent is durable before delivery. Ambiguous delivery is never retried.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

INSTANCE_ID = "02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2"
FRAME_KIND = "OWNER_ADMISSION_RETAINED_BEFORE_ARM"


class Refused(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def pin(value, length=64):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value) is not None


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Cycle:
    control_run: int
    nonce: str
    source_sha: str
    runtime_package_sha: str
    instance_id: str = INSTANCE_ID

    def __post_init__(self):
        need(type(self.control_run) is int and self.control_run > 0 and pin(self.nonce, 32)
             and pin(self.source_sha, 40) and pin(self.runtime_package_sha)
             and self.instance_id == INSTANCE_ID, "ADMIT_CYCLE")


@dataclass(frozen=True)
class Pins:
    request_sha256: str
    proof_sha256: str
    window_sha256: str

    def __post_init__(self):
        need(all(pin(value) for value in asdict(self).values()), "ADMIT_PINS")


def validate_ack(cycle, pins, ack):
    expected = dict(kind="COLLECT_ADMIT_HANDOFF_RETAINED", control_run=cycle.control_run,
                    nonce=cycle.nonce, proof_sha256=pins.proof_sha256,
                    window_sha256=pins.window_sha256, live_go=False)
    need(type(ack) is dict and set(ack) == set(expected) and
         all(type(ack[k]) is type(v) and ack[k] == v for k, v in expected.items()),
         "ADMIT_ORIGINAL_ACK")
    return dict(ack)


def validate_ack_wire(raw, ack):
    need(type(raw) is bytes and 0 < len(raw) <= 1024, "ADMIT_ACK_WIRE")
    def pairs(items):
        value = {}
        for key, item in items:
            need(key not in value, "ADMIT_ACK_DUPLICATE")
            value[key] = item
        return value
    def bad(_):
        raise Refused("ADMIT_ACK_NONFINITE")
    try:
        parsed = json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)
    except (ValueError, UnicodeError):
        raise Refused("ADMIT_ACK_WIRE") from None
    need(type(parsed) is dict and parsed == ack
         and all(type(parsed[k]) is type(v) for k, v in ack.items()), "ADMIT_ACK_WIRE")
    return digest(raw)


def validate_readback(cycle, pins, value, now_ms):
    expected = dict(kind="COLLECT_ADMIT_RETAINED_READBACK_V1", **asdict(cycle),
                    **asdict(pins), live_go=False)
    need(type(value) is dict and set(value) == set(expected) | {"observed_wall_ms", "expires_ms"}
         and all(type(value[k]) is type(v) and value[k] == v for k, v in expected.items()),
         "ADMIT_EXACT_READBACK")
    observed, expires = value["observed_wall_ms"], value["expires_ms"]
    need(type(now_ms) is int and type(observed) is int and type(expires) is int
         and 0 <= now_ms - observed < 10000 and expires - now_ms >= 275000,
         "ADMIT_READBACK_STALE")
    return dict(value)


def _directory(path):
    """Pin every component; private application parent must already exist."""
    need(os.name == "posix" and Path(path).is_absolute(), "ADMIT_POSIX_PRIVATE_PARENT")
    chain = []
    links = []
    try:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        chain.append(fd)
        for part in Path(path).parts[1:]:
            need(part not in ("", ".", ".."), "ADMIT_PARENT_SCOPE")
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            chain.append(child)
            info = os.fstat(child)
            # /tmp can be a sticky root-owned ancestor; the final parent cannot.
            sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            need(info.st_uid in (0, os.getuid()) and (not info.st_mode & 0o022 or sticky_root),
                 "ADMIT_PARENT_METADATA")
            links.append((fd, part, child, (info.st_uid, info.st_mode)))
            fd = child
        info = os.fstat(fd)
        need(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
             "ADMIT_PRIVATE_PARENT")
        return chain, links
    except BaseException:
        for fd in reversed(chain):
            os.close(fd)
        raise


def _check(links):
    for parent, name, opened, original in links:
        a = os.stat(name, dir_fd=parent, follow_symlinks=False)
        b = os.fstat(opened)
        sticky_root = b.st_uid == 0 and bool(b.st_mode & stat.S_ISVTX)
        need(stat.S_ISDIR(a.st_mode) and (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
             and a.st_uid == b.st_uid and a.st_mode == b.st_mode
             and (b.st_uid, b.st_mode) == original
             and b.st_uid in (0, os.getuid()) and (not b.st_mode & 0o022 or sticky_root),
             "ADMIT_DIRECTORY_DRIFT")
    final = os.fstat(links[-1][2])
    need(final.st_uid == os.getuid() and stat.S_IMODE(final.st_mode) == 0o700,
         "ADMIT_PRIVATE_DIRECTORY_DRIFT")


class Journal:
    """Create-only receipt attempt. Existing attempts are UNKNOWN, never replayed."""

    def __init__(self, parent, cycle, pins):
        need(type(cycle) is Cycle and type(pins) is Pins, "ADMIT_JOURNAL_INPUT")
        self.cycle, self.pins = cycle, pins
        self.ack = None
        self.ack_wire_sha256 = None
        self.dispatched = False
        self.readback = None
        self.records = {}
        self.chain, self.links = _directory(parent)
        self.fd = None
        self.path = Path(parent) / ("admission-" + str(cycle.control_run) + "-" + cycle.nonce)
        try:
            name = self.path.name
            try:
                os.mkdir(name, 0o700, dir_fd=self.chain[-1])
            except FileExistsError:
                raise Refused("ADMIT_ATTEMPT_EXISTS_UNKNOWN_NO_RETRY") from None
            os.fsync(self.chain[-1])
            self.fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=self.chain[-1])
            info = os.fstat(self.fd)
            self.links.append((self.chain[-1], name, self.fd, (info.st_uid, info.st_mode)))
            self._write("intent.json", dict(kind="OWNER_ADMISSION_INTENT_V1",
                        cycle=asdict(cycle), pins=asdict(pins), live_go=False))
        except BaseException:
            self.close()
            raise

    def _write(self, name, value):
        _check(self.links)
        raw = canonical(value)
        need(0 < len(raw) <= 8192, "ADMIT_RECEIPT_LIMIT")
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                     dir_fd=self.fd)
        try:
            before = os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                 and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600,
                 "ADMIT_RECEIPT_METADATA")
            offset = 0
            while offset < len(raw):
                written = os.write(fd, raw[offset:])
                need(written > 0, "ADMIT_RECEIPT_SHORT_WRITE")
                offset += written
            os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            need(os.read(fd, len(raw) + 1) == raw, "ADMIT_RECEIPT_READBACK")
            after = os.fstat(fd)
            named = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            fields = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_mode, s.st_nlink, s.st_size,
                                s.st_mtime_ns, s.st_ctime_ns)
            need(fields(after) == fields(named) and after.st_nlink == 1,
                 "ADMIT_RECEIPT_DRIFT")
            need(after.st_uid == os.getuid() and stat.S_IMODE(after.st_mode) == 0o600,
                 "ADMIT_RECEIPT_METADATA_DRIFT")
            self.records[name] = (raw, fields(after))
        finally:
            os.close(fd)
        os.fsync(self.fd)
        _check(self.links)

    def acknowledge(self, request_sha256, ack, ack_raw):
        need(self.ack is None and self.readback is None
             and self.dispatched is True
             and request_sha256 == self.pins.request_sha256, "ADMIT_ACK_ORDER_OR_REQUEST")
        value = validate_ack(self.cycle, self.pins, ack)
        wire_sha = validate_ack_wire(ack_raw, value)
        self._write("ack.json", dict(kind="OWNER_ADMISSION_ACK_V1", cycle=asdict(self.cycle),
                    pins=asdict(self.pins), receipt=value, receipt_wire_sha256=wire_sha,
                    receipt_wire_base64=base64.b64encode(ack_raw).decode("ascii"), live_go=False))
        self.ack = value
        self.ack_wire_sha256 = wire_sha

    def complete(self, value, now_ms):
        need(self.ack is not None and self.readback is None, "ADMIT_READBACK_ORDER")
        value = validate_readback(self.cycle, self.pins, value, now_ms)
        self._write("readback.json", value)
        self.readback = value

    def _verify_records(self, names):
        _check(self.links)
        need(set(self.records) == names, "ADMIT_RECEIPT_SET")
        for name, (raw, original) in self.records.items():
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
            try:
                before = os.fstat(fd)
                fields = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_mode, s.st_nlink, s.st_size,
                                    s.st_mtime_ns, s.st_ctime_ns)
                need(fields(before) == original and os.read(fd, len(raw) + 1) == raw
                     and fields(os.fstat(fd)) == original
                     and fields(os.stat(name, dir_fd=self.fd, follow_symlinks=False)) == original,
                     "ADMIT_DURABLE_RECEIPT_DRIFT")
            finally:
                os.close(fd)
        _check(self.links)

    def begin_delivery(self):
        need(not self.dispatched and self.ack is None and self.readback is None,
             "ADMIT_DELIVERY_ALREADY_ATTEMPTED")
        self._verify_records({"intent.json"})
        self._write("dispatch.json", dict(kind="OWNER_ADMISSION_DISPATCH_ONCE_V1",
                    cycle=asdict(self.cycle), request_sha256=self.pins.request_sha256, live_go=False))
        self.dispatched = True

    def require_fresh(self, now_ms):
        need(self.ack is not None and self.readback is not None, "ADMIT_NOT_COMMITTED")
        self._verify_records({"intent.json", "dispatch.json", "ack.json", "readback.json"})
        return validate_readback(self.cycle, self.pins, self.readback, now_ms)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        for fd in reversed(self.chain):
            os.close(fd)
        self.chain = []


def finish_before_arm(journal, delivery, readback, *, clock_ms=lambda: time.time_ns() // 1000000):
    """Only after actual delivery, durable ACK, then separate authenticated readback.

    A receipt or readback failure does not prove receive refused. Leave the
    create-only intent/partial receipts intact; never issue a second receive.
    """
    try:
        need(type(delivery) is dict and set(delivery) == {"receipt", "request_sha256", "receipt_wire_sha256"}
             and delivery["request_sha256"] == journal.pins.request_sha256,
             "ADMIT_DELIVERY")
        ack = validate_ack(journal.cycle, journal.pins, delivery["receipt"])
        need(journal.ack == ack and journal.ack_wire_sha256 == delivery["receipt_wire_sha256"],
             "ADMIT_ACK_NOT_DURABLE")
        value = readback(journal.cycle, journal.pins, dict(ack))
        journal.complete(value, clock_ms())
        value = journal.require_fresh(clock_ms())
        return dict(kind=FRAME_KIND, **asdict(journal.cycle), **asdict(journal.pins),
                    observed_wall_ms=value["observed_wall_ms"], expires_ms=value["expires_ms"],
                    armed=False, power_post_attempted=False, live_go=False)
    except BaseException:
        raise Refused("ADMIT_OUTCOME_UNKNOWN_DO_NOT_RETRY") from None


def validate_public_frame(value):
    need(type(value) is dict, "ADMIT_PUBLIC_FRAME")
    cycle = Cycle(**{k: value.get(k) for k in Cycle.__dataclass_fields__})
    pins = Pins(**{k: value.get(k) for k in Pins.__dataclass_fields__})
    expected = {"kind", "observed_wall_ms", "expires_ms", "armed", "power_post_attempted", "live_go"}
    need(set(value) == expected | set(asdict(cycle)) | set(asdict(pins))
         and value["kind"] == FRAME_KIND
         and all(value[k] is False for k in ("armed", "power_post_attempted", "live_go"))
         and type(value["observed_wall_ms"]) is int and type(value["expires_ms"]) is int
         and value["observed_wall_ms"] < value["expires_ms"], "ADMIT_PUBLIC_FRAME")
    return dict(value)
