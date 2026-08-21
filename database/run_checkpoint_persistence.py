#!/usr/bin/env python3
"""Persist observable Bridge Video execution checkpoints into Neon.

The video worker uses the deterministic ingestion_run UUID as the execution
identity because it is known before ASR/analysis output exists.  Checkpoint
rows are append-only and do not authorize methodology, publication, canon, or
student-profile changes.
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

# Support both module execution (python -m database.run_checkpoint_persistence)
# and direct script execution used by the production workflow.  Direct script
# execution puts database/ rather than the repository root on sys.path.
try:
    from database.runtime_worker_preflight import normalize_dsn
except ModuleNotFoundError:
    from runtime_worker_preflight import normalize_dsn

SCHOOL_STABLE_NAME = "Школа спортивного бриджа"
RUN_TYPE = "ingestion"
VALID_STATES = {"started", "progress", "completed", "failed", "cancelled"}


def ingestion_run_id(job_id: str) -> uuid.UUID:
    """Match database.video_result_persistence._stable_uuid('ingestion-run', job_id)."""
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"bridge-school:ingestion-run:{job_id}",
    )


def _runtime_checkpoint(job_id: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "source_drive_id": os.getenv("BRIDGE_ORIGINAL_SOURCE_DRIVE_ID") or None,
        "algorithm_revision": os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION") or None,
        "lesson_number": os.getenv("BRIDGE_LESSON_NUMBER") or None,
        "github_run_id": os.getenv("GITHUB_RUN_ID") or None,
        "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT") or None,
        "github_sha": os.getenv("GITHUB_SHA") or None,
        "workflow": os.getenv("GITHUB_WORKFLOW") or None,
    }


def record_checkpoint(
    raw_dsn: str,
    *,
    job_id: str,
    stage_key: str,
    checkpoint_state: str,
    error_class: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one serialized checkpoint event and return its observable identity."""
    dsn = normalize_dsn(raw_dsn)
    if not dsn:
        raise ValueError("BRIDGE_WORKER_DATABASE_URL is not configured")
    job_id = str(job_id or "").strip()
    stage_key = str(stage_key or "").strip()
    checkpoint_state = str(checkpoint_state or "").strip()
    if not job_id:
        raise ValueError("job_id is required")
    if not stage_key:
        raise ValueError("stage_key is required")
    if checkpoint_state not in VALID_STATES:
        raise ValueError(f"unsupported checkpoint state: {checkpoint_state}")

    run_id = ingestion_run_id(job_id)
    checkpoint = _runtime_checkpoint(job_id)
    event_details = dict(details or {})
    event_details.setdefault("authority", "technical_observation_only")
    event_details.setdefault("publication_allowed", False)
    event_details.setdefault("profile_write_allowed", False)
    event_details.setdefault("canon_write_allowed", False)

    with psycopg.connect(
        dsn,
        connect_timeout=10,
        application_name="bridge-video-run-checkpoint",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT school_id FROM public.school WHERE stable_name=%s",
                (SCHOOL_STABLE_NAME,),
            )
            rows = cur.fetchall()
            if len(rows) != 1:
                raise RuntimeError("expected exactly one bridge school registry row")
            school_id = rows[0][0]

            # Serialize sequence allocation for this run without adding schema or
            # mutable coordinator state.  The two-key advisory lock is transaction scoped.
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                (RUN_TYPE, str(run_id)),
            )
            cur.execute(
                """
                SELECT COALESCE(MAX(sequence_no), 0) + 1
                  FROM public.run_checkpoint_event
                 WHERE run_type=%s AND run_id=%s
                """,
                (RUN_TYPE, run_id),
            )
            sequence_no = int(cur.fetchone()[0])
            cur.execute(
                """
                INSERT INTO public.run_checkpoint_event
                    (school_id, run_type, run_id, sequence_no, stage_key,
                     checkpoint_state, checkpoint, error_class, details)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING run_checkpoint_event_id, recorded_at
                """,
                (
                    school_id,
                    RUN_TYPE,
                    run_id,
                    sequence_no,
                    stage_key,
                    checkpoint_state,
                    Jsonb(checkpoint),
                    error_class,
                    Jsonb(event_details),
                ),
            )
            event_id, recorded_at = cur.fetchone()

    return {
        "run_checkpoint_event_id": str(event_id),
        "run_type": RUN_TYPE,
        "run_id": str(run_id),
        "sequence_no": sequence_no,
        "stage_key": stage_key,
        "checkpoint_state": checkpoint_state,
        "error_class": error_class,
        "recorded_at": recorded_at.isoformat(),
    }


def latest_checkpoint(raw_dsn: str, job_id: str) -> dict[str, Any] | None:
    """Read the latest technical checkpoint for deterministic resume/status logic."""
    dsn = normalize_dsn(raw_dsn)
    if not dsn:
        return None
    run_id = ingestion_run_id(job_id)
    with psycopg.connect(
        dsn,
        connect_timeout=10,
        application_name="bridge-video-run-checkpoint-read",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sequence_no, stage_key, checkpoint_state, checkpoint,
                       error_class, details, recorded_at
                  FROM public.latest_run_checkpoint
                 WHERE run_type=%s AND run_id=%s
                """,
                (RUN_TYPE, run_id),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return {
        "run_type": RUN_TYPE,
        "run_id": str(run_id),
        "sequence_no": int(row[0]),
        "stage_key": row[1],
        "checkpoint_state": row[2],
        "checkpoint": row[3],
        "error_class": row[4],
        "details": row[5],
        "recorded_at": row[6].isoformat(),
    }


def _parse_details(raw: str) -> dict[str, Any]:
    value = json.loads(raw or "{}")
    if not isinstance(value, dict):
        raise ValueError("--details-json must contain a JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--state", required=True, choices=sorted(VALID_STATES))
    parser.add_argument("--error-class")
    parser.add_argument("--details-json", default="{}")
    args = parser.parse_args()

    job_id = os.getenv("BRIDGE_JOB_ID", "").strip()
    raw_dsn = os.getenv("BRIDGE_WORKER_DATABASE_URL", "").strip()
    strict = os.getenv("BRIDGE_CHECKPOINT_STRICT", "false").strip().casefold() in {
        "1", "true", "yes", "on"
    }
    if not job_id:
        raise SystemExit("RUN_CHECKPOINT_MISSING_JOB_ID")
    if not normalize_dsn(raw_dsn):
        print(json.dumps({"stage": "RUN_CHECKPOINT", "status": "SKIPPED_DB_NOT_CONFIGURED"}))
        return

    try:
        result = record_checkpoint(
            raw_dsn,
            job_id=job_id,
            stage_key=args.stage,
            checkpoint_state=args.state,
            error_class=args.error_class,
            details=_parse_details(args.details_json),
        )
    except Exception as exc:
        print(json.dumps({
            "stage": "RUN_CHECKPOINT",
            "status": "FAILED",
            "error_class": type(exc).__name__,
        }))
        if strict:
            raise
        return
    print(json.dumps({"stage": "RUN_CHECKPOINT", "status": "RECORDED", **result}))


if __name__ == "__main__":
    main()
