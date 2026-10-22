"""One reader for one accepted socket, across data and terminal persistence.

Wire v2 lifecycle. No native identity, provider, key or socket is created here.
Peer/policy/native authorization stays in the separate owner provider. Challenge
echo demonstrates fresh connection participation only, never native authority.
"""
from __future__ import annotations

from collections import deque
import queue
import secrets
import socket
import threading
import time

from . import diana20_transport_protocol as p

REQUESTS = {p.CONTROL_CHALLENGE: p.CONTROL_REPLY, p.CONTROL_CHECK: p.CONTROL_CHECKED,
            p.CONTROL_TERMINAL_ACK: p.CONTROL_ACKED, p.CONTROL_CLOSE: p.CONTROL_CLOSED}
RESPONSES = set(REQUESTS.values())
BUFFER = p.MAX_OBSERVATIONS + p.CAPS[p.RESULT] + p.CAPS[p.FRAME] + 131072


class _Data:
    """Semantic guards run on consumers, NEVER on the sole raw reader."""
    def __init__(self, control):
        self.control = control
        self.guard = control.io_check

    def read(self, expected, *, timeout=None):
        c = self.control
        end = c.deadline if timeout is None else min(c.deadline, c.clock() + timeout)
        while True:
            self.guard()
            with c.condition:
                c.check()
                if c.data:
                    kind, raw = c.data.popleft()
                    c.data_bytes -= len(raw)
                    choices = expected if type(expected) is tuple else (expected,)
                    p.need(kind in choices, 'RECORD_ORDER')
                    return (kind, raw) if type(expected) is tuple else raw
                p.need(not c.eof.is_set(), 'CONTROL_EOF_UNKNOWN')
                p.need(c.clock() < end, 'CONTROL_READ_TIMEOUT')
                c.condition.wait(.05)

    def write(self, kind, raw=b''):
        self.guard()
        self.control.write(kind, raw)


