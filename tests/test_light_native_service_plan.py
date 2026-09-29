"""The transient plan preserves sandboxing and never treats a log as success."""
import json
import pytest
from ops import light_native_service_plan as plan
from ops import oracle_light_active_hold_attest as hold


def prior():
    return hold.HoldIdentity('autopilot-lite-vnic',123,'a'*32,str(plan.LIGHT/'releases'/('b'*40)),'c'*64)


def test_fixed_worker_plan_preserves_hardening_and_original_pins():
    command=plan.pilot_command('d'*40,prior(),300,request_digest='e'*64)
    assert command[:4]==['/usr/bin/systemd-run','--quiet','--unit='+plan.PILOT_UNIT,'--service-type=exec']
    assert '--property=NoNewPrivileges=yes' in command
    assert '--property=ProtectSystem=strict' in command
    assert '--property=ReadWritePaths='+str(plan.LIGHT/'runtime') in command
    assert '--property=Restart=no' in command
    assert '--property=KillMode=control-group' in command
    assert '--property=EnvironmentFile='+prior().release+'/ops/autopilot/broker-hold.env' in command
    assert '--property=Environment=AUTOPILOT_ADMISSION_MODE=PILOT' in command
    assert command[-1]==str(plan.LIGHT/'releases'/('d'*40))
    assert 'sudo' not in command and '/bin/sh' not in command


@pytest.mark.parametrize('source',['../escape','a'*40+';id','A'*40,'a'*39,'a'*41])
def test_source_cannot_change_paths_or_commands(source):
    with pytest.raises(RuntimeError): plan.pilot_command(source,prior(),300,request_digest='e'*64)


@pytest.mark.parametrize('seconds',[True,0,29,1801,'300'])
def test_timeout_must_be_bounded(seconds):
    with pytest.raises(RuntimeError): plan.pilot_command('d'*40,prior(),seconds,request_digest='e'*64)


def test_supervisor_arms_fixed_restore_and_nonrenewing_timeout():
    command=plan.supervisor_command('d'*40,'e'*64,300)
    assert '--property=NoNewPrivileges=yes' in command
    assert '--property=Restart=no' in command
    assert '--property=RuntimeMaxSec=300' in command
    hook=[x for x in command if x.startswith('--property=ExecStopPost=')]
    assert hook==['--property=ExecStopPost=/usr/bin/python3 -I -S -B '+str(plan.ROOT/('e'*64)/'supervisor.py')+' restore '+'e'*64]
    assert command[-2:]==['run','e'*64]


def test_service_only_identity_cannot_be_reused_as_full_admission():
    value=hold.ServiceHoldIdentity(**vars(prior()))
    with pytest.raises(RuntimeError,match='PILOT_SERVICE_PRIOR'):
        plan.pilot_command('d'*40,value,300,request_digest='e'*64)


def marker():
    return dict(audit='LIGHT_NATIVE_SINGLE_PILOT_TERMINAL',dispatch_id='dispatch',
        provider_task_id='task_e_exact',terminal_sha256='a'*64)


def test_terminal_marker_requests_independent_readback():
    result=plan.classify_journal(json.dumps(marker()),'dispatch')
    assert result['state']=='READBACK_REQUIRED'
    assert plan.classify_journal('unrelated log','dispatch')=={'state':'RUNNING'}
    assert plan.classify_journal('{"audit":"LIGHT_NATIVE_SINGLE_PILOT_QUARANTINED"}','dispatch')=={'state':'QUARANTINED'}


@pytest.mark.parametrize('task_id',[
    'task_abc123', 'task_e_exact', 'task_'+'a'*120,
    'task_', 'task_e_bad-id', 'task_'+'a'*121, 'task_abc\n',
])
def test_terminal_identifier_matches_provider_contract(task_id):
    from oracle_autopilot.codex_cli_bridge import TASK_URL
    item=marker()
    item['provider_task_id']=task_id
    accepted=TASK_URL.fullmatch('https://chatgpt.com/codex/tasks/'+task_id)
    if accepted:
        assert plan.classify_journal(json.dumps(item),'dispatch')['state']=='READBACK_REQUIRED'
    else:
        with pytest.raises(RuntimeError,match='PILOT_SERVICE_TERMINAL_MARKER'):
            plan.classify_journal(json.dumps(item),'dispatch')


@pytest.mark.parametrize('damage',['different_dispatch','duplicate','quarantine','extra','bad_task'])
def test_ambiguous_or_wrong_markers_refuse(damage):
    item=marker()
    if damage=='different_dispatch': item['dispatch_id']='other'
    if damage=='extra': item['untrusted']='field'
    if damage=='bad_task': item['provider_task_id']='https://other'
    line=json.dumps(item)
    if damage=='duplicate': line+='\n'+line
    if damage=='quarantine': line+='\n{"audit":"LIGHT_NATIVE_SINGLE_PILOT_QUARANTINED"}'
    with pytest.raises(RuntimeError): plan.classify_journal(line,'dispatch')
