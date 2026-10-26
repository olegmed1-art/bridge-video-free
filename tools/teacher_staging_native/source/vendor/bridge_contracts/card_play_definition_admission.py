"""Read-only consumer of the existing sixteen Canon receipt/registry gates.

No issuer, SQL, receipt creation, DB IDs, promotion command or activation. The
host must supply an authenticated CURRENT read lease, outside request JSON.
Joined read projections are observations of existing rows, not new receipts.
The live definition provider is not implemented or installed by this module.
"""
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Callable

from bridge_contracts.card_play_definition import validate_card_play_definition
from bridge_contracts.canon_source_domain_policy import evaluate_canon_source_domain_policy
from bridge_contracts.video_canon_ai_promotion import REQUIRED_CHECKS, STATE_DEPENDENT_CHECKS

_SHA = re.compile(r'[0-9a-f]{64}')
_RANK = {'I0': 0, 'I1': 1, 'I2': 2, 'I3': 3}
_HIGH = ('SEMANTIC_PARSE', 'BRIDGE_LOGIC', 'HIDDEN_INFORMATION_FIREWALL')


@dataclass(frozen=True)
class CanonReceiptReadBinding:
    """Server wiring only, following the existing trusted-resolver pattern.

    lease(request) yields a callable CURRENT reader. The host authenticates its
    connection/target and holds/rechecks policy, registry and memberships until
    lease exit. A callable or this Python object is NOT proof of that host setup.
    No production binding is supplied here. SOURCE_ONLY doubles never authorize.
    """
    lease: Callable[[dict], AbstractContextManager]
    source_only: bool = True


class _Rejected(ValueError):
    pass


def _need(ok, code):
    if not ok:
        raise _Rejected(code)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _sha(value):
    return type(value) is str and bool(_SHA.fullmatch(value))


def _text(value):
    return type(value) is str and bool(value.strip())


def _moment(value):
    _need(type(value) is str, 'VALIDITY_TIME_INVALID')
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise _Rejected('VALIDITY_TIME_INVALID') from exc
    _need(moment.tzinfo is not None, 'VALIDITY_TIME_INVALID')
    return moment


