\set ON_ERROR_STOP on
BEGIN;

DO $migration$
DECLARE
    function_sql text;
    old_validation text := $old$
           OR COALESCE(p_summary->>'broker_provenance_sha256', '') !~ '^[0-9a-f]{64}$'
           OR COALESCE(p_summary->>'broker_host', '') !~$old$;
    new_validation text := $new$
           OR COALESCE(p_summary->>'broker_provenance_sha256', '') !~ '^[0-9a-f]{64}$'
           OR p_summary->>'broker_provenance_sha256' IS DISTINCT FROM encode(public.digest(
              convert_to(
                  '{"artifact_sha256":"' || (p_summary->>'broker_artifact_sha256') ||
                  '","policy_sha256":"' || (p_summary->>'broker_policy_sha256') ||
                  '","policy_version":"' || (p_summary->>'broker_policy_version') ||
                  '","source_sha":"' || (p_summary->>'broker_source_sha') || '"}',
                  'UTF8'
              ), 'sha256'
           ), 'hex')
           OR COALESCE(p_summary->>'broker_host', '') !~$new$;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0318_autopilot_broker_schema_gate'
           AND checksum = 'b525010d597630a93a42b93cd7d4cb3693bf1efb7d065f8b9ea3f5282a2411ae'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0318_REQUIRED_OR_CHECKSUM_INVALID';
    END IF;

    SELECT pg_get_functiondef(
        'autopilot.complete_task(uuid,text,bigint,text,text,jsonb)'::regprocedure
    ) INTO function_sql;
    IF function_sql IS NULL
       OR strpos(function_sql, old_validation) = 0
       OR strpos(function_sql, new_validation) <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_COMPLETE_TASK_0318_DEFINITION_UNEXPECTED';
    END IF;
    EXECUTE replace(function_sql, old_validation, new_validation);
END
$migration$;

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
    );
$$;
REVOKE ALL ON FUNCTION autopilot.verify_broker_schema() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.verify_broker_schema() TO autopilot_runtime;

COMMENT ON FUNCTION autopilot.complete_task(uuid, text, bigint, text, text, jsonb) IS
'Completes fenced shadow tasks with canonical broker provenance digest validation; upgraded by migration 0319.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0319_autopilot_broker_provenance_digest_guard')
ON CONFLICT DO NOTHING;

COMMIT;
