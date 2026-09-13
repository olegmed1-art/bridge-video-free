\set ON_ERROR_STOP on
BEGIN;

-- School Autopilot Controller v1.3 — exact-head, public, read-only CI triage.
-- The Oracle worker receives no GitHub credential and may only retain bounded
-- Check Runs metadata plus at most five short public failure diagnostics.

ALTER TABLE autopilot.task DROP CONSTRAINT task_goal_type_check;
ALTER TABLE autopilot.task ADD CONSTRAINT task_goal_type_check CHECK (goal_type IN (
    'AUTOPILOT_SMOKE_V1',
    'EXTERNAL_WAIT_SHADOW_V1',
    'OWNER_BOUNDARY_V1',
    'GITHUB_PR_READ_ONLY_V1',
    'GITHUB_CI_READ_ONLY_V1'
));

ALTER TABLE autopilot.step_attempt DROP CONSTRAINT step_attempt_capability_name_check;
ALTER TABLE autopilot.step_attempt ADD CONSTRAINT step_attempt_capability_name_check CHECK (
    capability_name IN (
        'shadow.noop', 'shadow.wait', 'policy.owner_boundary',
        'github.pr.snapshot', 'github.ci.snapshot'
    )
);

ALTER TABLE autopilot.evidence DROP CONSTRAINT evidence_evidence_class_check;
ALTER TABLE autopilot.evidence ADD CONSTRAINT evidence_evidence_class_check CHECK (
    evidence_class IN (
        'SYNTHETIC_SHADOW_COMPLETION',
        'SYNTHETIC_SHADOW_RESUME',
        'OWNER_BOUNDARY_PROOF',
        'GITHUB_PR_READ_ONLY_SNAPSHOT',
        'GITHUB_CI_READ_ONLY_SNAPSHOT'
    )
);

