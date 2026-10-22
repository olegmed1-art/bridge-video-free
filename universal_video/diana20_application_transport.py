"""ParentPorts binding for the new transport, with EXTERNAL RC2 owner provider.

No owner-channel implementation, credential generator, alternate pipeline,
runtime/publisher or activation is defined. Constructors are trusted-launcher
inputs; untrusted wire cannot inject these Python callables.
"""
from __future__ import annotations

from contextlib import contextmanager
import socket
import os
import stat
import threading
import time

from . import diana20_transport_protocol as p
from .diana20_transport_service import receive_output, seal
from .diana20_shared_control import SharedControl

SOCKET = '/run/bridge-diana20/transport.sock'


class ConnectedUnix:
    """Adapter over an already connected socket. No socket creation here."""
    def __init__(self, connected, guard, assert_server):
        self.socket = connected
        self.closed = False
        self.peer = None
        self.assert_server = assert_server
        p.need(callable(assert_server), 'BROKER_IDENTITY_REQUIRED')
        p.need(assert_server(connected) is None, 'BROKER_IDENTITY')
        self.records = p.Records(p.FdBytes(connected.fileno(), connected.fileno(), guard), guard)

    def finish_writes(self):
        self.socket.shutdown(socket.SHUT_WR)

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                if self.peer is not None:
                    self.peer.close()
            finally:
                self.socket.close()


def connect_unix(guard, *, assert_server, socket_factory=None, peer_factory=None):
    """Future approved client connection, NOT invoked during local tests/import.

    Only the fixed root-owned socket. No caller path, host, key or SSH options.
    Optional factories are explicit test/trusted-launcher ports, never wire data.
    Kernel peer binding is transport identity, not the RC2 owner's authority.
    """
    import pwd
    p.need(hasattr(socket, 'AF_UNIX') and hasattr(os, 'geteuid') and
           os.geteuid() == 996 and os.getegid() == 988, 'APPLICATION_IDENTITY')
    parent = os.lstat('/run/bridge-diana20')
    node = os.lstat(SOCKET)
    p.need(stat.S_ISDIR(parent.st_mode) and
           (parent.st_uid, parent.st_gid, stat.S_IMODE(parent.st_mode)) == (0, 988, 0o750), 'SOCKET_PARENT')
    p.need(stat.S_ISSOCK(node.st_mode) and
           (node.st_uid, node.st_gid, stat.S_IMODE(node.st_mode)) == (0, 988, 0o660), 'SOCKET_NODE')
    connected = peer = None
    try:
        guard()
        connected = (socket.socket if socket_factory is None else socket_factory)(socket.AF_UNIX, socket.SOCK_STREAM)
        connected.settimeout(8)
        connected.connect(SOCKET)
        after = os.lstat(SOCKET)
        p.need((node.st_dev, node.st_ino, node.st_mode, node.st_uid, node.st_gid) ==
               (after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid), 'SOCKET_CHANGED')
        principal = pwd.getpwnam('bridge-diana20-broker')  # systemd DynamicUser NSS
        peer = (p.LinuxPeer if peer_factory is None else peer_factory)(connected,
            uid=principal.pw_uid, gid=principal.pw_gid, unit='bridge-diana20-broker.service')
        def live():
            guard()
            peer.check()
        channel = ConnectedUnix(connected, live, assert_server)
        channel.peer = peer
        return channel
    except BaseException:
        try:
            if peer is not None:
                peer.close()
        finally:
            if connected is not None:
                connected.close()
        raise


