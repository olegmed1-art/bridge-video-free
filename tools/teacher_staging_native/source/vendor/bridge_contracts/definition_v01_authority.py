"""Typed authority validator from accepted v0.1; never authenticates its inputs.
Only the server-owned backend may supply observed authority facts.
"""
from copy import deepcopy
import hashlib
import json
import math

VERSION = 'DEFINITION_AUTHORITY_PROPOSAL_0_1'
VERIFIER = 'bridge_school_canon_verifier'
CONTROL = 'bridge_school_canon_control_verifier'
RESTORER = 'bridge_school_canon_restorer'
SEMANTIC = 'bridge_school_canon_semantic_verifier'
BRIDGE = 'bridge_school_canon_bridge_verifier'
FIREWALL = 'bridge_school_canon_firewall_verifier'
CAPABILITY = {'REGISTER_SET': VERIFIER, 'RESERVE_ATTEMPT': CONTROL,
    'RECORD_OUTCOME': CONTROL, 'RESTORE_KV': RESTORER}
CASE_CHECK = dict(POSITIVE='POSITIVE_TESTS', NEGATIVE='NEGATIVE_TESTS',
    BOUNDARY='BOUNDARY_TESTS', INTERFERENCE='INTERFERENCE_TESTS')
CHECKS = frozenset('''SOURCE_AUTHORITY SOURCE_BINDING SPEAKER_IDENTITY TRANSCRIPT_BINDING
 SEMANTIC_PARSE EXPLANATION_COMPLETENESS BRIDGE_LOGIC HIDDEN_INFORMATION_FIREWALL
 POSITIVE_TESTS NEGATIVE_TESTS BOUNDARY_TESTS INTERFERENCE_TESTS CANON_REGRESSION
 CANON_INTEGRITY CANON_CONFLICT_SCAN ROLLBACK_RESTORE'''.split())
STATE_CHECKS = frozenset({'CANON_REGRESSION','CANON_INTEGRITY','CANON_CONFLICT_SCAN','ROLLBACK_RESTORE'})
HIGH = {'SEMANTIC_PARSE': SEMANTIC, 'BRIDGE_LOGIC': BRIDGE, 'HIDDEN_INFORMATION_FIREWALL': FIREWALL}
FAMILY = {SEMANTIC:'semantic-model-a', BRIDGE:'bridge-engine-b', FIREWALL:'taint-analyzer', CONTROL:'formal-checker'}
RANK = {'I0':0,'I1':1,'I2':2,'I3':3}
COMMON = {'evidence_mode','contract_version','operation','request_key','candidate_hash','scope'}
CASE_FIELDS = {'manifest_sha256','set_version','case_key','case_version','case_kind'}
EXTRA = {'REGISTER_SET':{'manifest_sha256','set_version'}, 'RESERVE_ATTEMPT':CASE_FIELDS,
    'RECORD_OUTCOME':CASE_FIELDS | {'attempt_key','reservation_sha256','result','artifact_sha256'},
    'RESTORE_KV':{'restore_binding_sha256','verification_bundle_sha256'}}


class Denied(ValueError):
    pass


def need(value, code):
    if not value:
        raise Denied(code)


def hash_ok(value):
    return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def integer(value):
    return type(value) is int and 0 <= value <= 2**53-1


def label(value):
    return type(value) is str and bool(value.strip()) and len(value)<=512


def digest(domain, value):
    raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
    return hashlib.sha256(domain.encode('ascii')+b'\0'+raw).hexdigest()


def scope_ok(scope):
    return type(scope) is dict and set(scope)=={'school','profile','level','scope'} and all(label(v) for v in scope.values())


def result(permit, reason, **data):
    return dict(status='NOT_QUALIFIED', validator_only=True, decision='ABSTAIN',
        execution_allowed=False, admission_allowed=False, genuine_checks_created=0,
        policy_match=permit, reason=reason, **data)


def membership(facts, principal, capability):
    matches=[m for m in facts['memberships'] if m['principal']==principal and m['capability']==capability]
    need(len(matches)==1 and matches[0]['member'] is True and matches[0]['rolcanlogin'] is True,
        'CURRENT_CAPABILITY_MEMBERSHIP_REQUIRED')