def _gate(body, request, definition, now):
    """Validate a host-read projection; caller JSON alone never calls this gate."""
    fields = {'candidate_payload_hash', 'verification_bundle_sha256', 'scope',
        'source_binding', 'source_policy', 'speaker_id', 'source_class', 'semantic_scope',
        'semantic_confidence', 'canon_snapshot_sha256', 'checks', 'registry', 'memberships', 'source_status'}
    _need(type(body) is dict and set(body) == fields, 'RECEIPT_READ_FIELDS')
    _need(body['candidate_payload_hash'] == request['candidate_payload_hash']
        and body['verification_bundle_sha256'] == request['verification_bundle_sha256']
        and body['scope'] == request['scope'], 'CANDIDATE_BUNDLE_OR_SCOPE_MISMATCH')
    _need(body['source_binding'] == definition['source_binding'], 'SOURCE_PROVENANCE_MISMATCH')
    _need(body['source_status'] == 'active', 'SOURCE_REVOKED')
    scope, policy = request['scope'], body['source_policy']
    policy_fields = {'school_key', 'source_sha256', 'video_file_id', 'teacher_ids', 'semantic_scopes',
        'system_profile', 'learner_level', 'policy_version', 'authorization_evidence_sha256',
        'status', 'valid_from', 'valid_to', 'retired_at'}
    _need(type(policy) is dict and set(policy) == policy_fields, 'SOURCE_POLICY_FIELDS')
    _need(policy['status'] == 'active' and policy['retired_at'] is None, 'SOURCE_POLICY_REVOKED')
    _need(_moment(policy['valid_from']) <= now
        and (policy['valid_to'] is None or now < _moment(policy['valid_to'])), 'SOURCE_POLICY_EXPIRED')
    _need(policy['school_key'] == scope['school_key']
        and policy['source_sha256'] == definition['source_binding']['source_sha256']
        and policy['video_file_id'] == definition['source_binding']['source_drive_id']
        and policy['system_profile'] == scope['system_profile']
        and policy['learner_level'] == scope['learner_level']
        and policy['policy_version'] == 'school-video-auto-canon-v1'
        and _sha(policy['authorization_evidence_sha256']), 'SOURCE_POLICY_BINDING')
    _need(type(policy['teacher_ids']) is list and all(_text(x) for x in policy['teacher_ids'])
        and _text(body['speaker_id']) and body['speaker_id'] in policy['teacher_ids']
        and type(policy['semantic_scopes']) is list and all(_text(x) for x in policy['semantic_scopes'])
        and body['semantic_scope'] == definition['scope']
        and body['semantic_scope'] in policy['semantic_scopes'], 'TEACHER_OR_SEMANTIC_SCOPE')
    _need(body['source_class'] == 'SCHOOL_PRIMARY_EVIDENCE', 'SCHOOL_PRIMARY_REQUIRED')
    confidence = body['semantic_confidence']
    _need(type(confidence) in (int, float) and math.isfinite(confidence)
        and 0.95 <= confidence <= 1, 'SEMANTIC_CONFIDENCE')
    _need(_sha(body['canon_snapshot_sha256']), 'CANON_SNAPSHOT_REQUIRED')
    _need(type(body['registry']) is list and type(body['memberships']) is list, 'REGISTRY_READ_REQUIRED')
    registry = {}
    for lane in body['registry']:
        _need(type(lane) is dict and set(lane) == {'database_role', 'verifier_family',
            'allowed_check_ids', 'max_assurance_level', 'status'}, 'REGISTRY_FIELDS')
        _need(_text(lane['database_role']) and _text(lane['verifier_family'])
            and type(lane['allowed_check_ids']) is list
            and all(_text(x) for x in lane['allowed_check_ids'])
            and lane['max_assurance_level'] in ('I1', 'I2', 'I3'), 'REGISTRY_FIELDS')
        _need(lane['verifier_family'] not in registry
            and lane['database_role'] not in {r['database_role'] for r in registry.values()}, 'REGISTRY_AMBIGUOUS')
        registry[lane['verifier_family']] = lane
    members = {}
    for member in body['memberships']:
        _need(type(member) is dict and set(member) == {'execution_principal', 'database_role',
            'rolcanlogin', 'is_member'}, 'MEMBERSHIP_FIELDS')
        key = (member['execution_principal'], member['database_role'])
        _need(all(_text(x) for x in key) and key not in members, 'MEMBERSHIP_AMBIGUOUS')
        members[key] = member
    _need(type(body['checks']) is list, 'ALL_16_CHECKS_REQUIRED')
    seen = {}
    row_fields = {'check_id', 'result', 'verifier_family', 'verifier_version', 'execution_principal',
        'assurance_level', 'evidence_sha256', 'canon_snapshot_sha256',
        'candidate_payload_hash', 'verification_bundle_sha256', 'school_key'}
    for row in body['checks']:
        _need(type(row) is dict and set(row) == row_fields, 'VERIFICATION_ROW_FIELDS')
        check = row['check_id']
        _need(_text(check) and check in REQUIRED_CHECKS and check not in seen, 'ALL_16_CHECKS_REQUIRED')
        _need(row['result'] == 'PASS' and _text(row['verifier_version'])
            and _sha(row['evidence_sha256']), 'CHECK_NOT_PASSED')
        _need(row['candidate_payload_hash'] == request['candidate_payload_hash']
            and row['verification_bundle_sha256'] == request['verification_bundle_sha256']
            and row['school_key'] == scope['school_key'], 'VERIFICATION_ROW_BINDING')
        lane = registry.get(row['verifier_family'])
        _need(lane is not None and lane['status'] == 'active'
            and check in lane['allowed_check_ids'], 'VERIFIER_REVOKED_OR_WRONG_LANE')
        assurance = row['assurance_level']
        _need(assurance in _RANK and _RANK[assurance] <= _RANK[lane['max_assurance_level']], 'ASSURANCE_INVALID')
        member = members.get((row['execution_principal'], lane['database_role']))
        _need(member is not None and member['rolcanlogin'] is True and member['is_member'] is True,
            'VERIFIER_PRINCIPAL_REVOKED')
        if check in STATE_DEPENDENT_CHECKS:
            _need(row['canon_snapshot_sha256'] == body['canon_snapshot_sha256'], 'STALE_CANON_SNAPSHOT')
        else:
            _need(row['canon_snapshot_sha256'] is None, 'STATELESS_SNAPSHOT_CLAIM')
        seen[check] = row
    _need(set(seen) == REQUIRED_CHECKS, 'ALL_16_CHECKS_REQUIRED')
    _need(all(seen[k]['assurance_level'] in ('I2', 'I3') for k in _HIGH), 'HIGH_ASSURANCE_REQUIRED')
    _need(seen['SEMANTIC_PARSE']['verifier_family'] != seen['BRIDGE_LOGIC']['verifier_family']
        and len({seen[k]['execution_principal'] for k in _HIGH}) == 3, 'INDEPENDENT_VERIFIERS_REQUIRED')
    decision = evaluate_canon_source_domain_policy('CARD_PLAY', body['source_class'], confidence,
        {'asr_verified': seen['TRANSCRIPT_BINDING']['result'] == 'PASS',
         'slip_checked': seen['SEMANTIC_PARSE']['result'] == seen['BRIDGE_LOGIC']['result'] == 'PASS',
         'interpretation_verified': seen['SEMANTIC_PARSE']['result'] == 'PASS',
         'context_verified': seen['SOURCE_BINDING']['result'] == 'PASS',
         'canon_conflict_free': seen['CANON_CONFLICT_SCAN']['result'] == 'PASS',
         'provenance_bound': seen['SOURCE_AUTHORITY']['result'] == 'PASS'})
    _need(decision['auto_canon_allowed'] is True, 'SOURCE_DOMAIN_POLICY_REJECTED')


