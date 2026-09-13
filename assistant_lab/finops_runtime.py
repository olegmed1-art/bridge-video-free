"""Best-effort FinOps ledger writer for terminal Assistant Lab jobs.

The worker must never lose a terminal job state because accounting is unavailable.
This module therefore records usage after terminalization and treats ledger write
failures as non-fatal. Pricing is deliberately left unset until an audited rate
source is available.
"""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row


_RESOURCE_BY_KIND = {
    "DDS3_COMPUTE": "oracle_local_dds3",
    "BEN_COMPUTE": "oracle_local_ben_policy",
    "WORLD_GENERATE": "oracle_local_world_generator",
    "NOOP": "oracle_noop",
}


def record_missing_terminal_usage(dsn: str, *, limit: int = 100) -> int:
    """Backfill terminal jobs missing a FinOps row; never raise DB errors."""

    bounded_limit = max(1, min(int(limit), 1000))
    try:
        with psycopg.connect(
            dsn,
            connect_timeout=10,
            application_name="assistant-lab-finops",
            row_factory=dict_row,
        ) as conn, conn.cursor() as cur:
            cur.execute(
                """
                WITH candidates AS (
                    SELECT j.job_id, j.kind, j.status, j.attempts, j.max_attempts,
                           j.claimed_at, j.completed_at, j.error_text, j.source,
                           j.claimed_by, j.provenance_json
                    FROM assistant_lab.job j
                    WHERE j.status IN ('COMPLETED','FAILED')
                      AND j.completed_at IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM assistant_lab.finops_usage f
                          WHERE f.job_id = j.job_id
                      )
                    ORDER BY j.completed_at ASC
                    LIMIT %s
                )
                INSERT INTO assistant_lab.finops_usage(
                    job_id, workload_kind, provider, resource, wall_time_ms,
                    pricing_basis, metrics_json, provenance_json
                )
                SELECT c.job_id,
                       c.kind,
                       'oracle',
                       CASE c.kind
                           WHEN 'DDS3_COMPUTE' THEN 'oracle_local_dds3'
                           WHEN 'BEN_COMPUTE' THEN 'oracle_local_ben_policy'
                           WHEN 'WORLD_GENERATE' THEN 'oracle_local_world_generator'
                           ELSE 'oracle_noop'
                       END,
                       CASE
                           WHEN c.claimed_at IS NULL THEN NULL
                           ELSE GREATEST(0, ROUND(EXTRACT(EPOCH FROM (c.completed_at-c.claimed_at))*1000)::bigint)
                       END,
                       'runtime_observed_cost_pending',
                       jsonb_build_object(
                           'status', c.status,
                           'attempts', c.attempts,
                           'max_attempts', c.max_attempts,
                           'error_class', CASE
                               WHEN c.error_text IS NULL OR c.error_text = '' THEN NULL
                               ELSE split_part(c.error_text, ':', 1)
                           END
                       ),
                       jsonb_build_object(
                           'source', c.source,
                           'claimed_by', c.claimed_by,
                           'execution_path', COALESCE(
                               c.provenance_json->>'execution_path',
                               CASE c.kind
                                   WHEN 'DDS3_COMPUTE' THEN 'oracle_local_dds3'
                                   WHEN 'BEN_COMPUTE' THEN 'oracle_local_ben_policy'
                                   WHEN 'WORLD_GENERATE' THEN 'oracle_local_world_generator'
                                   ELSE 'oracle_noop'
                               END
                           )
                       )
                FROM candidates c
                RETURNING usage_id
                """,
                (bounded_limit,),
            )
            rows = cur.fetchall()
            conn.commit()
            return len(rows)
    except psycopg.Error:
        return 0
