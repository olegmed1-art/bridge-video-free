\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    repair_id uuid;
    claimed record;
    goal jsonb;
    valid_summary jsonb;
BEGIN
    goal := jsonb_build_object(
        'task_key', 'sql-draft-repair-1',
        'action_fingerprint', repeat('b', 64),
        'repository', 'olegmed1-art/bridge-video-free',
        'base_branch', 'main',
        'expected_base_sha', repeat('a', 40),
        'branch_name', 'autopilot/repair/a9edf3af0d29e309',
        'title', '[Autopilot draft] SQL broker canary',
        'changes', jsonb_build_array(jsonb_build_object(
            'path', 'docs/evidence/autopilot/phase3b-sql-canary.md',
            'operation', 'CREATE',
            'content_utf8', E'Bounded SQL canary.\n',
            'expected_blob_sha', null
        )),
        'require_draft', true,
        'allow_merge', false,
        'allow_force_push', false,
        'production_mutation', false
    );

    SELECT task_id INTO repair_id
      FROM autopilot.create_shadow_task(
        'sql-draft-repair-1', 'GITHUB_DRAFT_REPAIR_V1', goal,
        20, 0, 'database-test', 'SQL_TEST'
      );

    IF NOT EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE task_id = repair_id
           AND current_step_key = 'github.draft_repair'
           AND allowed_capabilities_json = '["github.draft_repair"]'::jsonb
           AND cost_cap_microusd = 0
           AND acceptance_contract_json->>'production_mutation' = 'false'
           AND acceptance_contract_json->>'exact_head_required' = 'true'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DRAFT_REPAIR_TASK_MAPPING_INVALID';
    END IF;

    SELECT * INTO claimed
      FROM autopilot.claim_next_task('sql-draft-repair-worker-1', 60);
    IF claimed.task_id <> repair_id
       OR claimed.current_step_key <> 'github.draft_repair' THEN
        RAISE EXCEPTION 'AUTOPILOT_DRAFT_REPAIR_CLAIM_INVALID';
    END IF;

    valid_summary := jsonb_build_object(
        'status', 'created',
        'repository', 'olegmed1-art/bridge-video-free',
        'task_key', 'sql-draft-repair-1',
        'action_fingerprint', repeat('b', 64),
        'manifest_version', 1,
        'base_sha', repeat('a', 40),
        'branch_name', 'autopilot/repair/a9edf3af0d29e309',
        'commit_sha', repeat('c', 40),
        'pull_request_number', 1001,
        'pull_request_url', 'https://github.com/olegmed1-art/bridge-video-free/pull/1001',
        'draft', true,
        'replayed', false,
        'token_exposed', false,
        'merge_allowed', false,
        'production_mutation', false,
        'operation_count', 10,
        'broker_host', 'bridge-school-autopilot-cslfiz83g-olegmed1-4368s-projects.vercel.app',
        'http_method', 'POST',
        'model_calls', 0,
        'cost_actual_microusd', 0,
        'task_id', repair_id::text,
        'task_kind', 'GITHUB_DRAFT_REPAIR_V1',
        'runtime', 'ORACLE_RESIDENT'
    );

    BEGIN
        PERFORM autopilot.complete_task(
            repair_id, 'sql-draft-repair-worker-1', claimed.lease_epoch,
            'GITHUB_DRAFT_REPAIR_EVIDENCE', repeat('d', 64),
            jsonb_set(valid_summary, '{token_exposed}', 'true'::jsonb)
        );
        RAISE EXCEPTION 'AUTOPILOT_DRAFT_REPAIR_TOKEN_EVIDENCE_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_DRAFT_REPAIR_EVIDENCE_INVALID%' THEN RAISE; END IF;
    END;

    BEGIN
        PERFORM autopilot.complete_task(
            repair_id, 'sql-draft-repair-worker-1', claimed.lease_epoch,
            'GITHUB_DRAFT_REPAIR_EVIDENCE', repeat('d', 64),
            jsonb_set(valid_summary, '{branch_name}', '"autopilot/repair/forged0000000000"'::jsonb)
        );
        RAISE EXCEPTION 'AUTOPILOT_DRAFT_REPAIR_FORGED_BRANCH_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_DRAFT_REPAIR_EVIDENCE_INVALID%' THEN RAISE; END IF;
    END;

    IF NOT autopilot.complete_task(
        repair_id, 'sql-draft-repair-worker-1', claimed.lease_epoch,
        'GITHUB_DRAFT_REPAIR_EVIDENCE', repeat('e', 64), valid_summary
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DRAFT_REPAIR_COMPLETION_REJECTED';
    END IF;
    IF (SELECT status FROM autopilot.task WHERE task_id = repair_id) <> 'DONE'
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.evidence
            WHERE task_id = repair_id
              AND evidence_class = 'GITHUB_DRAFT_REPAIR_EVIDENCE'
              AND retained
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DRAFT_REPAIR_EVIDENCE_GATE_FAILED';
    END IF;

    BEGIN
        PERFORM * FROM autopilot.create_shadow_task(
          'sql-draft-repair-unsafe', 'GITHUB_DRAFT_REPAIR_V1',
          jsonb_set(goal, '{allow_merge}', 'true'::jsonb),
          20, 0, 'database-test', 'SQL_TEST'
        );
        RAISE EXCEPTION 'AUTOPILOT_DRAFT_REPAIR_MERGE_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_DRAFT_REPAIR_CONTRACT_INVALID%' THEN RAISE; END IF;
    END;

    BEGIN
        PERFORM * FROM autopilot.create_shadow_task(
          'sql-draft-repair-unsafe-path', 'GITHUB_DRAFT_REPAIR_V1',
          jsonb_set(
              jsonb_set(goal, '{task_key}', '"sql-draft-repair-unsafe-path"'::jsonb),
              '{changes,0,path}', '".github/workflows/unsafe.yml"'::jsonb
          ),
          20, 0, 'database-test', 'SQL_TEST'
        );
        RAISE EXCEPTION 'AUTOPILOT_DRAFT_REPAIR_UNSAFE_PATH_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_DRAFT_REPAIR_CONTRACT_INVALID%' THEN RAISE; END IF;
    END;

    IF has_table_privilege('autopilot_runtime_principal', 'autopilot.task', 'SELECT')
       OR NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.complete_task(uuid,text,bigint,text,text,jsonb)',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DRAFT_REPAIR_RUNTIME_BOUNDARY_INVALID';
    END IF;
END $$;

ROLLBACK;