class SharedControl:
    """Source-bound constructor ports, not wire-selected authority callbacks.

    Server authority.open(ticket, peer, control=this) must bind the genuine
    OwnerApplicationChannel.control_connection_check to this exact endpoint.
    Client challenge_validator only validates the accepted binding; it MUST be
    nonblocking and must not call another control RPC/native execution bridge.
    """
    def __init__(self, raw_records, *, connection, role, assert_io,
                 challenge_validator=None, clock=time.monotonic,
                 timeout=2., heartbeat=1., lifetime=3600.):
        p.need(role in ('client', 'server') and callable(assert_io), 'CONTROL_PORT')
        p.need(type(timeout) in (int, float) and 0 < timeout <= 2
               and type(heartbeat) in (int, float) and 0 < heartbeat <= 1
               and type(lifetime) in (int, float) and 0 < lifetime <= 3600, 'CONTROL_LIMIT')
        p.need(role == 'server' or callable(challenge_validator), 'CONTROL_VALIDATOR_REQUIRED')
        self.raw, self.connection, self.role = raw_records, connection, role
        self.assert_io, self.validate_challenge = assert_io, challenge_validator
        self.clock, self.timeout, self.heartbeat = clock, timeout, heartbeat
        self.deadline = clock() + lifetime
        self.condition = threading.Condition(threading.RLock())
        self.write_lock, self.rpc_lock = threading.Lock(), threading.Lock()
        self.local = threading.local()
        self.stop_event, self.eof = threading.Event(), threading.Event()
        self.heartbeat_stop = threading.Event()
        self.requests = queue.Queue(maxsize=8)
        self.data, self.data_bytes = deque(), 0
        self.data_finished = False
        self.pending, self.serial, self.incoming_serial = None, 0, 0
        self.failure = None
        self.expect_eof = self.closed = False
        self.job = self.owner_live = None
        self.binding = self.ack_binding = self.close_request = None
        self.last_check = clock()
        self.reader = self.handler = self.heartbeat_thread = None
        self.records = _Data(self)
        self.raw.guard = self.io_check
        if isinstance(self.raw.stream, p.FdBytes):
            self.raw.stream.guard = self.io_check

    def check(self):
        if self.failure is not None:
            raise p.TransportError(self.failure) from None
        p.need(not self.closed and self.clock() < self.deadline, 'CONTROL_EXPIRED')
        if self.role == 'server' and self.job is not None:
            p.need(self.clock() - self.last_check <= 5, 'CONTROL_HEARTBEAT_LOST')

    def io_check(self):
        self.check()
        p.need(self.clock() < getattr(self.local, 'deadline', self.deadline), 'CONTROL_REQUEST_TIMEOUT')
        p.need(not self.stop_event.is_set(), 'CONTROL_STOPPED')
        p.need(self.assert_io() is None, 'CONTROL_IO_REFUSED')

    def fail(self, code='CONTROL_OUTCOME_UNKNOWN_NO_RETRY'):
        with self.condition:
            if self.failure is None:
                self.failure = 'DIANA20_TRANSPORT_' + code
            self.stop_event.set()
            self.condition.notify_all()
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass

    def start(self):
        p.need(self.reader is None, 'CONTROL_REUSED')
        try:
            self.reader = threading.Thread(target=self._read_loop, daemon=True)
            self.handler = threading.Thread(target=self._handle_loop, daemon=True)
            self.reader.start()
            self.handler.start()
            return self
        except BaseException:
            self.fail()
            self.stop()  # Caller may not yet own this object; clean it here.
            raise

    def write(self, kind, raw=b''):
        self.check()
        end = min(self.deadline, getattr(self.local, 'deadline', self.deadline))
        p.need(self.write_lock.acquire(timeout=max(0, end-self.clock())), 'CONTROL_WRITE_TIMEOUT')
        try:
            self.io_check()
            self.raw.write(kind, raw)
        finally:
            self.write_lock.release()

    def _read_loop(self):
        try:
            while not self.stop_event.is_set():
                record = self.raw.read_any(allow_eof=True)
                if record is None:
                    with self.condition:
                        p.need(self.expect_eof and not self.data, 'CONTROL_EOF_UNKNOWN')
                        self.eof.set()
                        self.condition.notify_all()
                    return
                kind, raw = record
                if kind in REQUESTS or kind in RESPONSES:
                    value = p.parse(raw)
                    p.need(type(value) is dict and set(value) == {'serial', 'body'}
                           and type(value['serial']) is int and 1 <= value['serial'] <= 2**53,
                           'CONTROL_ENVELOPE')
                    if kind in RESPONSES:
                        with self.condition:
                            pending = self.pending
                            p.need(pending is not None and pending['kind'] == kind
                                   and pending['serial'] == value['serial']
                                   and pending['response'] is None, 'CONTROL_CORRELATION')
                            pending['response'] = value['body']
                            self.condition.notify_all()
                    else:
                        p.need(value['serial'] == self.incoming_serial + 1, 'CONTROL_REPLAY')
                        self.incoming_serial = value['serial']
                        self.requests.put_nowait((kind, value, self.clock()))
                else:
                    with self.condition:
                        p.need(not self.data_finished, 'CONTROL_TRAILING_DATA')
                        p.need(len(self.data) < p.MAX_FRAMES + 8
                               and self.data_bytes + len(raw) <= BUFFER, 'CONTROL_BUFFER')
                        self.data.append((kind, raw))
                        self.data_bytes += len(raw)
                        self.condition.notify_all()
        except BaseException:
            if not self.stop_event.is_set():
                self.fail()

    def _handle_loop(self):
        try:
            while not self.stop_event.is_set():
                try:
                    kind, value, arrived = self.requests.get(timeout=.05)
                except queue.Empty:
                    self.io_check()  # Enforce CHECK cadence during compute too.
                    continue
                body = value['body']
                self.local.deadline = min(self.deadline, arrived + self.timeout)
                self.io_check()
                if self.role == 'client':
                    p.need(kind == p.CONTROL_CHALLENGE and type(body) is dict and
                           set(body) == {'challenge', 'scope_nonce', 'policy_sha256',
                                        'claim_sha256', 'context_sha256'}, 'CONTROL_CHALLENGE')
                    p.need(all(p.pin(body[k]) for k in ('challenge', 'scope_nonce', 'policy_sha256'))
                           and all(body[k] is None or p.pin(body[k]) for k in
                                   ('claim_sha256', 'context_sha256')), 'CONTROL_CHALLENGE')
                    started = self.clock()
                    p.need(self.validate_challenge(body) is None, 'CONTROL_CHALLENGE_REFUSED')
                    p.need(self.clock() - started <= self.timeout, 'CONTROL_CHALLENGE_TIMEOUT')
                else:
                    self._server_request(kind, value)
                    if kind == p.CONTROL_CLOSE:
                        continue  # final CLOSED comes only after authority exit
                self.write(REQUESTS[kind], p.canonical(value))
                self.local.deadline = self.deadline
        except BaseException:
            if not self.stop_event.is_set():
                self.fail()

    def request(self, kind, body):
        # Client validator cannot recursively request owner checks while the
        # server's OwnerScope.check is waiting for its reply.
        p.need(self.role != 'client' or threading.current_thread() is not self.handler,
               'CONTROL_REENTRANT')
        end = min(self.clock() + self.timeout, self.deadline,
                  getattr(self.local, 'deadline', self.deadline))
        acquired = self.rpc_lock.acquire(timeout=max(0, end-self.clock()))
        if not acquired:
            self.fail()
            p.need(False, 'CONTROL_REQUEST_TIMEOUT')
        previous = getattr(self.local, 'deadline', self.deadline)
        self.local.deadline = end
        try:
            self.check()
            with self.condition:
                self.serial += 1
                p.need(self.serial <= 2**53 and self.pending is None, 'CONTROL_SERIAL')
                self.pending = dict(serial=self.serial, kind=REQUESTS[kind], response=None)
            try:
                self.write(kind, p.canonical(dict(serial=self.serial, body=body)))
                with self.condition:
                    while self.pending['response'] is None:
                        self.check()
                        p.need(not self.eof.is_set() and self.clock() < end, 'CONTROL_ACK_UNKNOWN_NO_RETRY')
                        self.condition.wait(.02)
                    result = self.pending['response']
                p.need(self.clock() < end, 'CONTROL_ACK_UNKNOWN_NO_RETRY')
                p.need(p.canonical(result) == p.canonical(body), 'CONTROL_RESPONSE_BINDING')
                return result
            except BaseException:
                self.fail()
                raise
            finally:
                with self.condition:
                    self.pending = None
        finally:
            self.local.deadline = previous
            self.rpc_lock.release()

    def control_connection_check(self, accepted_socket, scope):
        """Exact semantic callback of existing OwnerApplicationChannel.

        No NativeAssignment, resolver, opaque cycle or UID1001 object crosses it.
        Nonce/policy/claim/context come from the actual scope supplied by owner.
        """
        p.need(self.role == 'server' and accepted_socket is self.connection, 'CONTROL_SOCKET_MISMATCH')
        claim = scope.claim
        body = dict(challenge=secrets.token_hex(32), scope_nonce=scope.nonce,
            policy_sha256=scope.policy_sha,
            claim_sha256=None if claim is None else p.digest(p.canonical(claim)),
            context_sha256=scope.context_sha)
        self.request(p.CONTROL_CHALLENGE, body)

    def bind_server(self, job, owner_live):
        p.need(self.role == 'server' and self.job is None and type(job) is p.Job
               and callable(owner_live), 'CONTROL_BIND')
        self.job, self.owner_live = job, owner_live
        self.last_check = self.clock()

    def _server_request(self, kind, value):
        p.need(self.job is not None and self.close_request is None, 'CONTROL_NOT_OPEN')
        body = value['body']
        p.need(self.owner_live() is None, 'OWNER_GUARD')
        if kind == p.CONTROL_CHECK:
            p.need(p.canonical(body) == p.canonical({'job_sha256': self.job.sha256}), 'CONTROL_JOB')
        elif kind == p.CONTROL_TERMINAL_ACK:
            p.need(self.binding is not None and self.ack_binding is None and type(body) is dict
                   and set(body) == set(self.binding) | {'ack_sha256', 'ack_size'}, 'CONTROL_TERMINAL_STATE')
            p.need(all(body.get(k) == v for k, v in self.binding.items())
                   and p.pin(body['ack_sha256']) and type(body['ack_size']) is int
                   and 0 < body['ack_size'] <= 65536, 'CONTROL_TERMINAL_BINDING')
            # This message acknowledges the source-bound caller's REAL receipt;
            # it is not independent proof of DB durability or native identity.
            p.need(self.owner_live() is None, 'OWNER_GUARD')
            self.ack_binding = p.parse(p.canonical(body))
        elif kind == p.CONTROL_CLOSE:
            p.need(self.ack_binding is not None and p.canonical(body) == p.canonical(self.ack_binding),
                   'CONTROL_CLOSE_BEFORE_ACK')
            with self.condition:
                self.close_request = value
                self.condition.notify_all()
        else:
            p.need(False, 'CONTROL_DIRECTION')
        self.last_check = self.clock()

    def mark_data_complete(self, job, raw, result_raw):
        p.need(job is self.job and self.binding is None, 'CONTROL_DATA_STATE')
        self.finish_data()
        self.binding = dict(job_sha256=job.sha256, stream_sha256=p.digest(raw),
                            result_sha256=p.digest(result_raw))

    def finish_data(self):
        """After the final consumed data record, only control RPCs are legal."""
        with self.condition:
            p.need(not self.data_finished and not self.data, 'CONTROL_TRAILING_DATA')
            self.data_finished = True

    def check_owner(self):
        p.need(self.role == 'client' and self.job is not None, 'CONTROL_CLIENT_STATE')
        self.request(p.CONTROL_CHECK, {'job_sha256': self.job.sha256})

    def start_heartbeat(self, job):
        p.need(self.role == 'client' and self.job is None, 'CONTROL_CLIENT_STATE')
        self.job = job
        def run():
            try:
                while not self.heartbeat_stop.wait(self.heartbeat):
                    self.check_owner()
            except BaseException:
                self.fail()
        try:
            self.heartbeat_thread = threading.Thread(target=run, daemon=True)
            self.heartbeat_thread.start()
        except BaseException:
            self.fail()
            self.stop()
            raise

    def stop_heartbeat(self):
        self.heartbeat_stop.set()
        if self.heartbeat_thread is not None and self.heartbeat_thread.ident is not None:
            self.heartbeat_thread.join(self.timeout + .5)
            p.need(not self.heartbeat_thread.is_alive(), 'CONTROL_CLEANUP_UNKNOWN')

    def wait_close(self):
        p.need(self.role == 'server', 'CONTROL_DIRECTION')
        while True:
            self.io_check()
            p.need(self.clock() - self.last_check <= 5, 'CONTROL_HEARTBEAT_LOST')
            with self.condition:
                if self.close_request is not None:
                    return
                self.condition.wait(.05)

    def deliver_closed(self):
        p.need(self.close_request is not None and self.ack_binding is not None, 'CONTROL_CLOSE_STATE')
        self.expect_eof = True
        self.write(p.CONTROL_CLOSED, p.canonical(self.close_request))

    def wait_eof(self):
        end = min(self.clock() + self.timeout, self.deadline)
        while not self.eof.wait(.02):
            self.check()
            p.need(self.clock() < end, 'CONTROL_CLEANUP_UNKNOWN')
        self.check()

    def stop(self):
        uncertain = False
        try:
            self.stop_heartbeat()
        except BaseException:
            uncertain = True
        self.stop_event.set()
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass
        with self.condition:
            self.condition.notify_all()
        for thread in (self.reader, self.handler):
            if thread is not None and thread.ident is not None and thread is not threading.current_thread():
                try:
                    thread.join(self.timeout + .5)
                    if thread.is_alive():
                        uncertain = True
                except BaseException:
                    uncertain = True
        self.closed = True
        p.need(not uncertain, 'CONTROL_CLEANUP_UNKNOWN')
