-- Fail-closed, read-only Oracle power-idle snapshot.
-- Every nonterminal workload is BUSY. The timestamp lets the caller reject
-- stale telemetry; the function never mutates queue state.

DROP FUNCTION IF EXISTS assistant_lab.oracle_idle_snapshot();

CREATE OR REPLACE FUNCTION assistant_lab.oracle_idle_snapshot()
RETURNS TABLE(
    telemetry_schema text,
    observed_at_epoch bigint,
    active_jobs bigint,
    active_research_jobs bigint,
    active_research_child_jobs bigint,
    active_control_commands bigint
)
LANGUAGE sql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, assistant_lab
AS $$
    SELECT
        'assistant-lab-oracle-idle-v2'::text,
        floor(extract(epoch FROM clock_timestamp()))::bigint,
        (SELECT count(*)
           FROM assistant_lab.job
          WHERE status IN ('QUEUED', 'CLAIMED', 'RUNNING'))::bigint,
        (SELECT count(*)
           FROM assistant_lab.research_job
          WHERE stage NOT IN ('COMPLETED', 'FAILED', 'CANCELLED'))::bigint,
        (SELECT count(*)
           FROM assistant_lab.research_job AS r
           JOIN assistant_lab.job AS j ON j.job_id = r.child_job_id
          WHERE j.status IN ('QUEUED', 'CLAIMED', 'RUNNING'))::bigint,
        (SELECT count(*)
           FROM assistant_lab.control_command
          WHERE status IN ('QUEUED', 'CLAIMED', 'RUNNING'))::bigint;
$$;

REVOKE ALL ON FUNCTION assistant_lab.oracle_idle_snapshot() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION assistant_lab.oracle_idle_snapshot() TO assistant_lab_worker;

COMMENT ON FUNCTION assistant_lab.oracle_idle_snapshot() IS
'Least-privilege fail-closed Oracle STOP telemetry: timestamp plus active Assistant Lab, ResearchJob child, and control-command counts; read-only.';
