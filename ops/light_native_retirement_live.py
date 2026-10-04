"""Authenticated checked-observation adapter for failed-prepare proposals.

No import-time DB/host/provider access. No SQL write, table lock, service change,
ACK, intake or provider submission occurs in this module. The driver is loaded
once by the authenticated child; historical helper packages are data, not imports.
"""
import base64
from contextlib import contextmanager
from dataclasses import asdict
import os

from ops import light_native_retirement as r


def tracked(view):
    from ops import light_native_lane_controller as owner
    return dict(_read=lambda path,pin=None: view.read(path.relative_to(owner.ROOT).as_posix(),pin),
                _names=lambda path: view.names(path.relative_to(owner.ROOT).as_posix()))


class IssueAuthority:
    """In-process issuer capability, never accepted from request JSON."""
    def __init__(self,policy,index,prepare):
        self.policy=policy;self.index=index;self.prepare=prepare


def catalogue(view,refs,*,pending=None,evidence=None):
    """Root history only. No caller-supplied descendant evidence is accepted."""
    from ops import light_native_lane_controller as owner
    from ops import light_native_lane_issuer as issuer
    from ops import light_native_lane_cycle as phases
    r.references(refs)
    allowed={x['policy_sha256']:x for x in refs};found=set();records=[];descendants=[]
    names=view.names('issuers')
    r.need('cycle.lock' in names,'LANE_ISSUER_HISTORY')
    view.read('issuers/cycle.lock')
    for digest in sorted(names-{'cycle.lock'}):
        r.need(r.digest(digest),'LANE_ISSUER_HISTORY')
        base='issuers/'+digest
        policy=r.parse(view.read(base+'/policy.json',digest))
        r.need(issuer.policy_shape(policy) and policy['action']=='issue','LANE_ISSUER_HISTORY')
        stop=None
        if digest in allowed:
            ref=allowed[digest];found.add(digest);stop=ref['index']
            raw=view.read(base+'/'+format(stop,'04d')+'/'+r.NAME,ref['record_sha256'])
            value=r.record(raw,ref['record_sha256'])
            r.need(value['policy_sha256']==digest and value['index']==stop,'LANE_RETIREMENT_BINDING')
            r.need(evidence is not None and digest in evidence,'LANE_RETIREMENT_PINS')
            # This also proves there are no issued entries after the retired one.
            local(view,value,evidence[digest]);records.append(value)
        elif pending is not None and digest==pending.policy:
            stop=pending.index
            r.need(view.names(base)=={'policy.json'}|{format(n,'04d') for n in range(stop+1)},
                   'LANE_ISSUER_HISTORY')
            path=base+'/'+format(stop,'04d')
            r.need(view.names(path)=={'intent.json'},'LANE_ISSUER_HISTORY')
            intent=r.parse(view.read(path+'/intent.json'))
            expected=issuer.derive(policy,digest,stop,pending.prepare['predecessor'],intent['start'])
            r.need(intent=={'start':intent['start'],'cycle':expected}
                   and expected['prepare']==pending.prepare,'LANE_RETIREMENT_ISSUER_BINDING')
        count,previous=issuer.progress(owner.ROOT/base,policy,digest,stop_before=stop,**tracked(view))
        if stop is not None:r.need(count==stop,'LANE_ISSUER_HISTORY')
        if pending is not None and digest==pending.policy:
            r.need(previous==pending.prepare['predecessor'],'LANE_RETIREMENT_PREDECESSOR')
        for index in range(count):
            path=base+'/'+format(index,'04d')
            intent=r.parse(view.read(path+'/intent.json'));prepare=intent['cycle']['prepare']
            plan_hash=prepare['accepted_plan_sha256'];cycle='cycles/'+plan_hash
            result=r.parse(view.read(cycle+'/complete.json'))
            terminal=r.parse(view.read(plan_hash+'/terminal.json',result['terminal_sha256']))
            plan=r.parse(view.read(plan_hash+'/plan.json',plan_hash))
            for name in ('intake.json','discovery.json','permit.json','execution.json','complete.json'):
                view.read(plan_hash+'/'+name)
            for action in phases.STEPS:
                r.need(view.read(cycle+'/'+action+'-intent.json')==owner.encoded(phases.derive(prepare,action)),
                       'LANE_RETIREMENT_DESCENDANTS')
                phases.checked_result(action,r.parse(view.read(cycle+'/'+action+'-done.json')),prepare)
            r.need(view.read(cycle+'/incident.json',optional=True) is None,'LANE_ISSUER_HISTORY')
            # Both the durable terminal and the catalogue scope identify this job.
            r.need(terminal['dispatch_id']==result['dispatch_id'] and terminal['task_id']==result['task_id'],
                   'LANE_RETIREMENT_DESCENDANTS')
            descendants.append(dict(sequence=result['sequence'],plan_sha256=plan_hash,
                terminal_sha256=result['terminal_sha256'],dispatch_id=result['dispatch_id'],
                predecessor=prepare['predecessor'],work_key=plan['work_key'],terminal=terminal,
                policy=policy))
    r.need(found==set(allowed),'LANE_RETIREMENT_REFERENCE')
    # Every post-anchor descendant explicitly accepted this exact retirement and
    # the same original-content pins. Old successful prefix policies predate it.
    for value in records:
        ref=allowed[value['policy_sha256']]
        for item in descendants:
            if item['sequence']>value['predecessor']['sequence']:
                p=item['policy']
                r.need(p['version']==2 and ref in p['retirements'] and
                       p['retirement_evidence'][value['policy_sha256']]==evidence[value['policy_sha256']],
                       'LANE_RETIREMENT_DESCENDANTS')
    return records,descendants


def history_gate(policy,accepted):
    from ops import light_native_lane_controller as owner
    from ops import light_native_lane_issuer as issuer
    refs=policy.get('retirements',[])
    if not refs:
        for path in (owner.ROOT/'issuers').iterdir():
            if path.name=='cycle.lock':continue
            r.need(r.digest(path.name),'LANE_ISSUER_HISTORY')
            issuer.progress(path,owner.parse(owner.read(path/'policy.json',path.name)),path.name)
        return
    r.need(all(x['policy_sha256']!=accepted for x in refs),'LANE_RETIREMENT_CANCELED_POLICY')
    with r.Snapshot(owner.ROOT,'unused') as view:
        records,_=catalogue(view,refs,evidence=policy['retirement_evidence'])
        for value in records:
            for entry in policy['plans']:
                plan=r.parse(base64.b64decode(entry['plan_base64'],validate=True))
                r.need(entry['accepted_plan_sha256']!=value['plan_sha256'] and
                       (plan['repository'],plan['work_key'])!=(value['repository'],value['failed_work_key']),
                       'LANE_RETIREMENT_CANCELED_WORK')
        view.check()


def bound_policy(prepare):
    from ops import light_native_lane_controller as owner
    from ops import light_native_lane_issuer as issuer
    binding=prepare.get('issuer')
    r.need(type(binding) is dict and set(binding)=={'policy_sha256','index'}
           and r.digest(binding['policy_sha256']) and type(binding['index']) is int
           and 0<=binding['index']<8,'LANE_RETIREMENT_ISSUER_BINDING')
    policy=r.parse(owner.read(owner.ROOT/'issuers'/binding['policy_sha256']/'policy.json',binding['policy_sha256']))
    r.need(issuer.policy_shape(policy) and policy['version']==2,'LANE_RETIREMENT_ISSUER_BINDING')
    pending=IssueAuthority(binding['policy_sha256'],binding['index'],prepare)
    return policy,pending


