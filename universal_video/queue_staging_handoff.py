"""Explicit one-lease staging capability for a local combined queue rehearsal.

The resident entrypoint does not construct this capability. Its writer is
supplied by the caller, never selected from the queue DSN or ambient process
environment. The approved writer may call persist_completed_drive_job, whose
candidate writer commits the replay receipt before returning.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from typing import Any, Iterator


class QueueStagingHandoffError(RuntimeError):
    error_code = "UV_STAGING_HANDOFF_FAILED"


class QueueStagingReceiptError(QueueStagingHandoffError):
    error_code = "UV_STAGING_RECEIPT_MISSING"


_BOUND_FIELDS = (
    "batch_id", "job_id", "lease_token", "stable_job_key",
    "source_file_id", "algorithm_revision",
)


@contextmanager
def _exact_writer_environment(claim: Mapping[str, Any], writer_dsn: str) -> Iterator[None]:
    values = {
        "BRIDGE_JOB_ID": str(claim["stable_job_key"]),
        "BRIDGE_ORIGINAL_SOURCE_DRIVE_ID": str(claim["source_file_id"]),
        "BRIDGE_OUTPUT_FOLDER_ID": str(claim["output_folder_id"]),
        "BRIDGE_WORK_FOLDER_ID": str(claim["work_folder_id"]),
        "BRIDGE_LESSON_NUMBER": str(claim["sequence"]),
        "BRIDGE_REQUESTED_ALGORITHM_REVISION": str(claim["algorithm_revision"]),
        "BRIDGE_PERSIST_DATABASE": "true",
        "BRIDGE_WORKER_DATABASE_URL": writer_dsn,
    }
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class ExactLeaseStagingHandoff:
    """Bind a supplied writer to one already-claimed queue lease.

    This is a capability object, not a feature flag. The caller must supply a
    writer DSN and the concrete persistence function explicitly. There is no
    automatic construction in process_one_neon or any production entrypoint.
    """

    def __init__(
        self, *, claim: Mapping[str, Any], worker_key: str, writer_dsn: str,
        persist_completed: Callable[[str], Mapping[str, Any]],
        read_committed_receipt: Callable[..., Mapping[str, Any]],
    ) -> None:
        if (not callable(persist_completed) or not callable(read_committed_receipt)
                or not isinstance(writer_dsn, str)
                or not writer_dsn.strip() or not isinstance(worker_key, str)
                or not worker_key):
            raise QueueStagingHandoffError("VIDEO_STAGING_EXPLICIT_WRITER_REQUIRED")
        try:
            self.binding = {key: str(claim[key]) for key in _BOUND_FIELDS}
            self.binding.update({key: str(claim[key]) for key in
                                 ("output_folder_id", "work_folder_id", "sequence")})
        except (KeyError, TypeError) as exc:
            raise QueueStagingHandoffError("VIDEO_STAGING_CLAIM_INCOMPLETE") from exc
        if any(not value or value == "None" for value in self.binding.values()):
            raise QueueStagingHandoffError("VIDEO_STAGING_CLAIM_INCOMPLETE")
        self.worker_key = worker_key
        self.writer_dsn = writer_dsn
        self.persist_completed = persist_completed
        self.read_committed_receipt = read_committed_receipt

    def __call__(self, claim: Mapping[str, Any], worker_key: str, token: str) -> dict[str, Any]:
        if (worker_key != self.worker_key or
                any(str(claim.get(key)) != value for key, value in self.binding.items())):
            raise QueueStagingHandoffError("VIDEO_STAGING_LEASE_BINDING_MISMATCH")
        if not isinstance(token, str) or not token:
            raise QueueStagingHandoffError("VIDEO_STAGING_DRIVE_TOKEN_REQUIRED")
        with _exact_writer_environment(claim, self.writer_dsn):
            result = self.persist_completed(token)
        if not isinstance(result, Mapping) or result.get("persisted") is not True:
            raise QueueStagingReceiptError("VIDEO_STAGING_PERSISTENCE_NOT_COMMITTED")
        outbox = result.get("outbox")
        identity = result.get("source_identity")
        checkpoint = result.get("checkpoint")
        staging = result.get("knowledge_staging")
        if (result.get("publication_authorization_status") != "blocked"
                or not isinstance(outbox, Mapping)
                or outbox.get("publication_state") != "BLOCKED_PENDING"
                or not isinstance(identity, Mapping)
                or identity.get("source_native_key") != "google-drive:" + self.binding["source_file_id"]
                or identity.get("source_id") != result.get("source_id")
                or not isinstance(checkpoint, Mapping)
                or checkpoint.get("stage_key") != "database_persist"
                or checkpoint.get("checkpoint_state") != "completed"
                or not isinstance(staging, Mapping)
                or staging.get("status") != "PERSISTED"):
            raise QueueStagingReceiptError("VIDEO_STAGING_PRODUCER_BOUNDARY_INVALID")
        try:
            receipt_id = str(uuid.UUID(str(staging["replay_receipt_id"])))
            run_id = str(uuid.UUID(str(result["analysis_run_id"])))
            source_id = str(uuid.UUID(str(result["source_id"])))
            candidate_records = staging["candidate_records"]
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise QueueStagingReceiptError("VIDEO_STAGING_RECEIPT_INVALID") from exc
        if type(candidate_records) is not int or candidate_records < 1:
            raise QueueStagingReceiptError("VIDEO_STAGING_CANDIDATE_COUNT_INVALID")
        # A separate committed read is required. The writer's return value and
        # the earlier database_persist checkpoint are not sufficient by themselves.
        witnessed = self.read_committed_receipt(
            receipt_id=receipt_id, analysis_run_id=run_id, source_id=source_id)
        if (not isinstance(witnessed, Mapping)
                or witnessed.get("status") != "COMMITTED_RECEIPT"
                or witnessed.get("receipt_id") != receipt_id
                or witnessed.get("analysis_run_id") != run_id
                or witnessed.get("source_id") != source_id):
            raise QueueStagingReceiptError("VIDEO_STAGING_DURABLE_READBACK_MISSING")
        return {
            "status": "PERSISTED",
            "replay_receipt_id": receipt_id,
            "analysis_run_id": run_id,
            "source_id": source_id,
            "candidate_records": candidate_records,
            "authorization_scope": "EXACT_CLAIM_LEASE",
        }


__all__ = ["ExactLeaseStagingHandoff", "QueueStagingHandoffError"]
