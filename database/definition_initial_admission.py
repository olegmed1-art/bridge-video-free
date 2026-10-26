"""Initial KI/KV admission and current accepted read; no default authority.

This consumes existing authenticated evidence. It never issues verifier receipts.
SOURCE_ONLY backends exercise the protocol but disclose no accepted IDs/packet.
"""
from copy import deepcopy
from datetime import datetime, timezone
from bridge_contracts.card_play_definition import validate_card_play_definition
from bridge_contracts.card_play_definition_admission import _gate, _digest, _sha, _text, _moment, _Rejected
from database.definition_contract_store import StoreDenied, OutcomeUnknown, need

SCHEMA = 'definition-initial-admission-v1'
SCOPE_FIELDS = {'school_key', 'system_profile', 'learner_level', 'activation_scope'}
PROPOSAL_FIELDS = {'schema', 'status', 'knowledge_item', 'knowledge_version',
    'knowledge_version_source', 'canon_activation', 'accepted_readback', 'db_write_performed'}


def result(status, reason, mode=None, **values):
    return dict(status=status, reason=reason, source_only=mode != 'OBSERVED',
        admission_allowed=False, ready=False, production_ready=False, FULL_CALL='ABSTAIN', session_binding=None,
        genuine_Canon_checks_created=0, receipt=None, teacher_packet=None,
        accepted_selectors=None, **values)


def same_json(left, right):
    """Preserve JSON types: Python False==0 / True==1 is not source identity."""
    return _digest(left) == _digest(right)


def evidence_gate(body, request, definition, now):
    _gate(body, request, definition, now)
    need(same_json(body['source_binding'], definition['source_binding']) and
        same_json(body['scope'], request['scope']), 'TYPED_SOURCE_OR_SCOPE_BINDING')


def proposal_check(proposal, scope):
    need(type(scope) is dict and set(scope) == SCOPE_FIELDS and
        all(_text(v) for v in scope.values()), 'HOST_SCOPE_REQUIRED')
    need(type(proposal) is dict and set(proposal) == PROPOSAL_FIELDS and
        proposal['schema'] == 'knowledge-storage-skill-proposal-only-v1' and
        proposal['status'] == 'PROPOSAL_NOT_INSERTABLE_WITHOUT_REAL_FOREIGN_KEYS' and
        proposal['db_write_performed'] is False and proposal['canon_activation'] is None and
        proposal['accepted_readback'] is None, 'UNACCEPTED_PROPOSAL_REQUIRED')
    item, version, link = (proposal[k] for k in
        ('knowledge_item', 'knowledge_version', 'knowledge_version_source'))
    need(set(item) == {'knowledge_item_id', 'school_id', 'stable_key', 'knowledge_type', 'title', 'status'} and
        item['knowledge_item_id'] is None and item['school_id'] is None and
        item['knowledge_type'] == 'skill' and item['status'] == 'candidate' and
        _text(item['stable_key']) and _text(item['title']), 'INITIAL_SKILL_ITEM_REQUIRED')
    need(set(version) == {'knowledge_version_id', 'knowledge_item_id', 'version_no', 'content',
        'authority_class', 'review_status', 'status', 'bidding_system_key', 'agreement_scope',
        'level_scope', 'effective_from', 'effective_to', 'method_version', 'provenance'} and
        version['knowledge_version_id'] is None and version['knowledge_item_id'] is None and
        type(version['version_no']) is int and version['version_no'] == 1 and
        version['authority_class'] == 'research_candidate' and version['review_status'] == 'unreviewed' and
        version['status'] == 'candidate' and _text(version['method_version']), 'INITIAL_VERSION_REQUIRED')
    need(version['bidding_system_key'] in (None, scope['system_profile']) and
        version['agreement_scope'] == {} and version['level_scope'] == {} and
        version['effective_from'] is None and version['effective_to'] is None,
        'EXPLICIT_INITIAL_SCOPE_REQUIRED')
    need(set(link) == {'knowledge_version_id', 'source_id', 'relation_type', 'source_locator'} and
        link['knowledge_version_id'] is None and link['source_id'] is None and
        link['relation_type'] == 'derived_from', 'SOURCE_LINK_REQUIRED')
    definition = version['content']['definition_evidence']
    validate_card_play_definition(definition)
    need(type(version['provenance']) is dict and type(link['source_locator']) is dict,
        'PROVENANCE_REQUIRED')
    need(len(__import__('json').dumps(proposal, ensure_ascii=False).encode('utf-8')) <= 524288,
        'PROPOSAL_BUDGET')
    return definition


