"""Separate read-only authority and public result contracts."""
from copy import deepcopy
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from ops import light_native_retirement as r
from ops import light_native_retirement_live as live
from ops import light_native_lane_owner_runner as runner
from test_light_native_retirement_reference import reference
from test_light_native_retirement import retained, request_bytes


@pytest.fixture
def observation(reference):
    t=reference
    private=r.parse(t.private)
    private['action']='observe-retirement'
    scope=dict(version=1,operation='READ_ONLY_VERIFY_FAILED_PREPARE_RETIREMENT',source=private['source'],
        controller_sha256=private['accepted_controller_sha256'],runtime_sha256=private['accepted_runtime_sha256'],
        record_sha256=private['record_sha256'],historical_journal_sha256=t.pins)
    private['agreement'].update(evidence='OWNER_ACCEPTED_READ_ONLY_RETIREMENT_OBSERVATION',
        operation_digest=r.sha(r.encoded(scope)))
    private['accepted_agreement_sha256']=r.sha(r.encoded(private['agreement']))
    t.private=r.encoded(private)
    t.public.update(action='observe-retirement-reference',agreement=private['agreement'],
        accepted_agreement_sha256=private['accepted_agreement_sha256'],private_request_sha256=r.sha(t.private))
    return t


def test_observation_reconstructs_separate_private_request(observation):
    t=observation;raw=r.encoded(t.public)
    assert live.resolve_reference(raw,r.sha(raw),t.request.controller,t.request.runtime,
        t.request.guard,read_only=True)==(t.private,r.sha(t.private))
    assert not (t.root/t.entry/r.NAME).exists()


@pytest.mark.parametrize('readonly',[False,True])
def test_opposite_action_cannot_cross_modes(reference,observation,readonly):
    # The observation fixture mutates its reference; explicitly select the opposite action.
    value=deepcopy(observation.public)
    if readonly:value['action']='retire-prepare-reference'
    raw=r.encoded(value)
    with pytest.raises(RuntimeError):live.public_reference(raw,
        action='observe-retirement-reference' if readonly else 'retire-prepare-reference')


@pytest.mark.parametrize('fault',['create-operation','create-evidence','private-action','journal-pin'])
def test_readonly_authority_cannot_borrow_create_agreement(observation,fault):
    t=observation;p=r.parse(t.private);v=deepcopy(t.public)
    if fault=='create-operation':p['agreement']['operation_digest']='0'*64
    if fault=='create-evidence':p['agreement']['evidence']=live.PUBLIC_EVIDENCE
    if fault=='private-action':p['action']='retire-prepare'
    if fault=='journal-pin':p['historical_journal_sha256']['baseline.json']='0'*64
    p['accepted_agreement_sha256']=r.sha(r.encoded(p['agreement']))
    v.update(agreement=p['agreement'],accepted_agreement_sha256=p['accepted_agreement_sha256'],
        private_request_sha256=r.sha(r.encoded(p)))
    raw=r.encoded(v)
    with pytest.raises(RuntimeError):live.resolve_reference(raw,r.sha(raw),t.request.controller,
        t.request.runtime,t.request.guard,read_only=True)
    assert not (t.root/t.entry/r.NAME).exists()


@pytest.mark.parametrize('readonly',[False,True])
def test_bootstrap_compiles_and_binds_mode(readonly):
    code=runner.bootstrap('a'*40,*(['b'*64]*4),123,1,read_only=readonly)
    tree=ast.parse(code)
    compile(tree,'generated-root-bootstrap','exec')
    selected=[x for x in ast.walk(tree) if isinstance(x,ast.If) and isinstance(x.test,ast.Constant)
        and type(x.test.value) is bool]
    assert len(selected)==1 and selected[0].test.value is readonly
    body=selected[0].body if readonly else selected[0].orelse
    calls=[x.func.id for node in body for x in ast.walk(node) if isinstance(x,ast.Call) and isinstance(x.func,ast.Name)]
    choices=[x.id for node in body for x in ast.walk(node) if isinstance(x,ast.Name)]
    assert ('inspect_retirement' in choices)==readonly
    assert ('inspect_local' in choices)==readonly
    assert ('phase' in calls)==(not readonly)


def test_dispatch_mode_rejects_create_before_transport(observation,tmp_path):
    t=observation;raw=r.encoded(t.public)
    inputs=dict(expected_main_sha=t.public['source'],accepted_controller_sha256=t.public['accepted_controller_sha256'],
        accepted_runtime_sha256=t.public['accepted_runtime_sha256'],accepted_payload_sha256=r.sha(raw),
        retirement_reference_json=raw.decode())
    path=tmp_path/'event.json';path.write_text(json.dumps({'inputs':inputs}))
    assert runner.dispatch_inputs(path,t.public['source'],read_only=True)[0]==raw
    with pytest.raises(RuntimeError):runner.dispatch_inputs(path,t.public['source'])


def safe_result():
    return dict(audit='LIGHT_LANE_RETIREMENT_OBSERVATION',state='OBSERVATION_REFUSED',phase='AUTHORITY',
        proposal_state='UNKNOWN',proposal_observation_final=False,metadata=None,record_sha256='a'*64,
        record_matches=False,content_shape_valid=False,journal_pins_verified=False,hold_db_provider_verified=False,
        incident_closed=False,execution_acknowledged=False,new_task_authorized=False)


