\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    ci_id uuid;
    claimed record;
    valid_summary jsonb;
BEGIN
    SELECT task_id INTO ci_id
      FROM autopilot.create_shadow_task(
        'sql-github-ci-1',
        'GITHUB_CI_READ_ONLY_V1',
        '{"repository":"olegmed1-art/bridge-video-free","pr_number":991,"expected_head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","require_draft":true}'::jsonb,
        20, 0, 'database-test', 'SQL_TEST'
      );

    IF NOT EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE task_id = ci_id
           AND current_step_key = 'github.ci.snapshot'
           AND allowed_capabilities_json = '["github.ci.snapshot"]'::jsonb
           AND cost_cap_microusd = 0
           AND acceptance_contract_json->>'production_mutation' = 'false'
           AND acceptance_contract_json->>'exact_head_required' = 'true'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_CI_TASK_MAPPING_INVALID';
    END IF;

    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-github-ci-worker-1', 60);
    IF claimed.task_id <> ci_id OR claimed.current_step_key <> 'github.ci.snapshot' THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_CI_CLAIM_INVALID';
    END IF;

    BEGIN
        PERFORM autopilot.complete_task(
            ci_id, 'sql-github-ci-worker-1', claimed.lease_epoch,
            'GITHUB_PR_READ_ONLY_SNAPSHOT', repeat('d', 64), '{}'::jsonb
        );
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_CI_WRONG_EVIDENCE_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_GITHUB_EVIDENCE_MISMATCH%' THEN RAISE; END IF;
    END;

    valid_summary := jsonb_build_object(
        'repository', 'olegmed1-art/bridge-video-free',
        'pr_number', 991,
        'state', 'open',
        'draft', true,
        'head_sha', repeat('a', 40),
        'updated_at', '2026-08-30T20:00:00Z',
        'check_total', 2,
        'completed_count', 2,
        'pending_count', 0,
        'conclusion_counts', jsonb_build_object(
            'action_required', 0,
            'cancelled', 0,
            'failure', 1,
            'neutral', 0,
            'none', 0,
            'skipped', 0,
            'stale', 0,
            'startup_failure', 0,
            'success', 1,
            'timed_out', 0
        ),
        'overall_state', 'FAIL',
        'failure_codes', '["CI_FAILURE"]'::jsonb,
        'failed_checks', jsonb_build_array(jsonb_build_object(
            'app_slug', 'github-actions',
            'check_run_id', 22,
            'conclusion', 'failure',
            'name', 'postgresql-18',
            'output_title', 'Migration failed',
            'output_summary_excerpt', 'Bounded failure summary',
            'output_truncated', false,
            'annotation_level', 'failure',
            'annotation_path', 'database/migrations/0302_autopilot_github_ci_read_only.sql',
            'annotation_message_excerpt', 'Evidence shape rejected',
            'annotations_truncated', false
        )),
        'failed_checks_truncated', false,
        'api_host', 'api.github.com',
        'http_method', 'GET',
        'production_mutation', false,
        'model_calls', 0,
        'cost_actual_microusd', 0,
        'task_id', ci_id::text,
        'task_kind', 'GITHUB_CI_READ_ONLY_V1',
        'runtime', 'ORACLE_RESIDENT'
    );

    BEGIN
        PERFORM autopilot.complete_task(
            ci_id, 'sql-github-ci-worker-1', claimed.lease_epoch,
            'GITHUB_CI_READ_ONLY_SNAPSHOT', repeat('e', 64),
            jsonb_set(valid_summary, '{head_sha}', to_jsonb(repeat('b', 40)))
        );
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_CI_FORGED_HEAD_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_GITHUB_CI_EVIDENCE_INVALID%' THEN RAISE; END IF;
    END;

    BEGIN
        PERFORM autopilot.complete_task(
            ci_id, 'sql-github-ci-worker-1', claimed.lease_epoch,
            'GITHUB_CI_READ_ONLY_SNAPSHOT', repeat('e', 64),
            jsonb_set(valid_summary, '{failure_codes}', '[]'::jsonb)
        );
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_CI_MISSING_FAILURE_CODE_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_GITHUB_CI_EVIDENCE_INVALID%' THEN RAISE; END IF;
    END;

    BEGIN
        PERFORM autopilot.complete_task(
            ci_id, 'sql-github-ci-worker-1', claimed.lease_epoch,
            'GITHUB_CI_READ_ONLY_SNAPSHOT', repeat('e', 64),
            jsonb_set(valid_summary, '{failed_checks,0,conclusion}', '"cancelled"'::jsonb)
        );
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_CI_FORGED_FAILURE_DETAIL_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_GITHUB_CI_EVIDENCE_INVALID%' THEN RAISE; END IF;
    END;

    IF NOT autopilot.complete_task(
        ci_id, 'sql-github-ci-worker-1', claimed.lease_epoch,
        'GITHUB_CI_READ_ONLY_SNAPSHOT', repeat('f', 64), valid_summary
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_CI_COMPLETION_REJECTED';
    END IF;
    IF (SELECT status FROM autopilot.task WHERE task_id = ci_id) <> 'DONE'
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.evidence
            WHERE task_id = ci_id
              AND evidence_class = 'GITHUB_CI_READ_ONLY_SNAPSHOT'
              AND retained
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_CI_EVIDENCE_GATE_FAILED';
    END IF;

    BEGIN
        PERFORM * FROM autopilot.create_shadow_task(
          'sql-github-ci-cost-1', 'GITHUB_CI_READ_ONLY_V1',
          '{"repository":"olegmed1-art/bridge-video-free","pr_number":991,"expected_head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","require_draft":true}'::jsonb,
          20, 1, 'database-test', 'SQL_TEST'
        );
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_CI_NONZERO_COST_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_GITHUB_CONTRACT_INVALID%' THEN RAISE; END IF;
    END;

    IF has_table_privilege('autopilot_runtime_principal', 'autopilot.task', 'SELECT')
       OR NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.complete_task(uuid,text,bigint,text,text,jsonb)',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_CI_RUNTIME_BOUNDARY_INVALID';
    END IF;
END $$;

ROLLBACK;
