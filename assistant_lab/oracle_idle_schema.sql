-- Fail-closed, read-only Oracle power-idle snapshot.
-- The Oracle worker may execute this RPC but receives no additional table privileges.
--
-- IMPORTANT: every nonterminal ResearchJob stage is busy. In particular,
-- ACCEPTED/CHECKPOINTED/VALIDATING must not be misclassified as idle merely
-- because no child process happens to be running at the instant of the check.

CREATE OR REPLACE FUNCTION assistant_lab.oracle_idle_snapshot()
RETURNS TABLE(active_jobs bigint, active_research_jobs bigint, active_control_commands bigint)
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, assistant_lab
AS $$
    SELECT
        (SELECT count(*)
           FROM assistant_lab.job
          WHERE status IN ('QUEUED', 'RUNNING'))::bigint,
        (SELECT count(*)
           FROM assistant_lab.research_job
          WHERE stage IN ('QUEUED', 'ACCEPTED', 'RUNNING', 'CHECKPOINTED', 'VALIDATING'))::bigint,
        (SELECT count(*)
           FROM assistant_lab.control_command
          WHERE status IN ('QUEUED', 'RUNNING'))::bigint;
$$;

REVOKE ALL ON FUNCTION assistant_lab.oracle_idle_snapshot() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION assistant_lab.oracle_idle_snapshot() TO assistant_lab_worker;

COMMENT ON FUNCTION assistant_lab.oracle_idle_snapshot() IS
'Least-privilege read-only snapshot for fail-closed Oracle VM stop decisions. Returns active queue counts including every nonterminal ResearchJob stage; never mutates work state.';
