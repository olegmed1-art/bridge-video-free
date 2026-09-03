\set ON_ERROR_STOP on
BEGIN;

-- Additive v1.7 completion gate for the Director-approved IBF read-only pilot.
CREATE OR REPLACE FUNCTION autopilot.complete_task(
    p_task_id uuid, p_worker_id text, p_lease_epoch bigint,
    p_evidence_class text, p_content_sha256 text, p_summary jsonb DEFAULT '{}'::jsonb
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    step_id uuid;
    old_state text;
    task_goal_type text;
    task_goal_json jsonb;
    github_summary_keys text[];
    ci_conclusion_keys text[];
    expected_failure_codes jsonb;
    hard_failure_count integer;
BEGIN
    IF p_evidence_class NOT IN (
        'SYNTHETIC_SHADOW_COMPLETION',
        'SYNTHETIC_SHADOW_RESUME',
        'GITHUB_PR_READ_ONLY_SNAPSHOT',
        'GITHUB_CI_READ_ONLY_SNAPSHOT',
        'GITHUB_DRAFT_REPAIR_EVIDENCE',
        'IBF_READ_ONLY_ANALYSIS_EVIDENCE'
    ) OR p_content_sha256 !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(COALESCE(p_summary, '{}'::jsonb)) <> 'object'
       OR octet_length(COALESCE(p_summary, '{}'::jsonb)::text) > 8192 THEN
        RAISE EXCEPTION 'AUTOPILOT_EVIDENCE_INVALID';
    END IF;
    SELECT status, goal_type, goal_json
      INTO old_state, task_goal_type, task_goal_json
      FROM autopilot.task
     WHERE task_id = p_task_id AND status = 'RUNNING'
       AND lease_owner = p_worker_id AND lease_epoch = p_lease_epoch
     FOR UPDATE;
    IF NOT FOUND THEN RETURN false; END IF;
    SELECT step_attempt_id INTO step_id
      FROM autopilot.step_attempt
     WHERE task_id = p_task_id AND lease_epoch = p_lease_epoch AND status = 'RUNNING'
     ORDER BY started_at DESC LIMIT 1;
    IF step_id IS NULL THEN RETURN false; END IF;

    IF NOT EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE task_id = p_task_id
           AND (
               (goal_type = 'GITHUB_PR_READ_ONLY_V1'
                AND current_step_key = 'github.pr.snapshot'
                AND cost_cap_microusd = 0
                AND p_evidence_class = 'GITHUB_PR_READ_ONLY_SNAPSHOT')
               OR
               (goal_type = 'GITHUB_CI_READ_ONLY_V1'
                AND current_step_key = 'github.ci.snapshot'
                AND cost_cap_microusd = 0
                AND p_evidence_class = 'GITHUB_CI_READ_ONLY_SNAPSHOT')
               OR
               (goal_type = 'GITHUB_DRAFT_REPAIR_V1'
                AND current_step_key = 'github.draft_repair'
                AND cost_cap_microusd = 0
                AND p_evidence_class = 'GITHUB_DRAFT_REPAIR_EVIDENCE')
               OR
               (goal_type = 'IBF_READ_ONLY_ANALYSIS'
                AND current_step_key = 'ibf.read_only_analysis'
                AND cost_cap_microusd = 0
                AND p_evidence_class = 'IBF_READ_ONLY_ANALYSIS_EVIDENCE')
               OR
               (goal_type NOT IN (
                    'GITHUB_PR_READ_ONLY_V1', 'GITHUB_CI_READ_ONLY_V1',
                    'GITHUB_DRAFT_REPAIR_V1', 'IBF_READ_ONLY_ANALYSIS'
                )
                AND p_evidence_class NOT IN (
                    'GITHUB_PR_READ_ONLY_SNAPSHOT', 'GITHUB_CI_READ_ONLY_SNAPSHOT',
                    'GITHUB_DRAFT_REPAIR_EVIDENCE', 'IBF_READ_ONLY_ANALYSIS_EVIDENCE'
                ))
           )
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_EVIDENCE_MISMATCH';
    END IF;

    IF task_goal_type = 'GITHUB_PR_READ_ONLY_V1' THEN
        SELECT array_agg(key ORDER BY key) INTO github_summary_keys
          FROM jsonb_object_keys(p_summary) AS keys(key);
        IF github_summary_keys IS DISTINCT FROM ARRAY[
               'api_host', 'cost_actual_microusd', 'draft', 'head_sha',
               'http_method', 'mergeable', 'model_calls', 'pr_number',
               'production_mutation', 'repository', 'runtime', 'state',
               'task_id', 'task_kind', 'updated_at'
           ]
           OR p_summary->'repository' IS DISTINCT FROM task_goal_json->'repository'
           OR p_summary->'pr_number' IS DISTINCT FROM task_goal_json->'pr_number'
           OR p_summary->'head_sha' IS DISTINCT FROM task_goal_json->'expected_head_sha'
           OR p_summary->'state' IS DISTINCT FROM '"open"'::jsonb
           OR p_summary->'draft' IS DISTINCT FROM task_goal_json->'require_draft'
           OR jsonb_typeof(p_summary->'mergeable') NOT IN ('boolean', 'null')
           OR jsonb_typeof(p_summary->'updated_at') <> 'string'
           OR length(p_summary->>'updated_at') NOT BETWEEN 1 AND 40
           OR p_summary->'api_host' IS DISTINCT FROM '"api.github.com"'::jsonb
           OR p_summary->'http_method' IS DISTINCT FROM '"GET"'::jsonb
           OR p_summary->'production_mutation' IS DISTINCT FROM 'false'::jsonb
           OR p_summary->'model_calls' IS DISTINCT FROM '0'::jsonb
           OR p_summary->'cost_actual_microusd' IS DISTINCT FROM '0'::jsonb
           OR p_summary->'task_id' IS DISTINCT FROM to_jsonb(p_task_id::text)
           OR p_summary->'task_kind' IS DISTINCT FROM '"GITHUB_PR_READ_ONLY_V1"'::jsonb
           OR p_summary->'runtime' IS DISTINCT FROM '"ORACLE_RESIDENT"'::jsonb THEN
            RAISE EXCEPTION 'AUTOPILOT_GITHUB_EVIDENCE_INVALID';
        END IF;
    ELSIF task_goal_type = 'GITHUB_CI_READ_ONLY_V1' THEN
        SELECT array_agg(key ORDER BY key) INTO github_summary_keys
          FROM jsonb_object_keys(p_summary) AS keys(key);
        IF github_summary_keys IS DISTINCT FROM ARRAY[
               'api_host', 'check_total', 'completed_count', 'conclusion_counts',
               'cost_actual_microusd', 'draft', 'failed_checks',
               'failed_checks_truncated', 'failure_codes', 'head_sha',
               'http_method', 'model_calls', 'overall_state', 'pending_count',
               'pr_number', 'production_mutation', 'repository', 'runtime',
               'state', 'task_id', 'task_kind', 'updated_at'
           ]
           OR p_summary->'repository' IS DISTINCT FROM task_goal_json->'repository'
           OR p_summary->'pr_number' IS DISTINCT FROM task_goal_json->'pr_number'
           OR p_summary->'head_sha' IS DISTINCT FROM task_goal_json->'expected_head_sha'
           OR p_summary->'state' IS DISTINCT FROM '"open"'::jsonb
           OR p_summary->'draft' IS DISTINCT FROM task_goal_json->'require_draft'
           OR jsonb_typeof(p_summary->'updated_at') <> 'string'
           OR length(p_summary->>'updated_at') NOT BETWEEN 1 AND 40
           OR p_summary->'api_host' IS DISTINCT FROM '"api.github.com"'::jsonb
           OR p_summary->'http_method' IS DISTINCT FROM '"GET"'::jsonb
           OR p_summary->'production_mutation' IS DISTINCT FROM 'false'::jsonb
           OR p_summary->'model_calls' IS DISTINCT FROM '0'::jsonb
           OR p_summary->'cost_actual_microusd' IS DISTINCT FROM '0'::jsonb
           OR p_summary->'task_id' IS DISTINCT FROM to_jsonb(p_task_id::text)
           OR p_summary->'task_kind' IS DISTINCT FROM '"GITHUB_CI_READ_ONLY_V1"'::jsonb
           OR p_summary->'runtime' IS DISTINCT FROM '"ORACLE_RESIDENT"'::jsonb
           OR jsonb_typeof(p_summary->'check_total') <> 'number'
           OR jsonb_typeof(p_summary->'completed_count') <> 'number'
           OR jsonb_typeof(p_summary->'pending_count') <> 'number'
           OR COALESCE(p_summary->>'check_total', '') !~ '^[0-9]{1,3}$'
           OR COALESCE(p_summary->>'completed_count', '') !~ '^[0-9]{1,3}$'
           OR COALESCE(p_summary->>'pending_count', '') !~ '^[0-9]{1,3}$'
           OR (p_summary->>'check_total')::integer > 100
           OR (p_summary->>'completed_count')::integer
              + (p_summary->>'pending_count')::integer
              <> (p_summary->>'check_total')::integer
           OR jsonb_typeof(p_summary->'conclusion_counts') <> 'object'
           OR jsonb_typeof(p_summary->'failure_codes') <> 'array'
           OR jsonb_typeof(p_summary->'failed_checks') <> 'array'
           OR jsonb_typeof(p_summary->'failed_checks_truncated') <> 'boolean'
           OR p_summary->>'overall_state' NOT IN ('PASS', 'FAIL', 'PENDING', 'NO_CHECKS') THEN
            RAISE EXCEPTION 'AUTOPILOT_GITHUB_CI_EVIDENCE_INVALID';
        END IF;

        SELECT array_agg(key ORDER BY key) INTO ci_conclusion_keys
          FROM jsonb_object_keys(p_summary->'conclusion_counts') AS keys(key);
        IF ci_conclusion_keys IS DISTINCT FROM ARRAY[
               'action_required', 'cancelled', 'failure', 'neutral', 'none',
               'skipped', 'stale', 'startup_failure', 'success', 'timed_out'
           ] OR EXISTS (
               SELECT 1 FROM jsonb_each(p_summary->'conclusion_counts') AS item(key, value)
                WHERE jsonb_typeof(value) <> 'number' OR value::text !~ '^[0-9]{1,3}$'
           ) OR (
               SELECT sum(value::text::integer)
                 FROM jsonb_each(p_summary->'conclusion_counts') AS item(key, value)
           ) <> (p_summary->>'check_total')::integer THEN
            RAISE EXCEPTION 'AUTOPILOT_GITHUB_CI_COUNTS_INVALID';
        END IF;

        hard_failure_count :=
            (p_summary->'conclusion_counts'->>'action_required')::integer
          + (p_summary->'conclusion_counts'->>'cancelled')::integer
          + (p_summary->'conclusion_counts'->>'failure')::integer
          + (p_summary->'conclusion_counts'->>'stale')::integer
          + (p_summary->'conclusion_counts'->>'startup_failure')::integer
          + (p_summary->'conclusion_counts'->>'timed_out')::integer;

        SELECT COALESCE(jsonb_agg(code ORDER BY code), '[]'::jsonb)
          INTO expected_failure_codes
          FROM (VALUES
              ('action_required', 'CI_ACTION_REQUIRED'),
              ('cancelled', 'CI_CANCELLED'),
              ('failure', 'CI_FAILURE'),
              ('stale', 'CI_STALE'),
              ('startup_failure', 'CI_STARTUP_FAILURE'),
              ('timed_out', 'CI_TIMED_OUT')
          ) AS codes(conclusion, code)
         WHERE (p_summary->'conclusion_counts'->>conclusion)::integer > 0;

        IF p_summary->'failure_codes' IS DISTINCT FROM expected_failure_codes
           OR jsonb_array_length(p_summary->'failed_checks') <> LEAST(hard_failure_count, 5)
           OR (p_summary->'failed_checks_truncated')::text::boolean
              IS DISTINCT FROM (hard_failure_count > 5)
           OR (p_summary->>'overall_state' = 'NO_CHECKS'
               AND (p_summary->>'check_total')::integer <> 0)
           OR (p_summary->>'overall_state' = 'PASS'
               AND ((p_summary->>'check_total')::integer = 0
                    OR (p_summary->>'pending_count')::integer <> 0
                    OR hard_failure_count <> 0))
           OR (p_summary->>'overall_state' = 'PENDING'
               AND ((p_summary->>'pending_count')::integer = 0
                    OR hard_failure_count <> 0))
           OR (p_summary->>'overall_state' = 'FAIL' AND hard_failure_count = 0)
           OR EXISTS (
               SELECT 1
                 FROM jsonb_array_elements(p_summary->'failed_checks') AS checks(item)
                WHERE (SELECT array_agg(key ORDER BY key)
                         FROM jsonb_object_keys(item) AS keys(key)) IS DISTINCT FROM ARRAY[
                           'annotation_level', 'annotation_message_excerpt',
                           'annotation_path', 'annotations_truncated', 'app_slug',
                           'check_run_id', 'conclusion', 'name',
                           'output_summary_excerpt', 'output_title', 'output_truncated'
                       ]
                   OR jsonb_typeof(item->'app_slug') <> 'string'
                   OR length(item->>'app_slug') NOT BETWEEN 1 AND 100
                   OR jsonb_typeof(item->'check_run_id') <> 'number'
                   OR COALESCE(item->>'check_run_id', '') !~ '^[1-9][0-9]{0,19}$'
                   OR jsonb_typeof(item->'conclusion') <> 'string'
                   OR item->>'conclusion' NOT IN (
                       'action_required', 'cancelled', 'failure', 'stale',
                       'startup_failure', 'timed_out'
                   )
                   OR jsonb_typeof(item->'name') <> 'string'
                   OR length(item->>'name') NOT BETWEEN 1 AND 200
                   OR (jsonb_typeof(item->'output_title') NOT IN ('string', 'null'))
                   OR (jsonb_typeof(item->'output_title') = 'string'
                       AND length(item->>'output_title') NOT BETWEEN 1 AND 120)
                   OR (jsonb_typeof(item->'output_summary_excerpt') NOT IN ('string', 'null'))
                   OR (jsonb_typeof(item->'output_summary_excerpt') = 'string'
                       AND length(item->>'output_summary_excerpt') NOT BETWEEN 1 AND 300)
                   OR jsonb_typeof(item->'output_truncated') <> 'boolean'
                   OR jsonb_typeof(item->'annotations_truncated') <> 'boolean'
                   OR (
                       (item->'annotation_level' = 'null'::jsonb
                        OR item->'annotation_path' = 'null'::jsonb
                        OR item->'annotation_message_excerpt' = 'null'::jsonb)
                       AND NOT (
                           item->'annotation_level' = 'null'::jsonb
                           AND item->'annotation_path' = 'null'::jsonb
                           AND item->'annotation_message_excerpt' = 'null'::jsonb
                           AND item->'annotations_truncated' = 'false'::jsonb
                       )
                   )
                   OR (
                       item->'annotation_level' <> 'null'::jsonb
                       AND (
                           item->>'annotation_level' NOT IN ('notice', 'warning', 'failure')
                           OR jsonb_typeof(item->'annotation_path') <> 'string'
                           OR length(item->>'annotation_path') NOT BETWEEN 1 AND 200
                           OR jsonb_typeof(item->'annotation_message_excerpt') <> 'string'
                           OR length(item->>'annotation_message_excerpt') NOT BETWEEN 1 AND 300
                       )
                   )
           ) OR EXISTS (
               SELECT 1
                 FROM jsonb_array_elements(p_summary->'failed_checks') AS checks(item)
                GROUP BY item->>'check_run_id'
               HAVING count(*) > 1
           ) OR EXISTS (
               SELECT 1
                 FROM (VALUES
                     ('action_required'), ('cancelled'), ('failure'), ('stale'),
                     ('startup_failure'), ('timed_out')
                 ) AS conclusions(conclusion)
                WHERE (
                    SELECT count(*)
                      FROM jsonb_array_elements(p_summary->'failed_checks') AS checks(item)
                     WHERE item->>'conclusion' = conclusions.conclusion
                ) > (p_summary->'conclusion_counts'->>conclusions.conclusion)::integer
                   OR (
                       hard_failure_count <= 5 AND (
                           SELECT count(*)
                             FROM jsonb_array_elements(p_summary->'failed_checks') AS checks(item)
                            WHERE item->>'conclusion' = conclusions.conclusion
                       ) <> (p_summary->'conclusion_counts'->>conclusions.conclusion)::integer
                   )
           ) THEN
            RAISE EXCEPTION 'AUTOPILOT_GITHUB_CI_EVIDENCE_INVALID';
        END IF;
    ELSIF task_goal_type = 'GITHUB_DRAFT_REPAIR_V1' THEN
        SELECT array_agg(key ORDER BY key) INTO github_summary_keys
          FROM jsonb_object_keys(p_summary) AS keys(key);
        IF github_summary_keys IS DISTINCT FROM ARRAY[
               'action_fingerprint', 'base_sha', 'branch_name', 'broker_host',
               'commit_sha', 'cost_actual_microusd', 'draft', 'http_method',
               'manifest_version', 'merge_allowed', 'model_calls',
               'operation_count', 'production_mutation', 'pull_request_number',
               'pull_request_url', 'replayed', 'repository', 'runtime', 'status',
               'task_id', 'task_key', 'task_kind', 'token_exposed'
           ]
           OR p_summary->'repository' IS DISTINCT FROM task_goal_json->'repository'
           OR p_summary->'task_key' IS DISTINCT FROM task_goal_json->'task_key'
           OR p_summary->'base_sha' IS DISTINCT FROM task_goal_json->'expected_base_sha'
           OR p_summary->'branch_name' IS DISTINCT FROM task_goal_json->'branch_name'
           OR p_summary->'action_fingerprint' IS DISTINCT FROM task_goal_json->'action_fingerprint'
           OR p_summary->'manifest_version' IS DISTINCT FROM '1'::jsonb
           OR COALESCE(p_summary->>'commit_sha', '') !~ '^[0-9a-f]{40}$'
           OR jsonb_typeof(p_summary->'pull_request_number') <> 'number'
           OR COALESCE(p_summary->>'pull_request_number', '') !~ '^[1-9][0-9]{0,6}$'
           OR (p_summary->>'pull_request_number')::integer > 1000000
           OR p_summary->>'pull_request_url' <> format(
               'https://github.com/olegmed1-art/bridge-video-free/pull/%s',
               p_summary->>'pull_request_number'
           )
           OR p_summary->>'status' NOT IN ('created', 'existing')
           OR jsonb_typeof(p_summary->'replayed') <> 'boolean'
           OR (p_summary->>'status' = 'existing' AND p_summary->'replayed' <> 'true'::jsonb)
           OR p_summary->'draft' IS DISTINCT FROM 'true'::jsonb
           OR p_summary->'token_exposed' IS DISTINCT FROM 'false'::jsonb
           OR p_summary->'merge_allowed' IS DISTINCT FROM 'false'::jsonb
           OR p_summary->'production_mutation' IS DISTINCT FROM 'false'::jsonb
           OR p_summary->'operation_count' IS DISTINCT FROM
              to_jsonb(8 + 2 * jsonb_array_length(task_goal_json->'changes'))
           OR COALESCE(p_summary->>'broker_host', '') !~
              '^bridge-school-autopilot-[a-z0-9]+-olegmed1-4368s-projects\.vercel\.app$'
           OR p_summary->'http_method' IS DISTINCT FROM '"POST"'::jsonb
           OR p_summary->'model_calls' IS DISTINCT FROM '0'::jsonb
           OR p_summary->'cost_actual_microusd' IS DISTINCT FROM '0'::jsonb
           OR p_summary->'task_id' IS DISTINCT FROM to_jsonb(p_task_id::text)
           OR p_summary->'task_kind' IS DISTINCT FROM '"GITHUB_DRAFT_REPAIR_V1"'::jsonb
           OR p_summary->'runtime' IS DISTINCT FROM '"ORACLE_RESIDENT"'::jsonb THEN
            RAISE EXCEPTION 'AUTOPILOT_DRAFT_REPAIR_EVIDENCE_INVALID';
        END IF;
    ELSIF task_goal_type = 'IBF_READ_ONLY_ANALYSIS' THEN
        SELECT array_agg(key ORDER BY key) INTO github_summary_keys
          FROM jsonb_object_keys(p_summary) AS keys(key);
        IF github_summary_keys IS DISTINCT FROM ARRAY[
               'analysis_scope', 'board_count', 'boards', 'cost_actual_microusd',
               'http_method', 'ibf_player_id', 'latest_participation',
               'member_page_sha256', 'model_calls', 'personal_page_sha256',
               'production_mutation', 'request_count', 'results_index_sha256',
               'session_page_sha256', 'source_authority'
           ]
           OR p_summary->'ibf_player_id' IS DISTINCT FROM task_goal_json->'ibf_player_id'
           OR p_summary->'source_authority' IS DISTINCT FROM task_goal_json->'source_authority'
           OR p_summary->'analysis_scope'
              IS DISTINCT FROM '"SOURCE_RETRIEVAL_AND_FIELD_EVIDENCE_ONLY"'::jsonb
           OR p_summary->'http_method' IS DISTINCT FROM '"GET"'::jsonb
           OR p_summary->'production_mutation' IS DISTINCT FROM 'false'::jsonb
           OR p_summary->'model_calls' IS DISTINCT FROM '0'::jsonb
           OR p_summary->'cost_actual_microusd' IS DISTINCT FROM '0'::jsonb
           OR jsonb_typeof(p_summary->'latest_participation') <> 'object'
           OR jsonb_typeof(p_summary->'boards') <> 'array'
           OR jsonb_typeof(p_summary->'board_count') <> 'number'
           OR jsonb_typeof(p_summary->'request_count') <> 'number'
           OR COALESCE(p_summary->>'board_count', '') !~ '^[1-9][0-9]{0,1}$'
           OR COALESCE(p_summary->>'request_count', '') !~ '^[1-9][0-9]{0,2}$'
           OR (p_summary->>'board_count')::integer > 32
           OR (p_summary->>'request_count')::integer > 96
           OR COALESCE(p_summary->>'member_page_sha256', '') !~ '^[0-9a-f]{64}$'
           OR COALESCE(p_summary->>'results_index_sha256', '') !~ '^[0-9a-f]{64}$'
           OR COALESCE(p_summary->>'session_page_sha256', '') !~ '^[0-9a-f]{64}$'
           OR COALESCE(p_summary->>'personal_page_sha256', '') !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'AUTOPILOT_IBF_EVIDENCE_INVALID';
        END IF;

        SELECT array_agg(key ORDER BY key) INTO github_summary_keys
          FROM jsonb_object_keys(p_summary->'latest_participation') AS keys(key);
        IF github_summary_keys IS DISTINCT FROM ARRAY[
               'date', 'event_id', 'personal_url', 'round_id', 'seat', 'session_url'
           ]
           OR COALESCE(p_summary->'latest_participation'->>'date', '')
              !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
           OR COALESCE(p_summary->'latest_participation'->>'event_id', '')
              !~ '^[1-9][0-9]{0,9}$'
           OR COALESCE(p_summary->'latest_participation'->>'round_id', '')
              !~ '^[1-9][0-9]{0,3}$'
           OR COALESCE(p_summary->'latest_participation'->>'seat', '')
              !~ '^[A-Za-z0-9:-]{1,24}$'
           OR p_summary->'latest_participation'->>'session_url' <> format(
               'https://bridge.co.il/viewer/session.php?event=%s&round=%s',
               p_summary->'latest_participation'->>'event_id',
               p_summary->'latest_participation'->>'round_id'
           )
           OR p_summary->'latest_participation'->>'personal_url' <> format(
               'https://bridge.co.il/viewer/personal.php?event=%s&round=%s&seat=%s',
               p_summary->'latest_participation'->>'event_id',
               p_summary->'latest_participation'->>'round_id',
               p_summary->'latest_participation'->>'seat'
           )
           OR jsonb_array_length(p_summary->'boards')
              <> (p_summary->>'board_count')::integer THEN
            RAISE EXCEPTION 'AUTOPILOT_IBF_EVIDENCE_INVALID';
        END IF;

        IF EXISTS (
            SELECT 1
              FROM jsonb_array_elements(p_summary->'boards') AS boards(item)
             WHERE (SELECT array_agg(key ORDER BY key)
                      FROM jsonb_object_keys(item) AS keys(key)) IS DISTINCT FROM ARRAY[
                       'board_number', 'field_page_sha256', 'field_row_count',
                       'percentage_token', 'personal_row_excerpt', 'score_token'
                   ]
                OR jsonb_typeof(item->'board_number') <> 'number'
                OR COALESCE(item->>'board_number', '') !~ '^[1-9][0-9]{0,2}$'
                OR jsonb_typeof(item->'field_row_count') <> 'number'
                OR COALESCE(item->>'field_row_count', '') !~ '^[1-9][0-9]{0,2}$'
                OR (item->>'field_row_count')::integer > 200
                OR COALESCE(item->>'field_page_sha256', '') !~ '^[0-9a-f]{64}$'
                OR jsonb_typeof(item->'personal_row_excerpt') <> 'string'
                OR length(item->>'personal_row_excerpt') NOT BETWEEN 1 AND 160
                OR jsonb_typeof(item->'percentage_token') NOT IN ('string', 'null')
                OR (jsonb_typeof(item->'percentage_token') = 'string'
                    AND (item->>'percentage_token') !~
                        '^(100(\.0+)?|[0-9]{1,2}(\.[0-9]+)?)$')
                OR jsonb_typeof(item->'score_token') NOT IN ('string', 'null')
                OR (jsonb_typeof(item->'score_token') = 'string'
                    AND (item->>'score_token') !~ '^[-+]?[1-9][0-9]{1,4}$')
        ) OR (
            SELECT count(DISTINCT item->>'board_number')
              FROM jsonb_array_elements(p_summary->'boards') AS boards(item)
        ) <> (p_summary->>'board_count')::integer THEN
            RAISE EXCEPTION 'AUTOPILOT_IBF_EVIDENCE_INVALID';
        END IF;
    END IF;

    INSERT INTO autopilot.evidence (
        task_id, step_attempt_id, evidence_class, provider,
        content_sha256, metadata_json
    ) VALUES (
        p_task_id, step_id, p_evidence_class, 'ORACLE_RESIDENT',
        p_content_sha256, COALESCE(p_summary, '{}'::jsonb)
    ) ON CONFLICT DO NOTHING;
    UPDATE autopilot.step_attempt
       SET status = 'COMPLETED', result_summary_json = COALESCE(p_summary, '{}'::jsonb),
           completed_at = now()
     WHERE step_attempt_id = step_id;
    UPDATE autopilot.task
       SET status = 'DONE', lease_owner = NULL, lease_until = NULL,
           terminal_reason_code = 'ACCEPTANCE_EVIDENCE_RETAINED',
           safe_summary_json = COALESCE(p_summary, '{}'::jsonb), completed_at = now()
     WHERE task_id = p_task_id;
    IF NOT EXISTS (SELECT 1 FROM autopilot.evidence WHERE task_id = p_task_id AND retained) THEN
        RAISE EXCEPTION 'AUTOPILOT_DONE_WITHOUT_EVIDENCE';
    END IF;
    PERFORM autopilot.record_event(
        p_task_id, 'TASK_DONE', old_state, 'DONE',
        jsonb_build_object('evidence_class', p_evidence_class, 'content_sha256', p_content_sha256),
        'ORACLE_WORKER', p_worker_id,
        'done:' || p_task_id::text || ':' || p_lease_epoch::text
    );
    RETURN true;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.complete_task(uuid, text, bigint, text, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.complete_task(uuid, text, bigint, text, text, jsonb)
    TO autopilot_runtime;

COMMENT ON FUNCTION autopilot.complete_task(uuid, text, bigint, text, text, jsonb) IS
'Completes fenced shadow tasks with exact evidence validation, including bounded official IBF snapshots.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0307_autopilot_ibf_completion')
ON CONFLICT DO NOTHING;

COMMIT;
