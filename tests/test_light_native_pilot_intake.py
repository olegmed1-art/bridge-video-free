"""One-item intake tests: accepted scope, ordering, rollback and publication."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from psycopg.pq import TransactionStatus

from database import light_native_pilot_intake as target
from ops import oracle_light_active_hold_attest as hold
from ops.native_maintenance_agreement import Agreement, COVERAGE
from oracle_autopilot.light_native_adapter import FALSE_FLAGS


WORK = '11111111-1111-4111-8111-111111111111'
TASK = '22222222-2222-4222-8222-222222222222'
DISPATCH = '33333333-3333-4333-8333-333333333333'
SOURCE = 'a' * 40
HEAD = 'b' * 40


def fixture():
    spec = {'assignment_schema':'SLAVIK_DISPATCH_ASSIGNMENT_V1',
            'repository':'olegmed1-art/bridge-video-free','target_pr':1150,
            'expected_head_sha':HEAD,'execution_mode':'READ_ONLY',
            'exact_head_binding':True,'cost_cap_microusd':0,'max_repair_attempts':0}
    spec.update(dict.fromkeys(FALSE_FLAGS,False))
    value = {'version':1,'source':SOURCE,'repository':'olegmed1-art/bridge-video-free',
             'target_pr':1150,'expected_head_sha':HEAD,'work_key':'native-pilot-one',
             'objective':'Audit this exact head without mutation.',
             'priority':0,'task_spec_json':spec,'branch':'codex/native-pilot-one'}
    raw = target.encoded(value)
    plan = target.Plan(raw,hashlib.sha256(raw).hexdigest())
    now = int(time.time())
    def timestamp(epoch):
        return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(epoch))
    agreement_value={'version':1,'owner':'olegmed1-art',
        'operation_digest':plan.scope_digest,'not_before':timestamp(now-1),
        'expires_at':timestamp(now+600),'coverage':COVERAGE,
        'evidence':'External accepted one-item no-write interval.'}
    accepted = target.digest(agreement_value)
    agreement=Agreement(agreement_value,accepted,plan.scope)
    prior=hold.HoldIdentity('autopilot-lite-vnic',123,'c'*32,
                           '/opt/bridge-school/school-autopilot-production-light/releases/'+('d'*40),'e'*64)
    baseline={'version':1,'source':SOURCE,'package_sha256':'f'*64,
        'agreement_sha256':accepted,'scope_sha256':plan.scope_digest,
        'prior':asdict(prior),'protected':{},'protected_sha256':target.digest({}),
        'observed_at':now}
    baseline_raw=target.encoded(baseline)
    return plan,agreement,prior,baseline_raw,hashlib.sha256(baseline_raw).hexdigest()


class Row:
    def __init__(self,value): self.value=value
    def fetchone(self): return self.value


class FakeConn:
    autocommit=True
    read_only=False
    def __init__(self, *, fault=None):
        self.fault=fault;self.events=[];self.committed=False;self.rolled_back=False
        self.terminal_stage=False
        self.blocked_terminal=False
        self.info=SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self.config={'singleton':True,'enabled':False,'cutover_at':'2026-01-01T00:00:00Z'}
        self.role={'role_id':'AUTOPILOT','enabled':True,'execution_scope':'REPOSITORY',
                   'can_repair':True}
    @contextmanager
    def transaction(self):
        self.events.append('BEGIN')
        try:
            yield
        except BaseException:
            self.rolled_back=True;self.events.append('ROLLBACK');raise
        else:
            self.committed=True;self.events.append('COMMIT')
    def execute(self,sql,args=()):
        self.events.append(sql)
        if 'role_dispatch_mailbox_registry m' in sql:
            return Row(({'mailbox_pr':1685 if self.fault=='mailbox_drift' else 1703},))
        if 'UPDATE autopilot.native_cli_config SET enabled=true' in sql:
            self.config['enabled']=True
            return Row(None)
        if 'UPDATE autopilot.native_cli_config SET enabled=%s' in sql:
            self.config['enabled']=args[0];self.config['cutover_at']=args[1]
            return Row(None)
        if 'UPDATE autopilot.role_registry SET can_repair=false' in sql:
            self.role['can_repair']=False
            return Row(None)
        if 'UPDATE autopilot.role_registry SET can_repair=%s' in sql:
            self.role['can_repair']=args[0]
            return Row(None)
        if 'native_cli_config c WHERE singleton FOR UPDATE' in sql:return Row((self.config.copy(),))
        if "role_registry r WHERE role_id='AUTOPILOT' FOR UPDATE" in sql:return Row((self.role.copy(),))
        if 'native_cli_config c WHERE singleton' in sql:return Row((self.config.copy(),))
        if "role_registry r WHERE role_id='AUTOPILOT'" in sql:return Row((self.role.copy(),))
        if 'SELECT count(*) FROM autopilot.task WHERE status IN' in sql:return Row((0,0))
        if 'register_universal_work_item' in sql:
            return Row(({'work_item_id':WORK,'created':self.fault!='replay','state':'READY'},))
        if 'claim_project_work_probe' in sql:
            return Row(({'work_item_id':WORK,'work_key':'native-pilot-one','role':'AUTOPILOT',
                         'target_pr':1150,'lease_epoch':1},))
        if 'materialize_project_work_probe' in sql:
            return Row(({'task_id':TASK,'created':True,'resulting_state':'ACTIVE'},))
        if 'claim_next_task' in sql:
            self.goal={'repository':'olegmed1-art/bridge-video-free',
                'mailbox_pr':1703,'role':'AUTOPILOT','target_pr':1150,
                'expected_head_sha':HEAD,'dispatch_epoch':1,
                'successor_task_key':None,'successor_role':None,
                'successor_target_pr':None,'successor_expected_head_sha':None}
            return Row(({'task_id':DISPATCH if self.fault=='wrong_task' else TASK,
                         'goal_type':'CHATGPT_ROLE_DISPATCH_V1',
                         'goal_json':self.goal,
                         'cost_cap_microusd':0,'cost_reserved_microusd':0,'lease_epoch':1},))
        if 'prepare_role_dispatch' in sql:
            return Row(({'dispatch_id':DISPATCH,'role':'AUTOPILOT','target_pr':1150,
                         'expected_head_sha':HEAD,'task_fingerprint':'c'*64},))
        if 'claim_role_dispatch_outbox_v2' in sql:
            return Row(({'dispatch_id':DISPATCH,'mode':'READ_ONLY','target_pr':1150,
                         'expected_head_sha':HEAD,'task_fingerprint':'c'*64,
                         'claim_epoch':1,'dispatch_epoch':1,'role':'AUTOPILOT'},))
        if 'get_dispatch_assignment' in sql:
            return Row(({'dispatch_id':DISPATCH,'task_id':TASK,'role':'AUTOPILOT','execution_scope':'REPOSITORY',
                         'can_repair':False,'task_kind':'REPOSITORY_AUDIT',
                         'task_spec_json':dict(self.spec,mailbox_pr=1703,role='AUTOPILOT',
                             work_key='native-pilot-one',source_task_kind='REPOSITORY_AUDIT',repair_attempt=0)},))
        if 'role_dispatch_outbox o' in sql:
            if self.terminal_stage:
                return Row(({'task_id':TASK,'status':'CALLBACK_ACCEPTED',
                             'delivery_contract_version':4},))
            if 'FOR UPDATE' in sql:
                return Row(({'status':'CLAIMED','claim_owner':self.worker,
                             'claim_epoch':1,'task_id':TASK,'expected_head_sha':HEAD,
                             'task_fingerprint':'c'*64,'mode':'READ_ONLY'},))
            return Row(({'status':'PUBLISHED','delivery_contract_version':3,
                         'github_dispatch_comment_id':1234,
                         'dispatch_body_sha256':self.body_sha},))
        if 'mark_role_dispatch_published' in sql:return Row((True,))
        if self.terminal_stage:
            if 'native_cli_receipt n' in sql:
                return Row(({'state':'TERMINAL','request':self.native_request,
                             'provider_task_id':'task_e_fixture','terminal':self.result,
                             'owner_name':target.EXPECTED_TARGET['recipient']},))
            if 'autopilot.task t WHERE task_id' in sql:
                return Row(({'task_id':TASK,'status':'FAILED_CLOSED' if self.blocked_terminal else 'DONE',
                             'goal_json':self.goal},))
            if 'project_work_item w' in sql:
                return Row(({'work_item_id':WORK,'last_task_id':TASK,
                             'state':'BLOCKED' if self.blocked_terminal else 'DONE'},))
            if 'project_work_task WHERE work_item_id' in sql:return Row((1,))
            if "goal_json->>'origin_task_id'" in sql:return Row((0,))
        return Row(None)


def gates(monkeypatch,prior):
    monkeypatch.setattr(target.hold,'attest',lambda:prior)
    monkeypatch.setattr(target.hold,'service_hold_identity',
                        lambda:hold.ServiceHoldIdentity(**asdict(prior)))
    monkeypatch.setattr(target.engine,'identity',lambda *a:None)
    monkeypatch.setattr(target.switch,'unchanged_files',lambda *a:None)


def test_plan_is_external_and_scope_binds_accepted_plan():
    plan,agreement,_,_,_=fixture()
    assert plan.scope['plan_sha256']==plan.digest
    assert agreement.scope==plan.scope_digest
    with pytest.raises(RuntimeError,match='NOT_ACCEPTED'):
        target.Plan(plan.raw, '0'*64)
    altered=dict(plan.value,branch='autopilot/dispatch/forbidden')
    raw=target.encoded(altered)
    with pytest.raises(RuntimeError,match='PLAN_SHAPE'):
        target.Plan(raw,hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize('fault',[None,'replay','wrong_task'])
def test_six_rpc_order_atomic_rollback_and_snapshot_before_update(tmp_path,monkeypatch,fault):
    plan,agreement,prior,baseline_raw,accepted_baseline=fixture()
    gates(monkeypatch,prior)
    conn=FakeConn(fault=fault);conn.spec=plan.value['task_spec_json']
    path=tmp_path/'before.json'
    if fault is None:
        result=target.prepare(conn,plan,agreement,baseline_raw,accepted_baseline,path,
            target_open=True,observed_head_sha=HEAD,observed_branch=plan.value['branch'])
        assert result['dispatch_id']==DISPATCH and result['pilot_authorized'] is False
        assert conn.committed and not conn.rolled_back
    else:
        with pytest.raises(RuntimeError):
            target.prepare(conn,plan,agreement,baseline_raw,accepted_baseline,path,
                target_open=True,observed_head_sha=HEAD,observed_branch=plan.value['branch'])
        assert conn.rolled_back and not conn.committed
    assert path.exists()
    names=['register_universal_work_item','claim_project_work_probe',
           'materialize_project_work_probe','claim_next_task',
           'prepare_role_dispatch','claim_role_dispatch_outbox_v2']
    present=[next(i for i,s in enumerate(conn.events) if name in s) for name in names[:4 if fault=='wrong_task' else 1 if fault=='replay' else 6]]
    assert present==sorted(present)
    assert next(i for i,s in enumerate(conn.events) if 'UPDATE autopilot.native_cli_config' in s) < present[0]


def test_wrong_actual_head_refuses_before_snapshot_or_sql(tmp_path,monkeypatch):
    plan,agreement,prior,baseline_raw,accepted_baseline=fixture()
    gates(monkeypatch,prior)
    conn=FakeConn();path=tmp_path/'before.json'
    with pytest.raises(RuntimeError,match='HEAD_CHANGED'):
        target.prepare(conn,plan,agreement,baseline_raw,accepted_baseline,path,
            target_open=True,observed_head_sha=HEAD,observed_branch='fix/other')
    assert conn.events==[] and not path.exists()


@pytest.mark.parametrize('fail_on',[1,2])
def test_authenticated_effect_guard_brackets_intake_mutation_and_commit(
        tmp_path,monkeypatch,fail_on):
    plan,agreement,prior,baseline_raw,accepted_baseline=fixture()
    gates(monkeypatch,prior)
    conn=FakeConn();conn.spec=plan.value['task_spec_json']
    calls=[]
    def guard():
        calls.append(len(conn.events))
        if len(calls)==fail_on:raise RuntimeError('RUN_CANCELLED')
    with pytest.raises(RuntimeError,match='RUN_CANCELLED'):
        target.prepare(conn,plan,agreement,baseline_raw,accepted_baseline,
            tmp_path/'before.json',target_open=True,observed_head_sha=HEAD,
            observed_branch=plan.value['branch'],effect_guard=guard)
    assert len(calls)==fail_on and conn.rolled_back and not conn.committed
    first_update=next((i for i,s in enumerate(conn.events)
                       if 'UPDATE autopilot.native_cli_config' in s),None)
    if fail_on==1:assert first_update is None
    else:assert first_update is not None and calls[1]>first_update


def test_outer_transaction_cannot_turn_commit_ack_into_savepoint(tmp_path,monkeypatch):
    plan,agreement,prior,baseline_raw,accepted_baseline=fixture()
    gates(monkeypatch,prior)
    conn=FakeConn();conn.info.transaction_status=TransactionStatus.INTRANS
    path=tmp_path/'before.json'
    with pytest.raises(RuntimeError,match='INTAKE_AUTHORITY'):
        target.prepare(conn,plan,agreement,baseline_raw,accepted_baseline,path,
            target_open=True,observed_head_sha=HEAD,observed_branch=plan.value['branch'])
    assert conn.events==[] and not path.exists()


def test_publication_requires_independently_accepted_real_discovery(monkeypatch):
    plan,agreement,prior,_,_=fixture();gates(monkeypatch,prior)
    conn=FakeConn();conn.worker=plan.worker
    dispatch={'dispatch_id':DISPATCH,'dispatch_epoch':1,'role':'AUTOPILOT',
              'task_fingerprint':'c'*64,'target_pr':1150,'mode':'READ_ONLY'}
    body=target.dispatch_body(dispatch);body_sha=hashlib.sha256(body.encode()).hexdigest()
    conn.body_sha=body_sha
    receipt={'plan_sha256':plan.digest,'dispatch_id':DISPATCH,'task_id':TASK,
             'claim_epoch':1,'dispatch':dispatch,'published':False,'pilot_authorized':False}
    discovery={'repository':plan.value['repository'],'number':1234,
        'url':'https://github.com/olegmed1-art/bridge-video-free/pull/1234',
        'state':'open','draft':True,'head_ref':'autopilot/dispatch/'+DISPATCH,
        'head_sha':'d'*40,'author_login':'bridge-school-oracle-autopilot[bot]',
        'author_id':322994314,'author_type':'Bot','dispatch_id':DISPATCH,
        'dispatch_file':'docs/evidence/autopilot/role-dispatch-'+DISPATCH+'.md',
        'dispatch_body':body,'dispatch_body_sha256':body_sha}
    raw=target.encoded(discovery);accepted=hashlib.sha256(raw).hexdigest()
    with pytest.raises(RuntimeError,match='NOT_ACCEPTED'):
        target.mark_reviewed_publication(conn,plan,agreement,receipt,raw,'f'*64)
    assert conn.events==[]
    checks=[]
    result=target.mark_reviewed_publication(conn,plan,agreement,receipt,raw,accepted,
                                            effect_guard=lambda:checks.append(len(conn.events)))
    assert result['published'] is True and result['pilot_authorized'] is False
    assert conn.committed and len(checks)==2
    assert checks[0]==next(i for i,s in enumerate(conn.events) if 'mark_role_dispatch_published' in s)
    assert checks[1]>checks[0] and checks[1]==conn.events.index('COMMIT')


def test_terminal_exact_readback_and_conditional_control_restore(tmp_path,monkeypatch):
    plan,agreement,prior,baseline_raw,accepted_baseline=fixture()
    gates(monkeypatch,prior)
    conn=FakeConn();conn.spec=plan.value['task_spec_json']
    path=tmp_path/'before.json'
    receipt=target.prepare(conn,plan,agreement,baseline_raw,accepted_baseline,path,
        target_open=True,observed_head_sha=HEAD,observed_branch=plan.value['branch'])
    conn.terminal_stage=True
    conn.native_request={'dispatch_id':DISPATCH,'assignment':receipt['assignment'],
        'branch':plan.value['branch'],'reservation_id':'44444444-4444-4444-8444-444444444444',
        'expected_head_sha':HEAD,'mode':'READ_ONLY','target_pr':1150,
        'task_fingerprint':'c'*64}
    conn.result={'status':'SUCCEEDED','result_code':'VERIFIED',
                 'summary':'Exact fixture passed.','target_head_sha':HEAD,
                 'provider_evidence_sha256':'d'*64}
    envelope={'version':1,'plan_sha256':plan.digest,'dispatch_id':DISPATCH,
              'task_id':TASK,'provider_task_id':'task_e_fixture',
              'request':conn.native_request,'result':conn.result}
    raw=target.encoded(envelope);accepted=hashlib.sha256(raw).hexdigest()
    conn.read_only=True
    observed=target.observe_terminal(conn,plan,receipt,raw,accepted)
    assert observed['success'] is True and observed['controls_restored'] is False
    conn.read_only=False
    checks=[]
    restored=target.restore_controls_after_terminal(conn,plan,receipt,raw,accepted,path,
                                                     effect_guard=lambda:checks.append(len(conn.events)))
    assert restored['success'] is True and restored['controls_restored'] is True
    assert conn.config['enabled'] is False and conn.role['can_repair'] is True
    assert len(checks)==2 and checks[1]>checks[0] and checks[1]==conn.events.index('COMMIT',checks[0])


def test_unknown_terminal_or_changed_controls_never_restores(tmp_path,monkeypatch):
    plan,agreement,prior,baseline_raw,accepted_baseline=fixture()
    gates(monkeypatch,prior)
    conn=FakeConn();conn.spec=plan.value['task_spec_json']
    path=tmp_path/'before.json'
    receipt=target.prepare(conn,plan,agreement,baseline_raw,accepted_baseline,path,
        target_open=True,observed_head_sha=HEAD,observed_branch=plan.value['branch'])
    envelope={'version':1,'plan_sha256':plan.digest,'dispatch_id':DISPATCH,
              'task_id':TASK,'provider_task_id':'task_e_fixture',
              'request':{'dispatch_id':DISPATCH,'assignment':receipt['assignment'],
                         'branch':plan.value['branch'],
                         'reservation_id':'44444444-4444-4444-8444-444444444444',
                         'expected_head_sha':HEAD,'mode':'READ_ONLY','target_pr':1150,
                         'task_fingerprint':'c'*64},
              'result':{'status':'SUCCEEDED','result_code':'VERIFIED',
                        'summary':'Fixture.','target_head_sha':HEAD,
                        'provider_evidence_sha256':'d'*64}}
    raw=target.encoded(envelope);accepted=hashlib.sha256(raw).hexdigest()
    with pytest.raises(RuntimeError,match='RPC_MISSING'):
        target.restore_controls_after_terminal(conn,plan,receipt,raw,accepted,path)
    assert conn.config['enabled'] is True
    conn.role['description']='foreign edit'
    with pytest.raises(RuntimeError,match='CONCURRENT_CHANGE'):
        target.restore_controls_after_terminal(conn,plan,receipt,raw,accepted,path)
    assert conn.config['enabled'] is True and conn.role['can_repair'] is False


def test_known_blocked_terminal_may_restore_controls_without_pilot_success(tmp_path,monkeypatch):
    plan,agreement,prior,baseline_raw,accepted_baseline=fixture()
    gates(monkeypatch,prior)
    conn=FakeConn();conn.spec=plan.value['task_spec_json']
    path=tmp_path/'before.json'
    receipt=target.prepare(conn,plan,agreement,baseline_raw,accepted_baseline,path,
        target_open=True,observed_head_sha=HEAD,observed_branch=plan.value['branch'])
    conn.terminal_stage=True;conn.blocked_terminal=True
    request={'dispatch_id':DISPATCH,'assignment':receipt['assignment'],
        'branch':plan.value['branch'],'reservation_id':'44444444-4444-4444-8444-444444444444',
        'expected_head_sha':HEAD,'mode':'READ_ONLY','target_pr':1150,'task_fingerprint':'c'*64}
    result={'status':'BLOCKED','result_code':'TARGET_HEAD_CHANGED',
            'summary':'Head moved.','target_head_sha':'e'*40,
            'provider_evidence_sha256':'d'*64}
    conn.native_request=request;conn.result=result
    envelope={'version':1,'plan_sha256':plan.digest,'dispatch_id':DISPATCH,
              'task_id':TASK,'provider_task_id':'task_e_fixture',
              'request':request,'result':result}
    raw=target.encoded(envelope);accepted=hashlib.sha256(raw).hexdigest()
    observed=target.restore_controls_after_terminal(conn,plan,receipt,raw,accepted,path)
    assert observed['success'] is False and observed['controls_restored'] is True


def test_task_mailbox_must_match_locked_active_registry(tmp_path,monkeypatch):
    plan,agreement,prior,baseline_raw,accepted_baseline=fixture()
    gates(monkeypatch,prior)
    conn=FakeConn(fault='mailbox_drift');conn.spec=plan.value['task_spec_json']
    with pytest.raises(RuntimeError,match='GOAL_DRIFT'):
        target.prepare(conn,plan,agreement,baseline_raw,accepted_baseline,
            tmp_path/'before.json',target_open=True,observed_head_sha=HEAD,
            observed_branch=plan.value['branch'])
    assert conn.rolled_back and not conn.committed
    assert not any('prepare_role_dispatch' in event for event in conn.events)