def entry_guard(prepare,authority=None):
    """All new-work routes respect unresolved history and canceled identities."""
    from ops import light_native_lane_controller as owner
    root=owner.ROOT/'issuers'
    if not root.exists():
        r.need(not root.is_symlink() and prepare.get('version')!=3,'LANE_ISSUER_HISTORY')
        return
    if prepare.get('version')==3:
        policy,pending=bound_policy(prepare)
        r.need(type(authority) is IssueAuthority and authority.__dict__==pending.__dict__,
               'LANE_RETIREMENT_ISSUER_BINDING')
    else:
        policy={'retirements':[]};pending=authority
        r.need(pending is None or type(pending) is IssueAuthority and pending.prepare==prepare,
               'LANE_RETIREMENT_ISSUER_BINDING')
    with r.Snapshot(owner.ROOT,'unused') as view:
        records,_=catalogue(view,policy.get('retirements',[]),pending=pending,
                            evidence=policy.get('retirement_evidence'))
        plan=r.parse(base64.b64decode(prepare['plan_base64'],validate=True))
        for value in records:
            r.need(prepare['accepted_plan_sha256']!=value['plan_sha256'] and
                   (plan['repository'],plan['work_key'])!=(value['repository'],value['failed_work_key']),
                   'LANE_RETIREMENT_CANCELED_WORK')
        view.check()


def consume(policy,accepted,proposed,conn,guard,*,pending=None):
    from ops import light_native_lane_controller as owner
    refs=policy.get('retirements',[])
    if not refs:return
    r.need(all(x['policy_sha256']!=accepted for x in refs),'LANE_RETIREMENT_CANCELED_POLICY')
    with r.Snapshot(owner.ROOT,'unused') as view:
        records,descendants=catalogue(view,refs,pending=pending,evidence=policy['retirement_evidence'])
        for value in records:
            for entry in policy['plans']:
                plan=r.parse(base64.b64decode(entry['plan_base64'],validate=True))
                r.need(entry['accepted_plan_sha256']!=value['plan_sha256'] and
                       (plan['repository'],plan['work_key'])!=(value['repository'],value['failed_work_key']),
                       'LANE_RETIREMENT_CANCELED_WORK')
            first=observer(view,value,conn,guard,pins=policy['retirement_evidence'][value['policy_sha256']],
                           latest=proposed['predecessor'],descendants=descendants)
            r.need(observer(view,value,conn,guard,pins=policy['retirement_evidence'][value['policy_sha256']],
                            latest=proposed['predecessor'],descendants=descendants)==first,'LANE_RETIREMENT_DRIFT')
        view.check()


def prepare_guard(value,authority,wheels,credential,guard):
    from ops import light_native_lane_controller as owner
    from ops import light_native_lane_cycle as cycle
    pending=None
    if authority is not None:
        r.need(type(authority) is cycle._Authority,'LANE_RETIREMENT_ISSUER_BINDING')
        if value['version']==3:
            _,pending=bound_policy(value)
        elif (owner.ROOT/'issuers').exists():
            for directory in (owner.ROOT/'issuers').iterdir():
                if directory.name=='cycle.lock':continue
                r.need(r.digest(directory.name),'LANE_ISSUER_HISTORY')
                for path in directory.iterdir():
                    if path.name=='policy.json':continue
                    r.need(path.name.isdigit() and len(path.name)==4,'LANE_ISSUER_HISTORY')
                    if owner.parse(owner.read(path/'intent.json'))['cycle']['prepare']==value:
                        r.need(pending is None,'LANE_ISSUER_HISTORY')
                        pending=IssueAuthority(directory.name,int(path.name),value)
    entry_guard(value,pending)
    if value['version']!=3:return
    r.need(authority is not None,'LANE_RETIREMENT_ISSUER_BINDING')
    policy,pending=bound_policy(value)
    from ops.light_native_bounded import run
    def check():
        from ops.native_maintenance_owner_host import loaded_runtime
        from ops.native_maintenance_owner_attest import parameters
        with loaded_runtime(wheels) as (psycopg,_):
            with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                conn.read_only=True
                consume(policy,pending.policy,{'predecessor':value['predecessor']},conn,guard,pending=pending)
        return True
    r.need(run(check) is True,'LANE_RETIREMENT_REFUSED')


WRITE_KEYS={'version','action','source','accepted_controller_sha256','accepted_runtime_sha256',
            'record_base64','record_sha256','historical_journal_sha256','agreement','accepted_agreement_sha256'}

REFERENCE_KEYS={'version','action','source','accepted_controller_sha256','accepted_runtime_sha256',
    'policy_sha256','index','record_sha256','journal_map_sha256','private_request_sha256',
    'agreement','accepted_agreement_sha256'}
PUBLIC_EVIDENCE='OWNER_ACCEPTED_ONE_CREATE_ONLY_RETIREMENT_PROPOSAL'


def public_result(value,accepted_record):
    r.need(type(value) is dict and set(value)=={'audit','state','record_sha256',
        'incident_closed','execution_acknowledged','new_task_authorized'}
        and value['audit']=='LIGHT_LANE_RETIREMENT'
        and value['state'] in ('PROPOSAL_RETAINED_UNACCEPTED','PROPOSAL_PRESENT_UNACCEPTED')
        and r.digest(accepted_record) and value['record_sha256']==accepted_record and all(value[k] is False for k in
        ('incident_closed','execution_acknowledged','new_task_authorized')), 'LANE_RETIREMENT_REFUSED')
    return value


def public_reference(raw,*,action='retire-prepare-reference'):
    """Only public pins and finite coordination metadata may cross dispatch."""
    from ops.native_maintenance_agreement import Agreement,COVERAGE
    value=r.parse(raw,limit=8192)
    r.need(type(value) is dict and set(value)==REFERENCE_KEYS and r.encoded(value)==raw
           and type(value['version']) is int and value['version']==1
           and action in ('retire-prepare-reference','observe-retirement-reference','diagnose-local-reference')
           and value['action']==action,'LANE_RETIREMENT_REFERENCE')
    r.references([{k:value[k] for k in ('policy_sha256','index','record_sha256')}])
    r.need(r.digest(value['source'],40) and all(r.digest(value[k]) for k in
        ('accepted_controller_sha256','accepted_runtime_sha256','journal_map_sha256',
         'private_request_sha256','accepted_agreement_sha256')),'LANE_RETIREMENT_REFERENCE')
    agreement=value['agreement']
    r.need(type(agreement) is dict and set(agreement)=={'version','owner','operation_digest',
        'not_before','expires_at','coverage','evidence'} and type(agreement['version']) is int
        and agreement['version']==1 and agreement['owner']=='olegmed1-art'
        and agreement['coverage']==COVERAGE and agreement['evidence']==(
            'OWNER_ACCEPTED_LOCAL_JOURNAL_DIAGNOSTIC' if action=='diagnose-local-reference' else
            'OWNER_ACCEPTED_READ_ONLY_RETIREMENT_OBSERVATION' if action=='observe-retirement-reference' else PUBLIC_EVIDENCE)
        and r.digest(agreement['operation_digest'])
        and r.sha(r.encoded(agreement))==value['accepted_agreement_sha256'],
        'LANE_RETIREMENT_REFERENCE')
    start,end=(Agreement.timestamp(agreement[k]) for k in ('not_before','expires_at'))
    r.need(0<end-start<=1800,'LANE_RETIREMENT_REFERENCE')
    return value


