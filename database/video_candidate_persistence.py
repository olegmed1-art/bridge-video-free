#!/usr/bin/env python3
"""Persist quality-v2 candidates into the generic staging table only."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Mapping

import psycopg
from psycopg.types.json import Jsonb

from database.runtime_worker_preflight import normalize_dsn

SCHOOL_STABLE_NAME = "Школа спортивного бриджа"


def _stable_uuid(kind: str, *parts: object) -> uuid.UUID:
    seed = "|".join(str(part) for part in parts)
    return uuid.uuid5(uuid.NAMESPACE_URL, f"bridge-school:{kind}:{seed}")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _table_exists(cursor) -> bool:
    cursor.execute("SELECT to_regclass('public.analysis_candidate') IS NOT NULL")
    return bool(cursor.fetchone()[0])


def persist_quality_candidates(raw_dsn: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Insert immutable candidate rows transactionally and idempotently.

    This function intentionally cannot write knowledge/canon/curriculum/student
    profile tables.  It returns ``SCHEMA_NOT_READY`` when migration 0014 has not
    yet been applied so an already completed media-analysis job is not lost.
    """
    dsn = normalize_dsn(raw_dsn)
    if not dsn:
        return {"status": "DATABASE_URL_NOT_CONFIGURED"}
    quality = payload.get("quality_v2") if isinstance(payload.get("quality_v2"), Mapping) else {}
    records = quality.get("candidate_staging_records") or []
    if not isinstance(records, list):
        raise ValueError("quality_v2.candidate_staging_records must be a list")
    job_id = str(payload.get("job_id") or "")
    input_fingerprint = str(
        ((quality.get("incremental_processing") or {}).get("input_fingerprint"))
        or _digest({"job_id": job_id, "quality": quality.get("method_version")})
    )
    method_version = str(quality.get("method_version") or payload.get("quality_method_version") or "unknown")

    inserted = 0
    existing = 0
    with psycopg.connect(
        dsn,
        connect_timeout=10,
        application_name="bridge-video-candidate-staging",
    ) as connection:
        with connection.cursor() as cursor:
            if not _table_exists(cursor):
                return {"status": "SCHEMA_NOT_READY", "required_migration": "0014_analysis_candidate_staging"}
            cursor.execute(
                "SELECT school_id FROM public.school WHERE stable_name = %s",
                (SCHOOL_STABLE_NAME,),
            )
            school_rows = cursor.fetchall()
            if len(school_rows) != 1:
                raise RuntimeError("expected exactly one bridge school registry row")
            school_id = school_rows[0][0]

            cursor.execute(
                """
                SELECT analysis_run_id,
                       (parameters_snapshot->>'source_drive_id') AS source_drive_id
                  FROM public.analysis_run
                 WHERE parameters_snapshot->>'job_id' = %s
                 ORDER BY completed_at DESC NULLS LAST, started_at DESC
                 LIMIT 1
                """,
                (job_id,),
            )
            run_row = cursor.fetchone()
            analysis_run_id = run_row[0] if run_row else None
            source_id = None
            if run_row and run_row[1]:
                cursor.execute(
                    """
                    SELECT s.source_id
                      FROM public.source s
                      JOIN public.source_identity si ON si.source_id = s.source_id
                     WHERE si.source_native_key = %s
                     ORDER BY si.created_at DESC
                     LIMIT 1
                    """,
                    (str(run_row[1]),),
                )
                source_row = cursor.fetchone()
                source_id = source_row[0] if source_row else None

            for raw_record in records:
                if not isinstance(raw_record, Mapping):
                    continue
                record = dict(raw_record)
                candidate_type = str(record.get("candidate_type") or "unknown")
                stable_key = str(record.get("stable_key") or record.get("candidate_id") or _digest(record))
                quality_status = str(record.get("quality_status") or "UNKNOWN")
                promotion_status = str(record.get("promotion_status") or "STAGING_ONLY").casefold()
                if promotion_status == "staging_only":
                    promotion_status = "staging"
                if promotion_status not in {"staging", "review_queue", "promoted", "rejected", "superseded"}:
                    promotion_status = "staging"
                candidate_payload = record.get("payload") if isinstance(record.get("payload"), Mapping) else record
                payload_hash = _digest(candidate_payload)
                candidate_id = _stable_uuid(
                    "analysis-candidate",
                    school_id,
                    candidate_type,
                    stable_key,
                    method_version,
                    input_fingerprint,
                )
                cursor.execute(
                    """
                    INSERT INTO public.analysis_candidate (
                        analysis_candidate_id,
                        school_id,
                        analysis_run_id,
                        source_id,
                        candidate_type,
                        stable_key,
                        input_fingerprint,
                        quality_status,
                        promotion_status,
                        payload,
                        payload_hash,
                        evidence_refs,
                        rejection_reasons,
                        method_version,
                        status
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, 'active'
                    )
                    ON CONFLICT (
                        school_id,
                        candidate_type,
                        stable_key,
                        method_version,
                        input_fingerprint
                    ) DO NOTHING
                    """,
                    (
                        candidate_id,
                        school_id,
                        analysis_run_id,
                        source_id,
                        candidate_type,
                        stable_key,
                        input_fingerprint,
                        quality_status,
                        promotion_status,
                        Jsonb(candidate_payload),
                        payload_hash,
                        Jsonb(list(record.get("evidence_refs") or [])),
                        Jsonb(list(record.get("reasons") or [])),
                        method_version,
                    ),
                )
                if cursor.rowcount:
                    inserted += 1
                else:
                    existing += 1
        connection.commit()
    return {
        "status": "PERSISTED",
        "inserted": inserted,
        "already_existing": existing,
        "candidate_records": len(records),
        "analysis_run_id": str(analysis_run_id) if analysis_run_id else None,
        "input_fingerprint": input_fingerprint,
        "method_version": method_version,
        "authoritative_tables_modified": False,
    }


__all__ = ["persist_quality_candidates"]
