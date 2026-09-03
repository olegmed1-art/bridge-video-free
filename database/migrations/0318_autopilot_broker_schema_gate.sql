\set ON_ERROR_STOP on
BEGIN;

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
    );
$$;

REVOKE ALL ON FUNCTION autopilot.verify_broker_schema() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.verify_broker_schema() TO autopilot_runtime;

COMMENT ON FUNCTION autopilot.verify_broker_schema() IS
'Narrow security-definer activation gate for exact broker receipt-schema migration checksums; exposes no table rows.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0318_autopilot_broker_schema_gate')
ON CONFLICT DO NOTHING;

COMMIT;
