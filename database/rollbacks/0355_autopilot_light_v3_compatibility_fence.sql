\set ON_ERROR_STOP on
BEGIN;

LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;

DO $guard$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0355_autopilot_light_v3_compatibility_fence'
    ) OR to_regclass('autopilot.migration_0355_function_backup') IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_LIGHT_V3_FENCE_ROLLBACK_MISSING';
    END IF;
    IF (SELECT enabled FROM autopilot.project_planner_state WHERE singleton)
       OR EXISTS (
           SELECT 1 FROM autopilot.task
            WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
              AND status IN ('READY','RUNNING')
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_LIGHT_V3_FENCE_ROLLBACK_REQUIRES_PAUSE';
    END IF;
    IF position(
        'LIGHT_RUNTIME_MAILBOX_V3_COMPATIBILITY_FENCE_V1' IN
        pg_get_functiondef('autopilot.claim_next_task(text,integer)'::regprocedure)
    )=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_LIGHT_V3_FENCE_ROLLBACK_DRIFT';
    END IF;
END $guard$;

DO $restore$
DECLARE
    original text;
BEGIN
    SELECT function_definition INTO original
      FROM autopilot.migration_0355_function_backup
     WHERE function_key='claim_next_task';
    IF original IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_LIGHT_V3_FENCE_BACKUP_INVALID';
    END IF;
    EXECUTE original;
END $restore$;

DROP TABLE autopilot.migration_0355_function_backup;
DELETE FROM public.schema_migration
 WHERE migration_key='0355_autopilot_light_v3_compatibility_fence';

COMMIT;