@pytest.mark.parametrize('phase',live.OBSERVATION_PHASES)
def test_fixed_phase_only_no_private_exception(phase):
    value=safe_result();value['phase']=phase
    assert live.public_observation_result(value,'a'*64)==value
    value['phase']='PRIVATE_SYNTHETIC_JOURNAL_OR_EXCEPTION'
    with pytest.raises(RuntimeError):live.public_observation_result(value,'a'*64)


@pytest.mark.parametrize('field',['incident_closed','execution_acknowledged','new_task_authorized',
    'journal_pins_verified','hold_db_provider_verified','proposal_observation_final'])
def test_refused_result_never_claims_final_proofs(field):
    value=safe_result();value[field]=True
    with pytest.raises(RuntimeError):live.public_observation_result(value,'a'*64)


def test_public_result_rejects_extra_private_metadata():
    value=safe_result();value['exception']='PRIVATE_SENTINEL'
    with pytest.raises(RuntimeError):live.public_observation_result(value,'a'*64)


def test_observation_workflow_pin_and_no_input_interpolation():
    from ops import light_native_lane_run_guard as guard
    root=Path(__file__).resolve().parents[1]
    raw=(root/guard.OBSERVATION_WORKFLOW).read_bytes()
    assert r.sha(raw)==guard.OBSERVATION_WORKFLOW_SHA256
    assert guard.LaneObservationBinding.workflow==guard.OBSERVATION_WORKFLOW
    assert guard.LaneObservationBinding.workflow_sha256==guard.OBSERVATION_WORKFLOW_SHA256
    assert guard.LaneObservationBinding.workflow!=guard.LaneRunBinding.workflow
    in_steps=False
    for line in raw.decode().splitlines():
        if line.startswith('    steps:'):in_steps=True
        elif line.startswith('  ') and not line.startswith('   ') and line.strip():in_steps=False
        if in_steps:assert 'inputs.' not in line and 'inputs[' not in line
    assert b'ops/light_native_retirement_observe_runner.py' in raw


@pytest.mark.parametrize('readonly',[False,True])
def test_workflow_profiles_cannot_cross_local_context(readonly):
    from ops import light_native_lane_run_guard as guard
    from ops.native_maintenance_run_guard import REPOSITORY,OWNER
    workflow=guard.OBSERVATION_WORKFLOW if readonly else guard.WORKFLOW
    env=dict(GITHUB_SHA='a'*40,GITHUB_REPOSITORY=REPOSITORY,GITHUB_REF='refs/heads/main',
        GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_WORKFLOW_REF=REPOSITORY+'/'+workflow+'@refs/heads/main',
        GITHUB_WORKFLOW_SHA='a'*40,GITHUB_JOB='step',GITHUB_ACTOR=OWNER,GITHUB_TRIGGERING_ACTOR=OWNER,
        GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1')
    assert guard.local_context(env,read_only=readonly)==('a'*40,123,1)
    with pytest.raises(RuntimeError):guard.local_context(env,read_only=not readonly)


@pytest.mark.parametrize('fault',['create','private-extra','malformed'])
def test_actual_observation_wrapper_refuses_before_transport(tmp_path,fault):
    from test_light_native_retirement_reference_i2 import event
    from ops import light_native_lane_run_guard as guard
    from ops.native_maintenance_run_guard import REPOSITORY,OWNER
    value=event()
    if fault=='private-extra':value['inputs']['private']='SYNTHETIC_PRIVATE_DO_NOT_PRINT'
    raw=r.encoded(value) if fault!='malformed' else b'SYNTHETIC_PRIVATE_DO_NOT_PRINT'
    path=tmp_path/'event.json';path.write_bytes(raw)
    root=Path(__file__).resolve().parents[1]
    env=dict(PATH=os.defpath,GITHUB_SHA='a'*40,GITHUB_REPOSITORY=REPOSITORY,GITHUB_REF='refs/heads/main',
        GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_WORKFLOW_REF=REPOSITORY+'/'+guard.OBSERVATION_WORKFLOW+'@refs/heads/main',
        GITHUB_WORKFLOW_SHA='a'*40,GITHUB_JOB='step',GITHUB_ACTOR=OWNER,GITHUB_TRIGGERING_ACTOR=OWNER,
        GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1',GITHUB_EVENT_PATH=str(path))
    script="""import os,runpy,socket,subprocess,sys
sys.path.insert(0,sys.argv[1])
from ops import light_native_lane_owner_runner
def forbidden(*a,**kw):os._exit(97)
socket.socket=forbidden
subprocess.run=forbidden
sys.argv=['observation','unused-key','unused-known','unused-wheels']
runpy.run_module('ops.light_native_retirement_observe_runner',run_name='__main__')
"""
    result=subprocess.run([sys.executable,'-I','-S','-B','-c',script,str(root)],
        env=env,capture_output=True,timeout=10)
    assert result.returncode==2 and result.stderr==b''
    assert json.loads(result.stdout)=={'audit':'LIGHT_LANE_OBSERVATION_RUNNER_REFUSED'}
