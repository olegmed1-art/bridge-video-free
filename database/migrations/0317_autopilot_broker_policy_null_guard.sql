\set ON_ERROR_STOP on
BEGIN;

-- Forward-only correction: SQL three-valued logic made JSON null pass the
-- policy-version comparison introduced by 0316.
DO $migration$
DECLARE
    function_sql text;
    old_validation text := $old$
           OR p_summary->>'broker_policy_version' <> 'physical-no-merge-v1'
           OR COALESCE(p_summary->>'broker_source_sha', '') !~$old$;
    new_validation text := $new$
           OR p_summary->>'broker_policy_version' IS DISTINCT FROM 'physical-no-merge-v1'
           OR COALESCE(p_summary->>'broker_source_sha', '') !~$new$;
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM public.schema_migration
         WHERE migration_key = '0316_autopilot_broker_receipt_attestation'
           AND checksum = '8eb7d2bc23e72c2dcfdb6ef0798f9d65caeecd3f13d709f017a5398355819cba'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0316_REQUIRED_OR_CHECKSUM_INVALID';
    END IF;

    SELECT pg_get_functiondef(
        'autopilot.complete_task(uuid,text,bigint,text,text,jsonb)'::regprocedure
    ) INTO function_sql;

    IF function_sql IS NULL
       OR strpos(function_sql, old_validation) = 0
       OR strpos(function_sql, new_validation) <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_COMPLETE_TASK_0316_DEFINITION_UNEXPECTED';
    END IF;

    EXECUTE replace(function_sql, old_validation, new_validation);
END
$migration$;

COMMENT ON FUNCTION autopilot.complete_task(uuid, text, bigint, text, text, jsonb) IS
'Completes fenced shadow tasks with exact fail-closed broker evidence validation; policy-version null guard upgraded by migration 0317.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0317_autopilot_broker_policy_null_guard')
ON CONFLICT DO NOTHING;

COMMIT;
