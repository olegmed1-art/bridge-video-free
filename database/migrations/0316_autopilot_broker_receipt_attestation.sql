\set ON_ERROR_STOP on
BEGIN;

-- Forward-only upgrade of the draft-repair completion receipt. Migration 0307
-- is immutable because deployed databases record and enforce its checksum.
DO $migration$
DECLARE
    function_sql text;
    old_keys text := $old$
               'action_fingerprint', 'base_sha', 'branch_name', 'broker_host',
               'commit_sha', 'cost_actual_microusd', 'draft', 'http_method',$old$;
    new_keys text := $new$
               'action_fingerprint', 'base_sha', 'branch_name',
               'broker_artifact_sha256', 'broker_host', 'broker_policy_sha256',
               'broker_policy_version', 'broker_provenance_sha256', 'broker_source_sha',
               'commit_sha', 'cost_actual_microusd', 'draft', 'http_method',$new$;
    old_validation text := $old$
           OR p_summary->'operation_count' IS DISTINCT FROM
              to_jsonb(8 + 2 * jsonb_array_length(task_goal_json->'changes'))
           OR COALESCE(p_summary->>'broker_host', '') !~$old$;
    new_validation text := $new$
           OR p_summary->'operation_count' IS DISTINCT FROM
              to_jsonb(8 + 2 * jsonb_array_length(task_goal_json->'changes'))
           OR p_summary->>'broker_policy_version' <> 'physical-no-merge-v1'
           OR COALESCE(p_summary->>'broker_source_sha', '') !~ '^[0-9a-f]{40}$'
           OR COALESCE(p_summary->>'broker_artifact_sha256', '') !~ '^[0-9a-f]{64}$'
           OR COALESCE(p_summary->>'broker_policy_sha256', '') !~ '^[0-9a-f]{64}$'
           OR COALESCE(p_summary->>'broker_provenance_sha256', '') !~ '^[0-9a-f]{64}$'
           OR COALESCE(p_summary->>'broker_host', '') !~$new$;
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM public.schema_migration
         WHERE migration_key = '0307_autopilot_ibf_completion'
           AND checksum = 'a334b6015f678e23b66558728c9e738326dfaa9ca8abb86e3e4551755a49a777'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0307_REQUIRED_OR_CHECKSUM_INVALID';
    END IF;

    SELECT pg_get_functiondef(
        'autopilot.complete_task(uuid,text,bigint,text,text,jsonb)'::regprocedure
    ) INTO function_sql;

    IF function_sql IS NULL
       OR strpos(function_sql, old_keys) = 0
       OR strpos(function_sql, old_validation) = 0
       OR strpos(function_sql, new_keys) <> 0
       OR strpos(function_sql, new_validation) <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_COMPLETE_TASK_0307_DEFINITION_UNEXPECTED';
    END IF;

    function_sql := replace(function_sql, old_keys, new_keys);
    function_sql := replace(function_sql, old_validation, new_validation);
    EXECUTE function_sql;
END
$migration$;

COMMENT ON FUNCTION autopilot.complete_task(uuid, text, bigint, text, text, jsonb) IS
'Completes fenced shadow tasks with exact evidence validation, including cryptographically bound broker provenance receipts; upgraded by migration 0316.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0316_autopilot_broker_receipt_attestation')
ON CONFLICT DO NOTHING;

COMMIT;
