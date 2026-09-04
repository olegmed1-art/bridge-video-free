#!/usr/bin/env python3
"""Thin runtime adapter for the fenced video-to-canon promotion queue.

All authorization, exact-hash verification and authoritative writes remain in
the database transaction.  This process only claims one lease and invokes the
guarded consumer RPC; it never reads video, Drive, WORLD, or person data.
"""
from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

_SAFE_FAILURES = {
    "CANON_CONFLICT",
    "PROFILE_AMBIGUITY",
    "HIDDEN_INFORMATION",
    "PROVENANCE_INVALID",
    "I2_I3_MISMATCH",
    "CANDIDATE_CHANGED",
    "STATE_STALE",
    "INTEGRITY_FAILED",
}


def _safe_error_code(error: BaseException) -> str:
    """Map failures to a bounded code without persisting exception contents."""
    message = str(error).upper()
    for code in _SAFE_FAILURES:
        if code in message:
            return code
    if "STALE" in message or "FENCE" in message or "LEASE" in message:
        return "STATE_STALE"
    if "INTEGRITY" in message:
        return "INTEGRITY_FAILED"
    return "RETRYABLE_DATABASE_ERROR"


def _connect(dsn: str):
    return psycopg.connect(
        dsn,
        connect_timeout=10,
        application_name="bridge-video-canon-promotion-consumer",
    )


def _normalize_dsn(raw: str) -> str:
    value = str(raw or "").strip().strip("'\"")
    if not value:
        return ""
    if not value.startswith(("postgresql://", "postgres://")):
        raise ValueError("promotion consumer DSN must be a complete PostgreSQL URI")
    parsed = conninfo_to_dict(value)
    if not all(parsed.get(field) for field in ("host", "dbname", "user")):
        raise ValueError("promotion consumer DSN requires host, dbname, and user")
    return value


def consume_one(raw_dsn: str, *, lease_seconds: int = 120) -> dict[str, Any]:
    """Claim and atomically consume at most one verified promotion job."""
    dsn = _normalize_dsn(raw_dsn)
    if not dsn:
        return {"status": "DATABASE_URL_NOT_CONFIGURED"}
    if not 30 <= lease_seconds <= 900:
        raise ValueError("lease_seconds must be between 30 and 900")

    with _connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM bidding.claim_video_canon_promotion(%s)",
                (lease_seconds,),
            )
            claim = cursor.fetchone()
        connection.commit()
    if claim is None:
        return {"status": "IDLE"}

    job_id = uuid.UUID(str(claim[0]))
    lease_token = uuid.UUID(str(claim[6]))
    fencing_token = int(claim[7])
    try:
        with _connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT bidding.heartbeat_video_canon_promotion(%s,%s,%s,%s)",
                    (job_id, lease_token, fencing_token, lease_seconds),
                )
                heartbeat = cursor.fetchone()
                if not heartbeat or heartbeat[0] is None:
                    raise RuntimeError("STATE_STALE")
            connection.commit()
    except Exception:
        return {"status": "LEASE_LOST", "job_id": str(job_id)}
    try:
        with _connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT bidding.consume_video_canon_promotion(%s,%s,%s)",
                    (job_id, lease_token, fencing_token),
                )
                row = cursor.fetchone()
                if not row or row[0] is None:
                    raise RuntimeError("INTEGRITY_FAILED")
                delivery_receipt_id = uuid.UUID(str(row[0]))
            connection.commit()
    except Exception as error:
        error_code = _safe_error_code(error)
        # A lost commit acknowledgement is ambiguous: the authoritative
        # transaction may already have committed. Re-run the idempotent consume
        # boundary first so its retained receipt reconciles that outcome.
        try:
            with _connect(dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT bidding.consume_video_canon_promotion(%s,%s,%s)",
                        (job_id, lease_token, fencing_token),
                    )
                    reconciled = cursor.fetchone()
                    if not reconciled or reconciled[0] is None:
                        raise RuntimeError("INTEGRITY_FAILED")
                    delivery_receipt_id = uuid.UUID(str(reconciled[0]))
                connection.commit()
            return {
                "status": "POST_WRITE_INTEGRITY_PASS",
                "job_id": str(job_id),
                "delivery_receipt_id": str(delivery_receipt_id),
                "fencing_token": fencing_token,
            }
        except Exception:
            pass
        # Both consume attempts failed. Record only a bounded failure code in a
        # new transaction; never persist raw errors.
        try:
            with _connect(dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT bidding.fail_video_canon_promotion(%s,%s,%s,%s)",
                        (job_id, lease_token, fencing_token, error_code),
                    )
                    failure_row = cursor.fetchone()
                connection.commit()
        except Exception:
            return {"status": "LEASE_LOST", "job_id": str(job_id)}
        return {
            "status": str(failure_row[0]) if failure_row else "BLOCKED",
            "job_id": str(job_id),
            "error_code": error_code,
        }

    return {
        "status": "POST_WRITE_INTEGRITY_PASS",
        "job_id": str(job_id),
        "delivery_receipt_id": str(delivery_receipt_id),
        "fencing_token": fencing_token,
    }


__all__ = ["consume_one"]
