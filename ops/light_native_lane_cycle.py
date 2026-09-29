"""One separately accepted finite owner cycle; no queue producer or retry API.

The outer review accepts this fixed derivation policy and exact prepare bytes.
Later digests come from create-only owner records, never caller overrides.
Existing phases still perform their original fresh primary-source checks.
"""
import fcntl
import os
import stat
import time
from contextlib import contextmanager

from ops import light_native_lane_controller as owner

STEPS=('prepare','publish','permit','execute','terminal','restore')
BINDINGS=(('publish','accepted_receipt_sha256','intake.json'),
          ('permit','accepted_discovery_sha256','discovery.json'),
          ('execute','accepted_permit_sha256','permit.json'),
          ('restore','accepted_terminal_sha256','terminal.json'))
KEYS={'version','action','source','accepted_controller_sha256','accepted_runtime_sha256',
      'plan_base64','accepted_plan_sha256','agreement','accepted_agreement_sha256',
      'accepted_receipt_sha256','accepted_discovery_sha256','accepted_permit_sha256',
      'accepted_terminal_sha256','predecessor'}
require=owner.require


def location(prepare):
    return owner.ROOT/'cycles'/prepare['accepted_plan_sha256']


def derive(prepare,action):
    """Only the six fixed actions, with immutable bindings read from root."""
    require(action in STEPS,'LANE_CYCLE_SCOPE')
    value=dict(prepare,action=action)
    directory=owner.ROOT/prepare['accepted_plan_sha256']
    for start,key,name in BINDINGS:
        if STEPS.index(action)>=STEPS.index(start):
            value[key]=owner.sha(owner.read(directory/name))
    return value


class _Authority:
    """In-process authority; never serialized or supplied by a workflow input."""
    def __init__(self,prepare,digest):
        self.prepare=prepare
        self.digest=digest


def authorize_phase(value,authority):
    # Existing separately accepted cleanup is still available after a failed
    # cycle. GitHub's common mutation concurrency serializes it with the cycle.
    if authority is None:
        if value['action'] in STEPS[:4]:
            require(not location(value).exists(),'LANE_CYCLE_REPLAY')
        return
    require(type(authority) is _Authority,'LANE_CYCLE_SCOPE')
    raw=owner.read(location(authority.prepare)/'intent.json',authority.digest)
    require(owner.parse(raw)['prepare']==authority.prepare,'LANE_CYCLE_SCOPE')
    if value['action']=='contain':
        require(value==dict(authority.prepare,action='contain'),'LANE_CYCLE_SCOPE')
    else:
        require(value==derive(authority.prepare,value['action']),'LANE_CYCLE_SCOPE')


@contextmanager
def exclusive(root):
    """Separate cycle lock; never holds the driver lock needed by supervisor."""
    owner.install.root_parent(root)
    path=root/'cycle.lock'
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        row=os.fstat(fd)
        require(stat.S_ISREG(row.st_mode) and row.st_uid==0 and row.st_nlink==1
                and stat.S_IMODE(row.st_mode)==0o600,'LANE_CYCLE_LOCK')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def validated(raw,accepted,controller_raw,retained_raw,run_guard):
    require(owner.sha(raw)==accepted,'LANE_CYCLE_SCOPE')
    outer=owner.parse(raw)
    require(type(outer) is dict and set(outer)=={'version','action','prepare'}
            and type(outer['version']) is int and outer['version']==1
            and outer['action']=='cycle','LANE_CYCLE_SCOPE')
    value=outer['prepare']
    require(type(value) is dict and set(value)==KEYS and type(value['version']) is int
            and value['version']==2 and value['action']=='prepare'
            and all(value[key] is None for _,key,_ in BINDINGS),'LANE_CYCLE_SCOPE')
    owner.sequence(value)
    owner.validate_package(controller_raw,value['source'],value['accepted_controller_sha256'])
    require(owner.sha(retained_raw)==value['accepted_runtime_sha256'],'LANE_CYCLE_SCOPE')
    plan,_=owner.scope(value)
    from ops.native_maintenance_agreement import Agreement
    agreement=Agreement(value['agreement'],value['accepted_agreement_sha256'],plan.scope)
    require(agreement.end-time.time()>=900,'LANE_CYCLE_WINDOW')
    run_guard.assert_current()
    owner.release.staging.require_current_main(value['source'])
    return value