def resolve_reference(raw,accepted,controller,runtime,guard,*,read_only=False,local_only=False):
    """Reconstruct private bytes in protected root memory; never mint acceptance.

    Reads use fixed existing paths and the same no-follow metadata verifier.
    The original writer subsequently rechecks every accepted byte and live proof.
    """
    from ops import light_native_lane_controller as owner
    from ops import light_native_lane_issuer as issuer
    from ops.native_maintenance_agreement import Agreement
    import time
    r.need(r.sha(raw)==accepted,'LANE_RETIREMENT_NOT_ACCEPTED')
    r.need(type(read_only) is bool and type(local_only) is bool and (not local_only or read_only),'LANE_RETIREMENT_REFERENCE')
    value=public_reference(raw,action='diagnose-local-reference' if local_only else 'observe-retirement-reference' if read_only else 'retire-prepare-reference')
    owner.validate_package(controller,value['source'],value['accepted_controller_sha256'])
    r.need(r.sha(runtime)==value['accepted_runtime_sha256'],'LANE_RETIREMENT_BINDING')
    guard.assert_current();owner.release.staging.require_current_main(value['source'])
    start,end=(Agreement.timestamp(value['agreement'][k]) for k in ('not_before','expires_at'))
    r.need(start<=time.time()<end,'LANE_RETIREMENT_REFERENCE')
    policy_hash=value['policy_sha256'];index=value['index']
    entry='issuers/'+policy_hash+'/'+format(index,'04d')
    with r.Snapshot(owner.ROOT,'unused' if read_only else entry) as view:
        policy=r.parse(view.read('issuers/'+policy_hash+'/policy.json',policy_hash))
        r.need(set(policy)==issuer.POLICY_KEYS and policy['version']==1
               and policy['action']=='issue' and index<len(policy['plans']), 'LANE_RETIREMENT_REFERENCE')
        plan_hash=policy['plans'][index]['accepted_plan_sha256']
        r.need(r.digest(plan_hash),'LANE_RETIREMENT_REFERENCE')
        plan=r.parse(view.read(plan_hash+'/plan.json',plan_hash))
        intent=r.parse(view.read(entry+'/intent.json'))
        cycle='cycles/'+plan_hash
        paths={name:plan_hash+'/'+name for name in
            ('baseline.json','before.json','contained.json','prepare.json','runtime-package.json','wheels.tar')}
        paths.update({'cycle-contain-intent':cycle+'/contain-intent.json','cycle-intent':cycle+'/intent.json',
            'driver-contain-intent':plan_hash+'/contain-intent.json','incident.json':cycle+'/incident.json',
            'issuer-intent':entry+'/intent.json'})
        pins={name:r.sha(view.read(path,limit=16*1024*1024)) for name,path in paths.items()}
        r.need(r.sha(r.encoded(pins))==value['journal_map_sha256'],'LANE_RETIREMENT_PINS')
        record=dict(version=1,kind='FAILED_PREPARE_RETIREMENT_PROPOSAL',
            requested_disposition='RETIRE_FAILED_PREPARE_AND_CANCEL_OLD_POLICY_REMAINDER',
            policy_sha256=policy_hash,index=index,plan_sha256=plan_hash,repository=plan['repository'],
            failed_work_key=plan['work_key'],failed_target_pr=plan['target_pr'],
            predecessor=intent['cycle']['prepare']['predecessor'],incident_sha256=pins['incident.json'],
            historical_controller_source=policy['source'],historical_controller_sha256=policy['accepted_controller_sha256'],
            retained_runtime_source=owner.install.RETAINED_SOURCE,retained_runtime_sha256=value['accepted_runtime_sha256'],
            original_failure='UNKNOWN',interpretation='EXPLICIT_NEW_POLICY_HASH_ALLOWLIST_AND_FRESH_GUARDS_REQUIRED',
            **{k:False for k in r.FALSE_FLAGS})
        proposal=r.encoded(record);r.record(proposal,value['record_sha256'])
        request={k:value[k] for k in ('version','source','accepted_controller_sha256',
            'accepted_runtime_sha256','record_sha256','agreement','accepted_agreement_sha256')}
        request.update(action='diagnose-local' if local_only else 'observe-retirement' if read_only else 'retire-prepare',record_base64=base64.b64encode(proposal).decode(),
                       historical_journal_sha256=pins)
        private=r.encoded(request)
        r.need(r.sha(private)==value['private_request_sha256'],'LANE_RETIREMENT_NOT_ACCEPTED')
        # Validate the independently approved finite scope before any write/DB access.
        write_request(private,value['private_request_sha256'],controller,runtime,guard,read_only=read_only,local_only=local_only)
        view.check()
    return private,value['private_request_sha256']


def write_request(raw,accepted,controller,runtime,guard,*,read_only=False,local_only=False):
    from ops import light_native_lane_controller as owner
    from ops.native_maintenance_agreement import Agreement
    r.need(r.sha(raw)==accepted,'LANE_RETIREMENT_NOT_ACCEPTED')
    value=r.parse(raw)
    r.need(type(value) is dict and set(value)==WRITE_KEYS and type(value['version']) is int
           and type(read_only) is bool and type(local_only) is bool and (not local_only or read_only) and value['version']==1
           and value['action']==('diagnose-local' if local_only else 'observe-retirement' if read_only else 'retire-prepare'),'LANE_RETIREMENT_SCHEMA')
    owner.validate_package(controller,value['source'],value['accepted_controller_sha256'])
    r.need(r.sha(runtime)==value['accepted_runtime_sha256'],'LANE_RETIREMENT_BINDING')
    payload=base64.b64decode(value['record_base64'],validate=True)
    proposal=r.record(payload,value['record_sha256']);r.journal_pins(value['historical_journal_sha256'])
    r.need(proposal['retained_runtime_sha256']==value['accepted_runtime_sha256'],'LANE_RETIREMENT_BINDING')
    scope=dict(version=1,operation=('READ_ONLY_DIAGNOSE_LOCAL_RETIREMENT_JOURNALS' if local_only else
               'READ_ONLY_VERIFY_FAILED_PREPARE_RETIREMENT' if read_only else
               'CREATE_FAILED_PREPARE_RETIREMENT_PROPOSAL'),source=value['source'],
               controller_sha256=value['accepted_controller_sha256'],runtime_sha256=value['accepted_runtime_sha256'],
               record_sha256=value['record_sha256'],historical_journal_sha256=value['historical_journal_sha256'])
    agreement=Agreement(value['agreement'],value['accepted_agreement_sha256'],scope)
    guard.assert_current();owner.release.staging.require_current_main(value['source'])
    return value,payload,agreement


def write(wheels,credential,controller,runtime,raw,accepted,guard):
    """One bounded owner action; sole durable effect is the create-only proposal."""
    from ops import light_native_lane_controller as owner
    from ops import light_native_lane_cycle as cycle
    from ops.light_native_bounded import run
    r.need(os.geteuid()==0 and os.uname().nodename=='autopilot-lite-vnic','LANE_RETIREMENT_ROOT')
    def operation():
        request,request_pin=raw,accepted
        if r.parse(raw).get('action')=='retire-prepare-reference':
            request,request_pin=resolve_reference(raw,accepted,controller,runtime,guard)
        value,payload,agreement=write_request(request,request_pin,controller,runtime,guard)
        retained=r.parse(runtime,limit=3*1024*1024)
        r.need(owner.release.encoded(retained)==runtime and retained['source']==owner.install.RETAINED_SOURCE,
               'LANE_RETIREMENT_BINDING')
        owner.release.validate(retained['runtime'],retained['source'],retained['runtime']['sha256'])
        owner.release.staging.verify_release(owner.execution.plan.source_path(retained['source']),retained['runtime'])
        from ops.native_maintenance_owner_host import loaded_runtime
        from ops.native_maintenance_owner_attest import parameters
        with cycle.exclusive(owner.ROOT/'issuers',create=False), cycle.exclusive(owner.ROOT/'cycles',create=False):
            with loaded_runtime(wheels) as (psycopg,_):
                with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                    conn.read_only=True
                    def observe(view,proposal):
                        for path in ('issuers/cycle.lock','cycles/cycle.lock'):view.read(path)
                        agreement.assert_held(agreement.scope)
                        owner.release.staging.require_current_main(value['source'])
                        result=observer(view,proposal,conn,guard,pins=value['historical_journal_sha256'])
                        agreement.assert_held(agreement.scope)
                        return result
                    result=r.write_proposal(owner.ROOT,payload,value['record_sha256'],observe)
        return dict(audit='LIGHT_LANE_RETIREMENT',**result,incident_closed=False,
                    execution_acknowledged=False,new_task_authorized=False)
    return run(operation)


def read_only_reference(raw):
    action=r.parse(raw,limit=8192).get('action')
    r.need(action in ('observe-retirement-reference','diagnose-local-reference'),'LANE_RETIREMENT_REFERENCE')
    return public_reference(raw,action=action)


