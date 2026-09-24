\set ON_ERROR_STOP on
BEGIN;
DO $guard$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM public.schema_migration
                WHERE migration_key='0372_autopilot_codex_terminal_readback_candidates') THEN
   RAISE EXCEPTION 'AUTOPILOT_TERMINAL_READBACK_ROLLBACK_NOT_APPLIED';
 END IF;
 IF EXISTS (SELECT 1 FROM autopilot.role_dispatch_codex_terminal_receipt
            WHERE ingress_provenance='GITHUB_REST_DOUBLE_READ') THEN
   RAISE EXCEPTION 'AUTOPILOT_REST_READBACK_RECEIPTS_RETAINED';
 END IF;
 IF EXISTS (SELECT 1 FROM autopilot.codex_rest_readback_diagnostic) THEN
   RAISE EXCEPTION 'AUTOPILOT_REST_READBACK_DIAGNOSTIC_RETAINED';
 END IF;
END $guard$;
DROP FUNCTION autopilot.record_codex_rest_readback_diagnostic(uuid,text,bigint);
DROP TABLE autopilot.codex_rest_readback_diagnostic;
DROP FUNCTION autopilot.accept_role_dispatch_codex_rest_readback(
 text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb);
DROP FUNCTION autopilot.codex_rest_readback_core_parity();
DROP FUNCTION autopilot.codex_rest_readback_terminal_core(
 text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb);
ALTER TABLE autopilot.role_dispatch_codex_terminal_receipt
 DROP COLUMN ingress_provenance;
DROP FUNCTION autopilot.codex_terminal_readback_candidates();
DELETE FROM public.schema_migration
WHERE migration_key='0372_autopilot_codex_terminal_readback_candidates';
COMMIT;
