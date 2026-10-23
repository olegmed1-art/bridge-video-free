import pytest
from ops.incident.light_pilot_controls_cleanup_20260928 import check_rows,TASK,DISPATCH


def fixture():
    receipt={'applied_config':{'enabled':True},'applied_role':{'can_repair':False},'goal_json_sha256':'goal','dispatch':{'expected_head_sha':'head','target_pr':2025}}
    task={'task_id':TASK,'status':'WAITING_EXTERNAL'}
    outbox={'dispatch_id':DISPATCH,'task_id':TASK,'status':'PUBLISHED','mode':'READ_ONLY','claim_owner':None,'delivery_contract_version':3,'expected_head_sha':'head','target_pr':2025}
    return [receipt['applied_config'].copy(),receipt['applied_role'].copy(),receipt,(0,1,1,0,1),task,outbox,'goal']


def test_exact_zero_submit():
    check_rows(*fixture())


@pytest.mark.parametrize('index,value',[(0,{'enabled':False}),(3,(1,1,1,0,1)),(3,(0,2,1,0,1)),(6,'changed')])
def test_drift_refused(index,value):
    args=fixture();args[index]=value
    with pytest.raises(Exception):check_rows(*args)


def test_claimed_outbox_refused():
    args=fixture();args[5]['claim_owner']='someone'
    with pytest.raises(Exception):check_rows(*args)