def inspect_local(wheels,credential,controller,runtime,raw,accepted,guard):
    """One bounded local-only diagnostic. DB/provider/service ports are absent."""
    from ops import light_native_lane_controller as owner
    from ops.native_maintenance_agreement import Agreement
    from ops.light_native_bounded import run
    import time
    r.need(os.geteuid()==0 and os.uname().nodename=='autopilot-lite-vnic','LANE_RETIREMENT_ROOT')
    reference=public_reference(raw,action='diagnose-local-reference')
    def operation():
        diagnostic=LocalDiagnostic()
        try:
            r.need(r.sha(raw)==accepted,'LANE_RETIREMENT_NOT_ACCEPTED')
            owner.validate_package(controller,reference['source'],reference['accepted_controller_sha256'])
            r.need(r.sha(runtime)==reference['accepted_runtime_sha256'],'LANE_RETIREMENT_BINDING')
            start,end=(Agreement.timestamp(reference['agreement'][k]) for k in ('not_before','expires_at'))
            r.need(start<=time.time()<end,'LANE_RETIREMENT_REFERENCE')
            guard.assert_current();owner.release.staging.require_current_main(reference['source'])
            diagnostic.at('LOCKS')
            with observation_locks(owner.ROOT),r.Snapshot(owner.ROOT,'unused') as view:
                diagnostic.at('RECONSTRUCTION')
                request,pin=resolve_reference(raw,accepted,controller,runtime,guard,read_only=True,local_only=True)
                diagnostic.at('REQUEST_AUTHORITY')
                value,payload,agreement=write_request(request,pin,controller,runtime,guard,read_only=True,local_only=True)
                proposal=r.record(payload,value['record_sha256'])
                diagnostic.at('LOCAL_START')
                local(view,proposal,value['historical_journal_sha256'],observation=True,diagnostic=diagnostic)
                diagnostic.at('FINAL_SNAPSHOT');view.check()
                diagnostic.at('FINAL_GUARDS');agreement.assert_held(agreement.scope)
                guard.assert_current();owner.release.staging.require_current_main(reference['source'])
            diagnostic.at('DONE');result=diagnostic.result()
        except BaseException as exc:
            result=diagnostic.result(exc)
        result['record_sha256']=reference['record_sha256']
        return public_local_result(result,reference['record_sha256'])
    return run(operation)


def _local_condition(diagnostic,checkpoint,callback):
    if diagnostic is not None:diagnostic.at(checkpoint)
    value=callback()
    return value


class LocalDiagnostic:
    """Fixed roles and checkpoint IDs only. Raw read values never cross egress."""
    ROLES=('BASELINE','BEFORE','CONTAINED','PREPARE','RUNTIME_PACKAGE','WHEELS',
        'CYCLE_CONTAIN_INTENT','CYCLE_INTENT','DRIVER_CONTAIN_INTENT','INCIDENT','ISSUER_INTENT',
        'POLICY','PLAN','CYCLE_PREPARE_INTENT','CYCLE_CONTAIN_DONE','HISTORICAL_PACKAGE','HISTORICAL_HELPER',
        'PRIOR_PLAN','PRIOR_TERMINAL','PRIOR_INTAKE','PRIOR_COMPLETE','PRIOR_RESTORED',
        'PRIOR_EXECUTION','PRIOR_PREPARE','PRIOR_RESTORE_INTENT','PRIOR_RESTART_INTENT')
    REASONS=('NONE','MISSING','METADATA','SIZE','HASH_MISMATCH','JSON_SYNTAX','JSON_KEYS',
        'JSON_TYPE','DRIFT','PREDICATE_FALSE','IO_REFUSED','UNCLASSIFIED')
    STAGES=('AUTHORITY','LOCKS','RECONSTRUCTION','REQUEST_AUTHORITY','LOCAL_START','FINAL_SNAPSHOT','FINAL_GUARDS','DONE')
    def __init__(self):
        self.checkpoint='AUTHORITY';self.role=-1;self.helper_index=-1
        self.trace=[[0,0,0,0] for _ in self.ROLES];self.raw_roles={}
    def at(self,checkpoint):self.checkpoint=checkpoint
    @staticmethod
    def reason(exc):
        if isinstance(exc,r.Refusal):
            return {'LANE_RETIREMENT_MISSING':'MISSING','LANE_RETIREMENT_METADATA':'METADATA',
                'LANE_RETIREMENT_SIZE':'SIZE','LANE_RETIREMENT_NOT_ACCEPTED':'HASH_MISMATCH',
                'LANE_RETIREMENT_DRIFT':'DRIFT'}.get(exc.code,'PREDICATE_FALSE')
        if isinstance(exc,KeyError):return 'JSON_KEYS'
        if isinstance(exc,(TypeError,AttributeError)):return 'JSON_TYPE'
        if isinstance(exc,(ValueError,UnicodeDecodeError)):return 'JSON_SYNTAX'
        if isinstance(exc,FileNotFoundError):return 'MISSING'
        if isinstance(exc,OSError):return 'IO_REFUSED'
        return 'UNCLASSIFIED'
    def wrap(self,view,value):
        from ops import light_native_lane_controller as owner
        plan=value['plan_sha256'];cycle='cycles/'+plan;entry=r.entry(value)
        old='controllers/'+value['historical_controller_source'];prior=value['predecessor']['plan_sha256']
        paths=[plan+'/'+name for name in ('baseline.json','before.json','contained.json','prepare.json','runtime-package.json','wheels.tar')]
        paths += [cycle+'/contain-intent.json',cycle+'/intent.json',plan+'/contain-intent.json',cycle+'/incident.json',entry+'/intent.json',
            'issuers/'+value['policy_sha256']+'/policy.json',plan+'/plan.json',cycle+'/prepare-intent.json',cycle+'/contain-done.json',old+'/package.json']
        self.paths={p:n for n,p in enumerate(paths)}
        self.paths.update({prior+'/'+name:17+n for n,name in enumerate(('plan.json','terminal.json','intake.json','complete.json',
            'controls-restored.json','execution.json','prepare.json','restore-intent.json','restart-intent.json'))})
        self.helpers={old+'/'+name:n for n,name in enumerate(sorted(set((*owner.release.HELPERS,*owner.EXTRA))))}
        diagnostic=self
        class Reader:
            def __getattr__(self,name):return getattr(view,name)
            def read(self,path,pin=None,**kwargs):
                role=diagnostic.paths.get(path,16 if path in diagnostic.helpers else -1)
                diagnostic.role=role;diagnostic.helper_index=diagnostic.helpers.get(path,-1)
                # Trusted prefix-history reads still use the original Snapshot.
                # Unknown roles carry no public path or invented schema guard.
                if role<0:return view.read(path,pin,**kwargs)
                row=diagnostic.trace[role]
                try:raw=view.read(path,pin,**kwargs)
                except BaseException as exc:
                    reason=diagnostic.reason(exc)
                    if reason=='METADATA':row[1]=2
                    elif reason=='HASH_MISMATCH':row[:3]=[1,1,2]
                    else:row[0]=2
                    raise
                row[:3]=[1,1,1 if pin is not None or row[2]==1 else 3]
                diagnostic.raw_roles[id(raw)]=(raw,role)
                return raw
        return Reader()
    def parse(self,raw,**kwargs):
        role=self.raw_roles.get(id(raw),(None,-1))[1]
        if role>=0:self.role=role
        try:value=r.parse(raw,**kwargs)
        except BaseException:
            if role>=0:self.trace[role][3]=2
            raise
        # Parsing alone does not certify the later semantic schema predicates.
        return value
    def result(self,exc=None):
        if exc is None:
            for index,row in enumerate(self.trace):
                if row[0]==1:row[3]=3 if index in (5,16) else 1
        return dict(audit='LIGHT_LANE_LOCAL_DIAGNOSTIC',version=1,state='LOCAL_CHECKED' if exc is None else 'LOCAL_REFUSED',
            checkpoint=self.checkpoint,reason='NONE' if exc is None else self.reason(exc),role=self.role,helper_index=self.helper_index,
            trace=self.trace,local_checks_final=exc is None,proposal_observation_final=False,hold_db_provider_verified=False,
            incident_closed=False,execution_acknowledged=False,new_task_authorized=False)


