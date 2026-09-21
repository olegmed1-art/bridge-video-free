\set ON_ERROR_STOP on
BEGIN;

LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.project_work_item IN SHARE ROW EXCLUSIVE MODE;

DO $guard$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0346_autopilot_canary_acceptance_guard'
    ) OR to_regclass('autopilot.migration_0346_function_backup') IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_CANARY_ACCEPTANCE_GUARD_ROLLBACK_MISSING';
    END IF;
    IF (SELECT enabled FROM autopilot.project_planner_state WHERE singleton)
       OR EXISTS (
           SELECT 1 FROM autopilot.role_dispatch_outbox
            WHERE status IN ('PENDING','CLAIMED','RETRY','PUBLISHED','SENT')
       )
       OR EXISTS (
           SELECT 1 FROM autopilot.task
            WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
              AND status NOT IN (
                  'OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED'
              )
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CANARY_ACCEPTANCE_GUARD_ROLLBACK_REQUIRES_PAUSE';
    END IF;
    IF position(
           'controller_verifies_delivery' IN pg_get_functiondef(
               'autopilot.get_dispatch_assignment(uuid)'::regprocedure
           )
       )=0
       OR pg_get_functiondef(
           'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure
       ) NOT LIKE '%CANARY_ACCEPTANCE_NO_REPAIR_V1%' THEN
        RAISE EXCEPTION 'AUTOPILOT_CANARY_ACCEPTANCE_GUARD_ROLLBACK_DRIFT';
    END IF;
END $guard$;

DO $restore$
DECLARE
    fixture record;
BEGIN
    FOR fixture IN
        SELECT function_key,function_definition
          FROM autopilot.migration_0346_function_backup
         ORDER BY function_key
    LOOP
        EXECUTE fixture.function_definition;
    END LOOP;
END $restore$;

DROP TABLE autopilot.migration_0346_function_backup;
DELETE FROM public.schema_migration
 WHERE migration_key='0346_autopilot_canary_acceptance_guard';
COMMIT;
