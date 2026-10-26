"""SOURCE_ONLY fixture builders, copied as data helpers; no old suites run."""
from copy import deepcopy
import hashlib,json
from bridge_contracts import definition_v01_authority as p
from bridge_contracts import definition_v01_records as m
sha=lambda x:hashlib.sha256(x.encode()).hexdigest()
h=sha
SCOPE=dict(school="synthetic:school",profile="synthetic:profile",level="synthetic:level",scope="synthetic:scope")
ACTOR="synthetic:operator"

def fixture(operation='RESTORE_KV'):
    req=dict(evidence_mode='SOURCE_ONLY',contract_version=p.VERSION,operation=operation,
        request_key='synthetic:command1',candidate_hash=sha('synthetic:opaque-external-candidate'),scope=deepcopy(SCOPE))
    if operation=='RESTORE_KV':
        req.update(restore_binding_sha256=sha('synthetic:restorebinding'),verification_bundle_sha256=sha('synthetic:restorebundle'))
    else:
        req.update(manifest_sha256=sha('synthetic:manifest'),set_version=1)
        if operation!='REGISTER_SET':
            req.update(case_key='synthetic:positive',case_version=1,case_kind='POSITIVE')
        if operation=='RECORD_OUTCOME':
            req.update(attempt_key='synthetic:attempt1',reservation_sha256=sha('synthetic:reservation'),result='PASS',artifact_sha256=sha('synthetic:artifact'))
    facts=dict(evidence_mode='SOURCE_ONLY',contract_version=p.VERSION,lease_kind='DEFINITION_MUTATION_ROOT',
        target_sha256=sha('synthetic:target'),revision=2,
        root=dict(backend_pid=1,transaction_id='synthetic:root1',owner_ref='synthetic:existing-owner-lease',mode='READ_WRITE'),
        session_user=ACTOR,rolcanlogin=True,actor_from_us=0,actor_until_us=500,lease_from_us=0,lease_until_us=500,
        memberships=[dict(principal=ACTOR,capability=p.CAPABILITY[operation],member=True,rolcanlogin=True)],
        candidate_hash=req['candidate_hash'],scope=deepcopy(SCOPE),domain='CARD_PLAY',kind='DEFINITION',item_type='skill',
        candidate_active=True,source_active=True,policy_active=True,set_active=True,
        manifest_sha256=sha('synthetic:manifest'),set_version=1,
        case=dict(key='synthetic:positive',version=1,kind='POSITIVE',executor=ACTOR),
        attempt=dict(key='synthetic:attempt1',reservation_sha256=sha('synthetic:reservation'),
            manifest_sha256=sha('synthetic:manifest'),case_key='synthetic:positive',case_version=1,executor=ACTOR,terminal_record_sha256=None,
            candidate_hash=req['candidate_hash'],scope=deepcopy(SCOPE),set_version=1,case_kind='POSITIVE'),
        registry=[],receipt_set=None,canon_snapshot_sha256=sha('synthetic:canon'),
        restore_binding_sha256=sha('synthetic:restorebinding'),restore_context_sha256=sha('synthetic:restorecontext'),
        predecessor_present=True,predecessor_eligible=True)
    for capability,family in p.FAMILY.items():
        ids=sorted(c for c in p.CHECKS if p.HIGH.get(c,p.CONTROL)==capability)
        if capability==p.CONTROL:ids.append('CORRECTION_REVIEW')
        facts['registry'].append(dict(capability=capability,family=family,allowed_check_ids=ids,
            max_assurance='I1' if capability==p.CONTROL else 'I3',status='active'))
        facts['memberships'].append(dict(principal='synthetic:check:'+family,capability=capability,member=True,rolcanlogin=True))
    rows=[]
    for check in sorted(p.CHECKS):
        capability=p.HIGH.get(check,p.CONTROL);family=p.FAMILY[capability]
        rows.append(dict(check_id=check,result='PASS',evidence_sha256=sha('synthetic:evidence:'+check),
            verifier_version='synthetic:verifier-v1',
            capability=capability,family=family,executor='synthetic:check:'+family,
            assurance='I2' if check in p.HIGH else 'I1',candidate_hash=req['candidate_hash'],
            verification_bundle_sha256=sha('synthetic:restorebundle'),restore_binding_sha256=sha('synthetic:restorebinding'),
            scope=deepcopy(SCOPE),snapshot_sha256=facts['canon_snapshot_sha256'] if check in p.STATE_CHECKS else None))
    facts['receipt_set']=dict(evidence_mode='SOURCE_ONLY',operation='RESTORE_KV',candidate_hash=req['candidate_hash'],scope=deepcopy(SCOPE),
        restore_binding_sha256=sha('synthetic:restorebinding'),verification_bundle_sha256=sha('synthetic:restorebundle'),
        restore_context_sha256=facts['restore_context_sha256'],semantic_confidence=0.95,checks=rows,assignments=deepcopy(rows))
    return req,facts

def restore_fixture(predecessor=True, finite_end=400):
    item = 'synthetic:ki'; old = 'synthetic:kv1'; new = 'synthetic:kv2'
    def version(key, no):
        return dict(item_key=item, version_no=no, candidate_hash=h(key+'candidate'), domain='CARD_PLAY',
            kind='DEFINITION', profile=SCOPE['profile'], level=SCOPE['level'], authority_class='school_canon',
            review_status='reviewed', status='active', effective_from_us=0, effective_to_us=finite_end,
            row_sha256=h(key+'row'))
    def source(key):
        return [dict(source_key='synthetic:source:'+key, relation='derived_from',
            identity_sha256=h(key+'source'), locator_sha256=h(key+'locator'))]
    versions = {new: version(new, 2)}
    if predecessor:
        versions[old] = version(old, 1)
    before = dict(evidence_mode='SOURCE_ONLY', revision=1,
        items={item: dict(school=SCOPE['school'], stable_key='synthetic:stable', knowledge_type='skill',
            status='active', row_sha256=h('synthetic:itemrow'))}, versions=versions,
        sources={k:source(k) for k in versions}, activations={}, journal={})
    if predecessor:
        before['activations']['synthetic:old'] = dict(version_key=old, scope=SCOPE['scope'], status='active',
            start_us=1, end_us=finite_end, immutable_sha256=h('synthetic:originalapproval'))
    after = deepcopy(before); after['revision'] = 2
    if predecessor:
        after['activations']['synthetic:old'].update(status='superseded', end_us=100)
    after['activations']['synthetic:new'] = dict(version_key=new, scope=SCOPE['scope'], status='active',
        start_us=100, end_us=finite_end, immutable_sha256=h('synthetic:newapproval'))
    context = dict(evidence_mode='SOURCE_ONLY', scope=deepcopy(SCOPE), restorer_ref='synthetic:existing-restorer',
        restore_authority_active=True, predecessor_eligible=True, predecessor_valid_until_us=finite_end,
        outgoing_source_active=True, school_snapshot_sha256=h('synthetic:snapshot'), dependency_sha256=h('synthetic:deps'))
    binding = m.make_restore_binding(before, after, item_key=item, scope=SCOPE,
        outgoing='synthetic:new', predecessor='synthetic:old' if predecessor else None)
    return before, after, context, binding