def registry_lane(facts, capability, check):
    lanes=[r for r in facts['registry'] if r['capability']==capability]
    need(len(lanes)==1, 'REGISTRY_AMBIGUOUS_OR_MISSING')
    lane=lanes[0]
    need(lane['family']==FAMILY[capability] and lane['status']=='active'
        and type(lane['allowed_check_ids']) is list and all(type(c) is str and c in CHECKS|{'CORRECTION_REVIEW'} for c in lane['allowed_check_ids'])
        and len(lane['allowed_check_ids'])==len(set(lane['allowed_check_ids']))
        and check in lane['allowed_check_ids'] and lane['max_assurance'] in ('I1','I2','I3')
        and (capability!=CONTROL or lane['max_assurance']=='I1'),
        'CURRENT_REGISTRY_LANE_REQUIRED')
    return lane


def check_restore_evidence(request, facts):
    bundle=facts['receipt_set']
    need(type(bundle) is dict and bundle.get('evidence_mode')==facts.get('evidence_mode'), 'SYNTHETIC_EVIDENCE_REQUIRED')
    need(bundle['operation']=='RESTORE_KV' and bundle['candidate_hash']==request['candidate_hash']
        and bundle['scope']==request['scope'] and bundle['restore_binding_sha256']==request['restore_binding_sha256']
        and bundle['verification_bundle_sha256']==request['verification_bundle_sha256']
        and bundle['restore_context_sha256']==facts['restore_context_sha256'], 'RESTORE_EVIDENCE_SUBJECT_MISMATCH')
    confidence=bundle['semantic_confidence']
    need(type(confidence) in (int,float) and math.isfinite(confidence) and 0.95<=confidence<=1,'CONFIDENCE_REQUIRED')
    rows=bundle['checks'];need(type(rows) is list and len(rows)==16,'ALL_16_CURRENT_CHECKS_REQUIRED')
    assigned=bundle['assignments']
    need(type(assigned) is list and len(assigned)==16,'EXACT_VERSIONED_ASSIGNMENTS_REQUIRED')
    assignments={}
    for a in assigned:
        need(a['check_id'] in CHECKS and a['check_id'] not in assignments,'EXACT_VERSIONED_ASSIGNMENTS_REQUIRED')
        assignments[a['check_id']]=a
    seen={}
    for row in rows:
        check=row['check_id'];need(check in CHECKS and check not in seen,'ALL_16_CURRENT_CHECKS_REQUIRED')
        need(type(row['verifier_version']) is str and 0<len(row['verifier_version'].strip())<=512
            and row==assignments.get(check),'ASSIGNED_VERIFIER_VERSION_OR_ROW_MISMATCH')
        need(row['result']=='PASS' and hash_ok(row['evidence_sha256'])
            and row['candidate_hash']==request['candidate_hash']
            and row['verification_bundle_sha256']==request['verification_bundle_sha256']
            and row['restore_binding_sha256']==request['restore_binding_sha256']
            and row['scope']==request['scope'], 'CHECK_FAILED_OR_FOREIGN')
        capability=HIGH.get(check,CONTROL); lane=registry_lane(facts,capability,check)
        need(row['capability']==capability and row['family']==lane['family'] and label(row['executor']), 'CHECK_LANE_OR_EXECUTOR')
        membership(facts,row['executor'],capability)
        need(row['assurance'] in RANK and RANK[row['assurance']]<=RANK[lane['max_assurance']], 'ASSURANCE_CEILING')
        need(check not in HIGH or RANK[row['assurance']]>=2,'HIGH_ASSURANCE_REQUIRED')
        expected=facts['canon_snapshot_sha256'] if check in STATE_CHECKS else None
        need(row['snapshot_sha256']==expected,'STALE_OR_STATELESS_SNAPSHOT')
        seen[check]=row
    need(set(seen)==CHECKS,'ALL_16_CURRENT_CHECKS_REQUIRED')
    need(len({seen[c]['executor'] for c in HIGH})==3,'INDEPENDENT_HIGH_EXECUTORS_REQUIRED')