def public_local_result(value,accepted_record):
    import re
    keys={'audit','version','state','checkpoint','reason','role','helper_index','trace','local_checks_final','record_sha256',
        'proposal_observation_final','hold_db_provider_verified','incident_closed','execution_acknowledged','new_task_authorized'}
    r.need(type(value) is dict and set(value)==keys and r.digest(accepted_record) and value['record_sha256']==accepted_record
        and value['audit']=='LIGHT_LANE_LOCAL_DIAGNOSTIC' and type(value['version']) is int and value['version']==1
        and value['state'] in ('LOCAL_CHECKED','LOCAL_REFUSED') and type(value['checkpoint']) is str
        and (value['checkpoint'] in LocalDiagnostic.STAGES or re.fullmatch(r'L(?:00[1-9]|0[1-8][0-9]|09[0-7])',value['checkpoint']))
        and value['reason'] in LocalDiagnostic.REASONS and type(value['role']) is int and -1<=value['role']<len(LocalDiagnostic.ROLES)
        and type(value['helper_index']) is int and -1<=value['helper_index']<35
        and type(value['local_checks_final']) is bool and value['local_checks_final']==(value['state']=='LOCAL_CHECKED')
        and ((value['state']=='LOCAL_CHECKED')==(value['checkpoint']=='DONE' and value['reason']=='NONE'))
        and all(value[k] is False for k in ('proposal_observation_final','hold_db_provider_verified','incident_closed',
            'execution_acknowledged','new_task_authorized')),'LANE_RETIREMENT_REFUSED')
    trace=value['trace']
    r.need(type(trace) is list and len(trace)==len(LocalDiagnostic.ROLES)
        and all(type(row) is list and len(row)==4 and all(type(x) is int and 0<=x<=3 for x in row) for row in trace)
        and len(r.encoded(value))<4096,'LANE_RETIREMENT_REFUSED')
    return value


