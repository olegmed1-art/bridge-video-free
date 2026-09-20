\set ON_ERROR_STOP on
BEGIN;

DO $guard$
DECLARE
    backed_up_at timestamptz;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0356_autopilot_provider_health_recovery'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0356_ROLLBACK_MIGRATION_MISSING';
    END IF;
    SELECT min(b.backed_up_at) INTO backed_up_at
      FROM autopilot.migration_0356_provider_backup b;
    IF backed_up_at IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_0356_ROLLBACK_PROVIDER_BACKUP_MISSING';
    END IF;
    IF EXISTS (
        SELECT 1 FROM autopilot.role_dispatch_outbox
         WHERE status='CALLBACK_ACCEPTED' AND completed_at>backed_up_at
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0356_ROLLBACK_NEW_PROVIDER_SUCCESS_PRESENT';
    END IF;
END $guard$;

DROP TRIGGER role_dispatch_provider_success ON autopilot.role_dispatch_outbox;
DROP FUNCTION autopilot.on_role_dispatch_provider_success();

UPDATE autopilot.provider_circuit_state circuit
   SET state=backup.state,
       consecutive_failures=backup.consecutive_failures,
       opened_at=backup.opened_at,
       hold_until=backup.hold_until,
       last_failure_code=backup.last_failure_code,
       last_success_at=backup.last_success_at,
       probe_in_flight=backup.probe_in_flight,
       updated_at=backup.updated_at
  FROM autopilot.migration_0356_provider_backup backup
 WHERE circuit.provider_id=backup.provider_id;

DO $restore_view$
DECLARE
    definition text;
    owner_name text;
BEGIN
    SELECT backup.view_definition,backup.owner_name INTO definition,owner_name
      FROM autopilot.migration_0356_view_backup backup
     WHERE backup.view_key='public.autopilot_operational_health_signal';
    IF definition IS NULL OR owner_name IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_0356_ROLLBACK_VIEW_BACKUP_MISSING';
    END IF;
    EXECUTE 'CREATE OR REPLACE VIEW public.autopilot_operational_health_signal AS '||definition;
    EXECUTE format('ALTER VIEW public.autopilot_operational_health_signal OWNER TO %I',owner_name);
END $restore_view$;

REVOKE ALL ON public.autopilot_operational_health_signal FROM PUBLIC;
GRANT SELECT ON public.autopilot_operational_health_signal TO bridge_school_health;
REVOKE EXECUTE ON FUNCTION autopilot.mailbox_rotation_readiness() FROM autopilot_callback;

DROP TABLE autopilot.migration_0356_view_backup;
DROP TABLE autopilot.migration_0356_provider_backup;
DELETE FROM public.schema_migration
 WHERE migration_key='0356_autopilot_provider_health_recovery';

COMMIT;
