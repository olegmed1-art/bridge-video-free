"""Owner policy/lifetime provider for the selected UID996 Unix broker design.

No transport wire implementation, credentials, key signing, queue, publisher or
power operations. Broker/wire owner invokes these semantic operations on its
one live control connection. No alternate opcodes or serialization are defined.
Native RC2 objects remain in their original UID1001 realm; they are never sent
over the broker wire or converted to JSON/fixture objects.
"""
from contextlib import AbstractContextManager
from dataclasses import dataclass
import math
import os
import re
import secrets
import socket
import time
import threading

from .owner_signed_policy import closed, encoded, need, pin, sha, strict_json, utc, verify_policy
from .owner_kernel_peer import KernelPeer
from .private_admission_retention import exact_claim


@dataclass(frozen=True)
class TerminalOwnerCapabilities:
    """Explicit trusted-launcher bindings to EXISTING owner operations.

    Not credentials or authentication. No defaults/loader/UID bridge exists.
    A constructor or a test callable proves no installed authority. Production
    must bind source-pinned owner operations, real atomic cross-process CAS,
    approved external head storage, fresh grant windows and exact durable ACK.
    None of these operations enrolls an Assignment or creates a READ_RESULT grant.
    """
    assert_live: object
    read_head: object
    compare_and_swap: object
    bind_committed: object
    read_window: object
    ack_window: object
    new_ack_id: object
    send_ack: object
    read_ack: object

    def __post_init__(self):
        need(all(callable(getattr(self, name)) for name in self.__dataclass_fields__),
             'OWNER_TERMINAL_CAPABILITIES_REQUIRED')


def bind_terminal_owner_store(*, store, assert_live, bind_committed, read_window,
                              ack_window, new_ack_id, enabled=False):
    """Compose the physical store with ORIGINAL owner operations, opt-in only.

    No issuance, enrollment, connection discovery, UID transition or new wire.
    The trusted UID1001 launcher must still supply the genuine source-bound
    authorization/window/post-COMMIT operations. This function cannot replace
    OwnerScope.issue_committed_assignment's closed runtime gate. Portable ledger
    tests and storage provisioning do not establish these owner capabilities.
    """
    need(type(enabled) is bool and enabled, 'OWNER_TERMINAL_STORE_DISABLED')
    from .owner_terminal_store import TerminalOwnerStore
    need(type(store) is TerminalOwnerStore and store.write_enabled,
         'OWNER_TERMINAL_NATIVE_STORE_REQUIRED')
    need(all(callable(port) for port in (assert_live, bind_committed, read_window,
                                        ack_window, new_ack_id)),
         'OWNER_TERMINAL_ORIGINAL_OPERATIONS_REQUIRED')
    store._check()  # Recheck the actual native principal/private inode binding.
    return TerminalOwnerCapabilities(assert_live=assert_live,
        read_head=store.read_head, compare_and_swap=store.compare_and_swap,
        bind_committed=bind_committed, read_window=read_window,
        ack_window=ack_window, new_ack_id=new_ack_id,
        send_ack=store.accept_ack, read_ack=store.read_ack)


def directory_binding(directory):
    directory.check()
    info = os.fstat(directory.fd)
    # Existing RC2 oracle_entry_gate.directory_identity, exact five fields.
    return sha(encoded([info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode]))


class ExclusiveFileLock:
    """One already approved open lock fd. No path or lock file creation."""
    def __init__(self, fd):
        need(type(fd) is int and fd >= 0, 'OWNER_SERIALIZATION_FD')
        self.fd, self.held = fd, False

    def acquire(self):
        import fcntl
        need(not self.held, 'OWNER_SCOPE_ALREADY_ACTIVE')
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.held = True

    def release(self):
        import fcntl
        if self.held:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.held = False


