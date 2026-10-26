"""Strict typed record/digest and pure restore planner from accepted v0.1.
A planner changes only a copy; the backend applies its bounded writes atomically.
No receipt authority is inferred from a hash or this module.
"""
from copy import deepcopy
import hashlib
import json

STATUS = 'NOT_QUALIFIED'
KINDS = frozenset({'POSITIVE', 'NEGATIVE', 'BOUNDARY', 'INTERFERENCE'})
MAX_INT = 2**53 - 1


class ContractError(ValueError):
    pass


def need(ok, reason):
    if not ok:
        raise ContractError(reason)


def canonical_bytes(value):
    def walk(v):
        if v is None or type(v) is bool:
            return
        if type(v) is int:
            need(abs(v) <= MAX_INT, 'INTEGER_RANGE')
        elif type(v) is str:
            try:
                v.encode('utf-8', 'strict')
            except UnicodeError as exc:
                raise ContractError('UNICODE_SCALAR_REQUIRED') from exc
        elif type(v) is list:
            for x in v:
                walk(x)
        elif type(v) is dict:
            need(all(type(k) is str for k in v), 'STRING_KEYS_REQUIRED')
            for k, x in v.items():
                walk(k); walk(x)
        else:
            raise ContractError('INTEGER_JSON_ENVELOPE_ONLY')
    walk(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def parse_canonical(raw):
    need(type(raw) is bytes, 'CANONICAL_BYTES_REQUIRED')
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, 'DUPLICATE_KEY')
            out[key] = value
        return out
    def invalid(_):
        raise ContractError('INTEGER_JSON_ENVELOPE_ONLY')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
            parse_float=invalid, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError('INVALID_JSON') from exc
    need(raw == canonical_bytes(value), 'NONCANONICAL_BYTES')
    return value


def digest(domain, value):
    return hashlib.sha256(domain.encode('ascii') + b'\0' + canonical_bytes(value)).hexdigest()


