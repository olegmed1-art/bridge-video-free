"""Inert DB-API source adapter; never connects or installs anything by itself.

Requires a fresh host-owned connection, exact native target/work pin and existing
authenticated context lease. The lease must serialize source/role revocation
through COMMIT and read genuine receipts from retained server rows. No provider
implementation, grant, principal, credential, DSN/env fallback or issuer here.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from copy import deepcopy
from threading import Lock
from bridge_contracts import definition_v01_records as records
from bridge_contracts import definition_v01_authority as authority
from .definition_contract_store import need,StoreDenied,OutcomeUnknown,GATE_PROFILE_SHA256

ROOT_SQL="""SELECT current_database()::text AS database_name,
 (SELECT oid::text FROM pg_catalog.pg_database WHERE datname=current_database()) AS database_oid,
 (SELECT system_identifier::text FROM pg_catalog.pg_control_system()) AS system_identifier,
 current_setting('server_version_num')::integer AS server_version_num,
 session_user::text AS session_user,current_user::text AS current_role,pg_backend_pid() AS backend_pid,
 pg_current_xact_id()::text AS transaction_id,
 current_setting('transaction_isolation') AS isolation,
 current_setting('transaction_read_only') AS read_only,
 pg_is_in_recovery() AS in_recovery,
 (SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE rolname=session_user) AS rolcanlogin"""
ROW_SQL="""SELECT revision,canonical_json,document_sha256 FROM bidding.video_canon_definition_state
 WHERE school_id=%s::uuid AND candidate_payload_hash=%s AND scope_sha256=%s"""
REGISTRY_SQL="""SELECT database_role AS capability,verifier_family AS family,
 allowed_check_ids,max_assurance_level AS max_assurance,status
 FROM bidding.video_canon_verifier_registry ORDER BY database_role"""
MEMBER_SQL="""SELECT rolcanlogin,pg_catalog.pg_has_role(rolname,%s,'MEMBER') AS member
 FROM pg_catalog.pg_roles WHERE rolname=%s"""
_LOCK=Lock();_OWNED={}


@dataclass(frozen=True)
class DefinitionTargetPin:
    database_name:str
    database_oid:str
    system_identifier:str
    server_version_num:int
    session_user:str
    current_role:str
    owner_lease_ref:str
    candidate_hash:str
    scope:dict


def one(conn,sql,args=()):
    cur=conn.cursor()
    try:
        cur.execute(sql,args);row=cur.fetchone()
        if row is None:return None
        return dict(zip([c.name if hasattr(c,'name') else c[0] for c in cur.description],row))
    finally:cur.close()


class DefinitionPostgresBackend:
    mode='OBSERVED';mutation_capable=True
    def __init__(self,*,connection_provider=None,context_provider=None,target=None):
        self.provider=connection_provider;self.context_provider=context_provider;self.target=deepcopy(target)

    def _bound(self,query):
        need(type(self.target) is DefinitionTargetPin and callable(self.provider)
            and self.context_provider is not None and callable(getattr(self.context_provider,'lease',None)),
            'AUTHENTICATED_CONTEXT_OR_CONNECTION_PROVIDER_UNBOUND')
        p=self.target
        need(all(type(x) is str and bool(x.strip()) for x in (p.database_name,p.database_oid,p.system_identifier,p.session_user,p.current_role,p.owner_lease_ref))
            and p.database_oid.isdecimal() and p.system_identifier.isdecimal()
            and type(p.server_version_num) is int and p.server_version_num>=140000
            and authority.hash_ok(p.candidate_hash) and authority.scope_ok(p.scope),'EXACT_NATIVE_TARGET_PIN_REQUIRED')
        need(query['candidate_hash']==p.candidate_hash and query['scope']==p.scope,'HOST_WORK_SCOPE_MISMATCH')

    def _attest(self,conn,readonly,root=None):
        row=one(conn,ROOT_SQL);p=self.target
        need(row is not None and all(type(row[k]) is type(getattr(p,k)) and row[k]==getattr(p,k)
            for k in ('database_name','database_oid','system_identifier','server_version_num','session_user','current_role')),
            'NATIVE_TARGET_OR_SESSION_MISMATCH')
        need(row['rolcanlogin'] is True and row['in_recovery'] is False and row['isolation']=='read committed'
            and row['read_only']==('on' if readonly else 'off') and type(row['backend_pid']) is int
            and row['backend_pid']>0 and type(row['transaction_id']) is str and row['transaction_id'].isdecimal(),
            'NATIVE_ROOT_OR_ISOLATION_MISMATCH')
        identity=(row['backend_pid'],row['transaction_id'])
        need(root is None or root==identity,'NATIVE_ROOT_CHANGED')
        return identity

    @contextmanager
    def _lease(self,query,readonly):
        self._bound(query);conn=None;owned=False;commit_attempted=False;completed=False;root=None
        target_before=records.digest('NATIVE_TARGET_CONFIGURATION_0_1',vars(self.target))
        try:
            conn=self.provider()
            with _LOCK:
                need(id(conn) not in _OWNED,'CONNECTION_ALREADY_OWNED')
                # A genuine provider transfers a fresh idle DB-API connection.
                need(getattr(getattr(conn,'info',None),'transaction_status',None)==0,'FRESH_IDLE_CONNECTION_REQUIRED')
                _OWNED[id(conn)]=conn;owned=True
            conn.autocommit=True
            conn.execute('BEGIN ISOLATION LEVEL READ COMMITTED '+('READ ONLY' if readonly else 'READ WRITE'))
            conn.execute('SET LOCAL search_path=pg_catalog,public,bidding')
            conn.execute("SET LOCAL statement_timeout='3000ms'")
            conn.execute("SET LOCAL lock_timeout='1000ms'")
            root=self._attest(conn,readonly)
            with self.context_provider.lease(conn,deepcopy(query),readonly=readonly) as context:
                need(context is not None and callable(getattr(context,'fence',None)),'AUTHENTICATED_CONTEXT_LEASE_REQUIRED')
                tx=DefinitionPostgresTransaction(self,conn,context,root,readonly)
                if not readonly:
                    one(conn,'SELECT pg_advisory_xact_lock(hashtextextended(%s,0)) AS locked',
                        (records.digest('DEFINITION_LOCK_0_1',{'candidate_hash':query['candidate_hash'],'scope':query['scope']}),))
                else:
                    tx.reader_authorized(query)
                yield tx
                need(context.fence(conn) is None,'CONTEXT_FENCE_RETURN_IS_NOT_AUTHORITY')
                need(records.digest('NATIVE_TARGET_CONFIGURATION_0_1',vars(self.target))==target_before,'HOST_TARGET_CONFIGURATION_CHANGED')
                self._attest(conn,readonly,root)
                # Recheck current native actor membership after the body. The
                # context lease must keep its policy/revoke fences through ACK.
                tx.actor_member('bridge_school_reader' if readonly else authority.CAPABILITY[query['operation']])
                commit_attempted=True;conn.commit();completed=True
            need(completed,'CONTEXT_LEASE_SUPPRESSED_FAILURE')
        except BaseException as exc:
            if completed:raise OutcomeUnknown('POST_COMMIT_LEASE_EXIT_UNKNOWN') from None
            root_confirmed=True
            if owned and root is not None:
                try:self._attest(conn,readonly,root)
                except BaseException:root_confirmed=False
            if owned and not completed:
                try:conn.rollback()
                except BaseException:raise OutcomeUnknown('ROLLBACK_ACK_UNKNOWN') from None
            if not root_confirmed:raise OutcomeUnknown('OWNED_ROOT_OUTCOME_UNCONFIRMED') from None
            if commit_attempted and not completed:raise OutcomeUnknown('COMMIT_ACK_UNKNOWN') from None
            raise
        finally:
            if owned:
                try:conn.close()
                except BaseException:raise OutcomeUnknown('CONNECTION_CLOSE_UNCONFIRMED') from None
                else:
                    with _LOCK:_OWNED.pop(id(conn),None)

    def transaction(self,command):return self._lease(command,False)
    def observer(self,query):return self._lease(query,True)


class DefinitionPostgresTransaction:
    mode='OBSERVED'
    def __init__(self,backend,conn,context,root,readonly):
        self.backend=backend;self.conn=conn;self.context=context;self.root=root;self.readonly=readonly

    def key(self,request):
        need(request['candidate_hash']==self.backend.target.candidate_hash and request['scope']==self.backend.target.scope,
            'NATIVE_OPERATION_OUTSIDE_BOUND_WORK')
        return (request['scope']['school'],request['candidate_hash'],records.digest('DEFINITION_SCOPE_0_1',request['scope']))

    def now_us(self):
        return one(self.conn,"SELECT (extract(epoch FROM clock_timestamp())*1000000)::bigint AS now_us")['now_us']

    def actor_member(self,capability):
        row=one(self.conn,MEMBER_SQL,(capability,self.backend.target.session_user))
        need(row is not None and row['rolcanlogin'] is True and row['member'] is True,'NATIVE_CAPABILITY_REVOKED')

    def reader_authorized(self,query):
        self.actor_member('bridge_school_reader')
        need(callable(getattr(self.context,'test_context',None)),'CURRENT_DIAGNOSTIC_CONTEXT_UNBOUND')

    def load(self,request):
        row=one(self.conn,ROW_SQL+(' FOR UPDATE' if not self.readonly else ''),self.key(request))
        if row is None:return None
        raw=row['canonical_json'].encode('utf-8');doc=records.parse_canonical(raw)
        need(row['document_sha256']==__import__('hashlib').sha256(raw).hexdigest()
            and type(row['revision']) is int and doc['revision']==row['revision'],'STORED_DOCUMENT_HASH_OR_VERSION')
        return doc

    def authority(self,request):
        need(not self.readonly and callable(getattr(self.context,'authority',None)),'MUTATION_AUTHORITY_UNBOUND')
        self.backend._attest(self.conn,False,self.root)
        facts=deepcopy(self.context.authority(self.conn,deepcopy(request)))
        need(facts['evidence_mode']=='OBSERVED' and facts['session_user']==self.backend.target.session_user
            and facts['candidate_hash']==self.backend.target.candidate_hash and facts['scope']==self.backend.target.scope,'NATIVE_AUTHORITY_WORK_BINDING')
        facts['root']=dict(backend_pid=self.root[0],transaction_id=self.root[1],owner_ref=self.backend.target.owner_lease_ref,mode='READ_WRITE')
        facts['target_sha256']=records.digest('NATIVE_DEFINITION_TARGET_0_1',vars(self.backend.target))
        facts['gate_profile_sha256']=GATE_PROFILE_SHA256
        self.actor_member(authority.CAPABILITY[request['operation']])
        cur=self.conn.cursor()
        try:
            cur.execute(REGISTRY_SQL);facts['registry']=[dict(zip([c.name if hasattr(c,'name') else c[0] for c in cur.description],r)) for r in cur.fetchall()]
        finally:cur.close()
        for member in facts['memberships']:
            row=one(self.conn,MEMBER_SQL,(member['capability'],member['principal']))
            need(row is not None,'NATIVE_EXECUTOR_NOT_FOUND')
            member.update(rolcanlogin=row['rolcanlogin'],member=row['member'])
        if request['operation']=='RESTORE_KV':
            need(callable(getattr(self.context,'restore_receipts',None)),'GENUINE_RESTORE_RECEIPT_PROVIDER_UNBOUND')
            facts['receipt_set']=deepcopy(self.context.restore_receipts(self.conn,deepcopy(request)))
        return facts

    def test_context(self,request):
        self.key(request)
        value=deepcopy(self.context.test_context(self.conn,deepcopy(request)))
        need(value['evidence_mode']=='OBSERVED','SYNTHETIC_CONTEXT_NOT_NATIVE')
        return value

    def restore_material(self,request):
        self.key(request)
        need(callable(getattr(self.context,'restore_material',None)),'GENUINE_RESTORE_MATERIAL_PROVIDER_UNBOUND')
        return deepcopy(self.context.restore_material(self.conn,deepcopy(request)))

    def current_restore_hash(self,request):
        material=self.restore_material(request);body=material['binding']['body']
        return records.digest('RESTORED_PROJECTION_0_1',records.projection(material['state'],body['item_key'],request['scope']))

    def apply_restore(self,request,writes,postimage):
        need(not self.readonly,'READ_LEASE_HAS_NO_WRITE_RIGHT')
        for w in writes:
            need(w['table']=='public.canon_activation' and set(w['set'])=={'status','end_us'}
                and w['key'] in postimage['activations'],'BOUNDED_RESTORE_WRITESET')
            old=postimage['activations'][w['key']];new=w['set']
            cur=self.conn.cursor()
            try:
                cur.execute("""UPDATE public.canon_activation SET status=%s,
 valid_to=CASE WHEN %s::bigint IS NULL THEN NULL ELSE timestamptz 'epoch'+%s::bigint*interval '1 microsecond' END
 WHERE canon_activation_id=%s::uuid AND knowledge_version_id=%s::uuid AND scope_key=%s
 AND status=%s AND valid_from=timestamptz 'epoch'+%s::bigint*interval '1 microsecond'
 AND valid_to IS NOT DISTINCT FROM CASE WHEN %s::bigint IS NULL THEN NULL ELSE timestamptz 'epoch'+%s::bigint*interval '1 microsecond' END""",
                    (new['status'],new['end_us'],new['end_us'],w['key'],old['version_key'],old['scope'],old['status'],old['start_us'],old['end_us'],old['end_us']))
                need(cur.rowcount==1,'RESTORE_ROW_CAS_FAILED')
            finally:cur.close()

    def put(self,request,document,*,expected_revision):
        need(not self.readonly,'READ_LEASE_HAS_NO_WRITE_RIGHT')
        raw=records.canonical_bytes(document).decode('utf-8');key=self.key(request);cur=self.conn.cursor()
        try:
            if expected_revision==0:
                need(callable(getattr(self.context,'candidate_id',None)),'GENUINE_PERSISTED_CANDIDATE_UNBOUND')
                candidate_id=self.context.candidate_id(self.conn,deepcopy(request))
                cur.execute("""INSERT INTO bidding.video_canon_definition_state
 (school_id,candidate_payload_hash,scope_sha256,scope_canonical_json,analysis_candidate_id,revision,canonical_json)
 VALUES (%s::uuid,%s,%s,%s,%s::uuid,%s,%s) ON CONFLICT DO NOTHING""",(*key,records.canonical_bytes(request['scope']).decode('utf-8'),candidate_id,document['revision'],raw))
            else:
                cur.execute("""UPDATE bidding.video_canon_definition_state SET revision=%s,canonical_json=%s
 WHERE school_id=%s::uuid AND candidate_payload_hash=%s AND scope_sha256=%s AND revision=%s""",
                    (document['revision'],raw,*key,expected_revision))
            need(cur.rowcount==1,'DOCUMENT_CAS_FAILED')
        finally:cur.close()

    def audit(self,request,event):
        need(not self.readonly,'READ_LEASE_HAS_NO_WRITE_RIGHT');cur=self.conn.cursor()
        try:
            cur.execute("""INSERT INTO bidding.video_canon_definition_audit
 (school_id,candidate_payload_hash,scope_sha256,request_key,event_canonical_json)
 VALUES (%s::uuid,%s,%s,%s,%s)""",(*self.key(request),request['request_key'],records.canonical_bytes(event).decode('utf-8')))
        finally:cur.close()
