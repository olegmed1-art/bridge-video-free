\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('autopilot.role-worker-capacity-v1', 0)
    );
END $$;

LOCK TABLE autopilot.project_work_item IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_chat_registry IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE goal_type IN (
             'CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1'
         )
           AND status IN (
             'NEW', 'VALIDATING', 'READY', 'RUNNING',
             'WAITING_EXTERNAL', 'EVALUATING'
           )
    ) OR EXISTS (
        SELECT 1 FROM autopilot.project_work_item
         WHERE probe_lease_owner IS NOT NULL
           AND probe_lease_until >= now()
    ) OR EXISTS (
        SELECT 1 FROM autopilot.role_dispatch_outbox
         WHERE status IN ('CLAIMED', 'PUBLISHED', 'SENT')
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_SIX_WORKER_ROLLBACK_ACTIVE_WORK';
    END IF;
END $$;

DROP TRIGGER autopilot_role_worker_capacity ON autopilot.task;
DROP FUNCTION autopilot.enforce_role_worker_capacity();
DROP FUNCTION autopilot.role_worker_capacity_snapshot();

-- Restore serialized planner materialization while retaining the independent
-- strict-lease hardening; rollback must not revive the legacy NULL bypass.
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
       OR p_worker_id IS NULL
       OR p_lease_epoch IS NULL
       OR item.state NOT IN ('READY', 'BLOCKED')
       OR item.probe_lease_owner IS NULL
       OR item.probe_lease_owner IS DISTINCT FROM p_worker_id
       OR item.probe_lease_epoch IS DISTINCT FROM p_lease_epoch
       OR item.probe_lease_until IS NULL
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

CREATE OR REPLACE FUNCTION autopilot.claim_project_work_probe(
    p_worker_id text,
    p_lease_seconds integer DEFAULT 60
)
RETURNS TABLE(
    work_item_id uuid,
    work_key text,
    repository text,
    role text,
    target_pr integer,
    prior_state text,
    prior_head_sha text,
    lease_epoch bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    selected autopilot.project_work_item;
BEGIN
    IF length(COALESCE(p_worker_id, '')) NOT BETWEEN 1 AND 256
       OR p_lease_seconds NOT BETWEEN 30 AND 300 THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_PROBE_LEASE_INVALID';
    END IF;
    IF NOT (SELECT enabled FROM autopilot.project_planner_state WHERE singleton) THEN
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1 FROM autopilot.task AS task
         WHERE task.goal_type IN (
             'CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1'
         )
           AND task.status IN (
             'NEW', 'VALIDATING', 'READY', 'RUNNING',
             'WAITING_EXTERNAL', 'EVALUATING'
           )
    ) THEN
        UPDATE autopilot.project_planner_state
           SET decision_count = decision_count + 1,
               last_decision_code = 'WAITING_FOR_ACTIVE_ROLE_TASK',
               last_work_item_id = NULL,
               last_decision_at = now()
         WHERE singleton;
        RETURN;
    END IF;

    SELECT item.* INTO selected
      FROM autopilot.project_work_item AS item
      JOIN autopilot.role_chat_registry AS chat
        ON chat.role_id = item.role AND chat.enabled
     WHERE item.state IN ('READY', 'BLOCKED')
       AND item.not_before <= now()
       AND (item.probe_lease_until IS NULL OR item.probe_lease_until < now())
     ORDER BY
       CASE item.state WHEN 'READY' THEN 0 ELSE 1 END,
       item.priority,
       item.not_before,
       item.created_at
     FOR UPDATE OF item SKIP LOCKED
     LIMIT 1;

    IF NOT FOUND THEN
        UPDATE autopilot.project_planner_state
           SET decision_count = decision_count + 1,
               last_decision_code = CASE
                   WHEN NOT EXISTS (
                       SELECT 1 FROM autopilot.project_work_item
                   ) THEN 'IDLE_NO_REGISTERED_WORK'
                   WHEN EXISTS (
                       SELECT 1 FROM autopilot.project_work_item
                        WHERE state = 'ACTIVE'
                   ) THEN 'WAITING_FOR_ACTIVE_WORK_ITEM'
                   WHEN EXISTS (
                       SELECT 1 FROM autopilot.project_work_item
                        WHERE state = 'WAITING_DEPENDENCY'
                   ) THEN 'WAITING_FOR_DEPENDENCY'
                   WHEN EXISTS (
                       SELECT 1
                         FROM autopilot.project_work_item AS item
                        WHERE item.state IN ('READY', 'BLOCKED')
                          AND item.not_before <= now()
                          AND (item.probe_lease_until IS NULL OR item.probe_lease_until < now())
                          AND NOT EXISTS (
                              SELECT 1
                                FROM autopilot.role_chat_registry AS chat
                               WHERE chat.role_id = item.role AND chat.enabled
                          )
                   ) THEN 'WAITING_FOR_CHAT_TARGET'
                   WHEN EXISTS (
                       SELECT 1 FROM autopilot.project_work_item
                        WHERE state IN ('READY', 'BLOCKED')
                          AND not_before > now()
                   ) THEN 'WAITING_FOR_RETRY_WINDOW'
                   WHEN EXISTS (
                       SELECT 1 FROM autopilot.project_work_item
                        WHERE state IN ('READY', 'BLOCKED')
                          AND probe_lease_until >= now()
                   ) THEN 'WAITING_FOR_PROBE_LEASE'
                   WHEN NOT EXISTS (
                       SELECT 1 FROM autopilot.project_work_item
                        WHERE state NOT IN ('DONE', 'PAUSED')
                   ) THEN 'PROJECT_DONE'
                   ELSE 'IDLE_NO_ELIGIBLE_TASK'
               END,
               last_work_item_id = NULL,
               last_decision_at = now()
         WHERE singleton;
        RETURN;
    END IF;

    UPDATE autopilot.project_work_item AS item
       SET probe_lease_owner = p_worker_id,
           probe_lease_epoch = item.probe_lease_epoch + 1,
           probe_lease_until = now() + make_interval(secs => p_lease_seconds),
           updated_at = now()
     WHERE item.work_item_id = selected.work_item_id
     RETURNING * INTO selected;
    UPDATE autopilot.project_planner_state
       SET decision_count = decision_count + 1,
           last_decision_code = 'PROBE_CLAIMED',
           last_work_item_id = selected.work_item_id,
           last_decision_at = now()
     WHERE singleton;

    RETURN QUERY SELECT
        selected.work_item_id,
        selected.work_key,
        selected.repository,
        selected.role,
        selected.target_pr,
        selected.state,
        selected.last_observed_head_sha,
        selected.probe_lease_epoch;
END;
$$;

COMMENT ON FUNCTION autopilot.claim_project_work_probe(text,integer) IS
'Claims only work with an enabled existing ChatGPT target; unmapped roles remain durable and report WAITING_FOR_CHAT_TARGET.';

ALTER TABLE autopilot.project_planner_state
    DROP CONSTRAINT project_planner_worker_capacity_check,
    DROP COLUMN max_active_workers,
    DROP COLUMN max_normal_workers;

DROP INDEX autopilot.role_chat_registry_chat_id_idx;
DROP INDEX autopilot.role_chat_registry_executor_id_idx;

DELETE FROM autopilot.role_chat_registry;
INSERT INTO autopilot.role_chat_registry(
    role_id, chat_id, chat_name, chat_url, executor_id,
    enabled, is_dispatcher, created_at, updated_at
)
SELECT role_id, chat_id, chat_name, chat_url, executor_id,
       enabled, is_dispatcher, created_at, updated_at
  FROM autopilot.role_chat_registry_0331_backup;

ALTER TABLE autopilot.role_chat_registry
    ADD CONSTRAINT role_chat_registry_chat_id_key UNIQUE (chat_id),
    ADD CONSTRAINT role_chat_registry_chat_url_key UNIQUE (chat_url),
    ADD CONSTRAINT role_chat_registry_executor_id_key UNIQUE (executor_id);

DROP TABLE autopilot.role_chat_registry_0331_backup;

DELETE FROM public.schema_migration
WHERE migration_key = '0331_autopilot_six_worker_capacity';

COMMIT;