def local(view, value, pins, *, observation=False, diagnostic=None):
    if diagnostic is not None:
        view = diagnostic.wrap(view, value)
    _parse = r.parse if diagnostic is None else diagnostic.parse
    if diagnostic is not None:
        diagnostic.at('L040')
    from ops import light_native_lane_controller as owner
    if diagnostic is not None:
        diagnostic.at('L041')
    from ops import light_native_lane_issuer as issuer
    if diagnostic is not None:
        diagnostic.at('L042')
    policy_root = 'issuers/' + value['policy_sha256']
    if diagnostic is not None:
        diagnostic.at('L043')
    entry = r.entry(value)
    if diagnostic is not None:
        diagnostic.at('L044')
    r.journal_pins(pins)
    if diagnostic is not None:
        diagnostic.at('L045')
    plan_hash = value['plan_sha256']
    if diagnostic is not None:
        diagnostic.at('L046')
    cycle = 'cycles/' + plan_hash
    if diagnostic is not None:
        diagnostic.at('L047')
    paths = {name: plan_hash + '/' + name for name in ('baseline.json', 'before.json', 'contained.json', 'prepare.json', 'runtime-package.json', 'wheels.tar')}
    if diagnostic is not None:
        diagnostic.at('L048')
    paths.update({'cycle-contain-intent': cycle + '/contain-intent.json', 'cycle-intent': cycle + '/intent.json', 'driver-contain-intent': plan_hash + '/contain-intent.json', 'incident.json': cycle + '/incident.json', 'issuer-intent': entry + '/intent.json'})
    if diagnostic is not None:
        diagnostic.at('L049')
    for name, path in paths.items():
        view.read(path, pins[name], limit=16 * 1024 * 1024)
    if diagnostic is not None:
        diagnostic.at('L050')
    policy = _parse(view.read(policy_root + '/policy.json', value['policy_sha256']))
    if diagnostic is not None:
        diagnostic.at('L051')
    r.need(_local_condition(diagnostic, 'L001', lambda: set(policy) == issuer.POLICY_KEYS) and _local_condition(diagnostic, 'L002', lambda: policy['version'] == 1) and _local_condition(diagnostic, 'L003', lambda: policy['action'] == 'issue') and _local_condition(diagnostic, 'L004', lambda: policy['source'] == value['historical_controller_source']) and _local_condition(diagnostic, 'L005', lambda: policy['accepted_controller_sha256'] == value['historical_controller_sha256']) and _local_condition(diagnostic, 'L006', lambda: policy['accepted_runtime_sha256'] == value['retained_runtime_sha256']), 'LANE_RETIREMENT_BINDING')
    if diagnostic is not None:
        diagnostic.at('L052')
    index = value['index']
    if diagnostic is not None:
        diagnostic.at('L053')
    r.need(_local_condition(diagnostic, 'L007', lambda: index < len(policy['plans'])), 'LANE_RETIREMENT_BINDING')
    if diagnostic is not None:
        diagnostic.at('L054')
    r.need(_local_condition(diagnostic, 'L008', lambda: view.names(policy_root) == {'policy.json'} | {format(n, '04d') for n in range(index + 1)}), 'LANE_RETIREMENT_LATER_ISSUE')
    if diagnostic is not None:
        diagnostic.at('L055')
    writing = view.output_parent == entry
    if diagnostic is not None:
        diagnostic.at('L056')
    if observation:
        r.need(_local_condition(diagnostic, 'L009', lambda: not writing) and _local_condition(diagnostic, 'L010', lambda: view.names(entry) in ({'intent.json'}, {'intent.json', r.NAME})), 'LANE_RETIREMENT_BINDING')
    else:
        r.need(_local_condition(diagnostic, 'L011', lambda: view.names(entry, allow_output=writing) == ({'intent.json'} if writing else {'intent.json', r.NAME})), 'LANE_RETIREMENT_BINDING')
    if diagnostic is not None:
        diagnostic.at('L057')
    intent = _parse(view.read(entry + '/intent.json'))
    if diagnostic is not None:
        diagnostic.at('L058')
    r.need(_local_condition(diagnostic, 'L012', lambda: set(intent) == {'start', 'cycle'}), 'LANE_RETIREMENT_BINDING')
    if diagnostic is not None:
        diagnostic.at('L059')
    count, previous = issuer.progress(owner.ROOT / policy_root, policy, value['policy_sha256'], stop_before=index, **tracked(view))
    if diagnostic is not None:
        diagnostic.at('L060')
    r.need(_local_condition(diagnostic, 'L013', lambda: count == index) and _local_condition(diagnostic, 'L014', lambda: previous == value['predecessor']), 'LANE_RETIREMENT_PREDECESSOR')
    if diagnostic is not None:
        diagnostic.at('L061')
    expected = issuer.derive(policy, value['policy_sha256'], index, previous, intent['start'])
    if diagnostic is not None:
        diagnostic.at('L062')
    r.need(_local_condition(diagnostic, 'L015', lambda: intent['cycle'] == expected), 'LANE_RETIREMENT_BINDING')
    if diagnostic is not None:
        diagnostic.at('L063')
    prepare = expected['prepare']
    if diagnostic is not None:
        diagnostic.at('L064')
    plan_hash = value['plan_sha256']
    if diagnostic is not None:
        diagnostic.at('L065')
    cycle = 'cycles/' + plan_hash
    if diagnostic is not None:
        diagnostic.at('L066')
    r.need(_local_condition(diagnostic, 'L016', lambda: prepare['accepted_plan_sha256'] == plan_hash), 'LANE_RETIREMENT_BINDING')
    if diagnostic is not None:
        diagnostic.at('L067')
    r.need(_local_condition(diagnostic, 'L017', lambda: view.names(cycle) == {'intent.json', 'prepare-intent.json', 'contain-intent.json', 'contain-done.json', 'incident.json'}), 'LANE_RETIREMENT_PHASE')
    if diagnostic is not None:
        diagnostic.at('L068')
    r.need(_local_condition(diagnostic, 'L018', lambda: view.read(cycle + '/intent.json') == owner.encoded(expected)) and _local_condition(diagnostic, 'L019', lambda: view.read(cycle + '/prepare-intent.json') == owner.encoded(prepare)) and _local_condition(diagnostic, 'L020', lambda: view.read(cycle + '/contain-intent.json') == owner.encoded(dict(prepare, action='contain'))), 'LANE_RETIREMENT_BINDING')
    if diagnostic is not None:
        diagnostic.at('L069')
    incident = _parse(view.read(cycle + '/incident.json', value['incident_sha256']))
    if diagnostic is not None:
        diagnostic.at('L070')
    r.need(_local_condition(diagnostic, 'L021', lambda: incident == {'phase': 'prepare', 'containment': 'INTAKE_ROLLED_BACK', 'state': 'RECONCILIATION_REQUIRED'}), 'LANE_RETIREMENT_PHASE')
    if diagnostic is not None:
        diagnostic.at('L071')
    r.need(_local_condition(diagnostic, 'L022', lambda: view.names(plan_hash) == {'plan.json', 'baseline.json', 'before.json', 'prepare.json', 'contain-intent.json', 'contained.json', 'runtime-package.json', 'wheels.tar'}), 'LANE_RETIREMENT_INTAKE')
    if diagnostic is not None:
        diagnostic.at('L072')
    raw_plan = view.read(plan_hash + '/plan.json', plan_hash)
    if diagnostic is not None:
        diagnostic.at('L073')
    r.need(_local_condition(diagnostic, 'L023', lambda: base64.b64decode(policy['plans'][index]['plan_base64'], validate=True) == raw_plan) and _local_condition(diagnostic, 'L024', lambda: view.read(plan_hash + '/prepare.json') == owner.encoded(prepare)), 'LANE_RETIREMENT_BINDING')
    if diagnostic is not None:
        diagnostic.at('L074')
    plan = _parse(raw_plan)
    if diagnostic is not None:
        diagnostic.at('L075')
    r.need(_local_condition(diagnostic, 'L025', lambda: plan['source'] == value['retained_runtime_source'] == owner.install.RETAINED_SOURCE) and _local_condition(diagnostic, 'L026', lambda: plan['repository'] == value['repository']) and _local_condition(diagnostic, 'L027', lambda: plan['work_key'] == value['failed_work_key']) and _local_condition(diagnostic, 'L028', lambda: plan['target_pr'] == value['failed_target_pr']), 'LANE_RETIREMENT_BINDING')
    if diagnostic is not None:
        diagnostic.at('L076')
    contained = view.read(plan_hash + '/contained.json')
    if diagnostic is not None:
        diagnostic.at('L077')
    r.need(_local_condition(diagnostic, 'L029', lambda: _parse(contained) == dict(audit='LIGHT_LANE_OWNER', phase='contain', state='INTAKE_ROLLED_BACK', plan_sha256=plan_hash, controls_restored=False, task_success=False, queue_retry_authorized=False)) and _local_condition(diagnostic, 'L030', lambda: view.read(cycle + '/contain-done.json') == contained) and _local_condition(diagnostic, 'L031', lambda: view.read(plan_hash + '/contain-intent.json') == owner.encoded(dict(plan_sha256=plan_hash, action='CONTAIN_PREEXECUTION'))), 'LANE_RETIREMENT_BINDING')
    if diagnostic is not None:
        diagnostic.at('L078')
    baseline = _parse(view.read(plan_hash + '/baseline.json'))
    if diagnostic is not None:
        diagnostic.at('L079')
    before = _parse(view.read(plan_hash + '/before.json'))
    if diagnostic is not None:
        diagnostic.at('L080')
    view.read(plan_hash + '/runtime-package.json', value['retained_runtime_sha256'], limit=3 * 1024 * 1024)
    if diagnostic is not None:
        diagnostic.at('L081')
    view.read(plan_hash + '/wheels.tar', limit=16 * 1024 * 1024)
    if diagnostic is not None:
        diagnostic.at('L082')
    package_path = 'controllers/' + value['historical_controller_source']
    if diagnostic is not None:
        diagnostic.at('L083')
    package = _parse(view.read(package_path + '/package.json', value['historical_controller_sha256'], limit=3 * 1024 * 1024), limit=3 * 1024 * 1024)
    if diagnostic is not None:
        diagnostic.at('L084')
    owner.validate_package(owner.encoded(package), value['historical_controller_source'], value['historical_controller_sha256'], allow_legacy=True)
    if diagnostic is not None:
        diagnostic.at('L085')
    for name, text in package['helpers'].items():
        r.need(_local_condition(diagnostic, 'L032', lambda: view.read(package_path + '/' + name, limit=3 * 1024 * 1024) == text.encode()), 'LANE_RETIREMENT_SOURCE')
    if diagnostic is not None:
        diagnostic.at('L086')
    prior = value['predecessor']
    if diagnostic is not None:
        diagnostic.at('L087')
    prior_root = prior['plan_sha256']
    if diagnostic is not None:
        diagnostic.at('L088')
    prior_plan = view.read(prior_root + '/plan.json', prior_root)
    if diagnostic is not None:
        diagnostic.at('L089')
    terminal = view.read(prior_root + '/terminal.json', prior['terminal_sha256'])
    if diagnostic is not None:
        diagnostic.at('L090')
    receipt = _parse(view.read(prior_root + '/intake.json'))
    if diagnostic is not None:
        diagnostic.at('L091')
    complete = _parse(view.read(prior_root + '/complete.json'))
    if diagnostic is not None:
        diagnostic.at('L092')
    restored = _parse(view.read(prior_root + '/controls-restored.json'))
    if diagnostic is not None:
        diagnostic.at('L093')
    execution = _parse(view.read(prior_root + '/execution.json'))
    if diagnostic is not None:
        diagnostic.at('L094')
    prepared = _parse(view.read(prior_root + '/prepare.json'))
    if diagnostic is not None:
        diagnostic.at('L095')
    binding = owner.encoded(dict(plan_sha256=prior_root, request_sha256=execution['request_sha256'], receipt_sha256=owner.sha(owner.encoded(receipt)), terminal_sha256=prior['terminal_sha256']))
    if diagnostic is not None:
        diagnostic.at('L096')
    r.need(_local_condition(diagnostic, 'L033', lambda: owner.sequence(prepared) == prior['sequence']) and _local_condition(diagnostic, 'L034', lambda: prepared['accepted_runtime_sha256'] == value['retained_runtime_sha256']) and _local_condition(diagnostic, 'L035', lambda: receipt['plan_sha256'] == prior_root) and _local_condition(diagnostic, 'L036', lambda: complete.get('controls_restored') is True) and _local_condition(diagnostic, 'L037', lambda: {k: v for k, v in complete.items() if k != 'native'} == restored) and _local_condition(diagnostic, 'L038', lambda: view.read(prior_root + '/restore-intent.json') == binding) and _local_condition(diagnostic, 'L039', lambda: view.read(prior_root + '/restart-intent.json') == binding), 'LANE_RETIREMENT_PREDECESSOR')
    if diagnostic is not None:
        diagnostic.at('L097')
    return dict(policy=policy, prepare=prepare, raw_plan=raw_plan, baseline=baseline, before=before, prior_plan=prior_plan, terminal=terminal, receipt=receipt, complete=complete)


