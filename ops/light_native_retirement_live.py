"""Authenticated checked-observation adapter for failed-prepare proposals.

No import-time DB/host/provider access. No SQL write, table lock, service change,
ACK, intake or provider submission occurs in this module. The driver is loaded
once by the authenticated child; historical helper packages are data, not imports.
"""
import base64
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


def public_reference(raw):
    """Only public pins and finite coordination metadata may cross dispatch."""
    from ops.native_maintenance_agreement import Agreement,COVERAGE
    value=r.parse(raw,limit=8192)
    r.need(type(value) is dict and set(value)==REFERENCE_KEYS and r.encoded(value)==raw
           and type(value['version']) is int and value['version']==1
           and value['action']=='retire-prepare-reference','LANE_RETIREMENT_REFERENCE')
    r.references([{k:value[k] for k in ('policy_sha256','index','record_sha256')}])
    r.need(r.digest(value['source'],40) and all(r.digest(value[k]) for k in
        ('accepted_controller_sha256','accepted_runtime_sha256','journal_map_sha256',
         'private_request_sha256','accepted_agreement_sha256')),'LANE_RETIREMENT_REFERENCE')
    agreement=value['agreement']
    r.need(type(agreement) is dict and set(agreement)=={'version','owner','operation_digest',
        'not_before','expires_at','coverage','evidence'} and type(agreement['version']) is int
        and agreement['version']==1 and agreement['owner']=='olegmed1-art'
        and agreement['coverage']==COVERAGE and agreement['evidence']==PUBLIC_EVIDENCE
        and r.digest(agreement['operation_digest'])
        and r.sha(r.encoded(agreement))==value['accepted_agreement_sha256'],
        'LANE_RETIREMENT_REFERENCE')
    start,end=(Agreement.timestamp(agreement[k]) for k in ('not_before','expires_at'))
    r.need(0<end-start<=1800,'LANE_RETIREMENT_REFERENCE')
    return value


def resolve_reference(raw,accepted,controller,runtime,guard):
    """Reconstruct private bytes in protected root memory; never mint acceptance.

    Reads use fixed existing paths and the same no-follow metadata verifier.
    The original writer subsequently rechecks every accepted byte and live proof.
    """
    from ops import light_native_lane_controller as owner
    from ops import light_native_lane_issuer as issuer
    from ops.native_maintenance_agreement import Agreement
    import time
    r.need(r.sha(raw)==accepted,'LANE_RETIREMENT_NOT_ACCEPTED')
    value=public_reference(raw)
    owner.validate_package(controller,value['source'],value['accepted_controller_sha256'])
    r.need(r.sha(runtime)==value['accepted_runtime_sha256'],'LANE_RETIREMENT_BINDING')
    guard.assert_current();owner.release.staging.require_current_main(value['source'])
    start,end=(Agreement.timestamp(value['agreement'][k]) for k in ('not_before','expires_at'))
    r.need(start<=time.time()<end,'LANE_RETIREMENT_REFERENCE')
    policy_hash=value['policy_sha256'];index=value['index']
    entry='issuers/'+policy_hash+'/'+format(index,'04d')
    with r.Snapshot(owner.ROOT,entry) as view:
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
        request.update(action='retire-prepare',record_base64=base64.b64encode(proposal).decode(),
                       historical_journal_sha256=pins)
        private=r.encoded(request)
        r.need(r.sha(private)==value['private_request_sha256'],'LANE_RETIREMENT_NOT_ACCEPTED')
        # Validate the independently approved finite scope before any write/DB access.
        write_request(private,value['private_request_sha256'],controller,runtime,guard)
        view.check()
    return private,value['private_request_sha256']