class ReplayLedger:
    """Create-only bounded replay evidence over existing RC2 Directory IO.

    All methods require the separately held serialization capability. Directory
    create supplies O_EXCL/file+directory fsync/readback. Unknown ACK raises;
    an existing scope/dispatch always refuses even if the prior ACK was unknown.
    It does not confer owner authority or replace native admission retention.
    The root-managed epoch/revocation source must itself be durable/anti-rollback.
    """
    def __init__(self, *, directory, serialization, lock_name=None):
        self.directory, self.serialization, self.lock_name = directory, serialization, lock_name
        need(all(callable(getattr(directory, name, None)) for name in ('check','read','create'))
             and type(directory.fd) is int and directory.fd >= 0, 'OWNER_REPLAY_DIRECTORY')
        need(lock_name is None or type(lock_name) is str and re.fullmatch('[A-Za-z0-9_.-]+', lock_name),
             'OWNER_REPLAY_LOCK_NAME')

    def append(self, name, value, *, bound, guard):
        need(self.serialization.held and 0 < bound <= 4194304, 'OWNER_REPLAY_SERIALIZATION')
        self.directory.check()
        names = os.listdir(self.directory.fd)
        need(len(names) <= 16384, 'OWNER_REPLAY_QUOTA')
        total = 0
        for leaf in names:
            if leaf == self.lock_name:
                continue
            need(re.fullmatch(r'(scope|dispatch)-[0-9a-f]{64}', leaf), 'OWNER_REPLAY_FOREIGN_FILE')
            raw, _ = self.directory.read(leaf, limit=65536)
            total += len(raw)
            need(total <= bound, 'OWNER_REPLAY_QUOTA')
        raw = encoded(value)
        need(0 < len(raw) <= 65536 and total + len(raw) <= bound, 'OWNER_REPLAY_QUOTA')
        # Exact name collision denies replay. No readback-to-success fallback.
        self.directory.create(name, raw, check=guard)
        need(self.directory.read(name, limit=65536)[0] == raw, 'OWNER_REPLAY_ACK_UNKNOWN')


@dataclass(frozen=True)
class NativeAssignment:
    """Original local native owner objects, supplied by its approved callsite.

    This is an integration envelope, never a wire/native-cycle ABI. Hashes bind
    approved source and capabilities, not authentication of arbitrary Python.
    """
    claim: object
    inputs: object
    context_raw: bytes
    namespace: object
    records: object
    anchors: object
    provider_sha256: str
    callsite_sha256: str
    durability_binding_sha256: str
    assignment_pins: object


class NativeOwnerBinding:
    """Direct existing RC2 and ClaimAdmissionResolver binding, no new factory.

    The existing production factory requires UID1001/autopilot-lite-vnic. Do not
    call from UID996 or DynamicUser broker and remove that guard. The approved
    native assignment callable/execution binding is still a runtime prerequisite.
    """
    def __init__(self, *, assignment_provider, parent_api, retention_api):
        need(callable(assignment_provider), 'OWNER_NATIVE_PROVIDER_REQUIRED')
        self.resolve, self.parent, self.retention_api = assignment_provider, parent_api, retention_api

    def bind(self, claim, scope):
        need(os.geteuid() == 1001 and socket.gethostname() == 'autopilot-lite-vnic',
             'RC2_NATIVE_OWNER_REALM_REQUIRED')
        selected = exact_claim(claim)
        scope.check()
        # Keep the original tuple private across the external provider call.
        native = self.resolve(strict_json(encoded(selected)))
        need(type(native) is NativeAssignment, 'OWNER_NATIVE_ASSIGNMENT_REQUIRED')
        policy = scope.policy
        pins = policy['rc2']
        need(exact_claim(native.claim) == selected and set(native.claim) == set(selected)
             and native.provider_sha256 == pins['assignment_provider_sha256']
             and native.callsite_sha256 == pins['callsite_sha256']
             and native.durability_binding_sha256 == pins['durability_binding_sha256']
             and type(native.context_raw) is bytes and sha(native.context_raw) == pins['context_sha256']
             and native.namespace.manifest_sha256 == pins['capsule_sha256']
             and native.assignment_pins == policy['assignment'], 'OWNER_NATIVE_BINDING')
        context = strict_json(native.context_raw)
        need(type(context) is dict and context.get('kind') == 'AUTHENTICATED_ORACLE_ENTRY_OWNER_CONTEXT_V1'
             and type(context.get('deadline_mono')) in (int,float)
             and math.isfinite(context['deadline_mono'])
             and type(context.get('expires_wall_ms')) is int
             and scope.channel.clock() < context['deadline_mono'] <= scope.channel.clock()+300
             and scope.channel.wall()*1000 < context['expires_wall_ms'], 'OWNER_NATIVE_CONTEXT_EXPIRED')
        # Original context schema/full native ABI is still validated by RC2.
        # Only its already defined expiry fields are read here, never renewed.
        original = native.inputs
        inputs = self.parent.AdminInputs(original.request_raw, original.request_sha256,
            original.cycle, strict_json(encoded(dict(original.saved_pins))))
        need(directory_binding(native.records) == pins['records_binding_sha256']
             and directory_binding(native.anchors) == pins['anchors_binding_sha256'], 'OWNER_NATIVE_DIRECTORIES')
        need(inputs.request_sha256 == policy['assignment']['request_sha256'], 'OWNER_NATIVE_REQUEST')
        retention = self.retention_api.AdmissionRetention(records=native.records, anchors=native.anchors,
            context_sha=pins['context_sha256'], owner_guard=scope.assert_authenticated_owner)
        resolver = self.parent.ClaimAdmissionResolver(expected_claim=selected, inputs=inputs,
            context_raw=native.context_raw, accepted_context_sha=pins['context_sha256'],
            owner_factory=native.namespace.owner_factory,
            assert_authenticated_owner=scope.assert_authenticated_owner, retention=retention)
        # Freeze native returned inputs before the next external control guard.
        scope._bind_native(selected, pins['context_sha256'], context['deadline_mono'], context['expires_wall_ms'])
        scope.check()
        return resolver


