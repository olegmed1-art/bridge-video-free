\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    work_id uuid;
    lease_epoch bigint;
    materialized record;
    current_head text:=repeat('e',40);
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0334_autopilot_github_probe_retry'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_PROBE_RETRY_MIGRATION_MISSING';
    END IF;
    IF NOT autopilot.project_work_transport_retryable(
        'GITHUB_API_TRANSIENT_ERROR'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_PROBE_ERROR_NOT_RETRYABLE';
    END IF;
    IF autopilot.project_work_transport_retryable('BOUNDED_DEFECT') THEN
        RAISE EXCEPTION 'AUTOPILOT_SEMANTIC_DEFECT_BECAME_RETRYABLE';
    END IF;

    SELECT work_item_id INTO work_id
      FROM autopilot.register_universal_work_item(
        'sql-github-probe-retry-334','VIDEO_QUEUE','REPOSITORY_AUDIT',
        'Audit one repository target after a transient GitHub head failure.',
        1150,0,jsonb_build_object(
          'repository','olegmed1-art/bridge-video-free',
          'target_pr',1150,
          'execution_mode','READ_ONLY'
        ),NULL,'database-test','SQL_TEST'
      );
    UPDATE autopilot.project_work_item
       SET state='BLOCKED',
           last_observed_head_sha=current_head,
           result_code='GITHUB_API_TRANSIENT_ERROR',
           result_summary='Project head probe will retry.',
           not_before=now()-interval '1 second',
           probe_lease_owner='sql-github-probe-worker-334',
           probe_lease_epoch=probe_lease_epoch+1,
           probe_lease_until=now()+interval '60 seconds'
     WHERE work_item_id=work_id
     RETURNING probe_lease_epoch INTO lease_epoch;

    -- Set the exact fenced lease directly so this regression remains
    -- deterministic even when run against a production-shaped clone that
    -- already contains higher-priority eligible work.
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
        work_id,'sql-github-probe-worker-334',lease_epoch,true,current_head
      );
    IF materialized.resulting_state IS DISTINCT FROM 'ACTIVE'
       OR materialized.created IS DISTINCT FROM true
       OR materialized.task_id IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_PROBE_RETRY_NOT_MATERIALIZED';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM autopilot.project_work_item
         WHERE work_item_id=work_id AND state='ACTIVE' AND generation=1
           AND result_code IS NULL AND last_task_id=materialized.task_id
    ) OR NOT EXISTS (
        SELECT 1 FROM autopilot.project_work_task
         WHERE work_item_id=work_id AND task_id=materialized.task_id
           AND run_kind='AUDIT'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_PROBE_RETRY_LINEAGE_INVALID';
    END IF;

    IF has_function_privilege(
        'autopilot_runtime',
        'autopilot.project_work_transport_retryable(text)','EXECUTE'
    ) OR has_table_privilege(
        'autopilot_runtime',
        'autopilot.migration_0334_work_rearm_backup','SELECT'
    ) OR has_table_privilege(
        'autopilot_callback',
        'autopilot.migration_0334_function_backup','SELECT'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_PROBE_RETRY_PRIVILEGE_WIDENED';
    END IF;
END $$;

ROLLBACK;
