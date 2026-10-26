"""Typed DEFINITION persistence service. No route, connection or issuer default.

Backend/context providers are host wiring, never request fields. Read ports have
no transaction method. Native backend attests its connection independently.
Validation records are unreviewed evidence, not Canon admission/activation.
"""
from copy import deepcopy
from bridge_contracts import definition_v01_records as records
from bridge_contracts import definition_v01_authority as authority

SCHEMA='definition-contract-store-v0.1'
MAX_BYTES=524288
MAX_ATTEMPTS=4096
GATE_PROFILE_SHA256=records.digest('DEFINITION_GATE_PROFILE_0_1',
    {'contract_version':authority.VERSION,'checks':sorted(authority.CHECKS)})
ATTEMPT_FIELDS={'attempt_key','case_key','case_version','attempt_seq','candidate_hash',
    'manifest_sha256','context_sha256','started_us'}
OUTCOME_FIELDS={'attempt_key','status','finished_us','observed_sha256','executor_sha256','artifact_sha256'}


class StoreDenied(ValueError):
    pass


class OutcomeUnknown(RuntimeError):
    pass


def need(ok,code):
    if not ok:raise StoreDenied(code)


def response(status,reason,mode=None,**data):
    return dict(status=status,reason=reason,source_only=mode!='OBSERVED',
        admission_allowed=False,ready=False,production_ready=False,FULL_CALL='ABSTAIN',
        session_binding=None,genuine_Canon_checks_created=0,**data)


def empty_document(request,mode):
    return dict(schema=SCHEMA,evidence_mode=mode,revision=0,candidate_hash=request['candidate_hash'],
        scope=deepcopy(request['scope']),sets={},current_set=None,attempts=[],outcomes=[],commands={})


def validate_document(doc,request,mode):
    need(type(doc) is dict and set(doc)=={'schema','evidence_mode','revision','candidate_hash','scope',
        'sets','current_set','attempts','outcomes','commands'},'DOCUMENT_FIELDS')
    need(doc['schema']==SCHEMA and doc['evidence_mode']==mode and records.integer(doc['revision'])
        and doc['candidate_hash']==request['candidate_hash'] and doc['scope']==request['scope'],'DOCUMENT_BINDING')
    need(type(doc['sets']) is dict and type(doc['commands']) is dict
        and type(doc['attempts']) is list and type(doc['outcomes']) is list
        and len(doc['attempts'])<=MAX_ATTEMPTS,'DOCUMENT_SHAPE')
    need(len(records.canonical_bytes(doc))<=MAX_BYTES,'DOCUMENT_BUDGET')
    versions=set()
    for key,manifest in doc['sets'].items():
        need(key==records.manifest_digest(manifest) and manifest['evidence_mode']==mode
            and manifest['candidate_hash']==doc['candidate_hash'] and manifest['scope']==doc['scope']
            and manifest['gate_profile_sha256']==GATE_PROFILE_SHA256
            and manifest['set_version'] not in versions,'STORED_MANIFEST_BINDING')
        versions.add(manifest['set_version'])
    need(doc['current_set'] is None and not doc['sets'] or doc['current_set'] in doc['sets'],'CURRENT_SET_BINDING')
    keys=set()
    for a in doc['attempts']:
        need(set(a)==ATTEMPT_FIELDS|{'executor_login','reservation_sha256'}
            and a['attempt_key'] not in keys and a['candidate_hash']==doc['candidate_hash']
            and a['manifest_sha256'] in doc['sets'],'STORED_ATTEMPT_BINDING')
        body={k:a[k] for k in ATTEMPT_FIELDS}
        need(a['reservation_sha256']==records.digest('DEFINITION_RESERVATION_0_1',body),'RESERVATION_HASH')
        keys.add(a['attempt_key'])
    seen=set()
    for outcome in doc['outcomes']:
        need(set(outcome)==OUTCOME_FIELDS and outcome['attempt_key'] in keys
            and outcome['attempt_key'] not in seen,'STORED_OUTCOME_BINDING')
        seen.add(outcome['attempt_key'])


def material_hash(doc):
    return records.digest('DEFINITION_STORED_MATERIAL_0_1',
        {k:doc[k] for k in ('evidence_mode','candidate_hash','scope','sets','current_set','attempts','outcomes')})


