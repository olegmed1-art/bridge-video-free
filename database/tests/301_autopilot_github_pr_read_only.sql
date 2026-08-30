\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    github_id uuid;
    claimed record;
BEGIN
    SELECT task_id INTO github_id
      FROM autopilot.create_shadow_task(
        'sql-github-pr-1',
        'GITHUB_PR_READ_ONLY_V1',
        '{"repository":"olegmed1-art/bridge-video-free","pr_number":991,"expected_head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","require_draft":true}'::jsonb,
        20, 0, 'database-test', 'SQL_TEST'
      );

    IF NOT EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE task_id = github_id
           AND current_step_key = 'github.pr.snapshot'
           AND allowed_capabilities_json = '["github.pr.snapshot"]'::jsonb
           AND cost_cap_microusd = 0
           AND acceptance_contract_json->>'production_mutation' = 'false'
           AND acceptance_contract_json->>'exact_head_required' = 'true'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_TASK_MAPPING_INVALID';
    END IF;

    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-github-worker-1', 60);
    BEGIN
        PERFORM autopilot.complete_task(
            github_id, 'sql-github-worker-1', claimed.lease_epoch,
            'SYNTHETIC_SHADOW_COMPLETION', repeat('e', 64), '{}'::jsonb
        );
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_WRONG_EVIDENCE_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_GITHUB_EVIDENCE_MISMATCH%' THEN RAISE; END IF;
    END;

    IF claimed.task_id <> github_id OR claimed.current_step_key <> 'github.pr.snapshot' THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_CLAIM_INVALID';
    END IF;
    IF NOT autopilot.complete_task(
        github_id, 'sql-github-worker-1', claimed.lease_epoch,
        'GITHUB_PR_READ_ONLY_SNAPSHOT', repeat('f', 64),
        '{"repository":"olegmed1-art/bridge-video-free","pr_number":991,"production_mutation":false,"model_calls":0,"cost_actual_microusd":0}'::jsonb
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_COMPLETION_REJECTED';
    END IF;
    IF (SELECT status FROM autopilot.task WHERE task_id = github_id) <> 'DONE'
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.evidence
            WHERE task_id = github_id
              AND evidence_class = 'GITHUB_PR_READ_ONLY_SNAPSHOT'
              AND retained
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_EVIDENCE_GATE_FAILED';
    END IF;

    BEGIN
        PERFORM * FROM autopilot.create_shadow_task(
          'sql-github-bad-repo-1', 'GITHUB_PR_READ_ONLY_V1',
          '{"repository":"other/repository","pr_number":991,"expected_head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","require_draft":true}'::jsonb,
          20, 0, 'database-test', 'SQL_TEST'
        );
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_BAD_REPOSITORY_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_GITHUB_CONTRACT_INVALID%' THEN RAISE; END IF;
    END;

    BEGIN
        PERFORM * FROM autopilot.create_shadow_task(
          'sql-github-nonzero-cost-1', 'GITHUB_PR_READ_ONLY_V1',
          '{"repository":"olegmed1-art/bridge-video-free","pr_number":991,"expected_head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","require_draft":true}'::jsonb,
          20, 1, 'database-test', 'SQL_TEST'
        );
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_NONZERO_COST_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_GITHUB_CONTRACT_INVALID%' THEN RAISE; END IF;
    END;

    IF has_table_privilege('autopilot_runtime_principal', 'autopilot.task', 'SELECT')
       OR NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.complete_task(uuid,text,bigint,text,text,jsonb)',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_RUNTIME_BOUNDARY_INVALID';
    END IF;
END $$;

ROLLBACK;