class ApplicationTransport:
    """One exact owner-assigned job, whole saved-frame stream, unchanged Mapping.

    job/ticket/frame_reader/connect/owner_assert are supplied by authenticated
    RC2 owner launcher. owner_assert(claim) MUST make a fresh real channel check
    whose application_scope spans through the parent's terminal persistence.
    A Job/ticket value or successful kernel UID check is not that authority.
    connect(guard) returns ConnectedUnix; creation/connect happens in the separate
    approved launcher, not on import or in these local qualification tests.
    """
    def __init__(self, *, job, ticket, frame_reader, connect, owner_assert,
                 challenge_validator=None, control_factory=SharedControl):
        p.need(type(job) is p.Job and type(ticket) is bytes and 0 < len(ticket) <= p.MAX_TICKET,
               'APPLICATION_ASSIGNMENT')
        p.need(all(callable(f) for f in (frame_reader, connect, owner_assert)), 'OWNER_PORT_REQUIRED')
        self.job, self.ticket = job, ticket
        self.frame_reader, self.connect, self.owner_assert = frame_reader, connect, owner_assert
        self.challenge_validator, self.control_factory = challenge_validator, control_factory
        self.lock = threading.Lock()
        self.application_lock = threading.Lock()
        self.used = self.active = self.called = self.completed = False
        self.channel = None
        self.sealed_stream = None
        self.control = None
        self.application_active = self.application_used = self.scope_completed = False
        self.terminal_ack_attempted = self.terminal_acknowledged = False
        self.terminal_ack_lock = threading.Lock()
        self.terminal_packet = self.result_raw = None

    def _guard(self, claim):
        p.need(p.canonical({k: claim[k] for k in p.CLAIM}) == self.job.claim_raw, 'CLAIM_MISMATCH')
        p.need(self.owner_assert(claim) is None, 'OWNER_GUARD')
        p.need(self.application_active and self.control is not None, 'APPLICATION_CONTROL_REQUIRED')
        self.control.check_owner()

    @contextmanager
    def application_scope(self):
        """One connection before owner entry, until actual persistence ACK/exit.

        Normal queue return alone is NOT an ACK. The actual parent uses
        TerminalAckQueue with a trusted post-finish terminal readback port.
        """
        p.need(self.application_lock.acquire(blocking=False), 'APPLICATION_BUSY')
        try:
            p.need(not self.application_used and callable(self.challenge_validator),
                   'APPLICATION_CONTROL_REQUIRED')
            self.application_used = True
            end = time.monotonic() + 3600
            def io_live():
                p.need(time.monotonic() < end, 'CONTROL_EXPIRED')
            self.channel = self.connect(io_live)
            self.control = self.control_factory(self.channel.records,
                connection=self.channel.socket, role='client', assert_io=self.channel.records.guard,
                challenge_validator=self.challenge_validator).start()
            self.control.records.write(p.OPEN, bytes.fromhex(self.job.sha256) + self.ticket)
            ready = self.control.records.read(p.CONTROL_READY, timeout=8)
            p.need(ready == p.canonical({'version': 2, 'job_sha256': self.job.sha256}), 'CONTROL_READY')
            self.control.start_heartbeat(self.job)
            self.application_active = True
            yield
            p.need(self.completed and self.terminal_acknowledged, 'TERMINAL_ACK_REQUIRED_NO_RETRY')
            self.control.stop_heartbeat()
            self.control.expect_eof = True
            # Remain writable during CLOSE: owner __exit__ still challenges us.
            self.control.request(p.CONTROL_CLOSE, self.terminal_packet)
            self.channel.finish_writes()
            self.control.wait_eof()
        except BaseException:
            if self.control is not None:
                self.control.fail()
            raise
        finally:
            self.application_active = False
            try:
                try:
                    if self.control is not None:
                        self.control.stop()
                finally:
                    if self.channel is not None:
                        self.channel.close()
            finally:
                self.application_lock.release()
        self.scope_completed = True  # only after all local cleanup succeeded

    @contextmanager
    def video_scope(self, claim):
        p.need(self.lock.acquire(blocking=False), 'BUSY')
        try:
            p.need(not self.used and self.application_active and self.control is not None,
                   'APPLICATION_CONTROL_REQUIRED')
            self.used = True
            self._guard(claim)
            self.guard = p.Guard(lambda: self._guard(claim))
            self.control.records.guard = self.guard
            self.active = True
            yield
            p.need(self.called and self.completed, 'PROCESSOR_INCOMPLETE')
            self.guard()
        except BaseException:
            if self.control is not None:
                self.control.fail()
            raise
        finally:
            self.active = False
            self.lock.release()

    def video_processor(self, claim):
        p.need(self.active and not self.called, 'PROCESSOR_STATE')
        self.called = True
        self._guard(claim)
        records = self.control.records
        for frame in self.job.frames:
            self.guard()
            raw = self.frame_reader(frame)  # exact bytes only; no caller paths on wire
            records.write(p.FRAME, p.frame_bytes(frame, raw))
            records.read(p.ACK)
        records.write(p.RUN)
        raw, result_raw = receive_output(records, self.job)
        records.write(p.ACK)
        p.need(records.read(p.DONE) == p.canonical({'job_sha256': self.job.sha256}), 'DONE')
        self.control.finish_data()
        self.guard()
        self.sealed_stream = raw  # never manufacture an owner-local ObservationReceipt
        self.result_raw = result_raw
        self.completed = True
        return p.parse(result_raw)  # preserve original terminal Mapping fields

    def terminal_persistence_ack(self, claim, acknowledgement):
        """Called ONLY after original native durable ACK + trusted readback.

        Wire sends receipt hash/size, never opaque NativeOwnerBinding objects.
        This protocol acknowledgment is not independent proof of DB durability.
        An exception/lost response is sticky UNKNOWN; never send/retry twice.
        """
        acquired = self.terminal_ack_lock.acquire(blocking=False)
        try:
            p.need(acquired and not self.terminal_ack_attempted, 'TERMINAL_ACK_REPLAY_NO_RETRY')
            self.terminal_ack_attempted = True
            self._guard(claim)
            p.need(self.completed and type(acknowledgement) is bytes
                   and 0 < len(acknowledgement) <= 65536, 'TERMINAL_ACK_READBACK_REQUIRED')
            packet = dict(job_sha256=self.job.sha256, stream_sha256=p.digest(self.sealed_stream),
                result_sha256=p.digest(self.result_raw), ack_sha256=p.digest(acknowledgement),
                ack_size=len(acknowledgement))
            self.control.request(p.CONTROL_TERMINAL_ACK, packet)
            self._guard(claim)
            self.terminal_packet = packet
            self.terminal_acknowledged = True
        except BaseException:
            if self.control is not None:
                self.control.fail()
            raise
        finally:
            if acquired:
                self.terminal_ack_lock.release()


