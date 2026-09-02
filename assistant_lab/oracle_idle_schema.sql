-- Fail-closed, read-only Oracle power-idle snapshot.
-- The Oracle worker may execute these RPCs but receives no direct table rights.
-- Every nonterminal ResearchJob stage is busy, including accepted/checkpointed/
-- validating parents; child work is reported independently as defense in depth.

CREATE OR REPLACE FUNCTION assistant_lab.oracle_idle_snapshot()
RETURNS TABLE(active_jobs bigint, active_research_jobs bigint, active_control_commands bigint)
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, assistant_lab
AS $$
    SELECT
        (SELECT count(*) FROM assistant_lab.job WHERE status IN ('QUEUED', 'RUNNING'))::bigint,
        (SELECT count(*) FROM assistant_lab.research_job
          WHERE stage IN ('QUEUED','ACCEPTED','RUNNING','CHECKPOINTED','VALIDATING'))::bigint,
        (SELECT count(*) FROM assistant_lab.control_command WHERE status IN ('QUEUED', 'RUNNING'))::bigint;
$$;

DROP FUNCTION IF EXISTS assistant_lab.oracle_idle_snapshot_v2();
CREATE OR REPLACE FUNCTION assistant_lab.oracle_idle_snapshot_v2(p_stale_after_seconds integer DEFAULT 900)
RETURNS TABLE(
    observed_at timestamptz,
    active_jobs bigint,
    stale_running_jobs bigint,
    active_control_commands bigint,
    active_research_jobs bigint,
    active_research_children bigint,
    active_ben_jobs bigint,
    active_bulk_jobs bigint,
    active_other_jobs bigint
)
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, assistant_lab
AS $$
    WITH active_jobs_cte AS (
        SELECT job_id, kind, source, provenance_json, status,
               coalesce(heartbeat_at, claimed_at, updated_at) AS lease_observed_at
          FROM assistant_lab.job
         WHERE status IN ('QUEUED','RUNNING')
    ), active_research_cte AS (
        SELECT research_id, child_job_id
          FROM assistant_lab.research_job
         WHERE stage IN ('QUEUED','ACCEPTED','RUNNING','CHECKPOINTED','VALIDATING')
    ), classified_jobs AS (
        SELECT *,
               (kind = 'BEN_COMPUTE') AS is_ben,
               (lower(coalesce(source,'')) LIKE '%bulk%'
                 OR lower(coalesce(provenance_json->>'workload_family','')) = 'bulk') AS is_bulk
          FROM active_jobs_cte
    )
    SELECT
        clock_timestamp(),
        (SELECT count(*) FROM active_jobs_cte)::bigint,
        (SELECT count(*) FROM active_jobs_cte
          WHERE status = 'RUNNING'
            AND (
                p_stale_after_seconds NOT BETWEEN 120 AND 86400
                OR lease_observed_at IS NULL
                OR lease_observed_at < clock_timestamp() - make_interval(secs => p_stale_after_seconds)
            ))::bigint,
        (SELECT count(*) FROM assistant_lab.control_command
          WHERE status IN ('QUEUED','RUNNING'))::bigint,
        (SELECT count(*) FROM active_research_cte)::bigint,
        (SELECT count(*)
           FROM active_research_cte r
           JOIN assistant_lab.job j ON j.job_id = r.child_job_id
          WHERE j.status IN ('QUEUED','RUNNING'))::bigint,
        (SELECT count(*) FROM classified_jobs WHERE is_ben)::bigint,
        (SELECT count(*) FROM classified_jobs WHERE is_bulk)::bigint,
        (SELECT count(*) FROM classified_jobs WHERE NOT is_ben AND NOT is_bulk)::bigint;
$$;

REVOKE ALL ON FUNCTION assistant_lab.oracle_idle_snapshot() FROM PUBLIC;
REVOKE ALL ON FUNCTION assistant_lab.oracle_idle_snapshot_v2(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION assistant_lab.oracle_idle_snapshot() TO assistant_lab_worker;
GRANT EXECUTE ON FUNCTION assistant_lab.oracle_idle_snapshot_v2(integer) TO assistant_lab_worker;

COMMENT ON FUNCTION assistant_lab.oracle_idle_snapshot() IS
'Compatibility read-only snapshot for fail-closed Oracle VM stop decisions. Every nonterminal ResearchJob stage is active.';
COMMENT ON FUNCTION assistant_lab.oracle_idle_snapshot_v2(integer) IS
'Least-privilege fresh idle snapshot for Oracle STOP guard. Reports active workloads plus stale RUNNING heartbeat evidence using the worker stale timeout; never mutates work state.';