def _result(reason, *, source_only=True, evidence_bound=False):
    return {'status': 'SOURCE_ONLY_INTERFACE_MATCH' if evidence_bound and source_only else
            'RECEIPT_READBACK_BOUND' if evidence_bound else 'ABSTAIN',
        'reason': reason, 'source_only': source_only,
        'admission_evidence_verified': evidence_bound and not source_only,
        'interface_checks_matched': 16 if evidence_bound else 0,
        'Canon_checks_closed_by_this_run': 0, 'admission_allowed': False,
        'receipt': None, 'record': None, 'activation': None, 'ready': False,
        'production_ready': False, 'FULL_CALL': 'ABSTAIN', 'session_binding': None}


class DefinitionAdmissionConsumer:
    """Recheck current trusted evidence per call; never cache an admission token."""
    def __init__(self, *, reader=None, scope=None, clock=None):
        self.reader = reader
        self.scope = deepcopy(scope)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def check(self, proposal, *, verification_bundle_sha256):
        source_only = True
        try:
            _need(type(self.reader) is CanonReceiptReadBinding and callable(self.reader.lease),
                'AUTHENTICATED_RECEIPT_READER_UNBOUND')
            _need(type(self.reader.source_only) is bool, 'READER_MODE_INVALID')
            source_only = self.reader.source_only
            _need(type(self.scope) is dict and set(self.scope) == {
                'school_key', 'system_profile', 'learner_level', 'activation_scope'}
                and all(_text(x) for x in self.scope.values()), 'HOST_SCOPE_REQUIRED')
            original_sha = _digest(proposal)
            frozen = deepcopy(proposal)
            definition = frozen['knowledge_version']['content']['definition_evidence']
            validate_card_play_definition(definition)
            item, version, link = (frozen[key] for key in ('knowledge_item', 'knowledge_version', 'knowledge_version_source'))
            _need(frozen.get('db_write_performed') is False
                and 'canon_activation' in frozen and frozen['canon_activation'] is None
                and 'accepted_readback' in frozen and frozen['accepted_readback'] is None
                and version.get('authority_class') == 'research_candidate'
                and version.get('review_status') == 'unreviewed' and version.get('status') == 'candidate'
                and item.get('status') == 'candidate'
                and all(k in obj and obj[k] is None for obj, keys in (
                    (item, ('knowledge_item_id', 'school_id')), (version, ('knowledge_version_id', 'knowledge_item_id')),
                    (link, ('knowledge_version_id', 'source_id'))) for k in keys), 'UNACCEPTED_PROPOSAL_REQUIRED')
            _need(_sha(verification_bundle_sha256), 'EXPECTED_BUNDLE_HASH_REQUIRED')
            request = {'candidate_payload_hash': original_sha,
                'verification_bundle_sha256': verification_bundle_sha256, 'scope': deepcopy(self.scope)}
            request_sha = _digest(request)
            completed = False
            with self.reader.lease(deepcopy(request)) as read_current:
                _need(callable(read_current), 'AUTHENTICATED_CURRENT_READER_REQUIRED')
                first = deepcopy(read_current(deepcopy(request)))
                _gate(first, request, definition, self.clock())
                second = deepcopy(read_current(deepcopy(request)))
                _gate(second, request, definition, self.clock())
                _need(_digest(first) == _digest(second), 'RECEIPT_READBACK_CHANGED')
                _need(_digest(proposal) == original_sha and _digest(request) == request_sha
                    and self.scope == request['scope'], 'PROPOSAL_OR_HOST_SCOPE_CHANGED')
                completed = True
            # Nothing is disclosed until the host lease has completed. Its exit
            # must revalidate/release the authenticated transaction safely.
            # A context manager may suppress an internal exception; suppression
            # must never turn a failed two-read validation into a positive result.
            _need(completed, 'AUTHENTICATED_LEASE_INCOMPLETE')
            _gate(second, request, definition, self.clock())
            _need(_digest(proposal) == original_sha and self.scope == request['scope'],
                'PROPOSAL_OR_HOST_SCOPE_CHANGED')
            return _result('CURRENT_RECEIPT_PROJECTION_MATCH', source_only=source_only, evidence_bound=True)
        except _Rejected as exc:
            return _result(str(exc), source_only=source_only)
        except Exception:
            return _result('AUTHENTICATED_READ_UNAVAILABLE', source_only=source_only)


__all__ = ['CanonReceiptReadBinding', 'DefinitionAdmissionConsumer']
