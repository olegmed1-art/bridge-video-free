"""Inert native source adapter. No connect(), grants, DDL, issuer or route.

The authenticated context provider must hold school/scope, source-policy,
registry/membership and Canon-revoke fences through COMMIT acknowledgement.
It resolves retained evidence; this library does not make that trust available.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from database.definition_contract_postgres import DefinitionPostgresBackend, one, _LOCK, _OWNED
from database.definition_contract_store import need, OutcomeUnknown, validate_document, evaluate_document, GATE_PROFILE_SHA256
from bridge_contracts import definition_v01_records as records
from bridge_contracts.card_play_definition_admission import _digest, _sha
from database.definition_initial_admission import same_json
from bridge_contracts.definition_staging_identity import definition_staging_keys

CAPS = {'INITIAL_DEFINITION_ACCEPT': 'bridge_school_canon_promoter',
    'READ_ACCEPTED_DEFINITION': 'bridge_school_reader'}
DEFINITION_BUNDLE_SCHEMA = 'video-canon-definition-admission-v1'


def rows(conn, sql, args=()):
    cur = conn.cursor()
    try:
        cur.execute(sql, args)
        names = [x.name if hasattr(x, 'name') else x[0] for x in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]
    finally:
        cur.close()


def iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def epoch_us(value):
    need(isinstance(value, datetime) and value.utcoffset() is not None, 'NATIVE_AWARE_TIME_REQUIRED')
    delta = value - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


class DefinitionInitialPostgres(DefinitionPostgresBackend):
    """Reuses frozen exact target/root attestation; owns no live credentials."""
    @contextmanager
    def _initial_lease(self, request, readonly):
        conn = None; owned = False; root = None; attempted = False; committed = False; tx = None
        need(request['operation'] == ('READ_ACCEPTED_DEFINITION' if readonly else 'INITIAL_DEFINITION_ACCEPT'),
            'PRIVATE_ENTRYPOINT_OPERATION_MISMATCH')
        need(self.target is not None, 'NATIVE_TARGET_UNBOUND')
        native = dict(candidate_hash=request['candidate_payload_hash'], scope=deepcopy(self.target.scope))
        self._bound(native)
        need((native['scope']['profile'], native['scope']['level'], native['scope']['scope']) ==
            tuple(request['scope'][k] for k in ('system_profile', 'learner_level', 'activation_scope')),
            'HOST_NATIVE_SCOPE_MISMATCH')
        target_hash = records.digest('NATIVE_TARGET_CONFIGURATION_0_1', vars(self.target))
        try:
            conn = self.provider()
            with _LOCK:
                need(id(conn) not in _OWNED and
                    getattr(getattr(conn, 'info', None), 'transaction_status', None) == 0,
                    'FRESH_OWNED_IDLE_CONNECTION_REQUIRED')
                _OWNED[id(conn)] = conn; owned = True
            conn.autocommit = True
            conn.execute('BEGIN ISOLATION LEVEL READ COMMITTED ' + ('READ ONLY' if readonly else 'READ WRITE'))
            conn.execute('SET LOCAL search_path=pg_catalog,public,bidding')
            conn.execute("SET LOCAL statement_timeout='3000ms'")
            conn.execute("SET LOCAL lock_timeout='1000ms'")
            root = self._attest(conn, readonly)
            school = one(conn, 'SELECT stable_name FROM public.school WHERE school_id=%s::uuid',
                (native['scope']['school'],))
            need(school is not None and school['stable_name'] == request['scope']['school_key'],
                'NATIVE_SCHOOL_SCOPE_MISMATCH')
            with self.context_provider.lease(conn, deepcopy(request), readonly=readonly) as context:
                need(context is not None and all(callable(getattr(context, k, None)) for k in
                    ('authorize', 'evidence', 'fence')), 'AUTHENTICATED_CONTEXT_UNBOUND')
                tx = InitialTransaction(self, conn, context, native, readonly)
                tx.authorize(request, write=not readonly)
                if not readonly:
                    one(conn, 'SELECT pg_advisory_xact_lock(hashtextextended(%s,0)) AS locked',
                        ('definition-initial:' + native['scope']['school'] + ':' + request['stable_key'],))
                yield tx
                need(context.fence(conn) is None, 'CONTEXT_FENCE_RETURN_IS_NOT_AUTHORITY')
                need(records.digest('NATIVE_TARGET_CONFIGURATION_0_1', vars(self.target)) == target_hash,
                    'NATIVE_TARGET_CONFIGURATION_CHANGED')
                self._attest(conn, readonly, root)
                tx.authorize(request, write=not readonly)
                attempted = True; conn.commit(); committed = True
            need(committed, 'CONTEXT_SUPPRESSED_FAILURE')
        except BaseException:
            confirmed = True
            if owned and root is not None:
                try:
                    self._attest(conn, readonly, root)
                except BaseException:
                    confirmed = False
            if owned and not committed:
                try:
                    conn.rollback()
                except BaseException:
                    raise OutcomeUnknown('ROLLBACK_ACK_UNKNOWN') from None
            if attempted or committed or not confirmed:
                raise OutcomeUnknown('NATIVE_ROOT_OR_COMMIT_OUTCOME_UNKNOWN') from None
            raise
        finally:
            if owned:
                try:
                    conn.close()
                except BaseException:
                    raise OutcomeUnknown('CONNECTION_CLOSE_UNCONFIRMED') from None
                else:
                    with _LOCK:
                        _OWNED.pop(id(conn), None)
        # No completion is exposed before context exit and successful close.
        if tx is not None:
            tx.finished = True

    def transaction(self, request):
        return self._initial_lease(request, False)

    def observer(self, request):
        return self._initial_lease(request, True)


class InitialTransaction:
    mode = 'OBSERVED'
    finished = False

    def __init__(self, backend, conn, context, native, readonly):
        self.backend, self.conn, self.context, self.native, self.readonly = backend, conn, context, native, readonly

    def now(self):
        return one(self.conn, 'SELECT clock_timestamp() AS value')['value']

    def authorize(self, request, *, write):
        need(write is (not self.readonly) and request['candidate_payload_hash'] == self.native['candidate_hash'],
            'READ_LEASE_CANNOT_WRITE_OR_FOREIGN_WORK')
        cap = CAPS[request['operation']]
        principal = self.backend.target.session_user
        member = one(self.conn, 'SELECT rolcanlogin,pg_has_role(rolname,%s,\'MEMBER\') AS member FROM pg_roles WHERE rolname=%s',
            (cap, principal))
        need(member is not None and member['rolcanlogin'] is True and member['member'] is True,
            'NATIVE_CAPABILITY_REVOKED')
        need(self.context.authorize(self.conn, deepcopy(request), capability=cap) == principal,
            'ACTOR_OR_AUTHENTICATED_WORK_LEASE_UNBOUND')
        return principal

    def candidate(self, request):
        logical=request['stable_key']
        keys=definition_staging_keys(logical,request['candidate_payload_hash'])
        found = rows(self.conn, '''SELECT c.analysis_candidate_id::text,c.source_id::text,c.payload,c.payload_hash,
 c.candidate_type,c.status,c.promotion_status,s.status AS source_status,c.stable_key,
 c.school_id::text AS candidate_school_id,s.school_id::text AS source_school_id
 FROM public.analysis_candidate c JOIN public.source s ON s.source_id=c.source_id
 WHERE c.school_id=%s::uuid AND c.payload_hash=%s AND c.stable_key=ANY(%s::text[])
 AND c.payload#>>'{knowledge_item,stable_key}'=%s
 AND s.school_id=c.school_id ORDER BY c.analysis_candidate_id LIMIT 2''',
            (self.native['scope']['school'], request['candidate_payload_hash'], list(keys), logical))
        need(len(found) == 1, 'EXACT_PERSISTED_DEFINITION_CANDIDATE_REQUIRED')
        c = found[0]
        need(c['candidate_type'] == 'video_school_canon_candidate' and c['status'] == 'active' and
            c['promotion_status'] in ('staging', 'review_queue') and c['source_status'] == 'active' and
            c['candidate_school_id']==c['source_school_id']==self.native['scope']['school'] and
            c['stable_key'] in keys and c['payload']['knowledge_item']['stable_key']==logical and
            c['payload_hash']==request['candidate_payload_hash'] and
            _digest(c['payload']) == request['candidate_payload_hash'], 'PERSISTED_CANDIDATE_REVOKED_OR_HASH')
        return c

    def source_id(self, request):
        return self.candidate(request)['source_id']

    def origin_actor(self, principal):
        row = one(self.conn, '''SELECT rolcanlogin,pg_has_role(rolname,'bridge_school_canon_promoter','MEMBER') AS member
 FROM pg_roles WHERE rolname=%s''', (principal,))
        need(row is not None and row['rolcanlogin'] is True and row['member'] is True,
            'ORIGIN_PROMOTER_REVOKED')

    def snapshot(self):
        return one(self.conn, 'SELECT bidding.current_school_canon_snapshot_sha256(%s::uuid) AS digest',
            (self.native['scope']['school'],))['digest']

    def evidence(self, request, *, archived):
        c = self.candidate(request)
        body = deepcopy(self.context.evidence(self.conn, deepcopy(request), archived=archived))
        # Neither context JSON nor approval_provenance is sufficient: retained native bundle and rows are mandatory.
        bundle = one(self.conn, '''SELECT bundle_canonical_json,bundle_payload FROM bidding.video_canon_ai_verification_bundle
 WHERE analysis_candidate_id=%s::uuid AND candidate_payload_hash=%s AND verification_bundle_sha256=%s''',
            (c['analysis_candidate_id'], request['candidate_payload_hash'], request['verification_bundle_sha256']))
        need(bundle is not None and hashlib.sha256(bundle['bundle_canonical_json'].encode('utf-8')).hexdigest() ==
            request['verification_bundle_sha256'] and same_json(json.loads(bundle['bundle_canonical_json']), bundle['bundle_payload']),
            'GENUINE_RETAINED_BUNDLE_REQUIRED')
        payload = bundle['bundle_payload']
        fields = {'schema', 'candidate_payload', 'candidate_payload_hash', 'scope', 'canon_snapshot_sha256',
            'gate_profile_sha256', 'definition_test_state_sha256', 'definition_restore_binding_sha256',
            'checks', 'source_binding', 'speaker_id', 'source_class', 'semantic_scope', 'semantic_confidence'}
        need(type(payload) is dict and set(payload) == fields and
            payload.get('schema') == DEFINITION_BUNDLE_SCHEMA and same_json(payload.get('candidate_payload'), c['payload']) and
            payload.get('candidate_payload_hash') == request['candidate_payload_hash'] and
            same_json(payload.get('scope'), request['scope']) and
            payload.get('canon_snapshot_sha256') == body['canon_snapshot_sha256'] and
            payload.get('gate_profile_sha256') == GATE_PROFILE_SHA256 and
            _sha(payload.get('definition_test_state_sha256')) and
            _sha(payload.get('definition_restore_binding_sha256')), 'DEFINITION_BUNDLE_BINDING_REQUIRED')
        # Existing0329 sealed assignments contain the original eight check fields.
        # Candidate/bundle/school bindings come from native FK columns, outside
        # the hashed bundle. Embedding the bundle's own SHA would be recursive.
        assignment_fields = {'check_id', 'result', 'verifier_family', 'verifier_version',
            'execution_principal', 'assurance_level', 'evidence_sha256', 'canon_snapshot_sha256'}
        assignments = payload['checks']
        need(type(assignments) is list and len(assignments) == 16 and
            all(type(row) is dict and set(row) == assignment_fields for row in assignments),
            'ORIGINAL_EIGHT_ASSIGNMENT_FIELDS_REQUIRED')
        projected = [dict(row, candidate_payload_hash=request['candidate_payload_hash'],
            verification_bundle_sha256=request['verification_bundle_sha256'],
            school_key=request['scope']['school_key']) for row in assignments]
        need(same_json(sorted(projected, key=lambda row: row['check_id']),
                sorted(body['checks'], key=lambda row: row['check_id'])) and
            all(same_json(payload[k], body[k]) for k in ('source_binding', 'speaker_id', 'source_class',
                'semantic_scope', 'semantic_confidence')), 'RETAINED_SEMANTIC_AND_SOURCE_HEADER_REQUIRED')
        checks = rows(self.conn, '''SELECT check_id,result,verifier_family,verifier_version,execution_principal,
 assurance_level,evidence_sha256,canon_snapshot_sha256,candidate_payload_hash,verification_bundle_sha256,
 %s::text AS school_key FROM bidding.video_canon_ai_verification
 WHERE analysis_candidate_id=%s::uuid AND candidate_payload_hash=%s AND verification_bundle_sha256=%s ORDER BY check_id''',
            (request['scope']['school_key'], c['analysis_candidate_id'], request['candidate_payload_hash'],
                request['verification_bundle_sha256']))
        need(same_json(sorted(body['checks'], key=lambda x: x['check_id']), checks), 'GENUINE_RETAINED_CHECK_ROWS_REQUIRED')
        body['registry'] = rows(self.conn, '''SELECT database_role,verifier_family,allowed_check_ids,max_assurance_level,status
 FROM bidding.video_canon_verifier_registry ORDER BY database_role''')
        memberships = []
        for check in checks:
            lane = [r for r in body['registry'] if r['verifier_family'] == check['verifier_family']]
            need(len(lane) == 1, 'NATIVE_VERIFIER_REGISTRY_REQUIRED')
            row = one(self.conn, '''SELECT %s::text AS execution_principal,%s::text AS database_role,rolcanlogin,
 pg_has_role(rolname,%s,'MEMBER') AS is_member FROM pg_roles WHERE rolname=%s''',
                (check['execution_principal'], lane[0]['database_role'], lane[0]['database_role'], check['execution_principal']))
            need(row is not None, 'NATIVE_VERIFIER_PRINCIPAL_MISSING')
            if row not in memberships:
                memberships.append(row)
        body['memberships'] = memberships
        policies = rows(self.conn, '''SELECT %s::text AS school_key,source_sha256,video_file_id,teacher_ids,semantic_scopes,
 system_profile,learner_level,policy_version,authorization_evidence_sha256,status,valid_from,valid_to,retired_at
 FROM bidding.video_canon_source_policy WHERE school_id=%s::uuid AND source_id=%s::uuid
 AND source_sha256=%s AND video_file_id=%s AND system_profile=%s AND learner_level=%s''',
            (request['scope']['school_key'], self.native['scope']['school'], c['source_id'],
             body['source_binding']['source_sha256'], body['source_binding']['source_drive_id'],
             request['scope']['system_profile'], request['scope']['learner_level']))
        need(len(policies) == 1, 'NATIVE_SOURCE_POLICY_REQUIRED')
        body['source_policy'] = {k: iso(v) for k, v in policies[0].items()}
        body['source_status'] = c['source_status']
        if not archived:
            need(body['canon_snapshot_sha256'] == self.snapshot(), 'CURRENT_SNAPSHOT_MISMATCH')
            need(callable(getattr(self.context, 'test_context', None)), 'CURRENT_TEST_CONTEXT_UNBOUND')
            scope_hash = records.digest('DEFINITION_SCOPE_0_1', self.native['scope'])
            state = one(self.conn, '''SELECT canonical_json,document_sha256 FROM bidding.video_canon_definition_state
 WHERE school_id=%s::uuid AND candidate_payload_hash=%s AND scope_sha256=%s''',
                (self.native['scope']['school'], request['candidate_payload_hash'], scope_hash))
            need(state is not None, 'NATIVE_DEFINITION_TEST_STATE_REQUIRED')
            raw = state['canonical_json'].encode('utf-8')
            need(hashlib.sha256(raw).hexdigest() == state['document_sha256'], 'NATIVE_TEST_STATE_HASH')
            doc = records.parse_canonical(raw); validate_document(doc, self.native, 'OBSERVED')
            context = self.context.test_context(self.conn, deepcopy(self.native))
            test = evaluate_document(doc, context, epoch_us(self.now()))
            need(test['structural_eligibility'] is True and test['state_sha256'] == payload['definition_test_state_sha256'],
                'CURRENT_TEST_STATE_NOT_ELIGIBLE')
        return body

    def load(self, request):
        items = rows(self.conn, '''SELECT ki.knowledge_item_id::text,ki.stable_key,ki.knowledge_type,ki.title,ki.status,
 school.stable_name AS school_key FROM public.knowledge_item ki JOIN public.school school USING(school_id)
 WHERE ki.school_id=%s::uuid AND ki.stable_key=%s''', (self.native['scope']['school'], request['stable_key']))
        if not items:
            return None
        need(len(items) == 1, 'AMBIGUOUS_INITIAL_KI')
        item = items[0]
        versions = rows(self.conn, '''SELECT knowledge_version_id::text,knowledge_item_id::text,version_no,content,
 authority_class,review_status,status,bidding_system_key,agreement_scope,level_scope,effective_from,effective_to,
 method_version,provenance FROM public.knowledge_version WHERE knowledge_item_id=%s::uuid''', (item['knowledge_item_id'],))
        need(len(versions) == 1, 'EXISTING_OR_AMBIGUOUS_VERSION_NOT_INITIAL')
        version = {k: iso(v) for k, v in versions[0].items()}
        links = rows(self.conn, '''SELECT knowledge_version_id::text,source_id::text,relation_type,source_locator
 FROM public.knowledge_version_source WHERE knowledge_version_id=%s::uuid''', (version['knowledge_version_id'],))
        acts = rows(self.conn, '''SELECT canon_activation_id::text,knowledge_version_id::text,scope_key,valid_from,valid_to,
 approval_provenance,status FROM public.canon_activation WHERE knowledge_version_id=%s::uuid''', (version['knowledge_version_id'],))
        need(len(links) == len(acts) == 1, 'INITIAL_SOURCE_OR_ACTIVATION_AMBIGUOUS')
        return dict(item=item, version=version, source_link=links[0],
            activation={k: iso(v) for k, v in acts[0].items()})

    def conflict(self, request):
        return one(self.conn, '''SELECT EXISTS(SELECT 1 FROM public.knowledge_relation rel
 JOIN public.knowledge_version left_v ON left_v.knowledge_version_id=rel.from_version_id
 JOIN public.knowledge_item left_i ON left_i.knowledge_item_id=left_v.knowledge_item_id
 JOIN public.knowledge_version right_v ON right_v.knowledge_version_id=rel.to_version_id
 JOIN public.knowledge_item right_i ON right_i.knowledge_item_id=right_v.knowledge_item_id
 WHERE rel.school_id=%s::uuid AND rel.relation_type='contradicts'
 AND (left_i.stable_key=%s OR right_i.stable_key=%s)) AS conflict''',
            (self.native['scope']['school'], request['stable_key'], request['stable_key']))['conflict']

    def insert_initial(self, request, proposal, audit):
        need(not self.readonly, 'READ_LEASE_CANNOT_WRITE')
        c = self.candidate(request)
        need(same_json(c['payload'], proposal), 'PERSISTED_PROPOSAL_MISMATCH')
        item, version, link = (proposal[k] for k in ('knowledge_item', 'knowledge_version', 'knowledge_version_source'))
        ki = one(self.conn, '''INSERT INTO public.knowledge_item(school_id,stable_key,knowledge_type,title,status)
 VALUES(%s::uuid,%s,'skill',%s,'active') RETURNING knowledge_item_id::text''',
            (self.native['scope']['school'], item['stable_key'], item['title']))['knowledge_item_id']
        kv = one(self.conn, '''INSERT INTO public.knowledge_version(knowledge_item_id,version_no,content,authority_class,
 review_status,status,bidding_system_key,agreement_scope,level_scope,effective_from,effective_to,method_version,provenance)
 VALUES(%s::uuid,1,%s::jsonb,'school_canon','approved','approved',%s,%s::jsonb,%s::jsonb,%s::timestamptz,NULL,%s,%s::jsonb)
 RETURNING knowledge_version_id::text''', (ki, json.dumps(version['content']), request['scope']['system_profile'],
             json.dumps({'activation_scope': request['scope']['activation_scope']}),
             json.dumps({'learner_level': request['scope']['learner_level']}), audit['admitted_at'],
             version['method_version'], json.dumps(version['provenance'])))['knowledge_version_id']
        cur = self.conn.cursor()
        try:
            cur.execute('''INSERT INTO public.knowledge_version_source(knowledge_version_id,source_id,relation_type,source_locator)
 VALUES(%s::uuid,%s::uuid,'derived_from',%s::jsonb)''', (kv, c['source_id'], json.dumps(link['source_locator'])))
        finally:
            cur.close()
        one(self.conn, '''INSERT INTO public.canon_activation(knowledge_version_id,scope_key,valid_from,valid_to,
 approved_by_person_id,approval_provenance,status) VALUES(%s::uuid,%s,%s::timestamptz,NULL,NULL,%s::jsonb,'active')
 RETURNING canon_activation_id::text''', (kv, request['scope']['activation_scope'], audit['admitted_at'], json.dumps(audit)))
