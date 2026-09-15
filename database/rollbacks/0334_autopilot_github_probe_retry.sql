\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    previous_definition text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0334_autopilot_github_probe_retry'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_PROBE_RETRY_NOT_APPLIED';
    END IF;
    SELECT function_definition INTO previous_definition
      FROM autopilot.migration_0334_function_backup
     WHERE function_key='project_work_transport_retryable';
    IF previous_definition IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_PROBE_RETRY_BACKUP_MISSING';
    END IF;
    EXECUTE previous_definition;
END $$;

REVOKE ALL ON FUNCTION autopilot.project_work_transport_retryable(text)
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

-- Do not rewind a lane that a worker advanced after the wake-up.  Rows whose
-- lineage is unchanged can safely recover their exact previous retry window.
UPDATE autopilot.project_work_item AS item
   SET not_before=backup.previous_not_before,
       updated_at=backup.previous_updated_at
  FROM autopilot.migration_0334_work_rearm_backup AS backup
 WHERE item.work_item_id=backup.work_item_id
   AND item.state='BLOCKED'
   AND item.result_code='GITHUB_API_TRANSIENT_ERROR'
   AND item.generation=backup.generation
   AND item.last_task_id IS NOT DISTINCT FROM backup.last_task_id;

DROP TABLE autopilot.migration_0334_work_rearm_backup;
DROP TABLE autopilot.migration_0334_function_backup;
DELETE FROM public.schema_migration
 WHERE migration_key='0334_autopilot_github_probe_retry';
COMMIT;