def write_request(raw,accepted,controller,runtime,guard):
    from ops import light_native_lane_controller as owner
    from ops.native_maintenance_agreement import Agreement
    r.need(r.sha(raw)==accepted,'LANE_RETIREMENT_NOT_ACCEPTED')
    value=r.parse(raw)
    r.need(type(value) is dict and set(value)==WRITE_KEYS and type(value['version']) is int
           and value['version']==1 and value['action']=='retire-prepare','LANE_RETIREMENT_SCHEMA')
    owner.validate_package(controller,value['source'],value['accepted_controller_sha256'])
    r.need(r.sha(runtime)==value['accepted_runtime_sha256'],'LANE_RETIREMENT_BINDING')
    payload=base64.b64decode(value['record_base64'],validate=True)
    proposal=r.record(payload,value['record_sha256']);r.journal_pins(value['historical_journal_sha256'])
    r.need(proposal['retained_runtime_sha256']==value['accepted_runtime_sha256'],'LANE_RETIREMENT_BINDING')
    scope=dict(version=1,operation='CREATE_FAILED_PREPARE_RETIREMENT_PROPOSAL',source=value['source'],
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


def local(view,value,pins):
    from ops import light_native_lane_controller as owner
    from ops import light_native_lane_issuer as issuer
    policy_root='issuers/'+value['policy_sha256'];entry=r.entry(value)
    r.journal_pins(pins)
    plan_hash=value['plan_sha256'];cycle='cycles/'+plan_hash
    paths={name:plan_hash+'/'+name for name in
           ('baseline.json','before.json','contained.json','prepare.json','runtime-package.json','wheels.tar')}
    paths.update({'cycle-contain-intent':cycle+'/contain-intent.json', 'cycle-intent':cycle+'/intent.json',
                  'driver-contain-intent':plan_hash+'/contain-intent.json', 'incident.json':cycle+'/incident.json',
                  'issuer-intent':entry+'/intent.json'})
    for name,path in paths.items():
        view.read(path,pins[name],limit=16*1024*1024)
    policy=r.parse(view.read(policy_root+'/policy.json',value['policy_sha256']))
    r.need(set(policy)==issuer.POLICY_KEYS and policy['version']==1 and policy['action']=='issue'
           and policy['source']==value['historical_controller_source']
           and policy['accepted_controller_sha256']==value['historical_controller_sha256']
           and policy['accepted_runtime_sha256']==value['retained_runtime_sha256'], 'LANE_RETIREMENT_BINDING')
    index=value['index'];r.need(index<len(policy['plans']),'LANE_RETIREMENT_BINDING')
    r.need(view.names(policy_root)=={'policy.json'}|{format(n,'04d') for n in range(index+1)},
           'LANE_RETIREMENT_LATER_ISSUE')
    writing=view.output_parent==entry
    r.need(view.names(entry,allow_output=writing)==({'intent.json'} if writing else {'intent.json',r.NAME}),
           'LANE_RETIREMENT_BINDING')
    intent=r.parse(view.read(entry+'/intent.json'))
    r.need(set(intent)=={'start','cycle'},'LANE_RETIREMENT_BINDING')
    count,previous=issuer.progress(owner.ROOT/policy_root,policy,value['policy_sha256'],stop_before=index,
                                  **tracked(view))
    r.need(count==index and previous==value['predecessor'],'LANE_RETIREMENT_PREDECESSOR')
    expected=issuer.derive(policy,value['policy_sha256'],index,previous,intent['start'])
    r.need(intent['cycle']==expected,'LANE_RETIREMENT_BINDING')
    prepare=expected['prepare'];plan_hash=value['plan_sha256'];cycle='cycles/'+plan_hash
    r.need(prepare['accepted_plan_sha256']==plan_hash,'LANE_RETIREMENT_BINDING')
    r.need(view.names(cycle)=={'intent.json','prepare-intent.json','contain-intent.json','contain-done.json','incident.json'},
           'LANE_RETIREMENT_PHASE')
    r.need(view.read(cycle+'/intent.json')==owner.encoded(expected)
           and view.read(cycle+'/prepare-intent.json')==owner.encoded(prepare)
           and view.read(cycle+'/contain-intent.json')==owner.encoded(dict(prepare,action='contain')),
           'LANE_RETIREMENT_BINDING')
    incident=r.parse(view.read(cycle+'/incident.json',value['incident_sha256']))
    r.need(incident=={'phase':'prepare','containment':'INTAKE_ROLLED_BACK','state':'RECONCILIATION_REQUIRED'},
           'LANE_RETIREMENT_PHASE')
    r.need(view.names(plan_hash)=={'plan.json','baseline.json','before.json','prepare.json',
           'contain-intent.json','contained.json','runtime-package.json','wheels.tar'},'LANE_RETIREMENT_INTAKE')
    raw_plan=view.read(plan_hash+'/plan.json',plan_hash)
    r.need(base64.b64decode(policy['plans'][index]['plan_base64'],validate=True)==raw_plan
           and view.read(plan_hash+'/prepare.json')==owner.encoded(prepare),'LANE_RETIREMENT_BINDING')
    plan=r.parse(raw_plan)
    r.need(plan['source']==value['retained_runtime_source']==owner.install.RETAINED_SOURCE
           and plan['repository']==value['repository'] and plan['work_key']==value['failed_work_key']
           and plan['target_pr']==value['failed_target_pr'],'LANE_RETIREMENT_BINDING')
    contained=view.read(plan_hash+'/contained.json')
    r.need(r.parse(contained)==dict(audit='LIGHT_LANE_OWNER',phase='contain',state='INTAKE_ROLLED_BACK',
           plan_sha256=plan_hash,controls_restored=False,task_success=False,queue_retry_authorized=False)
           and view.read(cycle+'/contain-done.json')==contained
           and view.read(plan_hash+'/contain-intent.json')==owner.encoded(dict(plan_sha256=plan_hash,action='CONTAIN_PREEXECUTION')),
           'LANE_RETIREMENT_BINDING')
    baseline=r.parse(view.read(plan_hash+'/baseline.json'))
    before=r.parse(view.read(plan_hash+'/before.json'))
    view.read(plan_hash+'/runtime-package.json',value['retained_runtime_sha256'],limit=3*1024*1024)
    view.read(plan_hash+'/wheels.tar',limit=16*1024*1024)
    package_path='controllers/'+value['historical_controller_source']
    package=r.parse(view.read(package_path+'/package.json',value['historical_controller_sha256'],limit=3*1024*1024),limit=3*1024*1024)
    owner.validate_package(owner.encoded(package),value['historical_controller_source'],
                           value['historical_controller_sha256'],allow_legacy=True)
    for name,text in package['helpers'].items():
        r.need(view.read(package_path+'/'+name,limit=3*1024*1024)==text.encode(),'LANE_RETIREMENT_SOURCE')
    prior=value['predecessor'];prior_root=prior['plan_sha256']
    prior_plan=view.read(prior_root+'/plan.json',prior_root)
    terminal=view.read(prior_root+'/terminal.json',prior['terminal_sha256'])
    receipt=r.parse(view.read(prior_root+'/intake.json'))
    complete=r.parse(view.read(prior_root+'/complete.json'))
    restored=r.parse(view.read(prior_root+'/controls-restored.json'))
    execution=r.parse(view.read(prior_root+'/execution.json'))
    prepared=r.parse(view.read(prior_root+'/prepare.json'))
    binding=owner.encoded(dict(plan_sha256=prior_root,request_sha256=execution['request_sha256'],
           receipt_sha256=owner.sha(owner.encoded(receipt)),terminal_sha256=prior['terminal_sha256']))
    r.need(owner.sequence(prepared)==prior['sequence'] and prepared['accepted_runtime_sha256']==value['retained_runtime_sha256']
           and receipt['plan_sha256']==prior_root and complete.get('controls_restored') is True
           and {k:v for k,v in complete.items() if k!='native'}==restored
           and view.read(prior_root+'/restore-intent.json')==binding
           and view.read(prior_root+'/restart-intent.json')==binding,'LANE_RETIREMENT_PREDECESSOR')
    return dict(policy=policy,prepare=prepare,raw_plan=raw_plan,baseline=baseline,before=before,
                prior_plan=prior_plan,terminal=terminal,receipt=receipt,complete=complete)


def observer(view,value,conn,guard,*,pins,latest=None,descendants=()):
    """Read-only checked view; sequence anchor is not frozen after later jobs."""
    from database import light_native_pilot_intake as intake
    from ops import light_native_lane_controller as owner
    from ops import light_native_lane_owner as acceptance
    from ops import light_native_lane_feed as feed
    guard.assert_current()
    data=local(view,value,pins)
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
