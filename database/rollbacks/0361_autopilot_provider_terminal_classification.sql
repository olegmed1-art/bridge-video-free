\set ON_ERROR_STOP on
BEGIN;

DO $guard$
DECLARE
    backed_up_at timestamptz;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0361_autopilot_provider_terminal_classification'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0361_ROLLBACK_MIGRATION_MISSING';
    END IF;

    SELECT min(backup.backed_up_at)
      INTO backed_up_at
      FROM autopilot.migration_0361_provider_backup backup;
    IF backed_up_at IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_0361_ROLLBACK_PROVIDER_BACKUP_MISSING';
    END IF;

    -- Restoring the old function after a newly classified terminal callback
    -- would erase durable provider-health evidence.
    IF EXISTS (
        SELECT 1
          FROM autopilot.role_dispatch_outbox
         WHERE status='CALLBACK_ACCEPTED'
           AND completed_at>backed_up_at
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0361_ROLLBACK_NEW_TERMINAL_CALLBACK_PRESENT';
    END IF;
END $guard$;

DO $restore_function$
DECLARE
    definition text;
BEGIN
    SELECT backup.function_definition
      INTO definition
      FROM autopilot.migration_0361_function_backup backup
     WHERE backup.function_key=
           'autopilot.on_role_dispatch_provider_success()';
    IF definition IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_0361_ROLLBACK_FUNCTION_BACKUP_MISSING';
    END IF;
    EXECUTE definition;
END $restore_function$;

UPDATE autopilot.provider_circuit_state AS circuit
   SET state=backup.state,
       consecutive_failures=backup.consecutive_failures,
       opened_at=backup.opened_at,
       hold_until=backup.hold_until,
       last_failure_code=backup.last_failure_code,
       last_success_at=backup.last_success_at,
       probe_in_flight=backup.probe_in_flight,
       updated_at=backup.updated_at
  FROM autopilot.migration_0361_provider_backup backup
 WHERE circuit.provider_id=backup.provider_id;

DROP TABLE autopilot.migration_0361_function_backup;
DROP TABLE autopilot.migration_0361_provider_backup;

DELETE FROM public.schema_migration
 WHERE migration_key='0361_autopilot_provider_terminal_classification';

COMMIT;