def observer(view,value,conn,guard,*,pins,latest=None,descendants=(),observation=False):
    """Read-only checked view; sequence anchor is not frozen after later jobs."""
    from database import light_native_pilot_intake as intake
    from ops import light_native_lane_controller as owner
    from ops import light_native_lane_owner as acceptance
    from ops import light_native_lane_feed as feed
    guard.assert_current()
    data=local(view,value,pins,observation=True) if observation else local(view,value,pins)
    plan=intake.Plan(data['raw_plan'],value['plan_sha256'])
    prior=value['predecessor'];prior_plan=intake.Plan(data['prior_plan'],prior['plan_sha256'])
    before=data['before'];baseline=data['baseline']
    r.need(before['version']==1 and before['plan_sha256']==plan.digest and before['target']==intake.EXPECTED_TARGET
           and baseline['source']==plan.value['source']
           and baseline['package_sha256']==value['retained_runtime_sha256']
           and baseline['scope_sha256']==plan.scope_digest
           and baseline['agreement_sha256']==data['prepare']['accepted_agreement_sha256'], 'LANE_RETIREMENT_BINDING')
    legacy=owner.install.hold.attest()
    r.need(asdict(legacy)==baseline['prior'],'LANE_RETIREMENT_HOLD')
    owner.switch.unchanged_files(baseline['protected'],legacy,baseline['protected_sha256'])
    owner.execution.stopped()
    cursor=feed.root_record(owner.install.CONTROL/'current.json',4096)
    native,history=feed.verify_serial_hold(owner.install.RETAINED_SOURCE,cursor)
    anchor=prior['sequence'];r.need(len(history)>anchor,'LANE_RETIREMENT_PREDECESSOR')
    anchor_local=r.parse(history[anchor][1]);anchor_intent=r.parse(history[anchor][0])
    terminal=r.parse(data['terminal']);receipt=data['receipt']
    r.need(anchor_intent['dispatch_id']==receipt['dispatch_id']
           and anchor_local['request']==terminal['request']
           and anchor_local['result']['terminal']==terminal['result']
           and anchor_local['result']['provider_task_id']==terminal['provider_task_id'], 'LANE_RETIREMENT_PREDECESSOR')
    # Every post-anchor history item must have an actual completed accepted
    # issuer cycle, not merely a matching sequence number in a service file.
    expected=list(range(anchor+1,len(history)))
    chain={item['sequence']:item for item in descendants if item['sequence']>anchor}
    r.need(len(chain)==len([x for x in descendants if x['sequence']>anchor]) and sorted(chain)==expected,
           'LANE_RETIREMENT_DESCENDANTS')
    current=prior
    for number in expected:
        item=chain[number]
        retained=item['terminal'];actual=r.parse(history[number][1])
        r.need(item['predecessor']==current and item['dispatch_id']==r.parse(history[number][0])['dispatch_id']
               and item['plan_sha256']!=value['plan_sha256']
               and r.sha(r.encoded(retained))==item['terminal_sha256']
               and actual['request']==retained['request']
               and actual['result']['terminal']==retained['result']
               and actual['result']['provider_task_id']==retained['provider_task_id'],
               'LANE_RETIREMENT_DESCENDANTS')
        current={k:item[k] for k in ('plan_sha256','terminal_sha256','sequence')}
    r.need((latest is None and current==prior or latest==current)
           and r.parse(cursor)['dispatch_id']==r.parse(history[-1][0])['dispatch_id'], 'LANE_RETIREMENT_DESCENDANTS')
    jobs={r.parse(item[0])['dispatch_id'] for item in history}
    r.need(set(os.listdir(owner.install.CONTROL/'jobs'))==jobs,'LANE_RETIREMENT_FEED')
    for number in expected:
        r.need(chain[number]['work_key']!=value['failed_work_key'],'LANE_RETIREMENT_CANCELED_WORK')
    # An unrelated later legitimate feed is allowed; the canceled plan has no
    # intake/dispatch and no root phase beyond failed preparation.
    if not expected:
        r.need(not (owner.install.LEDGER/(format(anchor+1,'08d')+'-feed-intent.json')).exists()
               and native==data['complete']['native'],'LANE_RETIREMENT_FEED')
    def database_check():
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.execute("SET LOCAL statement_timeout='5s'")
            conn.execute("SET LOCAL idle_in_transaction_session_timeout='5s'")
            intake.engine.identity(conn,intake.target())
            config=intake.one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')
            role=intake.one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT'")
            r.need(config==before['native_config'] and config['enabled'] is False and set(role)==set(before['autopilot_role'])
                   and all(role[k]==v for k,v in before['autopilot_role'].items() if k!='updated_at'), 'LANE_RETIREMENT_CONTROLS')
            counts=conn.execute("SELECT (SELECT count(*) FROM autopilot.project_work_item WHERE repository=%s AND work_key=%s),"
                 "(SELECT count(*) FROM autopilot.role_dispatch_outbox WHERE repository=%s AND target_pr=%s AND NOT(dispatch_id::text=ANY(%s::text[]))),"
                 "(SELECT count(*) FROM autopilot.task WHERE status NOT IN ('DONE','FAILED_CLOSED')),"
                 "(SELECT count(*) FROM autopilot.native_cli_receipt WHERE state<>'TERMINAL')",
                 (value['repository'],value['failed_work_key'],value['repository'],value['failed_target_pr'],
                  [chain[n]['dispatch_id'] for n in expected])).fetchone()
            r.need(counts==(0,0,0,0),'LANE_RETIREMENT_ACTIVITY')
            evidence=intake.terminal_evidence(data['terminal'],prior['terminal_sha256'],prior_plan,receipt)
            intake._terminal_rows(conn,prior_plan,receipt,evidence)
            mapping=conn.execute('SELECT task_id::text,run_kind FROM autopilot.project_work_task WHERE work_item_id=%s::uuid',
                                 (receipt['work_item_id'],)).fetchall()
            successors=conn.execute('SELECT count(*) FROM autopilot.role_dispatch_outbox WHERE prior_task_id=%s::uuid OR origin_task_id=%s::uuid',
                                    (receipt['task_id'],receipt['task_id'])).fetchone()
            r.need(mapping==[(receipt['task_id'],'AUDIT')] and successors==(0,),'LANE_RETIREMENT_PREDECESSOR')
        return config,role,counts,mapping,successors
    database_before=database_check()
    # retain_acceptance is intentionally NOT called: read the existing local
    # intent/terminal/permit and use only its read-only verifier.
    permit=feed.root_record(owner.install.CONTROL/'jobs'/receipt['dispatch_id']/'permit.json')
    accepted=acceptance.verify_terminal(conn,prior_plan,receipt,history[anchor][0],history[anchor][1],permit)
    r.need(feed.root_record(owner.install.CONTROL/'jobs'/receipt['dispatch_id']/'accepted-terminal.json',4096)==accepted,
           'LANE_RETIREMENT_PREDECESSOR')
    r.need(database_check()==database_before,'LANE_RETIREMENT_DATABASE_DRIFT')
    r.need(feed.verify_serial_hold(owner.install.RETAINED_SOURCE,cursor)==(native,history)
           and asdict(owner.install.hold.attest())==baseline['prior'],'LANE_RETIREMENT_HOLD')
    owner.switch.unchanged_files(baseline['protected'],legacy,baseline['protected_sha256'])
    view.check();guard.assert_current()
    return {'local_sha256':r.sha(r.encoded(view.rows)), 'history_sha256':r.sha(r.encoded([
        [r.sha(a),r.sha(b)] for a,b in history])), 'provider_sha256':terminal['result']['provider_evidence_sha256'],
        'controls_sha256':r.sha(r.encoded(database_before)), 'latest':current}


OBSERVATION_PHASES=('AUTHORITY','LOCKS','PROPOSAL','RECONSTRUCTION','REQUEST_AUTHORITY',
    'RUNTIME','LOCAL_JOURNALS','DATABASE_CONNECT','HOST_DB_PROVIDER','FINAL_GUARDS','DONE')


