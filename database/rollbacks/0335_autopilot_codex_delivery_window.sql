\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    previous_definition text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0335_autopilot_codex_delivery_window'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_DELIVERY_WINDOW_NOT_APPLIED';
    END IF;
    SELECT function_definition INTO previous_definition
      FROM autopilot.migration_0335_function_backup
     WHERE function_key='mark_role_dispatch_published';
    IF previous_definition IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_DELIVERY_WINDOW_BACKUP_MISSING';
    END IF;
    EXECUTE previous_definition;
END $$;

REVOKE ALL ON FUNCTION autopilot.mark_role_dispatch_published(
    uuid,text,bigint,bigint,text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.mark_role_dispatch_published(
    uuid,text,bigint,bigint,text
) TO autopilot_runtime;

-- Restore only an untouched PUBLISHED row.  An ACK or terminal transition is
-- forward progress and must never be rewound by rollback.
UPDATE autopilot.role_dispatch_outbox AS outbox
   SET delivery_deadline_at=backup.previous_delivery_deadline_at,
       updated_at=now()
  FROM autopilot.migration_0335_deadline_backup AS backup
 WHERE outbox.dispatch_id=backup.dispatch_id
   AND outbox.status='PUBLISHED'
   AND outbox.delivery_deadline_at=backup.extended_delivery_deadline_at;

DROP TABLE autopilot.migration_0335_deadline_backup;
DROP TABLE autopilot.migration_0335_function_backup;
DELETE FROM public.schema_migration
 WHERE migration_key='0335_autopilot_codex_delivery_window';
COMMIT;
