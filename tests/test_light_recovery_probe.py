from copy import deepcopy
import pytest
from oracle_autopilot import light_recovery_probe as target


def baseline():
    return [dict(task_id=target.CANARY, status='FAILED_CLOSED', attempts=1,
        lease_epoch=1, goal_type='CHATGPT_ROLE_DISPATCH_V1', lease_until=None,
        cost_reserved_microusd=0, cost_actual_microusd=0, cost_cap_microusd=0)]


def test_failed_closed_is_the_only_supported_baseline():
    target.validate_queue(baseline(), dict(active_workers=0, probe_reservations=0))


@pytest.mark.parametrize('field,value', [
    ('status','READY'), ('attempts',0), ('attempts',2), ('lease_epoch',2),
    ('lease_until','2026-09-24'), ('task_id','another'), ('goal_type','another'),
    ('cost_reserved_microusd',1), ('cost_actual_microusd',1), ('cost_cap_microusd',1)])
def test_queue_drift_fails(field, value):
    rows = deepcopy(baseline())
    rows[0][field] = value
    with pytest.raises(ValueError):
        target.validate_queue(rows, dict(active_workers=0, probe_reservations=0))


@pytest.mark.parametrize('rows,capacity', [
    ([],dict(active_workers=0,probe_reservations=0)),
    (baseline()*2,dict(active_workers=0,probe_reservations=0)),
    (baseline(),dict(active_workers=1,probe_reservations=0)),
    (baseline(),dict(active_workers=0,probe_reservations=1))])
def test_missing_or_competing_work_fails(rows,capacity):
    with pytest.raises(ValueError):
        target.validate_queue(rows,capacity)
