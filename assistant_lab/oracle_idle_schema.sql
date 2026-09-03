-- Fail-closed, read-only Oracle power-idle snapshot.
-- The Oracle worker may execute this RPC but receives no additional table
-- privileges. Every nonterminal workload state is BUSY. Expired active lease
-- telemetry is reported separately so the caller can classify it UNKNOWN.

CREATE TABLE IF NOT EXISTS assistant_lab.operator_maintenance_lease (
    lease_name text PRIMARY KEY
        CHECK (lease_name ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'),
    lease_kind text NOT NULL
        CHECK (lease_kind IN ('OPERATOR', 'MAINTENANCE')),
    owner_id text NOT NULL
        CHECK (length(owner_id) BETWEEN 1 AND 256),
    purpose text NOT NULL DEFAULT ''
        CHECK (length(purpose) <= 1024),
    active boolean NOT NULL DEFAULT true,
    acquired_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    released_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > acquired_at),
    CHECK (expires_at <= acquired_at + interval '24 hours'),
    CHECK (
        (active AND released_at IS NULL)
        OR (NOT active AND released_at IS NOT NULL)
    )
);

REVOKE ALL ON assistant_lab.operator_maintenance_lease FROM PUBLIC;
REVOKE ALL ON assistant_lab.operator_maintenance_lease FROM assistant_lab_worker;

DROP FUNCTION IF EXISTS assistant_lab.oracle_idle_snapshot();

CREATE FUNCTION assistant_lab.oracle_idle_snapshot()
RETURNS TABLE(
    schema_version integer,
    observed_at_epoch bigint,
    active_jobs bigint,
    active_research_jobs bigint,
    active_research_child_jobs bigint,
    active_control_commands bigint,
    active_operator_maintenance_leases bigint,
    stale_operator_maintenance_leases bigint
)
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, assistant_lab
AS $$
    SELECT
        2::integer,
        extract(epoch FROM current_timestamp)::bigint,
        (SELECT count(*)
           FROM assistant_lab.job
          WHERE status IN ('QUEUED', 'CLAIMED', 'RUNNING'))::bigint,
        (SELECT count(*)
           FROM assistant_lab.research_job
          WHERE stage IN (
              'QUEUED', 'ACCEPTED', 'RUNNING', 'CHECKPOINTED', 'VALIDATING'
          ))::bigint,
        (SELECT count(*)
           FROM assistant_lab.research_job AS research
           JOIN assistant_lab.job AS child
             ON child.job_id = research.child_job_id
          WHERE child.status IN ('QUEUED', 'CLAIMED', 'RUNNING'))::bigint,
        (SELECT count(*)
           FROM assistant_lab.control_command
          WHERE status IN ('QUEUED', 'CLAIMED', 'RUNNING'))::bigint,
        (SELECT count(*)
           FROM assistant_lab.operator_maintenance_lease
          WHERE active
            AND expires_at > current_timestamp)::bigint,
        (SELECT count(*)
           FROM assistant_lab.operator_maintenance_lease
          WHERE active
            AND expires_at <= current_timestamp)::bigint;
$$;

REVOKE ALL ON FUNCTION assistant_lab.oracle_idle_snapshot() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION assistant_lab.oracle_idle_snapshot()
    TO assistant_lab_worker;

COMMENT ON FUNCTION assistant_lab.oracle_idle_snapshot() IS
'Schema v2 least-privilege live snapshot for Oracle STOP decisions. Counts queued/claimed/running Assistant Lab jobs, every nonterminal ResearchJob, active research children, active control commands, and bounded operator/maintenance leases; expired active leases are stale telemetry.';