def bound_facts(tx,request,doc,*,history_only=False):
    facts=deepcopy(tx.authority(request))
    need(facts['evidence_mode']==tx.mode and facts['candidate_hash']==request['candidate_hash']
        and facts['scope']==request['scope'],'SERVER_WORK_BINDING')
    facts['revision']=doc['revision']
    manifest=doc['sets'].get(doc['current_set'])
    facts['set_version']=None if manifest is None else manifest['set_version']
    facts['manifest_sha256']=None if manifest is None else doc['current_set']
    if not history_only and request['operation'] in ('RESERVE_ATTEMPT','RECORD_OUTCOME'):
        need(manifest is not None,'MANIFEST_NOT_FOUND')
        cases=[c for c in manifest['cases'] if c['key']==request['case_key']]
        need(len(cases)==1,'CASE_NOT_FOUND')
        case=cases[0]
        # Executor attribution comes from the server's retained assignment.
        assignment=facts['case']
        need((assignment['key'],assignment['version'],assignment['kind'])==
            (case['key'],case['version'],case['kind']),'SERVER_CASE_ASSIGNMENT_CHANGED')
        if request['operation']=='RECORD_OUTCOME':
            attempts=[a for a in doc['attempts'] if a['attempt_key']==request['attempt_key']]
            need(len(attempts)==1,'RESERVATION_NOT_FOUND')
            a=attempts[0]
            terminal=next((o for o in doc['outcomes'] if o['attempt_key']==a['attempt_key']),None)
            facts['attempt']=dict(key=a['attempt_key'],reservation_sha256=a['reservation_sha256'],
                manifest_sha256=a['manifest_sha256'],case_key=a['case_key'],case_version=a['case_version'],
                executor=a['executor_login'],candidate_hash=a['candidate_hash'],scope=doc['scope'],
                set_version=manifest['set_version'],case_kind=case['kind'],
                terminal_record_sha256=None if terminal is None else records.digest('DEFINITION_OUTCOME_0_1',terminal))
    return facts


def evaluate_document(doc,context,now_us):
    manifest=doc['sets'].get(doc['current_set'])
    need(manifest is not None,'MANIFEST_NOT_FOUND')
    attempts=[{k:a[k] for k in ATTEMPT_FIELDS} for a in doc['attempts'] if a['manifest_sha256']==doc['current_set']]
    keys={a['attempt_key'] for a in attempts}
    outcomes=[o for o in doc['outcomes'] if o['attempt_key'] in keys]
    return records.evaluate_test_state(manifest,attempts,outcomes,context,now_us)