def sha(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def integer(value, minimum=0):
    return type(value) is int and minimum <= value <= MAX_INT


def fixture(value):
    need(type(value) is dict and value.get('evidence_mode') in ('SOURCE_ONLY','OBSERVED'), 'SYNTHETIC_FIXTURE_REQUIRED')


def label(value):
    need(type(value) is str and bool(value.strip()) and len(value)<=512, 'SYNTHETIC_IDENTIFIER_REQUIRED')


def scope_check(scope):
    need(type(scope) is dict and set(scope) == {'school', 'profile', 'level', 'scope'}, 'SCOPE_FIELDS')
    for x in scope.values():
        label(x)


def report(outcome, **data):
    return dict(status=STATUS, authority_proof=False, outcome=outcome,
        admission_allowed=False, genuine_receipts_created=0, Canon_checks_closed=0, **data)


def manifest_digest(manifest):
    fixture(manifest)
    need(set(manifest) == {'evidence_mode', 'contract', 'candidate_hash', 'scope', 'set_version',
        'gate_profile_sha256', 'max_attempt_us', 'max_age_us', 'cases'}, 'MANIFEST_FIELDS')
    need(manifest['contract'] == 'DEFINITION_TEST_STATE_PROPOSAL_0_1', 'CONTRACT_VERSION')
    need(sha(manifest['candidate_hash']) and sha(manifest['gate_profile_sha256']), 'MANIFEST_HASH')
    scope_check(manifest['scope'])
    need(integer(manifest['set_version'], 1) and manifest['set_version'] <= 2**31 - 1
        and integer(manifest['max_attempt_us'], 1) and integer(manifest['max_age_us'], 1), 'MANIFEST_NUMBER')
    need(type(manifest['cases']) is list and manifest['cases'], 'CASES_REQUIRED')
    keys, kinds = set(), set()
    for case in manifest['cases']:
        need(type(case) is dict and set(case) == {'key', 'version', 'kind', 'input_sha256',
            'oracle_sha256', 'executor_sha256', 'source_binding_sha256'}, 'CASE_FIELDS')
        label(case['key'])
        need(case['key'] not in keys and integer(case['version'], 1)
            and case['version'] <= 2**31 - 1 and case['kind'] in KINDS, 'CASE_IDENTITY')
        need(all(sha(case[k]) for k in ('input_sha256', 'oracle_sha256', 'executor_sha256', 'source_binding_sha256')), 'CASE_HASH')
        keys.add(case['key']); kinds.add(case['kind'])
    need(kinds == KINDS, 'FOUR_CLASSES_REQUIRED')
    ordered = deepcopy(manifest)
    ordered['cases'] = sorted(ordered['cases'], key=lambda c: c['key'].encode('utf-8'))
    return digest('DEFINITION_MANIFEST_PROPOSAL_0_1', ordered)


def evaluate_test_state(manifest, attempts, outcomes, context, now_us):
    """Structural witness evaluation, not a genuine test/Canon verification."""
    set_hash = manifest_digest(manifest)
    fixture(context); need(integer(now_us), 'CLOCK')
    context_fields = {'evidence_mode', 'school_snapshot_sha256', 'source_policy_sha256',
        'membership_sha256', 'policy_from_us', 'policy_until_us', 'candidate_active',
        'set_active', 'source_active', 'attestors_active'}
    need(set(context) == context_fields, 'TEST_CONTEXT_FIELDS')
    need(all(sha(context[k]) for k in ('school_snapshot_sha256', 'source_policy_sha256', 'membership_sha256')), 'CONTEXT_HASH')
    need(integer(context['policy_from_us']) and (context['policy_until_us'] is None
        or integer(context['policy_until_us']) and context['policy_until_us'] > context['policy_from_us']), 'CONTEXT_WINDOW')
    for k in ('candidate_active', 'set_active', 'source_active', 'attestors_active'):
        need(type(context[k]) is bool, 'CONTEXT_BOOLEAN')
    context_hash = digest('DEFINITION_DEPENDENCY_CONTEXT_0_1', context)
    need(type(attempts) is list and type(outcomes) is list, 'HISTORY_LISTS')
    cases = {c['key']: c for c in manifest['cases']}
    by_case = {key: [] for key in cases}; by_key = {}
    for attempt in attempts:
        need(type(attempt) is dict and set(attempt) == {'attempt_key', 'case_key', 'case_version',
            'attempt_seq', 'candidate_hash', 'manifest_sha256', 'context_sha256', 'started_us'}, 'ATTEMPT_FIELDS')
        label(attempt['attempt_key'])
        need(attempt['attempt_key'] not in by_key and attempt['case_key'] in cases, 'ATTEMPT_IDENTITY')
        case = cases[attempt['case_key']]
        need(attempt['case_version'] == case['version'] and type(attempt['case_version']) is int
            and attempt['candidate_hash'] == manifest['candidate_hash']
            and attempt['manifest_sha256'] == set_hash, 'FOREIGN_ATTEMPT')
        need(integer(attempt['attempt_seq'], 1) and integer(attempt['started_us'])
            and attempt['started_us'] <= now_us and sha(attempt['context_sha256']), 'ATTEMPT_VALUE')
        by_key[attempt['attempt_key']] = attempt; by_case[attempt['case_key']].append(attempt)
    results = {}
    for outcome in outcomes:
        need(type(outcome) is dict and set(outcome) == {'attempt_key', 'status', 'finished_us',
            'observed_sha256', 'executor_sha256', 'artifact_sha256'}, 'OUTCOME_FIELDS')
        key = outcome['attempt_key']
        need(key in by_key and key not in results, 'OUTCOME_IDENTITY')
        attempt, case = by_key[key], cases[by_key[key]['case_key']]
        need(outcome['status'] in ('PASS', 'FAIL', 'ERROR', 'CANCELLED')
            and integer(outcome['finished_us']) and attempt['started_us'] <= outcome['finished_us'] <= now_us
            and sha(outcome['artifact_sha256']) and outcome['executor_sha256'] == case['executor_sha256'], 'OUTCOME_VALUE')
        need(outcome['observed_sha256'] is None or sha(outcome['observed_sha256']), 'OBSERVED_HASH')
        if outcome['status'] == 'PASS':
            need(outcome['observed_sha256'] == case['oracle_sha256'], 'PASS_ORACLE_MISMATCH')
        results[key] = outcome
    blockers, latest, expiry = [], [], []
    if not all(context[k] for k in ('candidate_active', 'set_active', 'source_active', 'attestors_active')):
        blockers.append('REVOKED_OR_RETIRED')
    if now_us < context['policy_from_us'] or (context['policy_until_us'] is not None and now_us >= context['policy_until_us']):
        blockers.append('CONTEXT_EXPIRED_OR_FUTURE')
    if context['policy_until_us'] is not None:
        expiry.append(context['policy_until_us'])
    for key in sorted(cases, key=lambda s: s.encode('utf-8')):
        reserved = sorted(by_case[key], key=lambda a: a['attempt_seq'])
        need([a['attempt_seq'] for a in reserved] == list(range(1, len(reserved) + 1)), 'ATTEMPT_SEQUENCE_GAP_OR_DUPLICATE')
        if not reserved:
            blockers.append('NO_ATTEMPT:' + key); latest.append({'case_key': key, 'attempt': None, 'outcome': None}); continue
        attempt = reserved[-1]; outcome = results.get(attempt['attempt_key'])
        latest.append({'case_key': key, 'attempt': deepcopy(attempt), 'outcome': deepcopy(outcome)})
        if attempt['context_sha256'] != context_hash:
            blockers.append('STALE_CONTEXT:' + key)
        if outcome is None:
            blockers.append('PENDING:' + key); continue
        if outcome['status'] != 'PASS':
            blockers.append(outcome['status'] + ':' + key)
        if outcome['finished_us'] - attempt['started_us'] > manifest['max_attempt_us']:
            blockers.append('ATTEMPT_TIMEOUT:' + key)
        end = outcome['finished_us'] + manifest['max_age_us']
        need(integer(end), 'AGE_OVERFLOW')
        expiry.append(end)
        if now_us >= end:
            blockers.append('STALE_AGE:' + key)
    state = {'contract': 'DEFINITION_TEST_STATE_PROPOSAL_0_1', 'manifest_sha256': set_hash,
        'dependency_context_sha256': context_hash, 'latest': latest}
    valid_until = min(expiry) if expiry else None
    return report('TEST_STATE_MODELLED', manifest_sha256=set_hash,
        state_sha256=digest('DEFINITION_TEST_STATE_PROPOSAL_0_1', state),
        structural_eligibility=not blockers, blockers=blockers, latest=latest, valid_until_us=valid_until)


def validate_store(state):
    fixture(state); canonical_bytes(state)
    need(set(state) == {'evidence_mode', 'revision', 'items', 'versions', 'sources', 'activations', 'journal'}, 'STORE_FIELDS')
    need(integer(state['revision']) and all(type(state[k]) is dict for k in ('items', 'versions', 'sources', 'activations', 'journal')), 'STORE_SHAPE')
    for group in ('items', 'versions', 'activations', 'journal'):
        for key in state[group]:
            label(key)
    for key, row in state['items'].items():
        need(set(row) == {'school', 'stable_key', 'knowledge_type', 'status', 'row_sha256'}
            and sha(row['row_sha256']), 'ITEM_PROJECTION')
        label(row['school']); label(row['stable_key'])
    for key, row in state['versions'].items():
        need(set(row) == {'item_key', 'version_no', 'candidate_hash', 'domain', 'kind', 'profile', 'level',
            'authority_class', 'review_status', 'status', 'effective_from_us', 'effective_to_us', 'row_sha256'}
            and row['item_key'] in state['items'] and integer(row['version_no'], 1)
            and sha(row['candidate_hash']) and sha(row['row_sha256']), 'VERSION_PROJECTION')
        need(integer(row['effective_from_us']) and (row['effective_to_us'] is None or
            integer(row['effective_to_us']) and row['effective_to_us'] > row['effective_from_us']), 'VERSION_WINDOW')
        need(key in state['sources'] and type(state['sources'][key]) is list and state['sources'][key], 'SOURCE_LINKS_REQUIRED')
        source_keys = []
        for source in state['sources'][key]:
            need(set(source) == {'source_key', 'relation', 'identity_sha256', 'locator_sha256'}
                and source['relation'] == 'derived_from' and sha(source['identity_sha256'])
                and sha(source['locator_sha256']), 'SOURCE_PROJECTION')
            label(source['source_key']); source_keys.append(source['source_key'])
        need(len(set(source_keys)) == len(source_keys), 'SOURCE_DUPLICATE')
    need(set(state['sources']) == set(state['versions']), 'SOURCE_VERSION_SET')
    for key, row in state['activations'].items():
        need(set(row) == {'version_key', 'scope', 'status', 'start_us', 'end_us', 'immutable_sha256'}
            and row['version_key'] in state['versions'] and row['status'] in ('active', 'superseded', 'revoked', 'candidate')
            and integer(row['start_us']) and sha(row['immutable_sha256']), 'ACTIVATION_PROJECTION')
        label(row['scope'])
        need(row['end_us'] is None or integer(row['end_us']) and row['end_us'] > row['start_us'], 'ACTIVATION_WINDOW')
    active = [a for a in state['activations'].values() if a['status'] == 'active']
    for i, left in enumerate(active):
        for right in active[i+1:]:
            if left['scope'] != right['scope'] or state['versions'][left['version_key']]['item_key'] != state['versions'][right['version_key']]['item_key']:
                continue
            lend = left['end_us']; rend = right['end_us']
            need((lend is not None and lend <= right['start_us']) or
                (rend is not None and rend <= left['start_us']), 'ACTIVE_INTERVAL_OVERLAP')


def projection(state, item_key, scope):
    validate_store(state); scope_check(scope); label(item_key)
    need(item_key in state['items'] and state['items'][item_key]['school'] == scope['school'], 'SCHOOL_OR_ITEM')
    versions = {k: v for k, v in state['versions'].items() if v['item_key'] == item_key}
    return {'item_key': item_key, 'item': deepcopy(state['items'][item_key]),
        'versions': deepcopy(versions),
        'sources': {k: sorted(deepcopy(state['sources'][k]), key=lambda x: x['source_key'].encode('utf-8')) for k in versions},
        'activations': {k: deepcopy(a) for k, a in state['activations'].items() if a['version_key'] in versions and a['scope'] == scope['scope']}}


def active_projection(image, now_us):
    """Same-T active activation view; immutable rows are checked separately."""
    need(integer(now_us), 'PROJECTION_CLOCK')
    return {k: deepcopy(a) for k, a in image['activations'].items()
        if a['status'] == 'active' and a['start_us'] <= now_us
        and (a['end_us'] is None or now_us < a['end_us'])}


def make_restore_binding(before, after, *, item_key, scope, outgoing, predecessor):
    """Seal a synthetic bounded promotion's images, not an actual receipt."""
    pre, post = projection(before, item_key, scope), projection(after, item_key, scope)
    label(outgoing)
    need(outgoing not in pre['activations'] and outgoing in post['activations'], 'NEW_OUTGOING_REQUIRED')
    new = post['activations'][outgoing]
    need(new['status'] == 'active', 'OUTGOING_NOT_ACTIVE')
    expected = deepcopy(pre)
    expected['activations'][outgoing] = deepcopy(new)
    if predecessor is not None:
        label(predecessor)
        need(predecessor in pre['activations'] and pre['activations'][predecessor]['status'] == 'active', 'PREDECESSOR_REQUIRED')
        old = expected['activations'][predecessor]
        need(old['start_us'] < new['start_us'] and (old['end_us'] is None or new['start_us'] < old['end_us']), 'PROMOTION_WINDOW')
        old.update(status='superseded', end_us=new['start_us'])
    else:
        need(not any(a['status'] == 'active' for a in pre['activations'].values()), 'UNDECLARED_PREDECESSOR')
    need(expected == post, 'PROMOTION_DELTA_NOT_KV_ONLY')
    binding = {'evidence_mode': before['evidence_mode'], 'contract': 'DEFINITION_KV_RESTORE_PROPOSAL_0_1',
        'item_key': item_key, 'scope': deepcopy(scope), 'outgoing': outgoing, 'predecessor': predecessor,
        'candidate_hash': post['versions'][new['version_key']]['candidate_hash'], 'preimage': pre, 'postimage': post}
    return {'body': binding, 'model_binding_sha256': digest('DEFINITION_RESTORE_BINDING_0_1', binding)}


def validate_restore_binding(binding):
    """Validate structural relations, not authenticity of synthetic commitments."""
    need(type(binding) is dict and set(binding) == {'body', 'model_binding_sha256'}, 'BINDING_FIELDS')
    body = binding['body']; fixture(body)
    need(set(body) == {'evidence_mode', 'contract', 'item_key', 'scope', 'outgoing', 'predecessor',
        'candidate_hash', 'preimage', 'postimage'}
        and body['contract'] == 'DEFINITION_KV_RESTORE_PROPOSAL_0_1', 'RESTORE_CONTRACT')
    need(binding['model_binding_sha256'] == digest('DEFINITION_RESTORE_BINDING_0_1', body), 'BINDING_TAMPER')
    def image_state(image):
        need(type(image) is dict and set(image) == {'item_key', 'item', 'versions', 'sources', 'activations'}
            and image['item_key'] == body['item_key'], 'BINDING_IMAGE_FIELDS')
        return {'evidence_mode': body['evidence_mode'], 'revision': 0, 'items': {image['item_key']: deepcopy(image['item'])},
            'versions': deepcopy(image['versions']), 'sources': deepcopy(image['sources']),
            'activations': deepcopy(image['activations']), 'journal': {}}
    rebuilt = make_restore_binding(image_state(body['preimage']), image_state(body['postimage']),
        item_key=body['item_key'], scope=body['scope'], outgoing=body['outgoing'], predecessor=body['predecessor'])
    need(rebuilt == binding, 'BINDING_RELATION_MISMATCH')


def restore_context_check(context, scope):
    fixture(context)
    need(set(context) == {'evidence_mode', 'scope', 'restorer_ref', 'restore_authority_active',
        'predecessor_eligible', 'predecessor_valid_until_us', 'outgoing_source_active',
        'school_snapshot_sha256', 'dependency_sha256'}, 'RESTORE_CONTEXT_FIELDS')
    need(context['scope'] == scope and all(type(context[k]) is bool for k in
        ('restore_authority_active', 'predecessor_eligible', 'outgoing_source_active')), 'RESTORE_CONTEXT_SCOPE')
    label(context['restorer_ref'])
    need(context['restore_authority_active'], 'RESTORER_REVOKED')
    need(sha(context['school_snapshot_sha256']) and sha(context['dependency_sha256']), 'RESTORE_CONTEXT_HASH')
    need(context['predecessor_valid_until_us'] is None or integer(context['predecessor_valid_until_us']), 'PREDECESSOR_POLICY_WINDOW')


def plan_restore(state, binding, context, *, request_key, evidence_sha256):
    validate_restore_binding(binding)
    body = binding['body']; restore_context_check(context, body['scope']); label(request_key); need(sha(evidence_sha256), 'RESTORE_MODEL_EVIDENCE_HASH')
    validate_store(state)
    request = {'binding_sha256': binding['model_binding_sha256'], 'request_key': request_key,
        'scope': body['scope'], 'restorer_ref': context['restorer_ref'], 'evidence_sha256': evidence_sha256}
    return {'evidence_mode': binding['body']['evidence_mode'], 'binding': deepcopy(binding), 'request': request,
        'request_sha256': digest('DEFINITION_RESTORE_REQUEST_0_1', request), 'revision': state['revision'],
        'context_sha256': digest('DEFINITION_RESTORE_CONTEXT_0_1', context)}


class RestorePlanner:
    """Atomic replacement of Python fixture state, not PostgreSQL qualification."""
    def __init__(self, state):
        validate_store(state); self.state = deepcopy(state)

    def apply(self, plan, context, *, now_us, fault=None):
        fixture(plan); need(integer(now_us), 'RESTORE_CLOCK')
        need(fault in (None, 'BEFORE_COMMIT', 'AFTER_COMMIT_ACK_LOSS'), 'FAULT_MODE')
        need(set(plan) == {'evidence_mode', 'binding', 'request', 'request_sha256', 'revision', 'context_sha256'}, 'PLAN_FIELDS')
        validate_restore_binding(plan['binding'])
        body = plan['binding']['body']; restore_context_check(context, body['scope'])
        need(plan['request_sha256'] == digest('DEFINITION_RESTORE_REQUEST_0_1', plan['request']), 'REQUEST_TAMPER')
        request = plan['request']
        need(set(request) == {'binding_sha256', 'request_key', 'scope', 'restorer_ref', 'evidence_sha256'}
            and request['binding_sha256'] == plan['binding']['model_binding_sha256']
            and request['scope'] == body['scope'] and request['restorer_ref'] == context['restorer_ref']
            and sha(request['evidence_sha256']), 'REQUEST_BINDING_MISMATCH')
        key = plan['request']['request_key']
        label(key)
        prior = self.state['journal'].get(key)
        current = projection(self.state, body['item_key'], body['scope'])
        if prior is not None:
            need(prior['request_sha256'] == plan['request_sha256'], 'IDEMPOTENCY_CONFLICT')
            same = digest('RESTORED_PROJECTION_0_1', current) == prior['postimage_sha256']
            return report('PRIOR_EFFECT' if same else 'PRIOR_EFFECT_CURRENT_STATE_CHANGED',
                writes=[], original_applied_us=prior['applied_us'], model_journal_sha256=digest('MODEL_JOURNAL_0_1', prior))
        need(self.state['revision'] == plan['revision']
            and digest('DEFINITION_RESTORE_CONTEXT_0_1', context) == plan['context_sha256'], 'STALE_RESTORE_PLAN')
        need(current == body['postimage'], 'POSTIMAGE_CHANGED')
        need(current['item']['status'] == 'active' and current['item']['knowledge_type'] == 'skill', 'ITEM_RETIRED_OR_WRONG_TYPE')
        draft = deepcopy(self.state); outgoing = body['outgoing']; predecessor = body['predecessor']
        out = draft['activations'][outgoing]
        need(out['status'] == 'active' and out['start_us'] < now_us
            and (out['end_us'] is None or now_us < out['end_us']), 'OUTGOING_WINDOW')
        writes = [{'table': 'public.canon_activation', 'key': outgoing,
            'set': {'status': 'revoked', 'end_us': now_us}}]
        for activation_key in [outgoing] + ([predecessor] if predecessor is not None else []):
            version = draft['versions'][draft['activations'][activation_key]['version_key']]
            need(version['kind'] == 'DEFINITION' and version['domain'] == 'CARD_PLAY'
                and version['authority_class'] == 'school_canon' and version['review_status'] == 'reviewed'
                and version['status'] == 'active' and version['profile'] == body['scope']['profile']
                and version['level'] == body['scope']['level'], 'VERSION_INELIGIBLE')
        out.update(status='revoked', end_us=now_us)
        if predecessor is not None:
            original = body['preimage']['activations'][predecessor]
            end = original['end_us']; old = draft['activations'][predecessor]
            v = draft['versions'][old['version_key']]
            need(context['predecessor_eligible'] and original['start_us'] <= now_us
                and (end is None or now_us < end) and v['effective_from_us'] <= now_us
                and (v['effective_to_us'] is None or now_us < v['effective_to_us']), 'PREDECESSOR_INELIGIBLE')
            for limit in (context['predecessor_valid_until_us'], v['effective_to_us']):
                need(limit is None or end is not None and end <= limit, 'RESTORE_WOULD_EXTEND_AUTHORITY')
            old.update(status='active', end_us=end)
            writes.append({'table': 'public.canon_activation', 'key': predecessor, 'set': {'status': 'active', 'end_us': end}})
        validate_store(draft)
        restored = projection(draft, body['item_key'], body['scope'])
        for group in ('item', 'versions', 'sources'):
            need(restored[group] == body['preimage'][group], 'IMMUTABLE_DATA_CHANGED')
        need(active_projection(restored, now_us) == active_projection(body['preimage'], now_us), 'ACTIVE_PREIMAGE_NOT_RESTORED')
        witness = {'evidence_mode': draft['evidence_mode'], 'qualification': STATUS,
            'kind': 'DEFINITION_RESTORE_AUDIT_PLAN', 'request': deepcopy(request),
            'request_sha256': plan['request_sha256'], 'outcome': 'RESTORED_IN_MODEL',
            'binding_sha256': plan['binding']['model_binding_sha256'], 'applied_us': now_us,
            'preimage_sha256': digest('PREIMAGE_0_1', body['preimage']),
            'readset_sha256': digest('RESTORE_READSET_0_1', {'projection': current,
                'revision': self.state['revision'], 'context_sha256': plan['context_sha256']}),
            'postimage_sha256': digest('RESTORED_PROJECTION_0_1', restored),
            'writeset_sha256': digest('RESTORE_WRITESET_0_1', writes)}
        draft['journal'][key] = witness
        draft['revision'] += 1
        need(integer(draft['revision']), 'REVISION_OVERFLOW')
        if fault == 'BEFORE_COMMIT':
            return report('BEFORE_COMMIT_FAILURE', writes=[])
        self.state = draft  # One in-memory atomic replacement only.
        if fault == 'AFTER_COMMIT_ACK_LOSS':
            return report('OUTCOME_UNCONFIRMED', writes=None)
        return report('RESTORED_IN_MODEL', writes=writes + [{'table': 'PROPOSED_CANON_RESTORE_AUDIT', 'key': key, 'set': witness}], applied_us=now_us)
