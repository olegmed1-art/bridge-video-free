from copy import deepcopy

import pytest

from oracle_autopilot.codex_cli_queue import NativeQueue, NativeAuthority


def request():
    dispatch='12345678-1234-4234-8234-123456789012'
    return dict(dispatch_id=dispatch,reservation_id='12345678-1234-4234-8234-123456789013',
        expected_head_sha='a'*40,branch='codex/test',mode='READ_ONLY',target_pr=1546,task_fingerprint='b'*64,
        assignment=dict(dispatch_id=dispatch,execution_scope='REPOSITORY',can_repair=False,
            task_spec_json=dict(repository='olegmed1-art/bridge-video-free',target_pr=1546,
                               expected_head_sha='a'*40,execution_mode='READ_ONLY')))


def test_receipt_values_are_parameters_not_executable_sql():
    calls=[]
    def rpc(sql, args):
        calls.append((sql,args))
        return {'payload':{'state':'TERMINAL'}}
    queue=NativeQueue(rpc)
    marker="'); DROP TABLE autopilot.task; --"
    queue.finish(request(),'task_e_test',{'summary':marker})
    sql,args=calls[0]
    assert marker not in sql and marker in args[2]
    assert sql=='SELECT autopilot.native_cli_finish(%s::jsonb,%s,%s::jsonb) AS payload'


@pytest.mark.parametrize('response',[None,{}, {'payload':None},{'payload':'true'},{'payload':1}])
def test_unknown_database_authority_is_never_truthy(response):
    queue=NativeQueue(lambda *args:response)
    with pytest.raises(ValueError):
        queue.current(request())


def test_sql_failure_propagates_without_credential_or_legacy_fallback():
    calls=[]
    def denied(*args):
        calls.append(args)
        raise PermissionError('owner-only migration not activated')
    queue=NativeQueue(denied)
    with pytest.raises(PermissionError):
        queue.snapshot(request()['dispatch_id'])
    assert len(calls)==1


@pytest.mark.parametrize('changed', ['none','fork','branch','number','base_repo','paused'])
def test_primary_pr_and_database_authority_must_both_match(changed):
    req=request()
    repository={'full_name':'olegmed1-art/bridge-video-free'}
    pr={'number':1546,'state':'open','head':{'ref':'codex/test','sha':'a'*40,'repo':deepcopy(repository)},
        'base':{'repo':deepcopy(repository)}}
    if changed=='fork': pr['head']['repo']['full_name']='other/fork'
    if changed=='branch': pr['head']['ref']='codex/other'
    if changed=='number': pr['number']=1547
    if changed=='base_repo': pr['base']['repo']['full_name']='other/repo'
    queue=NativeQueue(lambda *args:{'payload':changed!='paused'})
    authority=NativeAuthority(queue,lambda number:pr)
    assert authority.inspect(req)['current'] is (changed=='none')