class _PostTerminalAckJournal:
    """Adapter ONLY for an explicitly post-terminal writer, never claim_journal.

    ClaimReceiptJournal.acknowledge_commit precedes compute in the real queue.
    Use diana20_terminal_queue.TerminalAckQueue for the actual parent instead.

    receipt_reader(claim)->bytes is the owner's actual durable readback port;
    it is mandatory and never replaced by None, a JSON flag or queue return.
    """
    def __init__(self, journal, transport, receipt_reader):
        p.need(type(transport) is ApplicationTransport and callable(receipt_reader)
               and all(callable(getattr(journal, name, None)) for name in
                   ('preflight', 'reserve', 'publish_precommit', 'acknowledge_commit')),
               'TERMINAL_JOURNAL_PORTS')
        self.journal, self.transport, self.receipt_reader = journal, transport, receipt_reader
        self.attempted = False
        self.lock = threading.Lock()

    def preflight(self):
        return self.journal.preflight()

    def reserve(self, *args, **kwargs):
        return self.journal.reserve(*args, **kwargs)

    def publish_precommit(self, *args, **kwargs):
        return self.journal.publish_precommit(*args, **kwargs)

    def acknowledge_commit(self, claim):
        from .owner_effects import OwnerEffectRefused
        acquired = self.lock.acquire(blocking=False)
        try:
            p.need(acquired and not self.attempted, 'TERMINAL_ACK_REPLAY_NO_RETRY')
            self.attempted = True
            p.need(self.transport.completed, 'TERMINAL_JOURNAL_REQUIRES_POSTCOMPUTE')
            self.transport._guard(claim)
            result = self.journal.acknowledge_commit(claim)  # ORIGINAL writer/ACK
            readback = self.receipt_reader(claim)  # ORIGINAL real durable receipt
            self.transport.terminal_persistence_ack(claim, readback)
            return result
        except Exception:
            if self.transport.control is not None:
                self.transport.control.fail()
            raise OwnerEffectRefused('OWNER_TERMINAL_CONTROL_ACK_UNKNOWN_NO_RETRY') from None
        finally:
            if acquired:
                self.lock.release()


def bind_parent_ports(parent_api, *, transport, application_scope, open_rc2_owner,
                      admission_inputs, record_admission):
    """Reuse original f1e2948a ParentPorts and its unchanged admission pipeline."""
    p.need(type(transport) is ApplicationTransport and all(callable(fn) for fn in
        (application_scope, open_rc2_owner, admission_inputs, record_admission)), 'PARENT_PORTS')
    @contextmanager
    def application_with_effects():
        from .owner_effects import owner_effect_scope
        # The sole dispatcher must already be alive when genuine owner entry
        # issues its first challenge, and survive its final __exit__ challenge.
        with transport.application_scope():
            with application_scope():
                with owner_effect_scope(transport.job.claim, transport._guard):
                    yield
    return parent_api.ParentPorts(application_scope=application_with_effects,
        open_rc2_owner=open_rc2_owner, admission_inputs=admission_inputs,
        record_admission=record_admission, video_scope=transport.video_scope,
        video_processor=transport.video_processor)
