"""Entirely synthetic SOURCE_ONLY data/adapters; never genuine Canon receipts."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import RLock
import hashlib
from bridge_contracts.card_play_definition_admission import _digest
from bridge_contracts.card_play_definition_admission import _gate
from bridge_contracts.video_canon_ai_promotion import REQUIRED_CHECKS, STATE_DEPENDENT_CHECKS
from database.definition_contract_store import need, OutcomeUnknown

NOW = datetime(2026, 10, 25, 8, tzinfo=timezone.utc)
sha = lambda x: hashlib.sha256(x.encode()).hexdigest()
SCOPE = dict(school_key='SOURCE_ONLY:school', system_profile='SOURCE_ONLY:profile',
    learner_level='SOURCE_ONLY:level', activation_scope='SOURCE_ONLY:scope')
BUNDLE = sha('SOURCE_ONLY:origin-bundle')
POST_BUNDLE = sha('SOURCE_ONLY:post-bundle')


def proposal():
    source = dict(source_candidate_id='SOURCE_ONLY:video', source_drive_id='SOURCE_ONLY:file',
        source_sha256=sha('SOURCE_ONLY:video-bytes'), source_segment_id='SOURCE_ONLY:segment',
        interval_seconds=[1, 2], raw_text='Synthetic definition.', raw_text_sha256=sha('Synthetic definition.'))
    definition = dict(schema='card-play-definition-proposal-v1', knowledge_domain='CARD_PLAY',
        knowledge_kind='DEFINITION', statement_text='Synthetic definition.', statement_sha256=sha('Synthetic definition.'),
        source_binding=source, owner_confirmation=dict(status='OWNER_CONFIRMED', message_ref='SOURCE_ONLY:message',
            record_sha256=sha('SOURCE_ONLY:owner'), confirmed_role='teacher', named_person=None, independent_audio_review=False),
        scope='DEFINITION_RECOGNITION_ONLY', requires_52_cards=False, semantic_confidence=None,
        source_authorization=None, admission=dict(status='BLOCKED', required_checks=sorted(REQUIRED_CHECKS), verification_receipts=[]))
    return dict(schema='knowledge-storage-skill-proposal-only-v1',
        status='PROPOSAL_NOT_INSERTABLE_WITHOUT_REAL_FOREIGN_KEYS', db_write_performed=False,
        canon_activation=None, accepted_readback=None,
        knowledge_item=dict(knowledge_item_id=None, school_id=None, stable_key='SOURCE_ONLY:skill',
            knowledge_type='skill', title='Synthetic fixture', status='candidate'),
        knowledge_version=dict(knowledge_version_id=None, knowledge_item_id=None, version_no=1,
            content=dict(definition_evidence=definition, recognition_scope_only=True),
            authority_class='research_candidate', review_status='unreviewed', status='candidate',
            bidding_system_key=None, agreement_scope={}, level_scope={}, effective_from=None, effective_to=None,
            method_version='SOURCE_ONLY:1', provenance={'fixture': 'SOURCE_ONLY'}),
        knowledge_version_source=dict(knowledge_version_id=None, source_id=None, relation_type='derived_from',
            source_locator={'fixture': 'SOURCE_ONLY:source-span'}))


def projection(p, bundle, snapshot):
    high = {'SEMANTIC_PARSE': 'semantic', 'BRIDGE_LOGIC': 'bridge', 'HIDDEN_INFORMATION_FIREWALL': 'firewall'}
    registry, members, checks = [], [], []
    for kind in ('semantic', 'bridge', 'firewall', 'control'):
        registry.append(dict(database_role='SOURCE_ONLY:role:' + kind, verifier_family='SOURCE_ONLY:family:' + kind,
            allowed_check_ids=[k for k in sorted(REQUIRED_CHECKS) if high.get(k, 'control') == kind],
            max_assurance_level='I3' if kind != 'control' else 'I1', status='active'))
        members.append(dict(execution_principal='SOURCE_ONLY:login:' + kind,
            database_role='SOURCE_ONLY:role:' + kind, rolcanlogin=True, is_member=True))
    for check in sorted(REQUIRED_CHECKS):
        kind = high.get(check, 'control')
        checks.append(dict(check_id=check, result='PASS', verifier_family='SOURCE_ONLY:family:' + kind,
            verifier_version='SOURCE_ONLY:1', execution_principal='SOURCE_ONLY:login:' + kind,
            assurance_level='I2' if kind != 'control' else 'I1', evidence_sha256=sha('SOURCE_ONLY:' + check),
            canon_snapshot_sha256=snapshot if check in STATE_DEPENDENT_CHECKS else None,
            candidate_payload_hash=_digest(p), verification_bundle_sha256=bundle, school_key=SCOPE['school_key']))
    definition = p['knowledge_version']['content']['definition_evidence']; source = definition['source_binding']
    return dict(candidate_payload_hash=_digest(p), verification_bundle_sha256=bundle, scope=deepcopy(SCOPE),
        source_binding=deepcopy(source), source_status='active', speaker_id='SOURCE_ONLY:teacher',
        source_class='SCHOOL_PRIMARY_EVIDENCE', semantic_scope=definition['scope'], semantic_confidence=0.95,
        canon_snapshot_sha256=snapshot, checks=checks, registry=registry, memberships=members,
        source_policy=dict(school_key=SCOPE['school_key'], source_sha256=source['source_sha256'],
            video_file_id=source['source_drive_id'], teacher_ids=['SOURCE_ONLY:teacher'], semantic_scopes=[definition['scope']],
            system_profile=SCOPE['system_profile'], learner_level=SCOPE['learner_level'],
            policy_version='school-video-auto-canon-v1', authorization_evidence_sha256=sha('SOURCE_ONLY:source-use'),
            status='active', valid_from=(NOW-timedelta(hours=1)).isoformat(),
            valid_to=(NOW+timedelta(hours=1)).isoformat(), retired_at=None))


class MemoryInitial:
    mode = 'SOURCE_ONLY'; mutation_capable = True
    def __init__(self, p):
        self.p = deepcopy(p); self.record = None; self.current = sha('SOURCE_ONLY:pre-state')
        self.bundles = {BUNDLE: projection(p, BUNDLE, self.current)}
        self.lock = RLock(); self.writes = 0; self.now_value = NOW
        self.write_authorized = True; self.read_authorized = True; self.origin_authorized = True
        self.live_source = True; self.conflicted = False; self.fail_step = None
        self.fail_exit = False; self.lose_ack = False; self.suppress = False
        self.on_evidence = None; self.on_exit = None; self.evidence_calls = 0

    def post_bundle(self):
        self.bundles[POST_BUNDLE] = projection(self.p, POST_BUNDLE, self.current)

    @contextmanager
    def lease(self, request, readonly):
        with self.lock:
            old = deepcopy((self.record, self.current, self.writes)); tx = MemoryTx(self, readonly)
            try:
                yield tx
                if self.fail_exit:
                    raise RuntimeError('SOURCE_ONLY:lease exit')
                expected_snapshot = self.current
                if self.on_exit:
                    self.on_exit()
                tx.authorize(request, write=not readonly)
                tx.source_id(request)
                need(self.current == expected_snapshot, 'SOURCE_ONLY:CONCURRENT_CANON_CHANGE')
                _gate(self.bundles[request['verification_bundle_sha256']], request,
                    self.p['knowledge_version']['content']['definition_evidence'], self.now_value)
                if self.lose_ack:
                    raise OutcomeUnknown('SOURCE_ONLY:lost commit ACK')
                tx.finished = True
            except OutcomeUnknown:
                raise
            except BaseException:
                self.record, self.current, self.writes = old
                if not self.suppress:
                    raise

    def transaction(self, request):
        return self.lease(request, False)

    def observer(self, request):
        return self.lease(request, True)


class MemoryTx:
    mode = 'SOURCE_ONLY'; finished = False
    def __init__(self, backend, readonly):
        self.b, self.readonly = backend, readonly

    def now(self):
        return self.b.now_value

    def authorize(self, request, *, write):
        need(write is (not self.readonly) and
            (self.b.write_authorized if write else self.b.read_authorized), 'SOURCE_ONLY:ACTOR_REVOKED')
        return 'SOURCE_ONLY:promoter' if write else 'SOURCE_ONLY:reader'

    def origin_actor(self, actor):
        need(actor == 'SOURCE_ONLY:promoter' and self.b.origin_authorized, 'SOURCE_ONLY:ORIGIN_ACTOR_REVOKED')

    def source_id(self, request):
        need(self.b.live_source and request['candidate_payload_hash'] == _digest(self.b.p), 'SOURCE_ONLY:SOURCE_REVOKED')
        return 'SOURCE_ONLY:source-row'

    def snapshot(self):
        return self.b.current

    def evidence(self, request, *, archived):
        self.source_id(request); self.b.evidence_calls += 1
        if self.b.on_evidence:
            self.b.on_evidence(self.b, archived)
        need(request['verification_bundle_sha256'] in self.b.bundles, 'SOURCE_ONLY:GENUINE_PROVIDER_MISSING')
        return deepcopy(self.b.bundles[request['verification_bundle_sha256']])

    def load(self, request):
        return deepcopy(self.b.record)

    def conflict(self, request):
        return self.b.conflicted

    def insert_initial(self, request, p, audit):
        need(not self.readonly and self.b.record is None and p == self.b.p, 'SOURCE_ONLY:INITIAL_CONFLICT')
        for step in range(1, 5):
            self.b.writes += 1
            if step == self.b.fail_step:
                raise RuntimeError('SOURCE_ONLY:partial write fault')
        item = dict(p['knowledge_item'], knowledge_item_id='SOURCE_ONLY:ki-row', school_key=SCOPE['school_key'], status='active')
        item.pop('school_id')
        version = dict(p['knowledge_version'], knowledge_item_id=item['knowledge_item_id'],
            knowledge_version_id='SOURCE_ONLY:kv-row', authority_class='school_canon', review_status='approved',
            status='approved', bidding_system_key=SCOPE['system_profile'],
            agreement_scope={'activation_scope': SCOPE['activation_scope']},
            level_scope={'learner_level': SCOPE['learner_level']}, effective_from=audit['admitted_at'])
        link = dict(p['knowledge_version_source'], knowledge_version_id=version['knowledge_version_id'], source_id=self.source_id(request))
        activation = dict(canon_activation_id='SOURCE_ONLY:activation-row', knowledge_version_id=version['knowledge_version_id'],
            scope_key=SCOPE['activation_scope'], valid_from=audit['admitted_at'], valid_to=None,
            status='active', approval_provenance=deepcopy(audit))
        self.b.record = dict(item=item, version=version, source_link=link, activation=activation)
        self.b.current = sha('SOURCE_ONLY:post-state')