def checked_result(action,result,prepare):
    """Bound public output to durable bytes; stdout alone cannot advance."""
    require(type(result) is dict and result.get('audit')=='LIGHT_LANE_OWNER'
            and result.get('phase')==action,'LANE_CYCLE_RESULT')
    directory=owner.ROOT/prepare['accepted_plan_sha256']
    if action in ('prepare','publish','permit','terminal'):
        name={'prepare':'intake.json','publish':'discovery.json',
              'permit':'permit.json','terminal':'terminal.json'}[action]
        record=owner.parse(owner.read(directory/name,result.get('record_sha256')))
        # read(..., None) intentionally permits reads elsewhere; not here.
        require(type(result.get('record_sha256')) is str
                and owner.sha(owner.encoded(record))==result['record_sha256'],'LANE_CYCLE_RESULT')
        receipt=owner.parse(owner.read(directory/'intake.json'))
        require(receipt['plan_sha256']==prepare['accepted_plan_sha256']
                and result.get('dispatch_id')==receipt['dispatch_id'],'LANE_CYCLE_RESULT')
        if action=='terminal':
            require(result.get('state')=='ACCEPTED'
                    and result.get('sequence')==owner.sequence(prepare),'LANE_CYCLE_RESULT')
    elif action=='execute':
        execution=owner.parse(owner.read(directory/'execution.json'))
        require(result.get('state')=='STOPPED_HOLD'
                and result.get('request_sha256')==execution['request_sha256'],
                'LANE_CYCLE_RESULT')
        stopped=owner.parse(owner.read(owner.execution.ROOT/execution['request_sha256']/'stopped-hold.json'))
        require(stopped.get('state')=='STOPPED_HOLD','LANE_CYCLE_RESULT')
    else:
        complete=owner.parse(owner.read(directory/'complete.json'))
        terminal=owner.read(directory/'terminal.json')
        require(result.get('state')=='HOLD' and result.get('controls_restored') is True
                and complete.get('controls_restored') is True
                and complete.get('terminal_sha256')==owner.sha(terminal)
                and complete.get('plan_sha256')==prepare['accepted_plan_sha256']
                and result.get('native_pid')==complete['native']['MainPID'],'LANE_CYCLE_RESULT')


def run(wheels,credential,token,controller_raw,retained_raw,raw,accepted,run_guard):
    require(os.geteuid()==0 and os.uname().nodename=='autopilot-lite-vnic','LANE_CYCLE_SCOPE')
    prepare=validated(raw,accepted,controller_raw,retained_raw,run_guard)
    # Source staging already exists for a serial successor. Never repair roots.
    owner.install.root_parent(owner.ROOT)
    root=owner.ROOT/'cycles'
    if not root.exists():owner.install.fresh_directory(root,0o700)
    with exclusive(root):
        directory=location(prepare)
        scope=owner.ROOT/prepare['accepted_plan_sha256']
        require(not directory.exists() and not scope.exists(),'LANE_CYCLE_REPLAY')
        owner.install.fresh_directory(directory,0o700)
        owner.retain(directory/'intent.json',raw)
        authority=_Authority(prepare,accepted)
        action='prepare'
        try:
            for action in STEPS:
                run_guard.assert_running()
                value=derive(prepare,action)
                phase_raw=owner.encoded(value)
                owner.retain(directory/(action+'-intent.json'),phase_raw)
                result=owner.phase(wheels,credential,token,controller_raw,retained_raw,
                    phase_raw,owner.sha(phase_raw),run_guard,_cycle=authority)
                checked_result(action,result,prepare)
                owner.retain(directory/(action+'-done.json'),owner.encoded(result))
            terminal=owner.parse(owner.read(scope/'terminal.json'))
            result=dict(audit='LIGHT_LANE_CYCLE',state='COMPLETE_HOLD',
                plan_sha256=prepare['accepted_plan_sha256'],sequence=owner.sequence(prepare),
                dispatch_id=terminal['dispatch_id'],task_id=terminal['task_id'],
                terminal_sha256=owner.sha(owner.encoded(terminal)),
                result_code=terminal['result']['result_code'],controls_restored=True)
            owner.retain(directory/'complete.json',owner.encoded(result))
            return result
        except BaseException:
            # Do not log exceptions: they can contain credentials. Never replay
            # any phase or fabricate a provider terminal. Execution has its own
            # PID1 cleanup; failed terminal/restore needs incident reconciliation.
            containment='NOT_ATTEMPTED'
            if action in STEPS[:4] and (scope/'before.json').exists() and (scope/'baseline.json').exists():
                try:
                    run_guard.assert_running()
                    # Execute may fail before feed publication. Inspect the
                    # actual feed/history boundary, never infer it from action.
                    owner.containment_host(scope,prepare)
                    value=dict(prepare,action='contain')
                    payload=owner.encoded(value)
                    owner.retain(directory/'contain-intent.json',payload)
                    result=owner.phase(wheels,credential,token,controller_raw,retained_raw,
                        payload,owner.sha(payload),run_guard,_cycle=authority)
                    require(result==owner.parse(owner.read(scope/'contained.json'))
                            and result.get('audit')=='LIGHT_LANE_OWNER'
                            and result.get('phase')=='contain'
                            and result.get('plan_sha256')==prepare['accepted_plan_sha256']
                            and result.get('state') in ('CONTAINED_UNRESOLVED','INTAKE_ROLLED_BACK')
                            and result.get('queue_retry_authorized') is False,'LANE_CYCLE_RESULT')
                    owner.retain(directory/'contain-done.json',owner.encoded(result))
                    containment=result['state']
                except BaseException:
                    containment='RECONCILIATION_REQUIRED'
            owner.retain(directory/'incident.json',owner.encoded(dict(
                phase=action,containment=containment,state='RECONCILIATION_REQUIRED')))
            raise RuntimeError('LANE_CYCLE_RECONCILIATION_REQUIRED') from None
