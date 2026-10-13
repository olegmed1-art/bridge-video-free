"""Local, closed-release candidate. Import has no network/process side effects."""
from __future__ import annotations

import ipaddress
import json
import math
import os
import selectors
import signal
import socket
import ssl
import struct
import subprocess
import sys
import time
from dataclasses import dataclass

LIVE_RELEASE_ENABLED = False  # No environment variable or CLI bypass.
HOST = "ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech"
PORT = 5432
SOURCE_COMMIT = "d49feb4cb333b041928a5d37efb56a9f97dd1822"
SOURCE_SHA256 = "1c391cd0bfde393994db6b3b2f71af7843f5ed48b79e5476eced02113338234a"
TOTAL_SECONDS = 15.0
CLEANUP_SECONDS = 2.0
BUDGET = {1: 5.0, 2: 3.0, 3: 2.0, 4: 5.0}
STAGES = {1: "dns", 2: "tcp", 3: "ssl_request", 4: "tls_handshake"}
SSL_REQUEST = struct.pack("!II", 8, 80877103)
# Fixed binary IPC: kind, enum, monotonic timestamp, five bounded counters.
# ssl_bytes=255 means sendall started but transmitted byte count is unknown.
FRAME = struct.Struct("!BBdBBBBB")
MAX_FRAMES = 5
STATUSES = {
    1: "tls_success", 2: "dns_timeout", 3: "dns_failure",
    4: "dns_no_public_ipv4", 5: "dns_invalid_result", 6: "tcp_timeout",
    7: "tcp_failure", 8: "ssl_request_timeout", 9: "ssl_request_failure",
    10: "ssl_not_accepted", 11: "ssl_unexpected_reply", 12: "ssl_eof",
    13: "tls_timeout", 14: "tls_certificate_failure", 15: "tls_failure",
    16: "tls_context_invalid", 17: "worker_internal_failure",
    18: "socket_cleanup_failure",
}
STATUS_STAGE = {1: 4, **{x: 1 for x in range(2, 6)}, 6: 2, 7: 2,
                **{x: 3 for x in range(8, 13)}, **{x: 4 for x in range(13, 17)}}


class Deadline(Exception):
    pass


class InvalidData(Exception):
    pass


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass
class Counts:
    resolver: int = 0
    tcp: int = 0
    ssl_request: int = 0
    handshake: int = 0
    ssl_bytes: int = 0

    def values(self):
        return (self.resolver, self.tcp, self.ssl_request, self.handshake, self.ssl_bytes)


class StageClock:
    def __init__(self, clock, start):
        if not finite(start):
            raise InvalidData()
        self.clock, self.start, self.last = clock, start, start
        self.overall, self.end = start + TOTAL_SECONDS, start + BUDGET[1]

    def now(self):
        now = self.clock()
        if not finite(now) or now < self.last:
            raise InvalidData()
        self.last = now
        return now

    def remaining(self):
        left = min(self.end, self.overall) - self.now()
        if left <= 0:
            raise Deadline()
        return left

    def enter(self, stage):
        now = self.now()
        # Completing the preceding stage late is a failure, never a fresh budget.
        if now >= min(self.end, self.overall):
            raise Deadline()
        if stage != 1:
            self.end = min(now + BUDGET[stage], self.overall)
        return now


def choose_ipv4(rows):
    if type(rows) not in (list, tuple) or not 1 <= len(rows) <= 64:
        raise InvalidData()
    selected = None
    for row in rows:
        if type(row) not in (tuple, list) or len(row) != 5:
            raise InvalidData()
        family, kind, protocol, _canonical, address = row
        if (family != socket.AF_INET or kind != socket.SOCK_STREAM or
                protocol != socket.IPPROTO_TCP or type(address) not in (tuple, list) or
                len(address) != 2 or type(address[0]) is not str or
                type(address[1]) is not int or address[1] != PORT):
            raise InvalidData()
        try:
            ip = ipaddress.IPv4Address(address[0])
        except (ValueError, ipaddress.AddressValueError):
            raise InvalidData() from None
        if (selected is None and ip.is_global and not ip.is_multicast and
                not ip.is_reserved and not ip.is_loopback and not ip.is_link_local):
            selected = (str(ip), PORT)
    return selected


