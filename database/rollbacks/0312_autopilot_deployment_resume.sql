\set ON_ERROR_STOP on
BEGIN;

-- Rolling back a completed resume must fail closed.  It never attempts to
-- recreate or delete the Oracle-local marker; the operator must retain it.
DO $$
BEGIN
    IF to_regclass('autopilot.online_resume_receipt') IS NOT NULL
       AND EXISTS (SELECT 1 FROM autopilot.online_resume_receipt)
       AND EXISTS (
            SELECT 1 FROM autopilot.online_pilot_state
             WHERE singleton AND NOT circuit_open
       ) THEN
        PERFORM autopilot.open_online_pilot_circuit(
            'ONLINE_RESUME_ROLLBACK',
            'online-resume-rollback:20260902-v1',
            NULL,
            NULL,
            jsonb_build_object(
                'resume_key',
                'online-stale-running-deployment-resume-20260902-v1'
            ),
            'Re-verify the deployment receipt and Oracle-local marker before any new resume.'
        );
    END IF;
END $$;

REVOKE ALL ON FUNCTION autopilot.online_resume_status()
    FROM autopilot_runtime;
DROP FUNCTION IF EXISTS autopilot.online_resume_status();
DROP FUNCTION IF EXISTS autopilot.resume_online_pilot_after_deployment(
    text,text,bigint,bigint,uuid
);
DROP TABLE IF EXISTS autopilot.online_resume_receipt;

DELETE FROM public.schema_migration
WHERE migration_key = '0312_autopilot_deployment_resume';

COMMIT;
