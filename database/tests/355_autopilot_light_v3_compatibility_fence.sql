\set ON_ERROR_STOP on
BEGIN;

DO $test$
DECLARE
    created_task_id uuid;
    claimed record;
    run_suffix text := txid_current()::text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0355_autopilot_light_v3_compatibility_fence'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0355_MIGRATION_MISSING';
    END IF;
    IF position(
        'LIGHT_RUNTIME_MAILBOX_V3_COMPATIBILITY_FENCE_V1' IN
        pg_get_functiondef('autopilot.claim_next_task(text,integer)'::regprocedure)
    )=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_0355_FENCE_DEFINITION_INVALID';
    END IF;

    SELECT task_id INTO created_task_id
      FROM autopilot.create_chatgpt_role_dispatch_task(
        'sql-light-v3-fence-355-'||run_suffix,
        jsonb_build_object(
            'repository','olegmed1-art/bridge-video-free',
            'mailbox_pr',1685,
            'role','AUTOPILOT',
            'target_pr',1150,
            'expected_head_sha',repeat('a',40),
            'dispatch_epoch',1,
            'successor_task_key',NULL,
            'successor_role',NULL,
            'successor_target_pr',NULL,
            'successor_expected_head_sha',NULL
        ),
        0,
        'SQL_TEST',
        'SQL_TEST'
      );

    SELECT * INTO claimed
      FROM autopilot.claim_next_task('oracle-autopilot-light-1',60);
    IF claimed.task_id IS NOT NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_0355_STALE_LIGHT_CLAIMED_ROLE_TASK';
    END IF;

    SELECT * INTO claimed
      FROM autopilot.claim_next_task('oracle-autopilot-shadow-test-355',60);
    IF claimed.task_id IS DISTINCT FROM created_task_id
       OR claimed.goal_type IS DISTINCT FROM 'CHATGPT_ROLE_DISPATCH_V1' THEN
        RAISE EXCEPTION 'AUTOPILOT_0355_COMPATIBLE_WORKER_COULD_NOT_CLAIM';
    END IF;

    IF NOT has_function_privilege(
        'autopilot_light_worker_login',
        'autopilot.claim_next_task(text,integer)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0355_LIGHT_RPC_PRIVILEGE_CHANGED';
    END IF;
END $test$;

SELECT 2 AS cases,0 AS failures;
ROLLBACK;