class DefinitionContractStore:
    """Four host-only RPC methods plus a read-only diagnostic projection."""
    def __init__(self,backend=None):
        self.backend=backend

    def _run(self,expected_operation,command,payload):
        mode=None;complete=False;answer=None;effect=None
        try:
            need(self.backend is not None and getattr(self.backend,'mutation_capable',False) is True
                and callable(getattr(self.backend,'transaction',None)),'AUTHENTICATED_MUTATION_BACKEND_UNBOUND')
            need(type(command) is dict and 'evidence_mode' not in command and 'fixture' not in command,'CLIENT_MODE_OR_AUTHORITY_FORBIDDEN')
            need(command.get('operation')==expected_operation,'RPC_OPERATION_MISMATCH')
            command_before=records.digest('CLIENT_COMMAND_0_1',command)
            payload_before=records.digest('CLIENT_PAYLOAD_0_1',payload)
            frozen=deepcopy(command);body=deepcopy(payload)
            with self.backend.transaction(deepcopy(frozen)) as tx:
                mode=tx.mode;need(mode in ('SOURCE_ONLY','OBSERVED'),'BACKEND_MODE_INVALID')
                request=dict(frozen,evidence_mode=mode)
                doc=tx.load(request) or empty_document(request,mode)
                validate_document(doc,request,mode)
                prior=doc['commands'].get(request['request_key'])
                facts=bound_facts(tx,request,doc,history_only=prior is not None)
                actor=authority.authorize(request,facts,tx.now_us(),history_only=prior is not None)
                effect=records.digest('DEFINITION_STORE_EFFECT_0_1',{'request':request,'payload':body,'actor':actor})
                if prior is not None:
                    need(prior['effect_sha256']==effect,'IDEMPOTENCY_CONFLICT')
                    same=prior['after_material_sha256']==material_hash(doc)
                    if prior.get('restore_postimage_sha256') is not None:
                        same=same and tx.current_restore_hash(request)==prior['restore_postimage_sha256']
                    answer=response('PRIOR_EFFECT' if same else 'PRIOR_EFFECT_CURRENT_STATE_CHANGED',
                        'NO_REAPPLY',mode,record=deepcopy(prior['record']),applied_us=prior['applied_us'],writes=0)
                else:
                    plan=authority.prepare(request,facts,now_us=tx.now_us())
                    need(plan['policy_match'],plan['reason'])
                    draft=deepcopy(doc);context=deepcopy(tx.test_context(request))
                    need(context['evidence_mode']==mode,'TEST_CONTEXT_MODE_MISMATCH')
                    now=tx.now_us();writes=[];restore_post=None
                    op=request['operation']
                    if op=='REGISTER_SET':
                        need(type(body) is dict and body['evidence_mode']==mode
                            and body['candidate_hash']==request['candidate_hash'] and body['scope']==request['scope']
                            and body['set_version']==request['set_version']
                            and body['gate_profile_sha256']==facts['gate_profile_sha256']==GATE_PROFILE_SHA256
                            and records.manifest_digest(body)==request['manifest_sha256'],'MANIFEST_PAYLOAD_HASH_OR_VERSION')
                        existing=draft['sets'].get(request['manifest_sha256'])
                        need(existing is None or existing==body,'MANIFEST_IMMUTABLE')
                        draft['sets'][request['manifest_sha256']]=body;draft['current_set']=request['manifest_sha256']
                        record={'manifest_sha256':request['manifest_sha256'],'set_version':request['set_version']}
                    elif op=='RESERVE_ATTEMPT':
                        need(body is None,'RESERVATION_PAYLOAD_FORBIDDEN')
                        seq=1+sum(a['manifest_sha256']==request['manifest_sha256'] and a['case_key']==request['case_key'] for a in draft['attempts'])
                        attempt=dict(attempt_key=records.digest('DEFINITION_ATTEMPT_KEY_0_1',{'effect':effect,'seq':seq}),
                            case_key=request['case_key'],case_version=request['case_version'],attempt_seq=seq,
                            candidate_hash=request['candidate_hash'],manifest_sha256=request['manifest_sha256'],
                            context_sha256=records.digest('DEFINITION_DEPENDENCY_CONTEXT_0_1',context),started_us=now)
                        attempt.update(executor_login=actor,reservation_sha256=records.digest('DEFINITION_RESERVATION_0_1',attempt))
                        draft['attempts'].append(attempt)
                        record={k:attempt[k] for k in ('attempt_key','attempt_seq','reservation_sha256')}
                    elif op=='RECORD_OUTCOME':
                        need(type(body) is dict and set(body)=={'finished_us','observed_sha256','executor_sha256'},'OUTCOME_PAYLOAD_FIELDS')
                        outcome=dict(body,attempt_key=request['attempt_key'],status=request['result'],artifact_sha256=request['artifact_sha256'])
                        draft['outcomes'].append(outcome)
                        record={'attempt_key':request['attempt_key'],'outcome_sha256':records.digest('DEFINITION_OUTCOME_0_1',outcome)}
                    else:
                        need(body is None,'CLIENT_RESTORE_IMAGES_FORBIDDEN')
                        material=tx.restore_material(request)
                        binding=material['binding']
                        need(binding['body']['evidence_mode']==mode and binding['model_binding_sha256']==request['restore_binding_sha256']
                            and binding['body']['candidate_hash']==request['candidate_hash']
                            and binding['body']['scope']==request['scope'],'RESTORE_STORED_BINDING_MISMATCH')
                        need(material['state']['evidence_mode']==mode and material['state']['journal']=={}
                            and material['context']['evidence_mode']==mode and material['context']['restorer_ref']==actor
                            and material['context']['scope']==request['scope']
                            and records.digest('DEFINITION_RESTORE_CONTEXT_0_1',material['context'])==facts['restore_context_sha256']
                            and material['context']['school_snapshot_sha256']==facts['canon_snapshot_sha256']
                            and material['context']['predecessor_eligible']==facts['predecessor_eligible']
                            and material['context']['outgoing_source_active']==facts['source_active'],
                            'RESTORE_CONTEXT_OR_ACTOR_BINDING')
                        records.validate_restore_binding(binding)
                    validate_document(draft,request,mode)
                    if draft['current_set'] is not None:
                        evaluate_document(draft,context,now)  # Strict record relations, not genuine Canon PASS.
                    fresh=bound_facts(tx,request,doc)
                    final_T=tx.now_us()
                    final=authority.commit_fence(plan['plan'],fresh,now_us=final_T)
                    need(final['policy_match'],final['reason'])
                    # All native operations occur under the backend's same owned root.
                    if op=='RESTORE_KV':
                        now=final_T
                        planner=records.RestorePlanner(material['state'])
                        restore_plan=records.plan_restore(material['state'],material['binding'],material['context'],
                            request_key=request['request_key'],evidence_sha256=request['verification_bundle_sha256'])
                        planned=planner.apply(restore_plan,material['context'],now_us=now)
                        need(planned['outcome']=='RESTORED_IN_MODEL','RESTORE_PLANNER_REJECTED')
                        writes=[w for w in planned['writes'] if w['table']=='public.canon_activation']
                        expected=records.projection(planner.state,material['binding']['body']['item_key'],request['scope'])
                        restore_post=records.digest('RESTORED_PROJECTION_0_1',expected)
                        record={'binding_sha256':request['restore_binding_sha256'],'postimage_sha256':restore_post}
                        tx.apply_restore(request,writes,material['binding']['body']['postimage'])
                        need(tx.current_restore_hash(request)==restore_post,'RESTORE_READBACK_MISMATCH')
                    draft['revision']=doc['revision']+1
                    command_row=dict(effect_sha256=effect,actor=actor,operation=op,applied_us=final_T,record=record,
                        after_material_sha256=material_hash(draft),restore_postimage_sha256=restore_post)
                    draft['commands'][request['request_key']]=command_row
                    validate_document(draft,request,mode)
                    readset=dict(authority_readset_sha256=plan['plan']['readset_sha256'],
                        document_sha256=records.digest('DOCUMENT_PREIMAGE_0_1',doc),
                        test_context_sha256=records.digest('TEST_CONTEXT_0_1',context),
                        restore_preimage_sha256=None if op!='RESTORE_KV' else records.digest('CANON_PREWRITE_0_1',material['state']))
                    full_writeset=[*writes,dict(table='bidding.video_canon_definition_state',
                        candidate_hash=request['candidate_hash'],scope=request['scope'],expected_revision=doc['revision'],
                        new_revision=draft['revision'],document_sha256=records.digest('DOCUMENT_POSTIMAGE_0_1',draft)),
                        dict(table='bidding.video_canon_definition_audit',request_key=request['request_key'],effect_sha256=effect,applied_us=final_T)]
                    tx.put(request,draft,expected_revision=doc['revision'])
                    tx.audit(request,dict(command_row,readset_sha256=records.digest('DEFINITION_STORE_READSET_0_1',readset),
                        writeset_sha256=records.digest('DEFINITION_WRITESET_0_1',full_writeset),evidence_mode=mode))
                    answer=response('SOURCE_ONLY_STORED' if mode=='SOURCE_ONLY' else 'VALIDATION_RECORD_STORED',
                        'UNREVIEWED_NOT_CANON_ADMISSION',mode,record=record,applied_us=final_T,writes=1+len(writes))
                need(records.digest('CLIENT_COMMAND_0_1',command)==command_before
                    and records.digest('CLIENT_PAYLOAD_0_1',payload)==payload_before,'CLIENT_INPUT_CHANGED')
                complete=True
            need(complete and answer is not None,'MUTATION_LEASE_INCOMPLETE')
            return answer
        except OutcomeUnknown:
            return response('OUTCOME_UNKNOWN','RECONCILE_SAME_KEY_NO_BLIND_RETRY',mode,effect_sha256=effect)
        except (StoreDenied,records.ContractError,authority.Denied) as exc:
            return response('ABSTAIN',str(exc),mode)
        except Exception:
            return response('ABSTAIN','TRUSTED_STORAGE_UNAVAILABLE',mode)

    def register_test_set(self,command,manifest):return self._run('REGISTER_SET',command,manifest)
    def reserve_attempt(self,command):return self._run('RESERVE_ATTEMPT',command,None)
    def record_outcome(self,command,outcome):return self._run('RECORD_OUTCOME',command,outcome)
    def restore_kv(self,command):return self._run('RESTORE_KV',command,None)

    def read_test_state(self,query):
        complete=False;answer=None;mode=None
        try:
            need(self.backend is not None and callable(getattr(self.backend,'observer',None)),'AUTHENTICATED_READER_UNBOUND')
            need(type(query) is dict and set(query)=={'candidate_hash','scope'},'READ_QUERY_FIELDS')
            original=records.digest('READ_QUERY_0_1',query);frozen=deepcopy(query)
            with self.backend.observer(deepcopy(frozen)) as tx:
                mode=tx.mode;first=deepcopy(tx.load(frozen));need(first is not None,'CONTRACT_STATE_NOT_FOUND')
                validate_document(first,frozen,mode)
                context=tx.test_context(frozen);need(context['evidence_mode']==mode,'TEST_CONTEXT_MODE_MISMATCH')
                state=evaluate_document(first,context,tx.now_us())
                second=deepcopy(tx.load(frozen));validate_document(second,frozen,mode)
                need(first==second,'READBACK_CHANGED')
                fresh_context=tx.test_context(frozen)
                need(records.digest('READ_CONTEXT_0_1',context)==records.digest('READ_CONTEXT_0_1',fresh_context),'READ_CONTEXT_CHANGED')
                state=evaluate_document(second,fresh_context,tx.now_us())
                answer=response('SOURCE_ONLY_READBACK' if mode=='SOURCE_ONLY' else 'VALIDATION_READBACK',
                    'DIAGNOSTIC_NOT_CANON_RECEIPT',mode,revision=first['revision'],
                    manifest_sha256=state['manifest_sha256'],state_sha256=state['state_sha256'],
                    structural_eligibility=state['structural_eligibility'],blockers=state['blockers'],
                    valid_until_us=state['valid_until_us'])
                need(records.digest('READ_QUERY_0_1',query)==original,'READ_QUERY_CHANGED')
                complete=True
            need(complete,'READ_LEASE_INCOMPLETE')
            return answer
        except (StoreDenied,records.ContractError,authority.Denied) as exc:
            return response('ABSTAIN',str(exc),mode)
        except Exception:
            return response('ABSTAIN','TRUSTED_READ_UNAVAILABLE',mode)

    def read_command(self,query,*,request_key,expected_effect_sha256):
        complete=False;answer=None;mode=None
        try:
            need(self.backend is not None and callable(getattr(self.backend,'observer',None)),'AUTHENTICATED_READER_UNBOUND')
            need(type(query) is dict and set(query)=={'candidate_hash','scope'}
                and authority.label(request_key) and authority.hash_ok(expected_effect_sha256),'COMMAND_OBSERVER_QUERY')
            original=records.digest('READ_QUERY_0_1',query);frozen=deepcopy(query)
            with self.backend.observer(deepcopy(frozen)) as tx:
                mode=tx.mode;first=deepcopy(tx.load(frozen))
                if first is None:
                    answer=response('OUTCOME_UNRESOLVED','ABSENCE_IS_NOT_NONCOMMIT_PROOF',mode)
                else:
                    validate_document(first,frozen,mode);record=first['commands'].get(request_key)
                    second=deepcopy(tx.load(frozen));validate_document(second,frozen,mode)
                    need(first==second,'COMMAND_OBSERVER_CHANGED')
                    if record is None:answer=response('OUTCOME_UNRESOLVED','ABSENCE_IS_NOT_NONCOMMIT_PROOF',mode)
                    else:
                        need(record['effect_sha256']==expected_effect_sha256,'IDEMPOTENCY_CONFLICT')
                        answer=response('SOURCE_ONLY_COMMAND_OBSERVED' if mode=='SOURCE_ONLY' else 'COMMAND_COMMIT_OBSERVED',
                            'HISTORICAL_EFFECT_NOT_CURRENT_CANON_AUTHORITY',mode,
                            applied_us=record['applied_us'],record=deepcopy(record['record']),effect_sha256=record['effect_sha256'])
                need(records.digest('READ_QUERY_0_1',query)==original,'READ_QUERY_CHANGED')
                complete=True
            need(complete,'COMMAND_OBSERVER_LEASE_INCOMPLETE')
            return answer
        except (StoreDenied,records.ContractError,authority.Denied) as exc:
            return response('ABSTAIN',str(exc),mode)
        except Exception:
            return response('ABSTAIN','TRUSTED_COMMAND_OBSERVER_UNAVAILABLE',mode)