class OwnerApplicationChannel:
    """Broker-side authority provider; wire v1 belongs to the transport owner.

    installed_material() reads enrolled root-managed policy/signature/signers and
    current durable registry as (raw, signature, allowed, installed_state). It is
    not a caller request. The caller unit lookup is the real read-only systemd
    adapter; both are pinned installation/runtime inputs, never allow defaults.
    accepted_socket is the original accepted control socket, held through terminal
    persistence. Connection liveness/fresh request/reply is supplied by the same
    broker wire dispatcher; peer checks alone do not detect/control EOF.
    """
    def __init__(self, *, accepted_socket, installed_material, verifier, unit_by_pid,
                 ledger, control_connection_check, native_binding,
                 clock=time.monotonic, wall=time.time):
        need(all(callable(port) for port in (installed_material,unit_by_pid,control_connection_check)),
             'OWNER_RUNTIME_PORTS_REQUIRED')
        self.socket, self.material, self.verifier = accepted_socket, installed_material, verifier
        self.lookup, self.ledger, self.control_check = unit_by_pid, ledger, control_connection_check
        self.native_binding, self.clock, self.wall = native_binding, clock, wall
        self.active = None

    def verify_policy(self, raw, signature, allowed, *, installed, now):
        return verify_policy(raw, signature, allowed, verifier=self.verifier,
                             installed=installed, now=now)

    def application_scope(self):
        need(self.active is None, 'OWNER_SCOPE_ALREADY_ACTIVE')
        return OwnerScope(self)


