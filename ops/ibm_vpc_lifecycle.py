#!/usr/bin/env python3
"""Fail-closed decision core for IBM VPC on-demand lifecycle.

This module only classifies a complete observation. It never calls IBM APIs,
starts work, or stops an instance. The resident Light Oracle controller must
obtain the observation from authoritative sources and serialize mutations.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
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
    "video_queue_snapshot_complete",
    "lease_snapshot_complete",
    "worker_snapshot_complete",
    "storage_snapshot_complete",
)



VIDEO_QUEUE_STATUSES = frozenset(
    {"PENDING_CANARY", "QUEUED", "LEASED", "REVIEW_READY", "AMBIGUOUS", "FAILED"}
)
VIDEO_BATCH_STATUSES = frozenset(
    {"QUEUED_CANARY", "RUNNING", "CANARY_BLOCKED", "CANARY_REVIEW", "REVIEW"}
)
VIDEO_CLAIMABLE_BATCH_STATUSES = frozenset({"QUEUED_CANARY", "RUNNING"})
VIDEO_READINESS_COUNT_FIELDS = (
    "runnable_now_count",
    "active_leases_count",
    "retry_waiting_count",
    "pending_canary_count",
    "expired_attempts_exhausted_count",
    "blocked_nonterminal_count",
    "unknown_job_status_count",
    "unknown_batch_status_count",
    "invalid_lease_shape_count",
)
POSTGRES_BIGINT_MAX = (1 << 63) - 1


class ObservationError(ValueError):
    """A queue observation cannot safely drive a lifecycle decision."""


def video_queue_work_counts(status_counts: dict[str, Any]) -> dict[str, int]:
    """Map Universal Video status counts to executable work.

    PENDING_CANARY is deliberately excluded: it is not runnable until a
    separate canary gate changes it to QUEUED. LEASED remains active work.
    """
    if not isinstance(status_counts, dict):
        raise ObservationError("video_queue_counts_invalid")
    counts: dict[str, int] = {}
    for status, value in status_counts.items():
        if status not in VIDEO_QUEUE_STATUSES:
            raise ObservationError("video_queue_status_unknown")
        if not _is_int(value) or value < 0:
            raise ObservationError("video_queue_count_invalid")
        counts[status] = value
    return {
        "eligible_pending_jobs": counts.get("QUEUED", 0),
        "running_jobs": counts.get("LEASED", 0),
    }


def _timestamp(value: Any) -> datetime:
    """Parse a PostgreSQL timestamptz value without accepting naive times."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ObservationError("video_queue_timestamp_invalid") from exc
    else:
        raise ObservationError("video_queue_timestamp_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ObservationError("video_queue_timestamp_timezone_missing")
    try:
        epoch = parsed.timestamp()
    except (OverflowError, OSError, ValueError) as exc:
        raise ObservationError("video_queue_timestamp_invalid") from exc
    if not math.isfinite(epoch) or epoch <= 0:
        raise ObservationError("video_queue_timestamp_invalid")
    return parsed.astimezone(timezone.utc)


def video_queue_readiness_observation_fields(
    rows: Any,
    *,
    snapshot_complete: bool,
    supported_worker_tuples: set[tuple[str, str]] | frozenset[tuple[str, str]],
    other_observed_at_epoch: Any,
) -> dict[str, Any]:
    """Map all rows from ``video_queue.compute_readiness`` into lifecycle fields.

    This is a pure adapter. It rejects partial/ambiguous snapshots and does not
    open a database connection. A caller must pass every row from one successful
    query, the timestamp of other observation sources, and the exact worker
    profile/revision tuples that its readiness check has independently verified.
    The older timestamp is retained so lifecycle freshness covers every source.

    Invalid data is represented as an incomplete queue observation so ``decide``
    returns HOLD before it can infer idle. The bounded error string is safe for
    diagnostics and contains no row identifiers.
    """
    incomplete: dict[str, Any] = {
        "video_queue_snapshot_complete": False,
        "video_queue_readiness_blocked": True,
        "video_queue_readiness_error": "video_queue_snapshot_unavailable",
        "video_queue_observed_at_epoch": 0,
        "eligible_pending_jobs": 0,
        "running_jobs": 0,
        "active_leases": 0,
        "video_queue_retry_waiting_jobs": 0,
        "next_queue_wake_epoch": None,
        "video_queue_pending_canary_jobs": 0,
        "video_queue_exhausted_leases": 0,
        "video_queue_blocked_nonterminal_jobs": 0,
        "video_queue_unknown_job_statuses": 0,
        "video_queue_unknown_batch_statuses": 0,
        "video_queue_invalid_lease_shapes": 0,
        "video_queue_unsupported_worker_rows": 0,
    }

    def failed(reason: str) -> dict[str, Any]:
        result = dict(incomplete)
        result["video_queue_readiness_error"] = reason
        return result

    if snapshot_complete is not True:
        return failed("video_queue_snapshot_incomplete")
    if not _is_int(other_observed_at_epoch) or other_observed_at_epoch <= 0:
        return failed("video_queue_other_timestamp_invalid")
    if not isinstance(rows, (list, tuple)) or not rows:
        return failed("video_queue_rows_missing")
    if not isinstance(supported_worker_tuples, (set, frozenset)) or not supported_worker_tuples:
        return failed("video_queue_worker_allowlist_missing")
    if any(
        not isinstance(pair, tuple) or len(pair) != 2
        or any(not isinstance(part, str) or not part.strip() for part in pair)
        for pair in supported_worker_tuples
    ):
        return failed("video_queue_worker_allowlist_invalid")

    required = {
        "observed_at", "processing_profile", "algorithm_revision", "batch_status",
        *VIDEO_READINESS_COUNT_FIELDS, "next_retry_at",
    }
    seen: set[tuple[str, str, str]] = set()
    observed_at: datetime | None = None
    totals = {field: 0 for field in VIDEO_READINESS_COUNT_FIELDS}
    unsupported_worker_rows = 0

    try:
        for row in rows:
            if not isinstance(row, dict) or not required.issubset(row):
                raise ObservationError("video_queue_row_missing_fields")
            profile = row["processing_profile"]
            revision = row["algorithm_revision"]
            batch_status = row["batch_status"]
            if not isinstance(profile, str) or not profile.strip():
                raise ObservationError("video_queue_worker_profile_invalid")
            if not isinstance(revision, str) or not revision.strip():
                raise ObservationError("video_queue_worker_revision_invalid")
            if not isinstance(batch_status, str) or batch_status not in VIDEO_BATCH_STATUSES:
                raise ObservationError("video_queue_batch_status_unknown")

            row_time = _timestamp(row["observed_at"])
            if observed_at is None:
                observed_at = row_time
            elif row_time != observed_at:
                raise ObservationError("video_queue_snapshot_timestamps_disagree")

            key = (profile, revision, batch_status)
            if key in seen:
                raise ObservationError("video_queue_duplicate_group")
            seen.add(key)

            counts: dict[str, int] = {}
            for field in VIDEO_READINESS_COUNT_FIELDS:
                value = row[field]
                if not _is_int(value) or not 0 <= value <= POSTGRES_BIGINT_MAX:
                    raise ObservationError("video_queue_count_invalid")
                counts[field] = value

            retry_count = counts["retry_waiting_count"]
            retry_at_value = row["next_retry_at"]
            if retry_count == 0:
                if retry_at_value is not None:
                    raise ObservationError("video_queue_retry_timestamp_unexpected")
            else:
                if retry_at_value is None:
                    raise ObservationError("video_queue_retry_timestamp_missing")
                retry_at = _timestamp(retry_at_value)
                if retry_at <= row_time:
                    raise ObservationError("video_queue_retry_timestamp_not_future")

            if batch_status in VIDEO_CLAIMABLE_BATCH_STATUSES:
                if counts["blocked_nonterminal_count"] != 0:
                    raise ObservationError("video_queue_claimable_batch_marked_blocked")
            else:
                if counts["runnable_now_count"] or retry_count:
                    raise ObservationError("video_queue_blocked_batch_marked_runnable")
                if counts["active_leases_count"] and not counts["blocked_nonterminal_count"]:
                    raise ObservationError("video_queue_blocked_lease_not_counted")
                if counts["expired_attempts_exhausted_count"]:
                    raise ObservationError("video_queue_exhausted_lease_in_blocked_batch")

            if key[:2] not in supported_worker_tuples and (
                counts["runnable_now_count"]
                or counts["active_leases_count"]
                or retry_count
                or counts["expired_attempts_exhausted_count"]
                or counts["blocked_nonterminal_count"]
            ):
                unsupported_worker_rows += 1
            for field, value in counts.items():
                totals[field] += value
                if totals[field] > POSTGRES_BIGINT_MAX:
                    raise ObservationError("video_queue_aggregate_overflow")
    except ObservationError as exc:
        return failed(str(exc))

    assert observed_at is not None
    retry_waiting = totals["retry_waiting_count"]
    if retry_waiting:
        retry_epochs = [
            math.ceil(_timestamp(row["next_retry_at"]).timestamp())
            for row in rows if row["retry_waiting_count"] > 0
        ]
        next_wake = min(retry_epochs)
        if next_wake <= math.floor(observed_at.timestamp()):
            return failed("video_queue_retry_timestamp_not_future")
    else:
        next_wake = None

    blocker_count = sum(
        totals[field]
        for field in (
            "expired_attempts_exhausted_count",
            "blocked_nonterminal_count",
            "unknown_job_status_count",
            "unknown_batch_status_count",
            "invalid_lease_shape_count",
        )
    )
    return {
        "video_queue_snapshot_complete": True,
        "video_queue_readiness_blocked": bool(blocker_count or unsupported_worker_rows),
        "video_queue_readiness_error": None,
        "video_queue_observed_at_epoch": math.floor(observed_at.timestamp()),
        "observed_at_epoch": min(
            other_observed_at_epoch,
            math.floor(observed_at.timestamp()),
        ),
        "eligible_pending_jobs": totals["runnable_now_count"],
        "running_jobs": totals["active_leases_count"],
        "active_leases": totals["active_leases_count"],
        "video_queue_retry_waiting_jobs": retry_waiting,
        "next_queue_wake_epoch": next_wake,
        "video_queue_pending_canary_jobs": totals["pending_canary_count"],
        "video_queue_exhausted_leases": totals["expired_attempts_exhausted_count"],
        "video_queue_blocked_nonterminal_jobs": totals["blocked_nonterminal_count"],
        "video_queue_unknown_job_statuses": totals["unknown_job_status_count"],
        "video_queue_unknown_batch_statuses": totals["unknown_batch_status_count"],
        "video_queue_invalid_lease_shapes": totals["invalid_lease_shape_count"],
        "video_queue_unsupported_worker_rows": unsupported_worker_rows,
    }


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def decide(
    observation: dict[str, Any],
    *,
    now_epoch: int,
    idle_grace_seconds: int = DEFAULT_IDLE_GRACE_SECONDS,
) -> dict[str, Any]:
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

    if observation.get("video_queue_readiness_blocked") is not False:
        return {"decision": "HOLD", "reason": "video_queue_readiness_blocked_or_unknown"}

    for field in COUNT_FIELDS:
        value = observation.get(field)
        if not _is_int(value) or value < 0:
            return {"decision": "HOLD", "reason": f"{field}_invalid"}

    retry_waiting = observation.get("video_queue_retry_waiting_jobs")
    next_wake = observation.get("next_queue_wake_epoch")
    if not _is_int(retry_waiting) or retry_waiting < 0:
        return {"decision": "HOLD", "reason": "video_queue_retry_waiting_jobs_invalid"}
    if retry_waiting == 0:
        if next_wake is not None:
            return {"decision": "HOLD", "reason": "video_queue_retry_wake_unexpected"}
    else:
        if not _is_int(next_wake) or next_wake <= observed:
            return {"decision": "HOLD", "reason": "video_queue_retry_wake_invalid"}
        if next_wake <= now_epoch:
            return {"decision": "HOLD", "reason": "video_queue_retry_due_without_runnable_count"}

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
        if retry_waiting:
            return {
                "decision": "WAIT_FOR_RETRY",
                "reason": "retry_not_due",
                "wake_at_epoch": next_wake,
            }
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
