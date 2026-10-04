"""Separate-process unconditional cutoff revoke; never calls the controller.

Must be launched/qualified and its authentic ARMED receipt retained BEFORE writes.
No cancel/disarm/heartbeat loophole. It ends this acceptance experiment at the
stage cutoff, including after controller success. Not a 24-hour hosting service.
"""
import time
from .launch_contract import require
from .fixed_adapter import RecoveryUnproven


def watch(launch, inspect, recover, clock, *, ready, sleep=time.sleep):
    launch.admit(clock())
    state = inspect()
    require(state.get("status")=="READ_ONLY_PLAN"
        and state.get("contract_hash")==launch.fingerprint
        and state.get("plan_hash")==launch.plan_hash
        and (state.get("state"),state.get("rows"),state.get("active"))==("absent",0,0),
        "watchdog_first_absence_required")
    ready({"status":"WATCHDOG_ARMED","contract_hash":launch.fingerprint,
           "plan_hash":launch.plan_hash,"cutoff":launch.public()["stage_until"],
           "unconditional_revoke":True})
    # It remains armed regardless of controller liveness or final acceptance.
    while clock() < launch.stage_until:
        sleep(min(.25,max(0,(launch.stage_until-clock()).total_seconds())))
    try:
        receipt=recover()
        require(receipt.get("status")=="OWNED_REVOKE_CONFIRMED"
            and receipt.get("contract_hash")==launch.fingerprint
            and receipt.get("plan_hash")==launch.plan_hash
            and receipt.get("active")==0 and receipt.get("rows",43)<=42,
            "watchdog_revoke_readback_required")
        return {"status":"WATCHDOG_REVOKE_CONFIRMED","receipt":receipt}
    except BaseException:
        raise RecoveryUnproven("watchdog_revoke_unproven") from None