def validate_record(record, proposal, request, *, now, current=False):
    """Check actual joined KI/KV/link/activation, not an approval JSON flag."""
    need(type(record) is dict and set(record) == {'item', 'version', 'source_link', 'activation'},
        'ACCEPTED_NATIVE_ROWS_REQUIRED')
    item, version, link, activation = (record[k] for k in ('item', 'version', 'source_link', 'activation'))
    pitem, pversion, plink = (proposal[k] for k in
        ('knowledge_item', 'knowledge_version', 'knowledge_version_source'))
    need(item['stable_key'] == pitem['stable_key'] and item['knowledge_type'] == 'skill' and
        item['title'] == pitem['title'] and item['school_key'] == request['scope']['school_key'] and
        item['status'] == 'active', 'KI_BINDING_OR_RETIREMENT')
    need(version['knowledge_item_id'] == item['knowledge_item_id'] and
        type(version['version_no']) is int and version['version_no'] == 1 and
        same_json(version['content'], pversion['content']) and same_json(version['provenance'], pversion['provenance']) and
        version['authority_class'] == 'school_canon' and version['review_status'] == 'approved' and
        version['status'] == 'approved' and version['method_version'] == pversion['method_version'] and
        version['bidding_system_key'] == request['scope']['system_profile'] and
        same_json(version['agreement_scope'], {'activation_scope': request['scope']['activation_scope']}) and
        same_json(version['level_scope'], {'learner_level': request['scope']['learner_level']}),
        'KV_BINDING_OR_REVOCATION')
    need(link['knowledge_version_id'] == version['knowledge_version_id'] and
        link['relation_type'] == 'derived_from' and same_json(link['source_locator'], plink['source_locator']) and
        activation['knowledge_version_id'] == version['knowledge_version_id'] and
        activation['scope_key'] == request['scope']['activation_scope'] and
        activation['status'] == 'active', 'LINK_OR_ACTIVATION_REVOKED')
    need(all(_text(obj[key]) for obj, key in ((item, 'knowledge_item_id'),
        (version, 'knowledge_version_id'), (link, 'source_id'), (activation, 'canon_activation_id'))),
        'NATIVE_IDS_REQUIRED')
    audit = activation['approval_provenance']
    need(type(audit) is dict and set(audit) == {'schema', 'request_key', 'actor', 'candidate_payload_hash',
        'origin_bundle_sha256', 'origin_projection_sha256', 'origin_snapshot_sha256',
        'effect_sha256', 'scope', 'admitted_at'} and audit['schema'] == SCHEMA and
        audit['request_key'] == request['request_key'] and _text(audit['actor']) and
        audit['candidate_payload_hash'] == request['candidate_payload_hash'] and
        same_json(audit['scope'], request['scope']) and all(_sha(audit[k]) for k in
        ('origin_bundle_sha256', 'origin_projection_sha256', 'origin_snapshot_sha256', 'effect_sha256')),
        'ACCEPTANCE_AUDIT_BINDING')
    start = _moment(activation['valid_from'])
    end = activation['valid_to']
    need(_moment(audit['admitted_at']) == start and
        version['effective_from'] == activation['valid_from'] and
        version['effective_to'] == end and (end is None or start < _moment(end)),
        'ACCEPTANCE_INTERVAL_BINDING')
    if current:
        need(start <= now and (end is None or now < _moment(end)), 'ACTIVATION_NOT_CURRENT')
    return audit


