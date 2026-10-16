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
    baseline text;
    baseline_fence_count integer;
    marker text := 'LIGHT_RUNTIME_MAILBOX_V3_COMPATIBILITY_FENCE_V1';
    fence text := $fence$           AND NOT ( -- LIGHT_RUNTIME_MAILBOX_V3_COMPATIBILITY_FENCE_V1
               p_worker_id = 'oracle-autopilot-light-1'
               AND t.goal_type IN (
                   'CHATGPT_ROLE_DISPATCH_V1',
                   'CHATGPT_ROLE_FOLLOWUP_V1'
               )
           )
$fence$;
BEGIN
    SELECT function_definition INTO original
      FROM autopilot.migration_0355_function_backup
     WHERE function_key='claim_next_task';
    IF original IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_LIGHT_V3_FENCE_BACKUP_INVALID';
    END IF;
    -- Retire only this predicate. Restoring the pre-0355 function would erase
    -- later claim guards (including 0373) while leaving them recorded as applied.
    original := pg_get_functiondef('autopilot.claim_next_task(text,integer)'::regprocedure);
    IF (length(original)-length(replace(original,fence,'')))/length(fence)<>1
       OR (length(original)-length(replace(original,marker,'')))/length(marker)<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_LIGHT_V3_FENCE_RETIREMENT_SOURCE_DRIFT';
    END IF;
    EXECUTE replace(original,fence,'');

    -- A later rollback of 0373 must not resurrect a separately retired fence.
    -- This is an internal definition snapshot, never task/dispatch history.
    IF EXISTS (SELECT FROM public.schema_migration
               WHERE migration_key='0373_autopilot_active_dispatch_capacity') THEN
        SELECT definition INTO STRICT baseline FROM autopilot.migration_0373_definition_backup
         WHERE object_key='autopilot.claim_next_task(text,integer)';
        baseline_fence_count := (length(baseline)-length(replace(baseline,fence,'')))/length(fence);
        -- Reinstalling 0355 after a retirement fences the current function but
        -- correctly leaves this older rollback snapshot unfenced.
        IF baseline_fence_count NOT IN (0,1)
           OR (length(baseline)-length(replace(baseline,marker,'')))/length(marker)<>baseline_fence_count THEN
            RAISE EXCEPTION 'AUTOPILOT_LIGHT_V3_FENCE_RETIREMENT_BACKUP_DRIFT';
        END IF;
        UPDATE autopilot.migration_0373_definition_backup
           SET definition=replace(baseline,fence,'')
         WHERE object_key='autopilot.claim_next_task(text,integer)';
    END IF;
END $restore$;

DROP TABLE autopilot.migration_0355_function_backup;
DELETE FROM public.schema_migration
 WHERE migration_key='0355_autopilot_light_v3_compatibility_fence';

COMMIT;
