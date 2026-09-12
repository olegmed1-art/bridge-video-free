-- One bounded retry for dynamic-role deliveries that failed before the first
-- proven dynamic dispatch.  A fresh HTTP failure remains fail-closed.
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0325_autopilot_durable_dependency_wakeup'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_LEGACY_ROLE_REARM_REQUIRES_0325';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION autopilot.materialize_project_work_probe(
    p_work_item_id uuid,
    p_worker_id text,
    p_lease_epoch bigint,
    p_target_open boolean,
    p_observed_head_sha text
)
RETURNS TABLE(task_id uuid, resulting_state text, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    item autopilot.project_work_item;
    created_task record;
    next_task_key text;
    legacy_dynamic_role_http_failure boolean := false;
BEGIN
    SELECT * INTO item FROM autopilot.project_work_item AS work
     WHERE work.work_item_id = p_work_item_id FOR UPDATE;
    IF NOT FOUND
       OR item.probe_lease_owner IS DISTINCT FROM p_worker_id
       OR item.probe_lease_epoch <> p_lease_epoch
       OR item.probe_lease_until < now() THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_PROBE_FENCED';
    END IF;
    IF p_observed_head_sha !~ '^[0-9a-f]{40}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_PROBE_HEAD_INVALID';
    END IF;

    IF p_target_open IS DISTINCT FROM true THEN
        UPDATE autopilot.project_work_item
           SET state = 'PAUSED',
               last_observed_head_sha = p_observed_head_sha,
               result_code = 'TARGET_PR_NOT_OPEN',
               result_summary = 'Target pull request is not open.',
               probe_lease_owner = NULL,
               probe_lease_until = NULL,
               updated_at = now()
         WHERE work_item_id = item.work_item_id;
        UPDATE autopilot.project_planner_state
           SET decision_count = decision_count + 1,
               last_decision_code = 'TARGET_PR_NOT_OPEN',
               last_work_item_id = item.work_item_id,
               last_decision_at = now()
         WHERE singleton;
        PERFORM pg_notify('autopilot_ready', 'project-work-paused');
        RETURN QUERY SELECT NULL::uuid, 'PAUSED'::text, false;
        RETURN;
    END IF;

    -- Four enabled roles failed with an HTTP contract error before the broker
    -- first proved a dynamic-role dispatch at 2026-09-12 15:00:38 UTC.  The
    -- retained outbox timestamp, rather than the repeatedly refreshed work
    -- item timestamp, makes this a one-shot capability-release retry.  If the
    -- new generation fails, its later outbox cannot satisfy this predicate.
    IF item.state = 'BLOCKED'
       AND item.result_code = 'ROLE_DISPATCH_HTTP_ERROR'
       AND item.role NOT IN ('RECOGNIZER', 'VIDEO', 'BOOKS', 'KNOWLEDGE') THEN
        SELECT EXISTS (
            SELECT 1
              FROM autopilot.role_dispatch_outbox AS outbox
             WHERE outbox.task_id = item.last_task_id
               AND outbox.status = 'FAILED_CLOSED'
               AND outbox.last_error_code = 'ROLE_DISPATCH_HTTP_ERROR'
               AND outbox.completed_at <
                   TIMESTAMPTZ '2026-09-12 15:00:38+00'
               AND EXISTS (
                   SELECT 1
                     FROM autopilot.role_registry AS registry
                    WHERE registry.role_id = item.role
                      AND registry.enabled
               )
        ) INTO legacy_dynamic_role_http_failure;
    END IF;

    -- Semantic and safety blockers stay pinned to the exact head.  Ordinary
    -- transport errors use the existing retry contract; the historical HTTP
    -- exception above is eligible exactly once after the broker capability
    -- release.  Generation-scoped task keys prevent aliasing and duplicates.
    IF item.state = 'BLOCKED'
       AND item.last_observed_head_sha = p_observed_head_sha
       AND NOT autopilot.project_work_transport_retryable(item.result_code)
       AND NOT legacy_dynamic_role_http_failure THEN
        UPDATE autopilot.project_work_item
           SET not_before = now() + interval '15 minutes',
               probe_lease_owner = NULL,
               probe_lease_until = NULL,
               updated_at = now()
         WHERE work_item_id = item.work_item_id;
        UPDATE autopilot.project_planner_state
           SET decision_count = decision_count + 1,
               last_decision_code = 'BLOCKED_HEAD_UNCHANGED',
               last_work_item_id = item.work_item_id,
               last_decision_at = now()
         WHERE singleton;
        RETURN QUERY SELECT NULL::uuid, 'BLOCKED'::text, false;
        RETURN;
    END IF;

    IF item.generation > 333331 THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_GENERATION_EXHAUSTED';
    END IF;
    next_task_key := 'project-work:' || item.work_key || ':' ||
        substr(p_observed_head_sha, 1, 12) || ':g' ||
        (item.generation + 1)::text;
    SELECT * INTO created_task
      FROM autopilot.create_chatgpt_role_dispatch_task(
          next_task_key,
          jsonb_build_object(
              'repository', item.repository,
              'mailbox_pr', item.mailbox_pr,
              'role', item.role,
              'target_pr', item.target_pr,
              'expected_head_sha', p_observed_head_sha,
              'dispatch_epoch', item.generation * 3 + 1,
              'successor_task_key', NULL,
              'successor_role', NULL,
              'successor_target_pr', NULL,
              'successor_expected_head_sha', NULL
          ),
          item.priority,
          'AUTOPILOT_PROJECT_PLANNER',
          'AUTOPILOT_PROJECT_WORK'
      );
    INSERT INTO autopilot.project_work_task(work_item_id, task_id, run_kind)
    VALUES (item.work_item_id, created_task.task_id, 'AUDIT')
    ON CONFLICT ON CONSTRAINT project_work_task_task_id_key DO NOTHING;
    UPDATE autopilot.project_work_item
       SET state = 'ACTIVE',
           generation = generation + 1,
           last_observed_head_sha = p_observed_head_sha,
           last_task_id = created_task.task_id,
           result_code = NULL,
           result_summary = NULL,
           probe_lease_owner = NULL,
           probe_lease_until = NULL,
           completed_at = NULL,
           updated_at = now()
     WHERE work_item_id = item.work_item_id;
    UPDATE autopilot.project_planner_state
       SET decision_count = decision_count + 1,
           last_decision_code = CASE
               WHEN legacy_dynamic_role_http_failure
                   THEN 'CAPABILITY_RELEASE_RETRY_MATERIALIZED'
               WHEN item.state = 'BLOCKED'
                   THEN 'TRANSPORT_RETRY_MATERIALIZED'
               ELSE 'TASK_MATERIALIZED'
           END,
           last_work_item_id = item.work_item_id,
           last_decision_at = now()
     WHERE singleton;
    RETURN QUERY SELECT created_task.task_id, 'ACTIVE'::text, created_task.created;
END;
$$;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0326_autopilot_legacy_dynamic_role_rearm')
ON CONFLICT DO NOTHING;

COMMIT;