class DefinitionInitialAdmission:
    """Private host entrypoints. Backend/provider is not a request argument."""
    def __init__(self, *, backend=None, scope=None, clock=None):
        self.backend, self.scope = backend, deepcopy(scope)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _backend(self, method):
        need(self.backend is not None and callable(getattr(self.backend, method, None)),
            'ADMISSION_PROVIDER_UNBOUND' if method == 'transaction' else 'ACCEPTED_READ_PROVIDER_UNBOUND')
        if getattr(self.backend, 'mode', None) != 'SOURCE_ONLY':
            # OBSERVED is available only through this exact native source adapter,
            # not an arbitrary host object's self-declared mode or client JSON.
            from database.definition_initial_postgres import DefinitionInitialPostgres
            need(type(self.backend) is DefinitionInitialPostgres, 'NATIVE_ADMISSION_BACKEND_REQUIRED')

    def _mode(self, tx):
        need(tx.mode in ('SOURCE_ONLY', 'OBSERVED'), 'BACKEND_MODE_INVALID')
        if tx.mode == 'OBSERVED':
            from database.definition_initial_postgres import DefinitionInitialPostgres, InitialTransaction
            need(type(self.backend) is DefinitionInitialPostgres and type(tx) is InitialTransaction and
                tx.backend is self.backend, 'NATIVE_ADMISSION_TRANSACTION_REQUIRED')
        return tx.mode

    def _request(self, proposal, bundle, key, operation):
        definition = proposal_check(proposal, self.scope)
        need(_sha(bundle) and _text(key) and len(key) <= 200, 'BUNDLE_OR_COMMAND_KEY_REQUIRED')
        return dict(operation=operation, candidate_payload_hash=_digest(proposal),
            verification_bundle_sha256=bundle, request_key=key, scope=deepcopy(self.scope),
            stable_key=proposal['knowledge_item']['stable_key']), definition

    def _origin(self, tx, request, proposal, record, definition):
        audit = validate_record(record, proposal, request, now=tx.now(), current=True)
        need(record['source_link']['source_id'] == tx.source_id(request), 'PERSISTED_SOURCE_MISMATCH')
        origin_request = dict(request, verification_bundle_sha256=audit['origin_bundle_sha256'])
        # The provider must resolve actual retained rows; audit metadata is not a receipt.
        body = deepcopy(tx.evidence(origin_request, archived=True))
        evidence_gate(body, origin_request, definition, _moment(audit['admitted_at']))
        need(_digest(body) == audit['origin_projection_sha256'] and
            body['canon_snapshot_sha256'] == audit['origin_snapshot_sha256'], 'ORIGIN_RECEIPT_BINDING')
        expected = _digest(dict(schema=SCHEMA, proposal=proposal, scope=request['scope'],
            origin_bundle_sha256=audit['origin_bundle_sha256'], request_key=request['request_key'],
            actor=audit['actor']))
        need(audit['effect_sha256'] == expected, 'ACCEPTANCE_EFFECT_HASH')
        tx.origin_actor(audit['actor'])
        return audit

    def admit(self, proposal, *, verification_bundle_sha256, request_key):
        mode = None; complete = False; tx = None; effect = None
        try:
            self._backend('transaction')
            need(getattr(self.backend, 'mutation_capable', False) is True, 'ADMISSION_PROVIDER_UNBOUND')
            request, definition = self._request(proposal, verification_bundle_sha256, request_key,
                'INITIAL_DEFINITION_ACCEPT')
            frozen, original = deepcopy(proposal), _digest(proposal)
            scope_before = deepcopy(self.scope)
            with self.backend.transaction(deepcopy(request)) as tx:
                mode = self._mode(tx)
                actor = tx.authorize(request, write=True)
                effect = _digest(dict(schema=SCHEMA, proposal=frozen, scope=request['scope'],
                    origin_bundle_sha256=verification_bundle_sha256, request_key=request_key, actor=actor))
                prior = tx.load(request)
                if prior is not None:
                    audit = self._origin(tx, request, frozen, prior, definition)
                    need(audit['effect_sha256'] == effect and audit['actor'] == actor,
                        'IDEMPOTENCY_CONFLICT')
                    status = 'ALREADY_COMMITTED_REVALIDATION_REQUIRED'
                else:
                    first = deepcopy(tx.evidence(request, archived=False))
                    evidence_gate(first, request, definition, tx.now())
                    need(first['canon_snapshot_sha256'] == tx.snapshot(), 'CURRENT_SNAPSHOT_MISMATCH')
                    second = deepcopy(tx.evidence(request, archived=False))
                    evidence_gate(second, request, definition, tx.now())
                    need(_digest(first) == _digest(second), 'EVIDENCE_CHANGED_BEFORE_WRITE')
                    need(not tx.conflict(request), 'CANON_CONFLICT')
                    audit = dict(schema=SCHEMA, request_key=request_key, actor=actor,
                        candidate_payload_hash=original, origin_bundle_sha256=verification_bundle_sha256,
                        origin_projection_sha256=_digest(second),
                        origin_snapshot_sha256=second['canon_snapshot_sha256'],
                        effect_sha256=effect, scope=deepcopy(request['scope']), admitted_at=tx.now().isoformat())
                    tx.insert_initial(request, frozen, audit)
                    record = tx.load(request)
                    validate_record(record, frozen, request, now=tx.now(), current=True)
                    need(record['source_link']['source_id'] == tx.source_id(request), 'PERSISTED_SOURCE_MISMATCH')
                    need(not tx.conflict(request), 'CANON_CONFLICT')
                    # Revalidate archive validity time; context fence protects live policy/registry/source.
                    evidence_gate(second, request, definition, tx.now())
                    status = 'COMMITTED_REVALIDATION_REQUIRED'
                need(_digest(proposal) == original and self.scope == scope_before, 'INPUT_OR_HOST_SCOPE_CHANGED')
                complete = True
            need(complete and tx.finished is True, 'ADMISSION_LEASE_INCOMPLETE')
            return result('SOURCE_ONLY_ADMISSION_EXERCISE' if mode == 'SOURCE_ONLY' else status,
                status, mode, storage_effect_sha256=effect)
        except OutcomeUnknown:
            return result('OUTCOME_UNKNOWN', 'RECONCILE_SAME_KEY_EFFECT_NO_BLIND_RETRY', mode,
                storage_effect_sha256=effect)
        except (StoreDenied, _Rejected) as exc:
            return result('ABSTAIN', str(exc), mode)
        except Exception:
            return result('ABSTAIN', 'ADMISSION_OR_AUTHORITY_UNAVAILABLE', mode)

    def read_accepted(self, proposal, *, verification_bundle_sha256, request_key):
        mode = None; complete = False; tx = None
        try:
            self._backend('observer')
            request, definition = self._request(proposal, verification_bundle_sha256, request_key,
                'READ_ACCEPTED_DEFINITION')
            frozen, original, scope_before = deepcopy(proposal), _digest(proposal), deepcopy(self.scope)
            with self.backend.observer(deepcopy(request)) as tx:
                mode = self._mode(tx)
                tx.authorize(request, write=False)
                first = deepcopy(tx.load(request)); need(first is not None, 'ACCEPTED_VERSION_NOT_FOUND')
                self._origin(tx, request, frozen, first, definition)
                body = deepcopy(tx.evidence(request, archived=False))
                evidence_gate(body, request, definition, tx.now())
                need(body['canon_snapshot_sha256'] == tx.snapshot(), 'CURRENT_SNAPSHOT_MISMATCH')
                need(not tx.conflict(request), 'CANON_CONFLICT')
                second = deepcopy(tx.load(request))
                validate_record(second, frozen, request, now=tx.now(), current=True)
                again = deepcopy(tx.evidence(request, archived=False))
                evidence_gate(again, request, definition, tx.now())
                need(_digest(first) == _digest(second) and _digest(body) == _digest(again) and
                    again['canon_snapshot_sha256'] == tx.snapshot(), 'ACCEPTED_READ_CHANGED')
                need(second['source_link']['source_id'] == tx.source_id(request), 'PERSISTED_SOURCE_MISMATCH')
                need(_digest(proposal) == original and self.scope == scope_before, 'INPUT_OR_HOST_SCOPE_CHANGED')
                complete = True
            need(complete and tx.finished is True, 'ACCEPTED_READ_LEASE_INCOMPLETE')
            validate_record(second, frozen, request, now=self.clock(), current=True)
            evidence_gate(again, request, definition, self.clock())
            need(_digest(proposal) == original and self.scope == scope_before, 'INPUT_OR_HOST_SCOPE_CHANGED')
            if mode == 'SOURCE_ONLY':
                return result('SOURCE_ONLY_ACCEPTED_READ_EXERCISE', 'INTERFACE_ONLY_NO_ACCEPTED_PACKET', mode)
            answer = result('ACCEPTED_READBACK', 'CURRENT_GENUINE_EVIDENCE_AND_NATIVE_ROWS_BOUND', mode)
            answer['accepted_selectors'] = {key: second[part][key] for part, key in
                (('item', 'knowledge_item_id'), ('version', 'knowledge_version_id'), ('activation', 'canon_activation_id'))}
            answer['teacher_packet'] = dict(authority_lane='ACTIVE_SCHOOL_CANON', scope=request['scope'],
                selectors=deepcopy(answer['accepted_selectors']), content=deepcopy(second['version']['content']),
                current_bundle_sha256=verification_bundle_sha256, lesson_binding=None)
            return answer
        except OutcomeUnknown:
            return result('OUTCOME_UNKNOWN', 'ACCEPTED_READ_COMPLETION_UNCONFIRMED', mode)
        except (StoreDenied, _Rejected) as exc:
            return result('ABSTAIN', str(exc), mode)
        except Exception:
            return result('ABSTAIN', 'ACCEPTED_READ_OR_AUTHORITY_UNAVAILABLE', mode)
