"""Concrete one-shot enrollment TRANSACTION, not an authenticated issuer.

Uses existing SQL0063 API and same physical terminal ledger. No connections,
credentials, roles, DDL, grants, read grants, services or wire are created.
The caller must be the ORIGINAL authorized owner; this data engine cannot
prove missing autopilot/native issuance. OwnerScope remains closed/unwired.
Portable tests do not establish installed DB/native authority.
"""
from dataclasses import dataclass
from datetime import datetime
import re
import threading
from collections.abc import Mapping

from . import diana20_transport_protocol as p
from .receipt_encoding import canonical_bytes
from .terminal_finish_contract import Assignment
from .diana20_terminal_queue import terminal_work_key
from .owner_terminal_store import SQLiteTerminalLedger, TerminalOwnerStore
from .owner_terminal_store import _enrollment_attempt, _enrollment_receipt


class EnrollmentError(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise EnrollmentError(code) from None


@dataclass(frozen=True)
class EnrollmentTarget:
    """Accepted target DATA, not proof of identity or authorization."""
    database: str
    owner_login: str
    worker_login: str
    host: str
    port: int
    target_identity_sha256: str

    def __post_init__(self):
        need(all(type(v) is str and re.fullmatch('[A-Za-z0-9_][A-Za-z0-9_.:-]{0,62}', v)
                 for v in (self.database,self.owner_login,self.worker_login))
             and self.owner_login != self.worker_login
             and type(self.host) is str and re.fullmatch('[a-z0-9][a-z0-9.-]{0,252}',self.host)
             and type(self.port) is int and 1 <= self.port <= 65535
             and p.pin(self.target_identity_sha256), 'OWNER_ENROLLMENT_TARGET_REQUIRED')


LOCAL_SETTINGS = ("SET LOCAL statement_timeout='3000ms'",
    "SET LOCAL lock_timeout='1000ms'", "SET LOCAL transaction_timeout='10000ms'",
    "SET LOCAL idle_in_transaction_session_timeout='5000ms'", "SET LOCAL search_path=pg_catalog")
SQL_IDENTITY = """SELECT current_database() AS database_name,session_user::text AS session_login,
current_user::text AS effective_login,pg_backend_pid() AS backend_pid"""

# Exact statements A1/A2/A3 from the reviewed existing template, not a new RPC.
TEMPLATE_SHA256 = 'b68bf59afabe115ac219d30f985babcaf36c6c577129cd75bee505d9f7dba671'
SQL_LOCK_JOB = "SELECT job_id,batch_id,source_file_id,stable_job_key,attempt_count,\n       lease_owner,lease_token,lease_expires_at\nFROM video_queue.job\nWHERE job_id=%(job_id)s::uuid AND batch_id=%(batch_id)s::uuid\n  AND status='LEASED' AND attempt_count=%(attempt_count)s\n  AND source_file_id=%(source_file_id)s AND stable_job_key=%(stable_job_key)s\n  AND lease_owner=%(worker_key)s AND lease_token=%(lease_token)s::uuid\n  AND lease_expires_at>clock_timestamp()\nFOR SHARE NOWAIT;"
SQL_LOCK_BATCH = "SELECT batch_id,processing_profile,algorithm_revision,output_folder_id\nFROM video_queue.batch\nWHERE batch_id=%(batch_id)s::uuid AND status='RUNNING'\n  AND processing_profile=%(processing_profile)s\n  AND algorithm_revision=%(algorithm_revision)s\n  AND output_folder_id=%(output_folder_id)s\nFOR SHARE NOWAIT;"
SQL_INSERT = 'WITH stamp AS MATERIALIZED (SELECT clock_timestamp() AS issued),\n     approved AS MATERIALIZED (\n       SELECT video_queue.post_finish_assignment_v1(%(assignment_text)s) AS a)\nINSERT INTO video_queue.terminal_assignment_v1\n (selector_sha256,assignment_sha256,assignment_text,principal,issued_at,expires_at)\nSELECT video_queue.post_finish_selector_v1(%(assignment_text)s),\n       video_queue.post_finish_sha_v1(approved.a),%(assignment_text)s,\n       %(worker_login)s::name,stamp.issued,%(approved_expires_at)s::timestamptz\nFROM approved CROSS JOIN stamp\nWHERE current_database()=%(database_name)s\n  AND session_user::text=%(owner_login)s AND current_user=session_user\n  AND %(worker_login)s::text=(%(worker_login)s::name)::text\n  AND %(worker_login)s<>%(owner_login)s\n  AND EXISTS (SELECT 1 FROM pg_catalog.pg_roles AS role\n              WHERE role.rolname=%(worker_login)s::name AND role.rolcanlogin)\n  AND approved.a#>>\'{video,job_id}\'=%(job_id)s::text\n  AND approved.a#>>\'{video,batch_id}\'=%(batch_id)s::text\n  AND approved.a#>>\'{video,source_file_id}\'=%(source_file_id)s\n  AND approved.a#>>\'{video,stable_job_key}\'=%(stable_job_key)s\n  AND (approved.a#>>\'{video,attempt_count}\')::integer=%(attempt_count)s\n  AND approved.a#>>\'{video,worker_key}\'=%(worker_key)s\n  AND approved.a#>>\'{video,lease_token}\'=%(lease_token)s::text\n  AND approved.a#>>\'{video,algorithm_revision}\'=%(algorithm_revision)s\n  AND approved.a#>>\'{video,output_folder_id}\'=%(output_folder_id)s\n  -- SH locks prevent row mutation, not wall-clock lease expiry. Recheck the\n  -- SAME already-locked job at INSERT; this acquires no new out-of-order lock.\n  AND EXISTS (SELECT 1 FROM video_queue.job AS j\n              WHERE j.job_id=%(job_id)s::uuid AND j.batch_id=%(batch_id)s::uuid\n                AND j.source_file_id=%(source_file_id)s\n                AND j.stable_job_key=%(stable_job_key)s\n                AND j.status=\'LEASED\' AND j.attempt_count=%(attempt_count)s\n                AND j.lease_owner=%(worker_key)s\n                AND j.lease_token=%(lease_token)s::uuid\n                AND j.lease_expires_at>clock_timestamp())\n  AND %(approved_expires_at)s::timestamptz>stamp.issued\n  AND %(approved_expires_at)s::timestamptz<=stamp.issued+interval \'1 hour\'\nRETURNING selector_sha256,assignment_sha256,assignment_text,principal,\n          to_char(issued_at AT TIME ZONE \'UTC\',\'YYYY-MM-DD"T"HH24:MI:SS.US"Z"\') AS issued_at_utc,\n          to_char(expires_at AT TIME ZONE \'UTC\',\'YYYY-MM-DD"T"HH24:MI:SS.US"Z"\') AS expires_at_utc;'

SQL_READBACK = """SELECT selector_sha256,assignment_sha256,assignment_text,principal,
to_char(issued_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS issued_at_utc,
to_char(expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS expires_at_utc
FROM video_queue.terminal_assignment_v1
WHERE selector_sha256=%(selector_sha256)s AND assignment_sha256=%(assignment_sha256)s
AND assignment_text=%(assignment_text)s AND principal=%(worker_login)s::name
AND revoked_at IS NULL AND issued_at<=clock_timestamp() AND expires_at>clock_timestamp()"""
RETURNED = {'selector_sha256','assignment_sha256','assignment_text','principal',
            'issued_at_utc','expires_at_utc'}


def _one(cursor, sql, params, keys):
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    need(type(rows) in (list,tuple) and len(rows) == 1, 'OWNER_ENROLLMENT_EXACT_ROW_REQUIRED')
    row = rows[0]
    if isinstance(row, Mapping):
        need(set(row) == set(keys), 'OWNER_ENROLLMENT_COLUMNS')
        return dict(row)
    names = tuple(c.name for c in cursor.description)
    need(type(row) in (list,tuple) and len(row) == len(keys) and set(names) == set(keys),
         'OWNER_ENROLLMENT_COLUMNS')
    return dict(zip(names,row))


class EnrollmentTransaction:
    """Real SQL/SQLite effects when explicitly used by an EXISTING owner driver.

    Default disabled. Enabling this class is NOT permission or authentication.
    SQLiteTerminalLedger is accepted for portable physical tests; real binding
    must use TerminalOwnerStore and genuine owner/native/task authority. This
    engine is deliberately NOT installed into OwnerScope or exposed on wire.
    """
    def __init__(self, *, ledger, connection, target, namespace, enabled=False, native_backend=None):
        need(type(enabled) is bool and enabled, 'OWNER_ENROLLMENT_DRIVER_DISABLED')
        need(type(ledger) in (SQLiteTerminalLedger,TerminalOwnerStore)
             and type(target) is EnrollmentTarget, 'OWNER_ENROLLMENT_BINDING_REQUIRED')
        self.ledger,self.connection,self.target = ledger,connection,target
        need(type(namespace) is str and 0 < len(namespace) <= 128, 'OWNER_ENROLLMENT_NAMESPACE_REQUIRED')
        self.namespace=namespace  # Accepted by the real launcher, not approval from payload.
        if native_backend is not None:
            from .native_video_binding_provider import NativeEnrollmentBackend
            need(type(native_backend) is NativeEnrollmentBackend and type(ledger) is TerminalOwnerStore,
                 'OWNER_ENROLLMENT_NATIVE_BACKEND_REQUIRED')
            need(native_backend.provider.store is ledger
                 and native_backend.provider.session.connection is connection
                 and native_backend.provider.session.target is target,
                 'OWNER_ENROLLMENT_NATIVE_CONNECTION_REQUIRED')
        self.native_backend=native_backend
        self._binding=(ledger,connection,target,namespace,native_backend)
        self.used=False
        self.lock=threading.Lock()

    def _assert_binding(self, binding):
        need(self._binding is binding and self.ledger is binding[0]
             and self.connection is binding[1] and self.target is binding[2]
             and self.namespace == binding[3] and self.native_backend is binding[4],
             'OWNER_ENROLLMENT_BINDING_CHANGED')

    def _connection(self, binding):
        self._assert_binding(binding)
        ledger,connection,target,_ = binding[:4]
        ledger._check()  # Injected ledger guard/inode, not task authority.
        self._assert_binding(binding)
        info=connection.info
        need(info.host == target.host and info.port == target.port
             and info.transaction_status.name == 'IDLE',
             'OWNER_ENROLLMENT_IDLE_TARGET_REQUIRED')
        self._assert_binding(binding)

    def _identity(self, cursor, binding):
        self._assert_binding(binding)
        target=binding[2]
        for sql in LOCAL_SETTINGS:
            cursor.execute(sql)
        row=_one(cursor,SQL_IDENTITY,None,
                 {'database_name','session_login','effective_login','backend_pid'})
        need(row['database_name']==target.database
             and row['session_login']==row['effective_login']==target.owner_login
             and type(row['backend_pid']) is int and row['backend_pid']>0,
             'OWNER_ENROLLMENT_DATABASE_IDENTITY_REQUIRED')
        self._assert_binding(binding)

    def enroll(self, *, work_key, scope_nonce_hash, intent, policy_sha256, claim,
               assignment, admission_receipt_raw, approved_expires_at):
        binding=self._binding
        ledger,connection,target,namespace,backend=binding
        acquired=self.lock.acquire(blocking=False)
        reserved=False
        attempt_raw=None
        try:
            need(acquired and not self.used,'OWNER_ENROLLMENT_REPLAY_READ_ONLY')
            self.used=True
            self._assert_binding(binding)
            need(type(intent) is p.Intent and type(assignment) is Assignment
                 and p.pin(work_key) and p.pin(scope_nonce_hash) and p.pin(policy_sha256),
                 'OWNER_ENROLLMENT_INPUTS')
            need(work_key == terminal_work_key(intent,namespace),
                 'OWNER_ENROLLMENT_STABLE_WORK_KEY_REQUIRED')
            claim_raw=canonical_bytes(dict(claim),max_bytes=16384)
            claim=p.parse(claim_raw)
            assignment.check_claim(claim,intent.value['worker_key'])
            a=assignment.value;i=intent.value
            need(all(type(claim.get(k)) is type(i[k]) and claim[k]==i[k] for k in
                     ('job_id','batch_id','source_file_id','attempt_count','processing_profile','algorithm_revision'))
                 and all(a[k]==i[k] for k in ('admission_id','source_fingerprint','source_sha256',
                     'unit_sha256','binding_sha256','generation_sha256','manifest_sha256','policy_id','policy_epoch'))
                 and i['worker_login']==target.worker_login
                 and i['target_identity_sha256']==target.target_identity_sha256,
                 'OWNER_ENROLLMENT_INTENT_CHANGED')
            from .owner_signed_policy import strict_json
            admission=strict_json(admission_receipt_raw)
            need(type(admission) is dict and set(admission)=={'state','durability_confirmed',
                 'record_sha256','claim_sha256','anchor_sha256','request_sha256'}
                 and admission['state']=='COMMITTED' and admission['durability_confirmed'] is True
                 and admission['claim_sha256']==p.digest(p.canonical({k:claim[k] for k in p.CLAIM}))
                 and admission['request_sha256']==a['request_sha256']
                 and p.pin(admission['record_sha256']) and p.pin(admission['anchor_sha256']),
                 'OWNER_ENROLLMENT_ADMISSION_INPUT_REQUIRED')
            try:
                expiry=datetime.strptime(approved_expires_at,'%Y-%m-%dT%H:%M:%S.%fZ')
            except (TypeError,ValueError,OverflowError):
                raise EnrollmentError('OWNER_ENROLLMENT_DATE') from None
            need(expiry.strftime('%Y-%m-%dT%H:%M:%S.%fZ')==approved_expires_at,
                 'OWNER_ENROLLMENT_DATE')
            attempt_raw=p.canonical(dict(schema='owner-enrollment-attempt/v1',work_key=work_key,
                scope_nonce_hash=scope_nonce_hash,intent_hash=intent.sha256,policy_sha256=policy_sha256,
                claim_sha256=p.digest(claim_raw),assignment_sha256=assignment.sha256,
                selector_sha256=assignment.selector_sha256,target_identity_sha256=i['target_identity_sha256'],
                principal=target.worker_login,admission_record_sha256=admission['record_sha256'],
                admission_anchor_sha256=admission['anchor_sha256']))
            self._connection(binding)
            # Physical BEGIN IMMEDIATE/create-only stable-key reservation, FULL
            # sync/barrier/readback BEFORE starting the PG write transaction.
            ledger.reserve_enrollment(attempt_raw)
            reserved=True
            self._assert_binding(binding)
            params=dict(job_id=claim['job_id'],batch_id=claim['batch_id'],
                source_file_id=claim['source_file_id'],stable_job_key=claim['stable_job_key'],
                attempt_count=claim['attempt_count'],worker_key=i['worker_key'],lease_token=claim['lease_token'],
                processing_profile=claim['processing_profile'],algorithm_revision=claim['algorithm_revision'],
                output_folder_id=claim['output_folder_id'],assignment_text=assignment.raw.decode('utf-8'),
                worker_login=target.worker_login,owner_login=target.owner_login,
                database_name=target.database,approved_expires_at=approved_expires_at,
                selector_sha256=assignment.selector_sha256,assignment_sha256=assignment.sha256)
            if backend is not None:
                # Same original durable stable-key ATTEMPT already exists.
                # Actual bind COMMIT must precede the enrollment transaction.
                backend.bind(self,binding)
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._identity(cursor,binding)
                    if backend is not None:
                        returned=backend.enroll(cursor,params)
                    else:
                        job=_one(cursor,SQL_LOCK_JOB,params,{'job_id','batch_id','source_file_id',
                            'stable_job_key','attempt_count','lease_owner','lease_token','lease_expires_at'})
                        need(all(str(job[k])==params[k] for k in ('job_id','batch_id','lease_token'))
                             and all(type(job[k]) is type(params[k]) and job[k]==params[k] for k in
                                 ('source_file_id','stable_job_key','attempt_count'))
                             and type(job['lease_owner']) is str and job['lease_owner']==i['worker_key'],
                             'OWNER_ENROLLMENT_LEASE_CHANGED')
                        batch=_one(cursor,SQL_LOCK_BATCH,params,{'batch_id','processing_profile',
                                                               'algorithm_revision','output_folder_id'})
                        need(str(batch['batch_id'])==params['batch_id']
                             and all(type(batch[k]) is str and batch[k]==params[k] for k in
                                 ('processing_profile','algorithm_revision','output_folder_id')),
                             'OWNER_ENROLLMENT_BATCH_CHANGED')
                        ledger._check()
                        self._assert_binding(binding)
                        returned=_one(cursor,SQL_INSERT,params,RETURNED)
                    receipt=self._receipt(returned,assignment,attempt_raw,approved_expires_at,binding)
                    ledger._check()  # Injected ledger guard, not native issuance proof.
                    self._assert_binding(binding)
            # Exiting the OUTERMOST psycopg transaction must acknowledge COMMIT.
            # A lost ACK throws before this point; it is never retried.
            self._connection(binding)
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._identity(cursor,binding)
                    readback=(backend.readback(cursor,params) if backend is not None
                              else _one(cursor,SQL_READBACK,params,RETURNED))
                    need(readback==returned,'OWNER_ENROLLMENT_COMMIT_READBACK_CHANGED')
                    ledger._check()
                    self._assert_binding(binding)
            self._connection(binding)
            if backend is not None:
                backend.retain(work_key,receipt)  # Full durable receipt + original anchor, before descriptor ACK.
            ledger.commit_enrollment(work_key,p.digest(attempt_raw),receipt)
            self._assert_binding(binding)
            return receipt  # DATA, not worker SQL authorization or a READ_RESULT grant.
        except BaseException:
            if reserved and not ledger._failed:
                # Best-effort durable UNKNOWN, never repeat an uncertain ledger
                # write/COMMIT. ATTEMPT alone already consumes this stable key.
                try:ledger.unknown_enrollment(work_key,p.digest(attempt_raw))
                except BaseException:pass
            raise EnrollmentError('OWNER_ENROLLMENT_UNKNOWN_NO_RETRY') from None
        finally:
            if acquired:self.lock.release()

    def _receipt(self,row,assignment,attempt_raw,approved_expires_at,binding):
        self._assert_binding(binding)
        a=_enrollment_attempt(attempt_raw)
        need(row['selector_sha256']==assignment.selector_sha256
             and row['assignment_sha256']==assignment.sha256
             and row['assignment_text']==assignment.raw.decode('utf-8')
             and row['principal']==a['principal']==binding[2].worker_login
             and row['expires_at_utc']==approved_expires_at,
             'OWNER_ENROLLMENT_RETURN_CHANGED')
        value={k:a[k] for k in ('intent_hash','policy_sha256','claim_sha256','assignment_sha256',
            'selector_sha256','target_identity_sha256','principal','admission_record_sha256',
            'admission_anchor_sha256')}
        value.update(schema='enrolled-assignment/v1',assignment_text=row['assignment_text'],
                     issued_at_utc=row['issued_at_utc'],expires_at_utc=row['expires_at_utc'])
        raw=p.canonical(value)
        _enrollment_receipt(raw,a)
        return raw