def authorize(request, facts, now_us, *, history_only=False):
    """Validate server observations; caller JSON alone is never authority."""
    need(type(request) is dict and request.get('evidence_mode') in ('SOURCE_ONLY','OBSERVED')
        and type(facts) is dict and facts.get('evidence_mode')==request.get('evidence_mode'),'SOURCE_ONLY_REQUIRED')
    op=request.get('operation');need(op in CAPABILITY,'OPERATION_UNKNOWN')
    need(set(request)==COMMON|EXTRA[op] and request['contract_version']==VERSION,'REQUEST_FIELDS_OR_VERSION')
    need(label(request['request_key']) and hash_ok(request['candidate_hash']) and scope_ok(request['scope']),'REQUEST_IDENTITY')
    need(integer(now_us),'FINAL_SERVER_CLOCK')
    need(facts['contract_version']==VERSION and facts['lease_kind']=='DEFINITION_MUTATION_ROOT','MUTATION_ROOT_REQUIRED_READ_LEASE_HAS_NO_WRITE_RIGHT')
    root=facts['root']
    need(set(root)=={'backend_pid','transaction_id','owner_ref','mode'}
        and integer(root['backend_pid']) and root['backend_pid']>0
        and label(root['transaction_id']) and label(root['owner_ref']) and root['mode']=='READ_WRITE'
        and hash_ok(facts['target_sha256']) and integer(facts['revision']),'OWNED_ROOT_TARGET_REQUIRED')
    actor=facts['session_user'];need(label(actor) and facts['rolcanlogin'] is True,'SERVER_SESSION_LOGIN_REQUIRED')
    need(integer(facts['actor_from_us']) and integer(facts['lease_from_us'])
        and facts['actor_from_us']<=now_us and facts['lease_from_us']<=now_us,'AUTHORITY_NOT_YET_VALID')
    for end in (facts['actor_until_us'],facts['lease_until_us']):
        need(end is None or integer(end) and now_us<end,'AUTHORITY_OR_LEASE_EXPIRED')
    membership(facts,actor,CAPABILITY[op])
    need(facts['candidate_hash']==request['candidate_hash'] and facts['scope']==request['scope'],'FOREIGN_CANDIDATE_OR_SCOPE')
    need((facts['domain'],facts['kind'],facts['item_type'])==('CARD_PLAY','DEFINITION','skill'),'TYPE_SCOPE_REQUIRED')
    need(all(type(facts[k]) is bool for k in ('policy_active','candidate_active','source_active',
        'set_active','predecessor_present','predecessor_eligible')),'LIFECYCLE_BOOLEAN_REQUIRED')
    need(facts['set_version'] is None or integer(facts['set_version']) and facts['set_version']>0,'STORED_SET_VERSION')
    need(facts['policy_active'] is True,'POLICY_REVOKED')
    if op!='RESTORE_KV':
        need(facts['candidate_active'] is True and facts['source_active'] is True,'CANDIDATE_OR_SOURCE_REVOKED')
        need(integer(request['set_version']) and 1<=request['set_version']<=2**31-1
            and hash_ok(request['manifest_sha256']),'MANIFEST_BINDING_REQUIRED')
    if history_only:
        return actor
    if op=='REGISTER_SET':
        old=facts['set_version']
        need(old is None or integer(old) and (request['set_version']>old or
            request['set_version']==old and request['manifest_sha256']==facts['manifest_sha256']), 'MANIFEST_VERSION_IMMUTABLE')
    elif op in ('RESERVE_ATTEMPT','RECORD_OUTCOME'):
        need(facts['set_active'] is True and request['set_version']==facts['set_version']
            and request['manifest_sha256']==facts['manifest_sha256'],'SET_REVOKED_OR_STALE')
        case=facts['case']
        need(request['case_kind'] in CASE_CHECK and label(request['case_key'])
            and type(request['case_version']) is int and request['case_version']>0
            and type(case['version']) is int
            and (request['case_key'],request['case_version'],request['case_kind'])==
                (case['key'],case['version'],case['kind']) and case['executor']==actor,'CASE_ASSIGNMENT_OR_EXECUTOR_MISMATCH')
        registry_lane(facts,CONTROL,CASE_CHECK[request['case_kind']])
        if op=='RECORD_OUTCOME':
            a=facts['attempt']
            need(type(a) is dict and label(request['attempt_key']) and hash_ok(request['reservation_sha256'])
                and type(a['set_version']) is int and 1<=a['set_version']<=2**31-1
                and type(a['case_version']) is int and 1<=a['case_version']<=2**31-1
                and a['key']==request['attempt_key'] and a['reservation_sha256']==request['reservation_sha256']
                and a['manifest_sha256']==request['manifest_sha256'] and a['case_key']==request['case_key']
                and a['case_version']==request['case_version'] and a['executor']==actor
                and a['candidate_hash']==request['candidate_hash'] and a['scope']==request['scope']
                and a['set_version']==request['set_version'] and a['case_kind']==request['case_kind'],'RESERVATION_OR_EXECUTOR_MISMATCH')
            need(a['terminal_record_sha256'] is None,'TERMINAL_OUTCOME_IMMUTABLE')
            need(request['result'] in ('PASS','FAIL','ERROR','CANCELLED') and hash_ok(request['artifact_sha256']),'OUTCOME_FIELDS')
    else:
        need(hash_ok(request['restore_binding_sha256']) and hash_ok(request['verification_bundle_sha256'])
            and request['restore_binding_sha256']==facts['restore_binding_sha256']
            and hash_ok(facts['restore_context_sha256']) and hash_ok(facts['canon_snapshot_sha256']),'RESTORE_BINDING_REQUIRED')
        need(facts['predecessor_present'] is False or facts['predecessor_eligible'] is True,'PREDECESSOR_REVOKED')
        check_restore_evidence(request,facts)
    return actor