def run_core(*, resolver, socket_factory, context_factory, clock, start, emit):
    """Injected worker logic; caller/supervisor must bound blocking DNS and cleanup.

    emit receives only fixed numeric frames, never endpoint/error/certificate text.
    No retry, application message after SSLRequest, TLS unwrap, or peer inspection.
    """
    budget = StageClock(clock, start)
    counts, raw, secured = Counts(), None, None
    stage, status = 1, 17

    def enter(next_stage):
        nonlocal stage
        ts = budget.enter(next_stage)
        stage = next_stage
        emit(FRAME.pack(1, stage, ts, *counts.values()))

    try:
        enter(1)
        counts.resolver = 1
        rows = resolver(HOST, PORT, socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        budget.remaining()
        try:
            address = choose_ipv4(rows)
        except InvalidData:
            status = 5
        else:
            if address is None:
                status = 4
            else:
                enter(2)
                raw = socket_factory(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
                raw.settimeout(budget.remaining())
                counts.tcp = 1
                raw.connect(address)
                budget.remaining()
                enter(3)
                raw.settimeout(budget.remaining())
                counts.ssl_request, counts.ssl_bytes = 1, 255
                raw.sendall(SSL_REQUEST)
                counts.ssl_bytes = 8
                raw.settimeout(budget.remaining())
                reply = raw.recv(1)
                budget.remaining()
                if reply == b"N":
                    status = 10
                elif reply == b"":
                    status = 12
                elif reply != b"S":
                    status = 11
                else:
                    enter(4)
                    ctx = context_factory(ssl.Purpose.SERVER_AUTH)
                    if (ctx.verify_mode != ssl.CERT_REQUIRED or ctx.check_hostname is not True or
                            ctx.protocol != ssl.PROTOCOL_TLS_CLIENT or ctx.keylog_filename is not None):
                        status = 16
                    else:
                        raw.settimeout(budget.remaining())
                        secured = ctx.wrap_socket(raw, server_hostname=HOST, do_handshake_on_connect=False)
                        secured.settimeout(budget.remaining())
                        counts.handshake = 1
                        secured.do_handshake()
                        budget.remaining()
                        status = 1
    except (Deadline, TimeoutError):
        status = {1: 2, 2: 6, 3: 8, 4: 13}[stage]
    except ssl.SSLCertVerificationError:
        status = 14 if stage == 4 else 17
    except (OSError, ssl.SSLError):
        status = {1: 3, 2: 7, 3: 9, 4: 15}[stage]
    except Exception:
        status = 17
    finally:
        # close() does not perform unwrap/close_notify or send a StartupMessage.
        # If it blocks, parent still owns the deadline and kills the whole group.
        for resource in (secured, raw):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    status = 18
    # terminal is emitted only after local socket cleanup; parent also requires reap.
    emit(FRAME.pack(2, status, budget.now(), *counts.values()))


class Protocol:
    def __init__(self, start):
        self.start, self.last = start, start
        self.stage, self.end = 0, start + BUDGET[1]
        self.overall = start + TOTAL_SECONDS
        self.buffer, self.frames, self.terminal = b"", 0, None
        self.counts = Counts()
        self.durations, self.stage_start = {}, start

    def feed(self, chunk, now):
        if type(chunk) is not bytes or len(chunk) > FRAME.size * MAX_FRAMES:
            raise InvalidData()
        if not finite(now) or now < self.last or now >= min(self.end, self.overall):
            raise InvalidData()
        self.buffer += chunk
        while len(self.buffer) >= FRAME.size:
            if self.terminal is not None or self.frames >= MAX_FRAMES:
                raise InvalidData()
            frame, self.buffer = self.buffer[:FRAME.size], self.buffer[FRAME.size:]
            kind, code, ts, resolver, tcp, request, handshake, sent = FRAME.unpack(frame)
            self.frames += 1
            if not finite(ts) or not self.last <= ts <= now or ts >= min(self.end, self.overall):
                raise InvalidData()
            values = (resolver, tcp, request, handshake)
            if (any(x not in (0, 1) for x in values) or sent not in (0, 8, 255) or
                    not resolver >= tcp >= request >= handshake or
                    any(a < b for a, b in zip(values, self.counts.values()[:4])) or
                    (request == 0 and sent != 0) or (request == 1 and sent == 0) or
                    (handshake == 1 and sent != 8)):
                raise InvalidData()
            if kind == 1:
                if code != self.stage + 1 or code not in STAGES:
                    raise InvalidData()
                expected = {1: (0, 0, 0, 0, 0), 2: (1, 0, 0, 0, 0),
                            3: (1, 1, 0, 0, 0), 4: (1, 1, 1, 0, 8)}[code]
                if (resolver, tcp, request, handshake, sent) != expected:
                    raise InvalidData()
                if self.stage:
                    self.durations[STAGES[self.stage]] = round((ts - self.stage_start) * 1000)
                self.stage, self.stage_start = code, ts if code != 1 else self.start
                self.end = min(self.overall, self.stage_start + BUDGET[code])
            elif kind == 2:
                if not self.stage or code not in STATUSES or STATUS_STAGE.get(code, self.stage) != self.stage:
                    raise InvalidData()
                allowed = {
                    1: {(1, 0, 0, 0, 0)},
                    2: {(1, 0, 0, 0, 0), (1, 1, 0, 0, 0)},
                    3: {(1, 1, 0, 0, 0), (1, 1, 1, 0, 255), (1, 1, 1, 0, 8)},
                    4: {(1, 1, 1, 0, 8), (1, 1, 1, 1, 8)},
                }
                if (resolver, tcp, request, handshake, sent) not in allowed[self.stage]:
                    raise InvalidData()
                if code in (10, 11, 12) and (request, sent) != (1, 8):
                    raise InvalidData()
                if code == 1 and (resolver, tcp, request, handshake, sent) != (1, 1, 1, 1, 8):
                    raise InvalidData()
                self.terminal = code
                self.durations[STAGES[self.stage]] = round((ts - self.stage_start) * 1000)
                # Exit/reap must still fit the stage AND overall deadline.
            else:
                raise InvalidData()
            self.counts = Counts(resolver, tcp, request, handshake, sent)
            self.last = ts


def result(status, protocol, *, complete=False, reaped=False, elapsed=0.0):
    values = protocol.counts
    return {
        "schema": "bridge_ipv4_tls_probe_candidate_v1",
        "contract_commit": SOURCE_COMMIT, "contract_sha256": SOURCE_SHA256,
        "endpoint_alias": "reviewed_production_pooler", "status": status,
        "last_stage": STAGES.get(protocol.stage, "dns"),
        "stage_duration_ms": protocol.durations,
        "elapsed_ms": round(max(0, min(elapsed, TOTAL_SECONDS + CLEANUP_SECONDS)) * 1000),
        "elapsed_ms_capped": elapsed > TOTAL_SECONDS + CLEANUP_SECONDS,
        "reported_calls": dict(zip(("resolver", "tcp_connect", "ssl_request_sendall", "tls_handshake"), values.values()[:4])),
        "ssl_request_bytes_sent": None if not complete or values.ssl_bytes == 255 else values.ssl_bytes,
        "counters_complete": complete, "max_calls_each": 1,
        "credentials_used": False, "startup_message_sent": False, "sql_sent": False,
        "worker_reaped": reaped, "live_release_enabled": LIVE_RELEASE_ENABLED,
    }


def supervise(*, launch, clock):
    """Pure supervisor; launch/child is injected in local tests.

    child.read(timeout,max_bytes)->bytes|None (b''=EOF), poll()->rc|None,
    kill_group(), wait(timeout)->rc, close(). Adapters must not add retries.
    """
    try:
        start = clock()
        if not finite(start):
            raise InvalidData()
    except Exception:
        return result("supervisor_clock_failure", Protocol(0))
    protocol = Protocol(start)
    child, reaped, status, complete = None, False, "supervisor_failure", False
    last_clock = start
    clock_failed = False
    def now():
        nonlocal last_clock, clock_failed
        try:
            value = clock()
            if not finite(value) or value < last_clock:
                raise InvalidData()
        except Exception:
            clock_failed = True
            raise InvalidData() from None
        last_clock = value
        return value
    try:
        child = launch(start)
        if getattr(child, "initialized", True) is not True:
            raise InvalidData()
        eof = False
        while True:
            current = now()
            deadline = min(protocol.end, protocol.overall)
            if current >= deadline:
                status = STAGES.get(protocol.stage, "dns") + "_supervisor_timeout"
                break
            if eof:
                # No pipe busy-spin after EOF; wait bounded by the same deadline.
                rc = child.wait(deadline - current)
                reaped = True
                if now() >= deadline:
                    status = "late_worker_exit"
                elif rc == 0 and protocol.terminal is not None and not protocol.buffer:
                    status, complete = STATUSES[protocol.terminal], True
                else:
                    status = "worker_exit_failure"
                break
            chunk = child.read(deadline - current, FRAME.size * MAX_FRAMES + 1)
            current = now()
            if current >= deadline:
                status = STAGES.get(protocol.stage, "dns") + "_supervisor_timeout"
                break
            if chunk is None:
                continue
            if chunk == b"":
                eof = True
            else:
                protocol.feed(chunk, current)
    except subprocess.TimeoutExpired:
        status = "worker_exit_timeout"
    except InvalidData:
        status = "worker_protocol_failure"
    except Exception:
        status = "supervisor_failure"
    finally:
        if child is not None:
            if not reaped:
                # Immediate SIGKILL, not TERM grace: no intentional post-deadline
                # network window. The only grace is for collection/reaping.
                cleanup_start = None
                try:
                    cleanup_start = now()
                except InvalidData:
                    pass
                cleanup_ok = True
                try:
                    child.kill_group()
                except Exception:
                    cleanup_ok = False
                # Even a kill error or broken clock must not skip a nonblocking
                # reap attempt. A broken clock earns no extra wait budget.
                remaining = 0.0
                try:
                    cleanup_now = now()
                    if cleanup_start is not None:
                        remaining = max(0.0, CLEANUP_SECONDS - (cleanup_now - cleanup_start))
                except InvalidData:
                    cleanup_ok = False
                try:
                    child.wait(remaining)
                    reaped = True
                except Exception:
                    cleanup_ok = False
                try:
                    cleanup_end = now()
                    if cleanup_start is None or cleanup_end > cleanup_start + CLEANUP_SECONDS:
                        cleanup_ok = False
                except InvalidData:
                    cleanup_ok = False
                if not cleanup_ok:
                    status, complete = "worker_cleanup_unconfirmed", False
            try:
                child.close()
            except Exception:
                status, complete = "worker_cleanup_unconfirmed", False
    try:
        elapsed = now() - start
    except InvalidData:
        elapsed = max(0, last_clock - start)
    if clock_failed:
        status, complete = "supervisor_clock_failure", False
    return result(status, protocol, complete=complete, reaped=reaped, elapsed=elapsed)


class PosixChild:
    """Real adapter is unreachable while the release gate is closed."""
    def __init__(self, start):
        if LIVE_RELEASE_ENABLED is not True or sys.platform != "linux":
            raise RuntimeError("live_release_closed")
        self.selector = selectors.DefaultSelector()
        self.process, self.initialized = None, False
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-I", "-S", "-B", os.path.abspath(__file__), "--internal-worker", repr(start)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={"LC_ALL": "C", "LANG": "C"}, close_fds=True, start_new_session=True,
                bufsize=0,
            )
        except BaseException:
            self.selector.close()
            raise
        # Return a cleanup-capable handle even if initialization failed after
        # Popen: the supervisor owns its same deadline, kill and reap budget.
        try:
            os.set_blocking(self.process.stdout.fileno(), False)
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
            self.initialized = True
        except BaseException:
            pass

    def read(self, timeout, max_bytes):
        if not self.selector.select(timeout):
            return None
        try:
            return os.read(self.process.stdout.fileno(), max_bytes)
        except BlockingIOError:
            return None

    def kill_group(self):
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def wait(self, timeout):
        return self.process.wait(timeout=timeout)

    def close(self):
        if self.selector is not None:
            self.selector.close()
        self.process.stdout.close()


def live_entry(argv):
    # Checked before argument parsing/importing app code/reading environment or
    # opening sockets, DNS, CA stores or subprocesses. No runtime enable switch.
    if LIVE_RELEASE_ENABLED is not True:
        return {"schema": "bridge_ipv4_tls_probe_candidate_v1", "status": "live_release_closed"}
    if sys.platform != "linux":
        return {"schema": "bridge_ipv4_tls_probe_candidate_v1", "status": "linux_required"}
    if len(argv) == 2 and argv[0] == "--internal-worker":
        start = float(argv[1])
        def emit(frame):
            sys.stdout.buffer.write(frame)
            sys.stdout.buffer.flush()
        run_core(resolver=socket.getaddrinfo, socket_factory=socket.socket,
                 context_factory=ssl.create_default_context, clock=time.monotonic,
                 start=start, emit=emit)
        return None
    if argv:
        return {"schema": "bridge_ipv4_tls_probe_candidate_v1", "status": "invalid_arguments"}
    return supervise(launch=PosixChild, clock=time.monotonic)


if __name__ == "__main__":
    try:
        output = live_entry(sys.argv[1:])
    except BaseException:
        output = {"schema": "bridge_ipv4_tls_probe_candidate_v1", "status": "entry_failure"}
    if output is not None:
        print(json.dumps(output, sort_keys=True, separators=(",", ":")))
        raise SystemExit(2)
