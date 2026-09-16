\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
 IF EXISTS(SELECT FROM autopilot.native_cli_receipt) THEN
  RAISE EXCEPTION 'NATIVE_ROLLBACK_REQUIRES_RETAINED_EVIDENCE_PLAN';
 END IF;
END $$;
DO $$ DECLARE source text; BEGIN
 FOR source IN SELECT definition FROM autopilot.migration_0339_function_backup LOOP EXECUTE source; END LOOP;
END $$;
DROP FUNCTION autopilot.native_cli_finish(jsonb,text,jsonb);
DROP FUNCTION autopilot.native_cli_ack(jsonb,text,text);
DROP FUNCTION autopilot.native_cli_begin(jsonb);
DROP FUNCTION autopilot.native_cli_reserve(uuid,jsonb,text);
DROP FUNCTION autopilot.native_cli_current(jsonb);
DROP FUNCTION autopilot.native_cli_snapshot(uuid);
DROP FUNCTION autopilot.native_cli_authority_locked(uuid,jsonb);
ALTER TABLE autopilot.role_dispatch_outbox DROP CONSTRAINT role_dispatch_delivery_contract_version_check;
ALTER TABLE autopilot.role_dispatch_outbox ADD CONSTRAINT role_dispatch_delivery_contract_version_check
 CHECK(delivery_contract_version IN (1,2,3));
DROP TABLE autopilot.native_cli_receipt,autopilot.native_cli_config,autopilot.migration_0339_function_backup;
DELETE FROM public.schema_migration WHERE migration_key='0339_autopilot_native_cli_receipts';
COMMIT;
