"""Model A implementation over original SQL/native/retention objects.

No connections, credentials, roles, services, listeners or compute are created.
Absent genuine installed inputs this is inactive. DB doubles prove protocol
ordering only, not SQL authorization, native authority or production readiness.
The original production wire still refuses an unenrolled JOB_READY/result ABI.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
from uuid import UUID
import os
import socket
import threading

from . import diana20_transport_protocol as p
from .receipt_encoding import canonical_bytes, strict_loads, sha256
from .native_video_source_intake import RetainedVideoSource
from .owner_assignment_enrollment import EnrollmentTarget, LOCAL_SETTINGS, SQL_IDENTITY, _one
from .terminal_finish_contract import Assignment, VIDEO_KEYS

OWNER = 'video_native_owner_login'
WORKER = 'autopilot_light_worker_login'
WORKER_KEY = 'db-login:' + WORKER
INTAKE = '5edc60affb18ddae38ec7c55fbedf1d6bb4cb7ea71293cf270698d9270b847c5'
# Saved target pin, not a fresh branch-binding assertion. Deployment must
# reconcile this exact endpoint against the control-plane receipt first.
HOST = 'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech'
SQL = {
 'intake': 'SELECT autopilot.register_native_video_intake_v1(%s,%s) AS result',
 'create': 'SELECT autopilot.create_native_video_task_v1(%s,%s::uuid) AS result',
 'task': 'SELECT autopilot.claim_native_video_task_v1(%s) AS result',
 'heartbeat': 'SELECT autopilot.heartbeat_native_video_task_v1(%s::uuid,%s,%s) AS result',
 'video': 'SELECT video_queue.claim_native_video_job_v1(%s::uuid,%s::uuid,%s::uuid,%s,%s) AS result',
 'bind': 'SELECT autopilot.bind_native_video_claim_v1(%s::uuid,%s,%s,%s) AS result',
 'inspect': 'SELECT autopilot.inspect_native_video_claim_v1(%s) AS result',
 'enroll': 'SELECT video_queue.enroll_native_video_assignment_v1(%s,%s) AS result',
}


class NativeBindingRefused(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise NativeBindingRefused(code) from None


def uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (TypeError, ValueError, AttributeError):
        return False


def bounded_expiry(database_expiry,scope):
    """Data calculation; never expands original policy/native/monotonic lifetime."""
    from .owner_signed_policy import utc
    db=datetime.strptime(database_expiry,'%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc).timestamp()
    wall,mono=scope.channel.wall(),scope.channel.clock()
    bounds=(db,scope._native_expires_wall_ms/1000,utc(scope.policy['not_after_UTC']),
            wall+scope.deadline-mono)
    need(all(type(x) in (int,float) and math.isfinite(x) for x in bounds)
         and min(bounds)>wall+1, 'NATIVE_ISSUANCE_AUTHORITY_EXPIRED')
    # Floor to ms and reserve one second; never round beyond any original cap.
    result=(math.floor(min(bounds)*1000)-1000)/1000
    return datetime.fromtimestamp(result,timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def rpc(cursor, name, params):
    """Closed SQL names, parameterized values, detached canonical result data."""
    row = _one(cursor, SQL[name], params, {'result'})
    need(type(row['result']) is dict, 'NATIVE_RPC_RESULT')
    return strict_loads(canonical_bytes(row['result'], max_bytes=65536), max_bytes=65536)


class NativeVideoSession:
    """An already authenticated connection with exact target/session assertions.

    A target descriptor is not authority. The server independently checks actual
    session_user and role attributes on all RPCs. Never supplies an admin LOGIN.
    A lost reply/COMMIT is sticky UNKNOWN, not an automatic reconnect/retry.
    """
    def __init__(self, *, connection, target, worker=False):
        need(type(target) is EnrollmentTarget and target.database == 'neondb'
             and target.owner_login == OWNER and target.worker_login == WORKER
             and target.host == HOST and target.port == 5432 and type(worker) is bool,
             'NATIVE_EXACT_DIRECT_TARGET_REQUIRED')
        self.connection, self.target, self.worker = connection, target, worker
        self._binding = (connection, target, worker)
        self.failed = False
        self.used = set()
        self.lock = threading.Lock()

    def check(self):
        c, t, w = self._binding
        need(self.connection is c and self.target is t and self.worker is w
             and not self.failed and c.info.host == t.host and c.info.port == t.port
             and c.info.transaction_status.name == 'IDLE', 'NATIVE_IDLE_ORIGINAL_CONNECTION')

    def identity(self, cursor):
        for sql in LOCAL_SETTINGS:
            cursor.execute(sql)
        r = _one(cursor, SQL_IDENTITY, None,
                 {'database_name', 'session_login', 'effective_login', 'backend_pid'})
        need(r['database_name'] == self.target.database
             and r['session_login'] == r['effective_login'] == (WORKER if self.worker else OWNER)
             and type(r['backend_pid']) is int and r['backend_pid'] > 0,
             'NATIVE_AUTHENTICATED_SESSION_REQUIRED')

    def call(self, name, params, *, precommit=None):
        acquired = self.lock.acquire(blocking=False)
        try:
            # Heartbeats are renewable before BIND; only ACKNOWLEDGED calls may
            # repeat. Any lost response/COMMIT still poisons this whole session.
            need(acquired and name in SQL and (name in ('inspect', 'heartbeat') or name not in self.used),
                 'NATIVE_CONSUMED_OR_BUSY_NO_REPLAY')
            need((self.worker and name in ('task','video','heartbeat'))
                 or (not self.worker and name in ('intake','create','bind','inspect','enroll')),
                 'NATIVE_CLIENT_ROLE')
            self.check()
            self.used.add(name)  # BEFORE a possible SQL effect or unknown reply.
            c, _, _ = self._binding
            with c.transaction():
                with c.cursor() as cursor:
                    self.identity(cursor)
                    result = rpc(cursor, name, params)
                    if precommit is not None:
                        precommit(result)  # Internal real journal publisher, never auth.
            self.check()  # Outermost COMMIT acknowledged, connection still original.
            return result
        except BaseException:
            self.failed = True
            raise NativeBindingRefused('NATIVE_SQL_UNKNOWN_NO_RETRY') from None
        finally:
            if acquired:
                self.lock.release()

    def accept_source_and_create(self, source, batch_id):
        need(type(source) is RetainedVideoSource and source.sha256 == INTAKE and uuid(batch_id),
             'NATIVE_DIANA20_SOURCE_REQUIRED')
        self.call('intake', (source.raw.decode('utf8'), source.sha256))
        return self.call('create', (source.sha256, batch_id))

    def terminal_completion(self, task_id, *, lease_epoch=1, enabled=False):
        """Preclaim owner port for an already created task; no SQL effect here.

        No Assignment or video lease exists yet. The terminal RPC independently
        checks the later committed binding/enrollment/receipt. Supplying a UUID
        cannot confer authority, and this factory never discovers credentials.
        """
        from .native_video_terminal import NativeVideoTerminalCompletion
        self.check()
        need(not self.worker, 'NATIVE_COMPLETION_OWNER_SESSION_REQUIRED')
        return NativeVideoTerminalCompletion(session=self, task_id=task_id,
            lease_epoch=lease_epoch, enabled=enabled)


@dataclass(frozen=True)
class CommittedNativeClaim:
    """Detached DB/ACK data. A successful constructor is NOT owner authority."""
    task_raw: bytes
    claim_raw: bytes
    commit_evidence_raw: bytes


class NativeVideoWorkerClaim:
    """Replacement claim phase for the EXISTING private one-job worker.

    Uses the original ClaimReceiptJournal. Returns its original full claim for
    existing processor/finish/terminal recovery; never runs another poller or
    claims the job again through the legacy helper. Launcher wiring is explicit.
    """
    def __init__(self, *, session, journal, lease_seconds=120):
        from .private_one_job_operator import ClaimReceiptJournal
        need(type(session) is NativeVideoSession and session.worker
             and type(journal) is ClaimReceiptJournal
             and type(lease_seconds) is int and 60 <= lease_seconds <= 300,
             'NATIVE_WORKER_ORIGINAL_JOURNAL_REQUIRED')
        self.session, self.journal, self.seconds = session, journal, lease_seconds
        self._binding = (session, journal, lease_seconds, canonical_bytes(journal.binding))
        self.used = False
        self.issue_lock = threading.Lock()

    def claim_one(self):
        session, journal, seconds, binding_raw = self._binding
        need(self.issue_lock.acquire(blocking=False),'NATIVE_WORKER_BUSY')
        try:
            need(not self.used and self.session is session and self.journal is journal
                 and self.seconds == seconds and canonical_bytes(journal.binding) == binding_raw,
                 'NATIVE_WORKER_REPLAY_OR_BINDING_CHANGED')
            self.used = True
        finally:self.issue_lock.release()
        journal.reserve()  # Durable create-only fence BEFORE either SQL claim.
        t = session.call('task', (seconds,))  # Actual task claim COMMIT ACK.
        b = journal.binding
        need(uuid(t.get('task_id')) and t.get('lease_epoch') == 1 and type(t.get('lease_epoch')) is int
             and t.get('worker_id') == WORKER_KEY and t.get('intake_sha256') == INTAKE
             and t.get('job_id') == b['expected_job_id'] and t.get('batch_id') == b['expected_batch_id'],
             'NATIVE_AUTHENTICATED_TASK_CHANGED')
        def publish(c):
            need(c.get('job_id') == t['job_id'] and c.get('batch_id') == t['batch_id']
                 and c.get('attempt_count') == 1, 'NATIVE_VIDEO_CLAIM_CHANGED')
            journal.publish_precommit(c, WORKER_KEY, b['admission_id'], b['source_fingerprint'])
        c = session.call('video', (t['task_id'], t['batch_id'], t['job_id'], t['lease_epoch'], seconds),
                         precommit=publish)
        journal.acknowledge_commit(c)  # Only AFTER actual outer video COMMIT ACK.
        return CommittedNativeClaim(canonical_bytes(t), canonical_bytes(c), journal.committed_evidence(c))


class OriginalNativeVideoProvider:
    """Concrete provider at NativeOwnerBinding, not a fixture/native-cycle factory.

    Genuine in-process NativeAssignment objects must come from the original RC2
    owner callsite. This class does not manufacture opaque cycles or read worker
    private files. SQL session + original OwnerScope + RC2 verify/read/retention
    remain distinct. No untrusted callback or payload supplies their authority.
    """
    def __init__(self, *, session, source, task_id, native, store, intent):
        from .owner_application_channel import NativeAssignment
        from .owner_terminal_store import TerminalOwnerStore
        need(type(session) is NativeVideoSession and not session.worker
             and type(source) is RetainedVideoSource and source.sha256 == INTAKE
             and uuid(task_id) and type(native) is NativeAssignment
             and type(store) is TerminalOwnerStore and type(intent) is p.Intent,
             'NATIVE_ORIGINAL_OWNER_INPUTS_REQUIRED')
        self.session, self.source, self.task_id = session, source, task_id
        self.native, self.store, self.intent = native, store, intent
        self._binding = (session, source, task_id, native, store, intent)
        self.used = False
        self.issue_lock = threading.Lock()

    def _check(self, scope=None):
        from .owner_application_channel import OwnerScope, ProductionOwnerApplicationChannel
        need((self.session, self.source, self.task_id, self.native, self.store, self.intent) == self._binding
             and all(x is y for x,y in zip((self.session,self.source,self.native,self.store,self.intent),
                 (self._binding[0],self._binding[1],self._binding[3],self._binding[4],self._binding[5]))),
             'NATIVE_OWNER_BINDING_CHANGED')
        need(hasattr(os,'geteuid') and os.geteuid() == 1001 and os.getegid() == 1001
             and socket.gethostname() == 'autopilot-lite-vnic', 'NATIVE_ORIGINAL_UID1001_REALM')
        self.store._check()
        if scope is not None:
            need(type(scope) is OwnerScope and type(scope.channel) is ProductionOwnerApplicationChannel
                 and getattr(scope.channel.native_binding.resolve,'__self__',None) is self,
                 'NATIVE_ORIGINAL_PRODUCTION_SCOPE')
            scope.check()  # Actual signed registry, peer/unit, same live channel challenge.

    def _snapshot(self):
        self._check()
        s = self.session.call('inspect', ('task:' + self.task_id,))
        need(s.get('task_id') == self.task_id and s.get('intake_sha256') == INTAKE
             and s.get('worker_id') == WORKER_KEY and s.get('lease_epoch') == 1,
             'NATIVE_SQL_TASK_PROOF_CHANGED')
        return s

    def resolve(self, selected):
        from .private_admission_retention import exact_claim
        self._check()
        s = self._snapshot()
        need(exact_claim(s['claim']) == exact_claim(selected) == exact_claim(self.native.claim)
             and self.native.provider_sha256 == sha256(Path(__file__).read_bytes()),
             'NATIVE_ORIGINAL_PROVIDER_SOURCE_OR_CLAIM')
        return self.native  # Original opaque objects; NativeOwnerBinding verifies all original pins/fds.

    def execution_budget(self, scope, claim_raw, *, required_seconds, cleanup_seconds=5):
        """Original owner supplies timing data AFTER real committed enrollment.

        The required duration is an explicit bounded processor contract. Unknown
        duration or missing installed authority stays closed; this creates none.
        """
        from .native_video_lease import NativeExecutionBudget
        self._check(scope)
        selected = strict_loads(claim_raw, max_bytes=16384)
        need(canonical_bytes(selected, max_bytes=16384) == claim_raw,
             'NATIVE_BUDGET_CANONICAL_CLAIM')
        snapshot = self._snapshot()
        need(snapshot.get('claim') == selected, 'NATIVE_BUDGET_ORIGINAL_CLAIM')
        return NativeExecutionBudget.from_snapshot(snapshot=snapshot, claim=selected,
            required_seconds=required_seconds, cleanup_seconds=cleanup_seconds)

    def issue_committed_assignment(self, scope, claim_raw, commit_evidence_raw):
        from .owner_assignment_enrollment import EnrollmentTransaction
        from .diana20_terminal_queue import terminal_work_key
        self._check(scope)
        need(self.issue_lock.acquire(blocking=False),'NATIVE_OWNER_ISSUER_BUSY')
        try:
            need(not self.used, 'NATIVE_OWNER_ISSUER_CONSUMED')
            self.used = True
        finally:self.issue_lock.release()
        claim = strict_loads(claim_raw,max_bytes=16384)
        evidence = strict_loads(commit_evidence_raw,max_bytes=4096)
        need(canonical_bytes(claim,max_bytes=16384) == claim_raw and type(evidence) is dict
             and set(evidence) == {'schema','state','claim_sha256','journal_sha256'}
             and evidence['schema'] == 'claim-commit-evidence/v1'
             and evidence['state'] == 'COMMIT_ACKNOWLEDGED'
             and evidence['claim_sha256'] == sha256(claim_raw) and p.pin(evidence['journal_sha256']),
             'NATIVE_AUTHENTICATED_WORKER_ACK_REQUIRED')
        # ACK is an attestation from the actual pinned worker peer. The hash
        # does not independently prove journal fsync or confer DB permissions.
        s = self._snapshot()
        need(s['claim'] == claim, 'NATIVE_FULL_COMMITTED_CLAIM_CHANGED')
        selected = p.exact_claim({k:claim[k] for k in p.CLAIM})
        inputs = scope.admission_inputs(selected)
        with scope.open_rc2_owner(selected) as owner:
            verified = p.parse(p.canonical(owner.verify(inputs.request_raw,inputs.request_sha256,inputs.cycle)))
            readback = p.parse(p.canonical(owner.read_saved(inputs.cycle,inputs.saved_pins)))
            completed = True
        need(locals().get('completed') is True, 'NATIVE_RC2_EXIT_SUPPRESSED_FAILURE')
        scope.record_admission(selected,verified,readback)
        self._check(scope)
        policy = scope.policy
        need(self.intent.value['target_identity_sha256'] == self.session.target.target_identity_sha256
             and self.intent.value['worker_login'] == WORKER, 'NATIVE_ENROLLED_TARGET_REQUIRED')
        a = dict(schema='diana20-owner-assignment/v1',unit=policy['caller']['unit'],
            autopilot=dict(task_id=s['task_id'],worker_id=s['worker_id'],lease_epoch=s['lease_epoch'],
                           intake_source_revision=self.source.value['intake_source_revision']),
            video={k:(WORKER_KEY if k=='worker_key' else claim[k]) for k in VIDEO_KEYS},
            admission_id=s['admission_id'],policy_id=policy['policy_id'],policy_epoch=policy['epoch'],
            source_fingerprint=s['source_fingerprint'],context_sha256=scope.context_sha,
            production_source_sha256=policy['caller']['source_sha256'],
            unit_sha256=policy['caller']['unit_sha256'],binding_sha256=policy['caller']['binding_sha256'])
        a.update({k:policy['assignment'][k] for k in ('source_sha256','request_sha256',
                                                  'generation_sha256','manifest_sha256')})
        assignment = Assignment.from_mapping(a)
        self.source.check_assignment_data(assignment,claim)
        admission = p.parse(scope._admission_receipt_raw)
        expires=bounded_expiry(s['expires_at_utc'],scope)
        request = canonical_bytes(dict(schema='native-video-binding/v1',assignment=assignment.value,
            admission_receipt=admission,claim_sha256=sha256(claim_raw),journal_sha256=evidence['journal_sha256'],
            scope_nonce_hash=sha256(scope.nonce.encode()),policy_sha256=scope.policy_sha,
            expires_at_utc=expires),max_bytes=32768)
        backend = NativeEnrollmentBackend(provider=self,scope=scope,request_raw=request)
        namespace = policy['assignment']['queue_namespace']
        txn = EnrollmentTransaction(ledger=self.store,connection=self.session.connection,target=self.session.target,
                                    namespace=namespace,enabled=True,native_backend=backend)
        receipt = txn.enroll(work_key=terminal_work_key(self.intent,namespace),
            scope_nonce_hash=sha256(scope.nonce.encode()),intent=self.intent,policy_sha256=scope.policy_sha,
            claim=claim,assignment=assignment,admission_receipt_raw=scope._admission_receipt_raw,
            approved_expires_at=expires)
        self._check(scope)
        return IssuedNativeAssignment(assignment.raw,receipt,claim_raw)


@dataclass(frozen=True)
class IssuedNativeAssignment:
    """Local result DATA, not a wire/job/dispatch/READ_RESULT authorization ABI."""
    assignment_raw: bytes
    enrollment_receipt_raw: bytes
    claim_raw: bytes


class NativeEnrollmentBackend:
    """Concrete SQL64 seam in the existing one-shot EnrollmentTransaction.

    The original ledger reserves ATTEMPT before bind SQL; each COMMIT has its
    own outer transaction. Any uncertainty consumes the stable key forever.
    """
    def __init__(self, *, provider, scope, request_raw):
        need(type(provider) is OriginalNativeVideoProvider, 'NATIVE_EXACT_PROVIDER_REQUIRED')
        self.provider, self.scope, self.raw = provider, scope, request_raw
        self._binding = (provider,scope,request_raw)
        self.request = strict_loads(request_raw,max_bytes=32768)
        need(canonical_bytes(self.request,max_bytes=32768) == request_raw, 'NATIVE_BIND_REQUEST')
        self.binding_sha256 = sha256(request_raw)

    def check(self):
        a,b,c = self._binding
        need(self.provider is a and self.scope is b and self.raw is c
             and canonical_bytes(self.request,max_bytes=32768) == c
             and self.binding_sha256 == sha256(c), 'NATIVE_ENROLL_BACKEND_CHANGED')
        a._check(b)

    def bind(self, transaction, binding):
        self.check()
        transaction._connection(binding)
        connection = binding[1]
        with connection.transaction():
            with connection.cursor() as cursor:
                transaction._identity(cursor,binding)
                row = rpc(cursor,'bind',(self.provider.task_id,
                    self.request['assignment']['autopilot']['lease_epoch'],self.raw.decode('utf8'),
                    self.request['journal_sha256']))
                need(row == dict(binding_sha256=self.binding_sha256,task_id=self.provider.task_id),
                     'NATIVE_BIND_RETURN_CHANGED')
                self.check()
        transaction._connection(binding)  # Actual bind COMMIT ACK before enrollment.

    def enroll(self,cursor,params):
        self.check()
        return rpc(cursor,'enroll',(self.binding_sha256,params['assignment_text']))

    def readback(self,cursor,params):
        self.check()
        s = rpc(cursor,'inspect',(self.binding_sha256,))
        need(s.get('assignment_sha256') == params['assignment_sha256']
             and s.get('binding_sha256') == self.binding_sha256, 'NATIVE_ENROLL_READBACK_CHANGED')
        return s['enrollment']

    def retain(self,work_key,receipt_raw):
        self.check()
        self.scope.resolver.retention.record_enrollment(work_key,receipt_raw)
        self.check()
