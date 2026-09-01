\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF to_regclass('autopilot.online_recovery_canary') IS NOT NULL
       AND EXISTS (
           SELECT 1
             FROM autopilot.online_recovery_canary AS receipt
             JOIN autopilot.task AS task
               ON task.task_id = receipt.canary_task_id
            WHERE task.status NOT IN (
                'OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP',
                'DONE', 'CANCELLED'
            )
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_ROLLBACK_REFUSES_ACTIVE_CANARY';
    END IF;
END $$;

DROP FUNCTION IF EXISTS autopilot.evaluate_online_stale_recovery_canary(text);
DROP FUNCTION IF EXISTS autopilot.register_online_stale_recovery_canary(
    text,bigint,uuid
);
DROP TABLE IF EXISTS autopilot.online_recovery_canary;
DELETE FROM public.schema_migration
 WHERE migration_key = '0310_autopilot_stale_recovery_canary';

COMMIT;