def prepare(request, observations, *, now_us):
    try:
        actor=authorize(request,observations,now_us)
        effect={'request':deepcopy(request),'session_user':actor,'capability':CAPABILITY[request['operation']]}
        plan={'evidence_mode':request['evidence_mode'],'contract_version':VERSION,'effect':effect,
            'effect_sha256':digest('DEFINITION_EFFECT_0_1',effect),
            'readset_sha256':digest('DEFINITION_AUTHORITY_READSET_0_1',observations),'prepared_us':now_us}
        return result(True,'MODEL_PREPARED',plan=plan)
    except (Denied,KeyError,TypeError,ValueError,UnicodeError) as exc:
        return result(False,str(exc) if type(exc) is Denied else 'MALFORMED_MODEL_INPUT',plan=None)


def commit_fence(plan, current_observations, *, now_us):
    """No commit occurs. Returns fixed writeset description after final recheck."""
    try:
        need(type(plan) is dict and set(plan)=={'evidence_mode','contract_version','effect','effect_sha256','readset_sha256','prepared_us'}
            and plan['evidence_mode'] in ('SOURCE_ONLY','OBSERVED') and plan['contract_version']==VERSION,'PLAN_FIELDS')
        need(integer(plan['prepared_us']) and integer(now_us) and now_us>=plan['prepared_us'],'FINAL_T_BEFORE_PREPARE')
        need(plan['effect_sha256']==digest('DEFINITION_EFFECT_0_1',plan['effect']),'EFFECT_TAMPER')
        effect=plan['effect'];request=effect['request']
        actor=authorize(request,current_observations,now_us)
        need(effect['session_user']==actor and effect['capability']==CAPABILITY[request['operation']],'ACTOR_OR_CAPABILITY_CHANGED')
        need(plan['readset_sha256']==digest('DEFINITION_AUTHORITY_READSET_0_1',current_observations),'STALE_ROOT_OR_READSET')
        op=request['operation']
        writes={'REGISTER_SET':['typed_validation_manifest_append'],
            'RESERVE_ATTEMPT':['typed_attempt_reservation_append_store_allocated_sequence'],
            'RECORD_OUTCOME':['typed_terminal_outcome_append'],
            'RESTORE_KV':['existing_outgoing_activation_status_valid_to',
                *(['existing_predecessor_activation_status_original_valid_to'] if current_observations['predecessor_present'] else []),
                'typed_restore_audit_append']}[op]
        return result(True,'MODEL_FINAL_FENCE_MATCH',proposed_writeset=writes,
            actual_writes=[],effect_sha256=plan['effect_sha256'],actual_final_T=now_us)
    except (Denied,KeyError,TypeError,ValueError,UnicodeError) as exc:
        return result(False,str(exc) if type(exc) is Denied else 'MALFORMED_MODEL_INPUT',actual_writes=[])
