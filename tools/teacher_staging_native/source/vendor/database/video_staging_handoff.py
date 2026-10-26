"""Strict, source-bound handoff from a completed video result to staging only."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def persist_quality_handoff(
    raw_dsn: str,
    payload: Mapping[str, Any],
    persisted_result: Mapping[str, Any],
    *,
    writer: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Require a real blocked producer result and a full staging replay.

    The writer verifies exact source/run/media lineage in the database and
    commits candidates, replay receipt and output binding idempotently. This
    wrapper never publishes the technical event or changes school Canon.
    """
    if not isinstance(persisted_result, Mapping) or persisted_result.get("persisted") is not True:
        raise ValueError("EXACT_PERSISTED_RESULT_REQUIRED")
    outbox = persisted_result.get("outbox")
    if (persisted_result.get("publication_authorization_status") != "blocked"
            or not isinstance(outbox, Mapping)
            or outbox.get("publication_state") != "BLOCKED_PENDING"):
        raise ValueError("VIDEO_STAGING_PUBLICATION_BOUNDARY_CHANGED")
    if not isinstance(payload, Mapping) or not isinstance(payload.get("job_id"), str) or not payload["job_id"]:
        raise ValueError("VIDEO_STAGING_JOB_REQUIRED")
    quality = payload.get("quality_v2")
    if not isinstance(quality, Mapping):
        raise ValueError("VIDEO_STAGING_QUALITY_REQUIRED")
    records = quality.get("candidate_staging_records")
    if not isinstance(records, list) or not records:
        raise ValueError("VIDEO_STAGING_CANDIDATES_REQUIRED")
    replay = quality.get("video_canon_auto_pipeline")
    if not isinstance(replay, Mapping):
        raise ValueError("FULL_STAGING_REPLAY_REQUIRED")
    if replay.get("schema") != "video-canon-staging-replay-v1":
        # Ordinary videos can contain non-canonical quality observations but
        # have no source-bound replay. Keep their artifacts, not DB staging.
        if (replay.get("schema") == "video-canon-auto-pipeline-v1"
                and replay.get("status") == "NOT_REQUESTED"
                and not any(isinstance(r, Mapping) and
                            str(r.get("candidate_type", "")).startswith("video_") for r in records)):
            return {"status": "BLOCKED_NO_REPLAY", "replay_receipt_id": None,
                    "candidate_records": 0, "authoritative_tables_modified": False}
        raise ValueError("FULL_STAGING_REPLAY_REQUIRED")
    if not isinstance(raw_dsn, str) or not raw_dsn.strip():
        raise ValueError("BRIDGE_WORKER_DATABASE_URL is not configured")

    if writer is None:
        from database.video_candidate_persistence import persist_quality_candidates
        writer = persist_quality_candidates
    result = writer(raw_dsn, payload, persisted_result=persisted_result)
    if (not isinstance(result, dict) or result.get("status") != "PERSISTED"
            or not result.get("replay_receipt_id")):
        raise RuntimeError("VIDEO_STAGING_RECEIPT_MISSING")
    return result


__all__ = ["persist_quality_handoff"]
