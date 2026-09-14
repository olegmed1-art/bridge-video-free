\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM autopilot.task
         WHERE source = 'BOUNDED_UV_P1_INGRESS_V1'
           AND status NOT IN (
               'OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP',
               'DONE', 'CANCELLED'
           )
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_ROLLBACK_REFUSES_ACTIVE_TASK';
    END IF;
END $$;

DROP FUNCTION IF EXISTS autopilot.register_approved_uv_p1_ci(text);
DELETE FROM public.schema_migration
 WHERE migration_key = '0309_autopilot_uv_p1_bounded_ingress';

COMMIT;
