"""Explicit opt-in client for the installed 0060 worker API.

The caller must supply a collector bridge that returns committed ATTEMPT
observation IDs. This module neither collects observations nor issues approvals.
Constructing it does not change the resident entrypoint or enable runtime.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Literal, Mapping, Protocol
from uuid import UUID


class SubsetWorkerError(RuntimeError):
    pass


SAFETY_FLAGS = (
    "execution_allowed", "release_allowed", "database_persistence_allowed",
    "canonical_promotion_allowed",
)


def _timestamp(value: Any) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


class ObservationProvider(Protocol):
    def __call__(
        self, claim: Mapping[str, Any], worker_key: str, *,
        boundary: Literal["before_work", "before_finish"],
    ) -> str:
        """Return a fresh, committed ATTEMPT observation for this exact lease."""
        ...


def _connect_subset(database_url: str):
    from psycopg import connect
    from psycopg.rows import dict_row
    return connect(
        database_url, row_factory=dict_row, connect_timeout=8,
        options="-c statement_timeout=10000 -c lock_timeout=2000",
        application_name="universal-video-subset-worker",
    )


def _execute(
    database_url: str, sql: str, params: tuple[Any, ...], *,
    connection_validator: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    with _connect_subset(database_url) as conn:
        if connection_validator is not None:
            connection_validator(conn)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                raise SubsetWorkerError("VIDEO_SUBSET_RESPONSE_MISSING")
    # Do not report success before the connection context commits.
    if not isinstance(row, Mapping):
        raise SubsetWorkerError("VIDEO_SUBSET_RESPONSE_INVALID")
    return dict(row)


class SubsetWorkerAdapter:
    def __init__(
        self, observation_provider: ObservationProvider, *,
        connection_validator: Callable[[Any], None] | None = None,
    ) -> None:
        if not callable(observation_provider):
            raise SubsetWorkerError("VIDEO_SUBSET_OBSERVATION_PROVIDER_REQUIRED")
        if connection_validator is not None and not callable(connection_validator):
            raise SubsetWorkerError("VIDEO_SUBSET_CONNECTION_VALIDATOR_INVALID")
        self.observation_provider = observation_provider
        self.connection_validator = connection_validator

    def _execute(self, database_url: str, sql: str,
                 params: tuple[Any, ...]) -> dict[str, Any]:
        if self.connection_validator is None:
            return _execute(database_url, sql, params)
        return _execute(database_url, sql, params,
                        connection_validator=self.connection_validator)

    def _observe(
        self, claim: Mapping[str, Any], worker_key: str,
        boundary: Literal["before_work", "before_finish"],
    ) -> str:
        value = self.observation_provider(dict(claim), worker_key, boundary=boundary)
        try:
            return str(UUID(str(value)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise SubsetWorkerError("VIDEO_SUBSET_OBSERVATION_ID_INVALID") from exc

    def before_work(
        self, database_url: str, claim: Mapping[str, Any], worker_key: str,
    ) -> str:
        observation_id = self._observe(claim, worker_key, "before_work")
        row = self._execute(
            database_url,
            "SELECT video_queue.authorize_subset_attempt(%s, %s, %s, %s) AS authorization",
            (str(claim["job_id"]), str(claim["lease_token"]), worker_key, observation_id),
        )
        receipt = row.get("authorization")
        expected = {
            "status": "SOURCE_AND_LEASE_VERIFIED",
            "job_id": str(claim["job_id"]),
            "lease_token": str(claim["lease_token"]),
            "attempt_count": claim["attempt_count"],
            "observation_id": observation_id,
        }
        if (set(row) != {"authorization"} or not isinstance(receipt, dict)
                or any(receipt.get(k) != v for k, v in expected.items())
                or type(receipt.get("attempt_count")) is not int
                or any(receipt.get(flag) is not False for flag in SAFETY_FLAGS)):
            raise SubsetWorkerError("VIDEO_SUBSET_AUTHORIZATION_MISMATCH")
        return observation_id

    def heartbeat_job(
        self, database_url: str, *, job_id: str, lease_token: str,
        worker_key: str, extend_seconds: int = 900,
    ) -> Any:
        row = self._execute(
            database_url,
            "SELECT video_queue.heartbeat_subset_job(%s, %s, %s, %s) AS lease_expires_at",
            (job_id, lease_token, worker_key, extend_seconds),
        )
        if set(row) != {"lease_expires_at"} or not _timestamp(row["lease_expires_at"]):
            raise SubsetWorkerError("VIDEO_SUBSET_HEARTBEAT_RESPONSE_INVALID")
        return row["lease_expires_at"]

    def retry_job(
        self, database_url: str, *, job_id: str, lease_token: str,
        worker_key: str, error_code: str, max_attempts: int = 3,
        base_delay_seconds: int = 60,
    ) -> dict[str, Any]:
        # 0060 fixes the limit at three; its SQL signature has no max_attempts.
        if max_attempts != 3:
            raise SubsetWorkerError("VIDEO_SUBSET_RETRY_LIMIT_FIXED")
        row = self._execute(
            database_url,
            "SELECT * FROM video_queue.retry_subset_job(%s, %s, %s, %s, %s)",
            (job_id, lease_token, worker_key, error_code, base_delay_seconds),
        )
        if (set(row) != {"job_status", "batch_status", "retry_after"}
                or type(row["job_status"]) is not str or type(row["batch_status"]) is not str
                or row["job_status"] not in {"QUEUED", "FAILED"}
                or (row["job_status"] == "QUEUED" and (
                    row["batch_status"] != "RUNNING" or not _timestamp(row["retry_after"])))
                or (row["job_status"] == "FAILED" and (
                    row["batch_status"] not in {"CANARY_BLOCKED", "RUNNING", "REVIEW"}
                    or row["retry_after"] is not None))):
            raise SubsetWorkerError("VIDEO_SUBSET_RETRY_RESPONSE_INVALID")
        if row["retry_after"] is not None:
            row["retry_after"] = row["retry_after"].isoformat()
        return row

    def finish_job(
        self, database_url: str, claim: Mapping[str, Any], worker_key: str, *,
        before_work_observation_id: str, outcome: str, output: Mapping[str, Any],
        error_code: str | None = None,
    ) -> dict[str, Any]:
        observation_id = self._observe(claim, worker_key, "before_finish")
        if UUID(observation_id) == UUID(before_work_observation_id):
            raise SubsetWorkerError("VIDEO_SUBSET_DISTINCT_OBSERVATION_REQUIRED")
        # finish_subset_job consumes the new observation itself, atomically with finish.
        row = self._execute(
            database_url,
            "SELECT * FROM video_queue.finish_subset_job(%s, %s, %s, %s, %s::jsonb, %s, %s)",
            (str(claim["job_id"]), str(claim["lease_token"]), worker_key, outcome,
             json.dumps(dict(output), allow_nan=False), observation_id, error_code),
        )
        canary_status = "CANARY_REVIEW" if outcome == "REVIEW_READY" else "CANARY_BLOCKED"
        allowed_batch = {canary_status, "RUNNING", "REVIEW"}
        if claim.get("is_canary") is True:
            allowed_batch = {canary_status}
        elif claim.get("is_canary") is False:
            allowed_batch = {"RUNNING", "REVIEW"}
        if (set(row) != {"job_status", "batch_status", "released_jobs"}
                or outcome not in {"REVIEW_READY", "AMBIGUOUS", "FAILED"}
                or row["job_status"] != outcome
                or type(row["batch_status"]) is not str or row["batch_status"] not in allowed_batch
                or type(row["released_jobs"]) is not int or row["released_jobs"] != 0):
            # COMMIT may already have succeeded. Never resubmit this transition here.
            raise SubsetWorkerError("VIDEO_SUBSET_FINISH_RESPONSE_INVALID")
        return row

    def finish_private_job(
        self, database_url: str, claim: Mapping[str, Any], worker_key: str, *,
        before_work_observation_id: str, admission_id: str,
        source_fingerprint: str, receipt_id: str, outcome: str,
        output: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Versioned private-result finish; never call the public-candidate path."""
        if self.connection_validator is None:
            raise SubsetWorkerError("VIDEO_PRIVATE_CONNECTION_VALIDATOR_REQUIRED")
        if outcome != "REVIEW_READY":
            raise SubsetWorkerError("VIDEO_PRIVATE_FINISH_OUTCOME_INVALID")
        try:
            admission_id = str(UUID(admission_id))
            receipt_id = str(UUID(receipt_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise SubsetWorkerError("VIDEO_PRIVATE_FINISH_ID_INVALID") from exc
        if (not isinstance(source_fingerprint, str) or len(source_fingerprint) != 64
                or any(c not in "0123456789abcdef" for c in source_fingerprint)):
            raise SubsetWorkerError("VIDEO_PRIVATE_FINISH_SOURCE_INVALID")
        observation_id = self._observe(claim, worker_key, "before_finish")
        if UUID(observation_id) == UUID(before_work_observation_id):
            raise SubsetWorkerError("VIDEO_SUBSET_DISTINCT_OBSERVATION_REQUIRED")
        row = self._execute(
            database_url,
            "SELECT * FROM video_queue.finish_one_job_private_result_v1("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (admission_id, str(claim["job_id"]), str(claim["lease_token"]),
             worker_key, claim["attempt_count"], source_fingerprint, receipt_id,
             observation_id, json.dumps(dict(output), allow_nan=False)),
        )
        if (set(row) != {"job_status", "batch_status", "released_jobs"}
                or row["job_status"] != "REVIEW_READY"
                or row["batch_status"] not in {"RUNNING", "REVIEW"}
                or type(row["released_jobs"]) is not int
                or row["released_jobs"] != 0):
            # COMMIT may already have succeeded; never resubmit automatically.
            raise SubsetWorkerError("VIDEO_PRIVATE_FINISH_RESPONSE_INVALID")
        return row
