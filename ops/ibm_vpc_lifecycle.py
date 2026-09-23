#!/usr/bin/env python3
"""Fail-closed decision core for IBM VPC on-demand lifecycle.

This module only classifies a complete observation. It never calls IBM APIs,
starts work, or stops an instance. The resident Light Oracle controller must
obtain the observation from authoritative sources and serialize mutations.
"""
from __future__ import annotations

from typing import Any

SCHEMA = "ibm-vpc-lifecycle-observation-v1"
MAX_OBSERVATION_AGE_SECONDS = 30
MAX_FUTURE_SKEW_SECONDS = 5
DEFAULT_IDLE_GRACE_SECONDS = 600
KNOWN_VPC_STATES = {
    "running", "stopped", "starting", "stopping", "restarting", "pending", "failed"
}
COUNT_FIELDS = (
    "eligible_pending_jobs",
    "running_jobs",
    "active_leases",
    "host_active_jobs",
    "active_spool_items",
    "maintenance_leases",
)
COMPLETENESS_FIELDS = (
    "queue_snapshot_complete",
    "lease_snapshot_complete",
    "worker_snapshot_complete",
    "storage_snapshot_complete",
)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def decide(
    observation: dict[str, Any],
    *,
    now_epoch: int,
    idle_grace_seconds: int = DEFAULT_IDLE_GRACE_SECONDS,
) -> dict[str, str]:
    """Return one lifecycle intent or HOLD; never perform a mutation."""
    if not isinstance(observation, dict) or observation.get("schema") != SCHEMA:
        return {"decision": "HOLD", "reason": "schema_unknown"}

    observed = observation.get("observed_at_epoch")
    if not _is_int(now_epoch) or now_epoch <= 0 or not _is_int(observed) or observed <= 0:
        return {"decision": "HOLD", "reason": "timestamp_invalid"}
    age = now_epoch - observed
    if age > MAX_OBSERVATION_AGE_SECONDS:
        return {"decision": "HOLD", "reason": "observation_stale"}
    if age < -MAX_FUTURE_SKEW_SECONDS:
        return {"decision": "HOLD", "reason": "observation_from_future"}

    state = observation.get("vpc_status")
    if state not in KNOWN_VPC_STATES:
        return {"decision": "HOLD", "reason": "vpc_status_unknown"}

    for field in COMPLETENESS_FIELDS:
        if observation.get(field) is not True:
            return {"decision": "HOLD", "reason": f"{field}_not_proven"}

    for field in COUNT_FIELDS:
        value = observation.get(field)
        if not _is_int(value) or value < 0:
            return {"decision": "HOLD", "reason": f"{field}_invalid"}

    work_exists = any(observation[field] > 0 for field in COUNT_FIELDS)
    if work_exists:
        if observation.get("disk_snapshot_complete") is not True:
            return {"decision": "HOLD", "reason": "disk_snapshot_complete_not_proven"}
        if observation.get("disk_headroom_safe") is not True:
            return {"decision": "HOLD", "reason": "disk_headroom_not_proven"}
        if observation["eligible_pending_jobs"] + observation["running_jobs"] == 0:
            return {"decision": "HOLD", "reason": "orphan_lease_or_host_work"}
        if state == "stopped":
            return {"decision": "START", "reason": "admitted_heavy_work"}
        if state == "running":
            return {"decision": "KEEP_RUNNING", "reason": "heavy_work_or_lease_active"}
        if state == "stopping":
            return {"decision": "WAIT_THEN_RECONCILE", "reason": "stop_in_progress_with_work"}
        if state == "failed":
            return {"decision": "HOLD", "reason": "vpc_failed"}
        return {"decision": "WAIT_FOR_READY", "reason": f"vpc_{state}"}

    if state == "stopped":
        return {"decision": "IDLE_STOPPED", "reason": "no_work"}
    if state == "failed":
        return {"decision": "HOLD", "reason": "vpc_failed"}
    if state != "running":
        return {"decision": "WAIT_FOR_READY", "reason": f"vpc_{state}"}

    idle_since = observation.get("idle_since_epoch")
    if not _is_int(idle_since) or idle_since <= 0 or idle_since > now_epoch:
        return {"decision": "HOLD", "reason": "idle_since_invalid"}
    if not _is_int(idle_grace_seconds) or not 60 <= idle_grace_seconds <= 86400:
        return {"decision": "HOLD", "reason": "idle_grace_invalid"}
    if now_epoch - idle_since < idle_grace_seconds:
        return {"decision": "KEEP_RUNNING", "reason": "idle_grace_not_elapsed"}
    return {"decision": "STOP", "reason": "complete_idle_proof_and_grace"}


def main() -> int:
    import argparse
    import json
    import sys
    parser = argparse.ArgumentParser()
    parser.add_argument("--now-epoch", type=int, required=True)
    parser.add_argument("--idle-grace-seconds", type=int, default=DEFAULT_IDLE_GRACE_SECONDS)
    args = parser.parse_args()
    try:
        observation = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        result = {"decision": "HOLD", "reason": "observation_invalid"}
    else:
        result = decide(
            observation,
            now_epoch=args.now_epoch,
            idle_grace_seconds=args.idle_grace_seconds,
        )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["decision"] != "HOLD" else 3


if __name__ == "__main__":
    raise SystemExit(main())