def public_observation_result(value,accepted_record):
    keys={'audit','state','phase','proposal_state','proposal_observation_final','metadata','record_sha256','record_matches',
          'content_shape_valid','journal_pins_verified','hold_db_provider_verified',
          'incident_closed','execution_acknowledged','new_task_authorized'}
    r.need(type(value) is dict and set(value)==keys and value['audit']=='LIGHT_LANE_RETIREMENT_OBSERVATION'
        and value['state'] in ('OBSERVED','OBSERVATION_REFUSED') and value['phase'] in OBSERVATION_PHASES
        and value['proposal_state'] in ('UNKNOWN','ABSENT','EXACT','CONFLICT')
        and r.digest(accepted_record) and value['record_sha256']==accepted_record
        and all(type(value[k]) is bool for k in ('proposal_observation_final','record_matches','content_shape_valid',
            'journal_pins_verified','hold_db_provider_verified'))
        and all(value[k] is False for k in ('incident_closed','execution_acknowledged','new_task_authorized')),
        'LANE_RETIREMENT_REFUSED')
    meta=value['metadata']
    r.need(meta is None or type(meta) is dict and set(meta)=={'uid','gid','mode','nlink','size'}
        and all(type(n) is int for n in meta.values()) and meta['uid']==meta['gid']==0
        and meta['mode']==0o600 and meta['nlink']==1 and 0<=meta['size']<=4096,'LANE_RETIREMENT_REFUSED')
    r.need((value['proposal_state'] in ('UNKNOWN','ABSENT'))==(meta is None)
        and value['proposal_observation_final']==(value['state']=='OBSERVED')
        and value['record_matches']==(value['proposal_state']=='EXACT')
        and (not value['record_matches'] or value['content_shape_valid'])
        and (value['state']!='OBSERVATION_REFUSED' or not value['journal_pins_verified']
             and not value['hold_db_provider_verified'])
        and (value['state']!='OBSERVED' or value['phase']=='DONE' and value['proposal_state']!='UNKNOWN'
             and value['journal_pins_verified'] and value['hold_db_provider_verified']), 'LANE_RETIREMENT_REFUSED')
    return value


@contextmanager
def observation_locks(root):
    """Existing fixed lock inodes only; read-only descriptors, no creation."""
    import fcntl
    with r.Snapshot(root,'unused') as view:
        held=[]
        try:
            for directory in ('issuers','cycles'):
                with view.directory(directory) as parent:
                    fd=os.open('cycle.lock',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_NOATIME,dir_fd=parent)
                    held.append(fd);r.Snapshot.file_meta(os.fstat(fd))
                    fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
                    view.read(directory+'/cycle.lock')
                    r.need(r.identity(os.stat('cycle.lock',dir_fd=parent,follow_symlinks=False))==
                           r.identity(os.fstat(fd)),'LANE_RETIREMENT_DRIFT')
            view.check();yield;view.check()
        finally:
            for fd in reversed(held):os.close(fd)


def proposal_observation(view,value):
    """Track the target as an original, with no writer metadata exemption."""
    path=r.entry(value)+'/'+r.NAME
    raw=view.read(path,optional=True,limit=4096)
    if raw is None:
        result=dict(proposal_state='ABSENT',metadata=None,record_matches=False,content_shape_valid=False)
    else:
        meta=view.rows[path][0]
        shape=True
        try:r.record(raw)
        except (RuntimeError,ValueError,UnicodeDecodeError):shape=False
        match=r.sha(raw)==value['record_sha256']
        result=dict(proposal_state='EXACT' if match else 'CONFLICT',
            metadata=dict(uid=meta[3],gid=meta[4],mode=meta[2]&0o777,nlink=meta[5],size=meta[6]),
            record_matches=match,content_shape_valid=shape)
    view.check()
    return result


def inspect_retirement(wheels,credential,controller,runtime,raw,accepted,guard):
    """Strictly observation: never calls write, retain, or a create-only entrypoint.

    Ordinary failures return a fixed CURRENT phase, not the lost historical cause.
    Supervisor crash/deadline still means UNKNOWN; no raw exceptions leave root.
    Temporary driver/helper files belong to the existing authenticated bootstrap;
    this function never writes journals, DB rows, services or controls.
    """
    from ops import light_native_lane_controller as owner
    from ops.native_maintenance_agreement import Agreement
    from ops.light_native_bounded import run
    import time
    r.need(os.geteuid()==0 and os.uname().nodename=='autopilot-lite-vnic','LANE_RETIREMENT_ROOT')
    reference=public_reference(raw,action='observe-retirement-reference')
    def operation():
        phase='AUTHORITY'
        result=dict(audit='LIGHT_LANE_RETIREMENT_OBSERVATION',state='OBSERVATION_REFUSED',phase=phase,
            proposal_state='UNKNOWN',proposal_observation_final=False,metadata=None,record_sha256=reference['record_sha256'],
            record_matches=False,content_shape_valid=False,journal_pins_verified=False,
            hold_db_provider_verified=False,incident_closed=False,execution_acknowledged=False,new_task_authorized=False)
        try:
            r.need(r.sha(raw)==accepted,'LANE_RETIREMENT_NOT_ACCEPTED')
            owner.validate_package(controller,reference['source'],reference['accepted_controller_sha256'])
            r.need(r.sha(runtime)==reference['accepted_runtime_sha256'],'LANE_RETIREMENT_BINDING')
            start,end=(Agreement.timestamp(reference['agreement'][k]) for k in ('not_before','expires_at'))
            r.need(start<=time.time()<end,'LANE_RETIREMENT_REFERENCE')
            guard.assert_current();owner.release.staging.require_current_main(reference['source'])
            phase='LOCKS'
            with observation_locks(owner.ROOT),r.Snapshot(owner.ROOT,'unused') as view:
                phase='PROPOSAL';result.update(proposal_observation(view,reference))
                phase='RECONSTRUCTION'
                request,pin=resolve_reference(raw,accepted,controller,runtime,guard,read_only=True)
                phase='REQUEST_AUTHORITY'
                value,payload,agreement=write_request(request,pin,controller,runtime,guard,read_only=True)
                proposal=r.record(payload,value['record_sha256'])
                phase='RUNTIME';retained=r.parse(runtime,limit=3*1024*1024)
                r.need(owner.release.encoded(retained)==runtime and retained['source']==owner.install.RETAINED_SOURCE,
                       'LANE_RETIREMENT_BINDING')
                owner.release.validate(retained['runtime'],retained['source'],retained['runtime']['sha256'])
                owner.release.staging.verify_release(owner.execution.plan.source_path(retained['source']),retained['runtime'])
                phase='LOCAL_JOURNALS';local(view,proposal,value['historical_journal_sha256'],observation=True)
                phase='RUNTIME'
                from ops.native_maintenance_owner_host import loaded_runtime
                with loaded_runtime(wheels) as (psycopg,_):
                    from ops.native_maintenance_owner_attest import parameters
                    phase='DATABASE_CONNECT'
                    with psycopg.connect(**parameters(credential),autocommit=True) as conn:
                        conn.read_only=True
                        phase='HOST_DB_PROVIDER'
                        agreement.assert_held(agreement.scope)
                        before=observer(view,proposal,conn,guard,pins=value['historical_journal_sha256'],observation=True)
                        r.need(observer(view,proposal,conn,guard,pins=value['historical_journal_sha256'],observation=True)==before,
                               'LANE_RETIREMENT_DRIFT')
                phase='FINAL_GUARDS'
                agreement.assert_held(agreement.scope);guard.assert_current()
                owner.release.staging.require_current_main(reference['source'])
                r.need(proposal_observation(view,reference)=={k:result[k] for k in
                    ('proposal_state','metadata','record_matches','content_shape_valid')},'LANE_RETIREMENT_DRIFT')
                view.check()
            result.update(state='OBSERVED',phase='DONE',proposal_observation_final=True,journal_pins_verified=True,hold_db_provider_verified=True)
        except BaseException:
            result.update(state='OBSERVATION_REFUSED',phase=phase,journal_pins_verified=False,hold_db_provider_verified=False)
        return public_observation_result(result,reference['record_sha256'])
    return run(operation)
