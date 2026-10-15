"""Read a committed replay receipt on a separate read-only connection.

The caller owns the connection and must use an authorized read principal.
There is deliberately no DSN or production connection fallback here.
"""
from __future__ import annotations

import hashlib
import json
from uuid import UUID


def read_committed_receipt_on_connection(
    connection, *, receipt_id: str, analysis_run_id: str, source_id: str,
) -> dict[str, str]:
    from database.video_result_persistence import SCHOOL_STABLE_NAME

    ids = tuple(str(UUID(str(value))) for value in
                (receipt_id, analysis_run_id, source_id))
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            if cursor.fetchone()[0] != "on":
                raise ValueError("STAGING_RECEIPT_READ_ONLY_REQUIRED")
            cursor.execute("SHOW transaction_isolation")
            if cursor.fetchone()[0].lower() != "repeatable read":
                raise ValueError("STAGING_RECEIPT_REPEATABLE_READ_REQUIRED")
            cursor.execute("""SELECT c.analysis_candidate_id,c.analysis_run_id,c.source_id,
                                     c.payload,c.payload_hash,c.stable_key,c.method_version
                                FROM public.analysis_candidate c
                                JOIN public.analysis_run r ON r.analysis_run_id=c.analysis_run_id
                                   AND r.school_id=c.school_id
                                JOIN public.source s ON s.source_id=c.source_id
                                   AND s.school_id=c.school_id
                                JOIN public.school school ON school.school_id=c.school_id
                               WHERE c.analysis_candidate_id=%s
                                 AND c.analysis_run_id=%s AND c.source_id=%s
                                 AND school.stable_name=%s AND school.status='active'
                                 AND c.candidate_type='video_staging_replay_snapshot'
                                 AND c.status='active' AND c.promotion_status='staging'
                                 AND c.method_version='video-staging-replay-snapshot-v1'
                                 AND EXISTS (SELECT 1 FROM public.analysis_run_output o
                                       WHERE o.analysis_run_id=c.analysis_run_id
                                         AND o.output_entity_id=c.analysis_candidate_id
                                         AND o.output_entity_type='analysis_candidate'
                                         AND o.output_role='derived' AND o.status='staging'
                                         AND o.artifact_version_id IS NULL
                                         AND o.publication_id IS NULL)""",
                           (*ids, SCHOOL_STABLE_NAME))
            rows = cursor.fetchall()
    if len(rows) != 1:
        raise ValueError("STAGING_RECEIPT_NOT_DURABLE")
    stored_id, stored_run, stored_source, payload, payload_hash, stable_key, method = rows[0]
    if (not isinstance(payload, dict)
            or payload.get("schema") != "video-staging-replay-snapshot-v1"
            or not isinstance(payload.get("members"), list)
            or not payload["members"]
            or not isinstance(payload.get("binding"), dict)
            or str(payload["binding"].get("analysis_run_id")) != ids[1]
            or str(payload["binding"].get("source_id")) != ids[2]
            or method != "video-staging-replay-snapshot-v1"):
        raise ValueError("STAGING_RECEIPT_PAYLOAD_INVALID")
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if payload_hash != digest or stable_key != digest:
        raise ValueError("STAGING_RECEIPT_HASH_MISMATCH")
    return {"status": "COMMITTED_RECEIPT", "receipt_id": str(stored_id),
            "analysis_run_id": str(stored_run), "source_id": str(stored_source)}


__all__ = ["read_committed_receipt_on_connection"]