class OwnerScope(AbstractContextManager):
    def __init__(self, channel):
        self.channel, self.state = channel, 'NEW'
        self._claim_raw, self.context_sha, self.peer = None, None, None
        self.resolver, self.dispatched = None, False
        self._lock = threading.RLock()
        self._owns_lock = False
        self._checking = False
        self._native_expires_wall_ms = None
        self._admission_receipt_raw = None

    @property
    def policy(self):
        from .owner_signed_policy import strict_json
        return strict_json(self._policy)

    @property
    def claim(self):
        from .owner_signed_policy import strict_json
        return None if self._claim_raw is None else strict_json(self._claim_raw)

    def __enter__(self):
        c = self.channel
        need(self.state == 'NEW' and c.active is None, 'OWNER_SCOPE_REUSED')
        c.ledger.serialization.acquire()
        self._owns_lock = True
        c.active = self
        try:
            raw, signature, allowed, installed = c.material()
            self._policy = encoded(c.verify_policy(raw, signature, allowed,
                                        installed=installed, now=c.wall()))
            self.policy_raw, self.policy_sha = raw, sha(raw)
            self._signature, self._allowed = signature, allowed
            need(directory_binding(c.ledger.directory) == self.policy['broker']['replay_binding_sha256'],
                 'OWNER_REPLAY_BINDING')
            self.peer = KernelPeer(c.socket, caller=self.policy['caller'], unit_by_pid=c.lookup)
            self.nonce = secrets.token_hex(32)
            start, wall = c.clock(), c.wall()
            need(math.isfinite(start) and math.isfinite(wall), 'OWNER_CLOCK_INVALID')
            self.deadline = min(start + self.policy['limits']['scope_seconds'],
                                start + utc(self.policy['not_after_UTC']) - wall)
            self.last_clock = self.last_heartbeat = start
            self.state = 'OPEN'
            self.check()
            c.ledger.append('scope-' + sha(self.nonce.encode()),
                dict(policy_sha256=self.policy_sha, nonce_sha256=sha(self.nonce.encode())),
                bound=self.policy['limits']['replay_bytes'], guard=self.check)
            self.check()
            return self
        except BaseException:
            self._close()
            raise

    def _time(self):
        c = self.channel
        now, wall = c.clock(), c.wall()
        need(math.isfinite(now) and math.isfinite(wall) and now >= self.last_clock
             and now < self.deadline and now - self.last_heartbeat <= self.policy['limits']['heartbeat_seconds']
             and utc(self.policy['not_before_UTC']) <= wall < utc(self.policy['not_after_UTC'])
             and (self._native_expires_wall_ms is None or wall*1000 < self._native_expires_wall_ms),
             'OWNER_SCOPE_EXPIRED')
        self.last_clock = now
        return now

    def check(self):
        with self._lock:
            if self._checking:
                self.state = 'FAILED'
                need(False, 'OWNER_GUARD_REENTRANT')
            self._checking = True
            try:
                return self._check()
            except BaseException:
                self.state = 'FAILED'  # Revoke/loss/expiry cannot resurrect a scope.
                raise
            finally:
                self._checking = False

    def _check(self):
        need(self.state in ('OPEN','BOUND') and self.channel.active is self, 'OWNER_SCOPE_NOT_LIVE')
        c = self.channel
        self._time()
        raw, signature, allowed, installed = c.material()
        # Current root registry and enrolled signer revocation are checked fresh;
        # signature/policy bytes stay exact for this one scope, never hot-updated.
        need(raw == self.policy_raw and signature == self._signature and allowed == self._allowed
             and sha(raw) == installed['policy_sha256']
             and installed['epoch'] == self.policy['epoch'] and type(installed['epoch']) is int
             and installed['policy_revoked'] is False and installed['signer_revoked'] is False
             and installed['signer_fingerprint'] == self.policy['owner_signer_fingerprint'], 'OWNER_SCOPE_REVOKED')
        from .owner_signed_policy import signer_fingerprint
        need(signer_fingerprint(allowed) == installed['signer_fingerprint'], 'OWNER_SCOPE_SIGNER_CHANGED')
        self.peer.check()
        started = c.clock()
        # Existing transport owner must implement a real challenge on this same
        # live socket, bound to nonce/policy/claim/context; no cached boolean.
        need(c.control_check(c.socket, self) is None, 'OWNER_CONTROL_REFUSED')
        finished = self._time()
        need(finished - started <= self.policy['limits']['challenge_seconds'], 'OWNER_CONTROL_TIMEOUT')
        need(self.state in ('OPEN','BOUND') and c.active is self and self._owns_lock,
             'OWNER_SCOPE_NOT_LIVE')
        self.last_heartbeat = finished

    def _bind_native(self, claim, context_sha, native_deadline, native_expires_wall_ms):
        need(self.state == 'OPEN' and pin(context_sha)
             and context_sha == self.policy['rc2']['context_sha256'], 'OWNER_SCOPE_BIND_ONCE')
        selected = exact_claim(claim)
        self.check()
        self._claim_raw, self.context_sha, self.state = encoded(selected), context_sha, 'BOUND'
        self.deadline = min(self.deadline, native_deadline)
        self._native_expires_wall_ms = native_expires_wall_ms
        self.check()

    def assert_authenticated_owner(self, context_sha):
        need(self.state == 'BOUND' and context_sha == self.context_sha, 'OWNER_CONTEXT_NOT_BOUND')
        self.check()

    def issue_committed_assignment(self, claim_raw, commit_evidence_raw):
        """Opt-in concrete SQL64 provider; every installed/native gate remains.

        No general callback, boolean activation, JSON uid or admin LOGIN escape.
        The original wire still rejects an unenrolled result/dispatch ABI.
        """
        try:
            from .native_video_binding_provider import OriginalNativeVideoProvider
            resolve = getattr(getattr(self.channel,'native_binding',None),'resolve',None)
            provider = getattr(resolve, '__self__', None)
            need(type(self.channel) is ProductionOwnerApplicationChannel
                 and type(provider) is OriginalNativeVideoProvider
                 and getattr(resolve,'__func__',None) is OriginalNativeVideoProvider.resolve,
                 'OWNER_UID1001_ISSUER_CAPABILITY_AND_BINDING_UNPROVEN')
            self.check()
            return provider.issue_committed_assignment(self,claim_raw,commit_evidence_raw)
        except BaseException:
            self.state = 'FAILED'
            raise

    def assert_authorized(self, claim):
        need(self.state == 'BOUND' and exact_claim(claim) == self.claim, 'OWNER_CLAIM_NOT_BOUND')
        self.assert_authenticated_owner(self.context_sha)

    def admission_inputs(self, claim):
        if self.resolver is None:
            try:
                need(self.state == 'OPEN', 'OWNER_ASSIGNMENT_ALREADY_BOUND')
                self.resolver = self.channel.native_binding.bind(claim, self)
            except BaseException:
                self.state = 'FAILED'
                raise
        return self.resolver.admission_inputs(claim)

    def open_rc2_owner(self, claim):
        need(self.resolver is not None, 'OWNER_ASSIGNMENT_REQUIRED')
        return self.resolver.open_rc2_owner(claim)

    def record_admission(self, claim, verified, readback):
        try:
            need(self.resolver is not None and self._admission_receipt_raw is None,
                 'OWNER_ADMISSION_ALREADY_RECORDED_OR_UNBOUND')
            self.assert_authorized(claim)
            self.resolver.record_admission(claim, verified, readback)
            raw = encoded(self.resolver.retention.last_receipt)
            receipt = strict_json(raw)
            closed(receipt, 'state durability_confirmed record_sha256 claim_sha256 anchor_sha256 request_sha256')
            need(receipt['state'] == 'COMMITTED' and receipt['durability_confirmed'] is True
                 and receipt['claim_sha256'] == sha(encoded(self.claim))
                 and receipt['request_sha256'] == self.policy['assignment']['request_sha256']
                 and all(pin(receipt[key]) for key in ('record_sha256','claim_sha256','anchor_sha256','request_sha256')),
                 'OWNER_ADMISSION_NOT_DURABLE')
            self.check()
            self._admission_receipt_raw = raw
        except BaseException:
            self.state = 'FAILED'
            raise

    def reserve_dispatch(self, claim):
        self.assert_authorized(claim)
        need(self._admission_receipt_raw is not None, 'OWNER_NATIVE_ADMISSION_REQUIRED')
        need(not self.dispatched, 'OWNER_DISPATCH_ALREADY_RESERVED')
        receipt = strict_json(self._admission_receipt_raw)
        assignment = self.policy['assignment']
        key = sha(encoded([assignment[k] for k in
            ('queue_namespace','source_sha256','generation_sha256','manifest_sha256')]))
        try:
            self.channel.ledger.append('dispatch-' + key,
                dict(policy_sha256=self.policy_sha, claim_sha256=sha(encoded(self.claim)),
                     assignment_sha256=sha(encoded(assignment)),
                     admission_record_sha256=receipt['record_sha256'],
                     admission_anchor_sha256=receipt['anchor_sha256'], state='DISPATCH_RESERVED_OUTCOME_UNKNOWN'),
                bound=self.policy['limits']['replay_bytes'], guard=self.check)
        except BaseException:
            self.state = 'FAILED'
            raise
        self.dispatched = True
        self.check()  # Only then may the existing transport owner dispatch.

    def _close(self):
        self.state = 'CLOSED'
        try:
            if self.peer is not None:
                self.peer.close()
        finally:
            if self._owns_lock:
                self._owns_lock = False
                if self.channel.active is self:
                    self.channel.active = None
                self.channel.ledger.serialization.release()

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.check()  # Outer scope includes terminal durability barrier.
        finally:
            self._close()
        return False


class ProductionOwnerApplicationChannel(OwnerApplicationChannel):
    """Only policy selection changes. Native issuer/authority gates stay intact."""
    def verify_policy(self, raw, signature, allowed, *, installed, now):
        from .owner_signed_policy import verify_production_policy
        need(hasattr(os, 'geteuid') and os.geteuid() == 1001
             and os.getegid() == 1001 and socket.gethostname() == 'autopilot-lite-vnic',
             'OWNER_PRODUCTION_UID1001_REALM_REQUIRED')
        return verify_production_policy(raw, signature, allowed, verifier=self.verifier,
                                        installed=installed, now=now)
