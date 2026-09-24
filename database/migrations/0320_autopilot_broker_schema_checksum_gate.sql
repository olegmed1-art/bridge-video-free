\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0319_autopilot_broker_provenance_digest_guard'
           AND checksum = 'c9d203405e881bc71691f799a9f1258de54ef657fcfe88f2a25f0763b1a9676a'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0319_REQUIRED_OR_CHECKSUM_INVALID';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION autopilot.verify_broker_schema()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0316_autopilot_broker_receipt_attestation'
           AND checksum = '8eb7d2bc23e72c2dcfdb6ef0798f9d65caeecd3f13d709f017a5398355819cba'
    ) AND EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0317_autopilot_broker_policy_null_guard'
           AND checksum = '737359a67dec22fa97a9c3e23a84fa8f4b57adda5b2b73b9a810b0ade07479b4'
    ) AND EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0319_autopilot_broker_provenance_digest_guard'
           AND checksum = 'c9d203405e881bc71691f799a9f1258de54ef657fcfe88f2a25f0763b1a9676a'
    );
$$;
REVOKE ALL ON FUNCTION autopilot.verify_broker_schema() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.verify_broker_schema() TO autopilot_runtime;

COMMENT ON FUNCTION autopilot.verify_broker_schema() IS
'Narrow activation gate requiring exact immutable checksums for broker receipt schema and provenance validation through migration 0319.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0320_autopilot_broker_schema_checksum_gate')
ON CONFLICT DO NOTHING;

COMMIT;