CREATE OR REPLACE FUNCTION autopilot.create_shadow_task(
    p_task_key text,
    p_goal_type text,
    p_goal_json jsonb DEFAULT '{}'::jsonb,
    p_priority integer DEFAULT 20,
    p_cost_cap_microusd bigint DEFAULT 0,
    p_created_by text DEFAULT 'DIRECTOR',
    p_source text DEFAULT 'CHATGPT'
)
RETURNS TABLE(task_id uuid, status text, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    existing autopilot.task;
    inserted autopilot.task;
    github_keys text[];
BEGIN
    IF p_task_key IS NULL OR p_task_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_TASK_KEY_INVALID';
    END IF;
    IF p_goal_type NOT IN (
        'AUTOPILOT_SMOKE_V1', 'EXTERNAL_WAIT_SHADOW_V1',
        'OWNER_BOUNDARY_V1', 'GITHUB_PR_READ_ONLY_V1',
        'GITHUB_CI_READ_ONLY_V1'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CAPABILITY_UNKNOWN';
    END IF;
    IF p_priority NOT IN (0, 10, 20, 30) THEN
        RAISE EXCEPTION 'AUTOPILOT_PRIORITY_INVALID';
    END IF;
    IF jsonb_typeof(COALESCE(p_goal_json, '{}'::jsonb)) <> 'object'
       OR octet_length(COALESCE(p_goal_json, '{}'::jsonb)::text) > 8192 THEN
        RAISE EXCEPTION 'AUTOPILOT_GOAL_INVALID';
    END IF;
    IF p_goal_type = 'EXTERNAL_WAIT_SHADOW_V1'
       AND COALESCE(p_goal_json->>'correlation_id', '') !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_CORRELATION_INVALID';
    END IF;
    IF p_goal_type IN ('GITHUB_PR_READ_ONLY_V1', 'GITHUB_CI_READ_ONLY_V1') THEN
        SELECT array_agg(key ORDER BY key) INTO github_keys
          FROM jsonb_object_keys(p_goal_json) AS keys(key);
        IF github_keys <> ARRAY['expected_head_sha', 'pr_number', 'repository', 'require_draft']
           OR p_goal_json->>'repository' <> 'olegmed1-art/bridge-video-free'
           OR COALESCE(p_goal_json->>'expected_head_sha', '') !~ '^[0-9a-f]{40}$'
           OR jsonb_typeof(p_goal_json->'pr_number') <> 'number'
           OR (p_goal_json->>'pr_number') !~ '^[1-9][0-9]{0,6}$'
           OR (p_goal_json->>'pr_number')::integer > 1000000
           OR p_goal_json->'require_draft' <> 'true'::jsonb
           OR p_cost_cap_microusd <> 0 THEN
            RAISE EXCEPTION 'AUTOPILOT_GITHUB_CONTRACT_INVALID';
        END IF;
    ELSIF (p_goal_type = 'EXTERNAL_WAIT_SHADOW_V1'
           AND (SELECT count(*) FROM jsonb_object_keys(COALESCE(p_goal_json, '{}'::jsonb))) <> 1)
       OR (p_goal_type <> 'EXTERNAL_WAIT_SHADOW_V1'
           AND COALESCE(p_goal_json, '{}'::jsonb) <> '{}'::jsonb) THEN
        RAISE EXCEPTION 'AUTOPILOT_GOAL_FIELDS_INVALID';
    END IF;
    IF p_cost_cap_microusd NOT BETWEEN 0 AND 20000000 THEN
        RAISE EXCEPTION 'AUTOPILOT_COST_CAP_INVALID';
    END IF;
    IF length(COALESCE(p_created_by, '')) NOT BETWEEN 1 AND 256
       OR length(COALESCE(p_source, '')) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'AUTOPILOT_ORIGIN_INVALID';
    END IF;

    SELECT * INTO existing FROM autopilot.task WHERE task_key = p_task_key;
    IF FOUND THEN
        IF existing.goal_type <> p_goal_type OR existing.goal_json <> COALESCE(p_goal_json, '{}'::jsonb)
           OR existing.priority <> p_priority::smallint
           OR existing.cost_cap_microusd <> p_cost_cap_microusd
           OR existing.created_by <> p_created_by OR existing.source <> p_source THEN
            RAISE EXCEPTION 'AUTOPILOT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN QUERY SELECT existing.task_id, existing.status, false;
        RETURN;
    END IF;

    INSERT INTO autopilot.task (
        task_key, goal_type, goal_json, status, current_step_key,
        acceptance_contract_json, allowed_capabilities_json, priority,
        cost_cap_microusd, created_by, source
    ) VALUES (
        p_task_key, p_goal_type, COALESCE(p_goal_json, '{}'::jsonb), 'READY',
        CASE p_goal_type
            WHEN 'AUTOPILOT_SMOKE_V1' THEN 'shadow.noop'
            WHEN 'EXTERNAL_WAIT_SHADOW_V1' THEN 'shadow.wait'
            WHEN 'GITHUB_PR_READ_ONLY_V1' THEN 'github.pr.snapshot'
            WHEN 'GITHUB_CI_READ_ONLY_V1' THEN 'github.ci.snapshot'
            ELSE 'policy.owner_boundary'
        END,
        jsonb_build_object(
            'retained_evidence_required', true,
            'production_mutation', false,
            'exact_head_required', p_goal_type IN (
                'GITHUB_PR_READ_ONLY_V1', 'GITHUB_CI_READ_ONLY_V1'
            )
        ),
        CASE p_goal_type
            WHEN 'AUTOPILOT_SMOKE_V1' THEN '["shadow.noop"]'::jsonb
            WHEN 'EXTERNAL_WAIT_SHADOW_V1' THEN '["shadow.wait"]'::jsonb
            WHEN 'GITHUB_PR_READ_ONLY_V1' THEN '["github.pr.snapshot"]'::jsonb
            WHEN 'GITHUB_CI_READ_ONLY_V1' THEN '["github.ci.snapshot"]'::jsonb
            ELSE '["policy.owner_boundary"]'::jsonb
        END,
        p_priority::smallint, p_cost_cap_microusd, p_created_by, p_source
    ) ON CONFLICT (task_key) DO NOTHING
    RETURNING * INTO inserted;

    IF inserted.task_id IS NULL THEN
        SELECT * INTO existing FROM autopilot.task WHERE task_key = p_task_key;
        IF NOT FOUND OR existing.goal_type <> p_goal_type
           OR existing.goal_json <> COALESCE(p_goal_json, '{}'::jsonb)
           OR existing.priority <> p_priority::smallint
           OR existing.cost_cap_microusd <> p_cost_cap_microusd
           OR existing.created_by <> p_created_by OR existing.source <> p_source THEN
            RAISE EXCEPTION 'AUTOPILOT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN QUERY SELECT existing.task_id, existing.status, false;
        RETURN;
    END IF;

    PERFORM autopilot.record_event(
        inserted.task_id, 'TASK_READY', 'NEW', 'READY',
        jsonb_build_object('goal_type', inserted.goal_type),
        'DIRECTOR', left(p_created_by, 256), 'create:' || p_task_key
    );
    RETURN QUERY SELECT inserted.task_id, inserted.status, true;
END;
$$;

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
        'GITHUB_CI_READ_ONLY_SNAPSHOT'
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
               (goal_type NOT IN ('GITHUB_PR_READ_ONLY_V1', 'GITHUB_CI_READ_ONLY_V1')
                AND p_evidence_class NOT IN (
                    'GITHUB_PR_READ_ONLY_SNAPSHOT', 'GITHUB_CI_READ_ONLY_SNAPSHOT'
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

COMMENT ON SCHEMA autopilot IS
'School Autopilot canonical orchestration state. v1.3 permits exact public draft-PR and CI snapshots and no external mutation.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0302_autopilot_github_ci_read_only')
ON CONFLICT DO NOTHING;

COMMIT;
