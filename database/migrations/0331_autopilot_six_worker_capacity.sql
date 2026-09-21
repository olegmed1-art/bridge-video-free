\set ON_ERROR_STOP on
BEGIN;

-- One existing Slavik chat coordinates bounded transient workers.  The
-- durable queue may fill five normal worker slots and one P0-only reserve,
-- but it must never materialize more than six active role tasks.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0328_autopilot_chat_target_gate'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_SIX_WORKER_CAPACITY_REQUIRES_0328';
    END IF;
END $$;

DO $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('autopilot.role-worker-capacity-v1', 0)
    );
END $$;

-- Quiesce materialization, task admission, and publication in their runtime
-- lock order before changing either capacity or routing.
LOCK TABLE autopilot.project_work_item IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_chat_registry IN ACCESS EXCLUSIVE MODE;

-- Rebinding an already delivered or running message would invalidate its
-- delivery proof.  Drain those rows before applying this migration.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM autopilot.role_dispatch_outbox
         WHERE status IN ('CLAIMED', 'PUBLISHED', 'SENT')
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ORCHESTRATOR_REBIND_ACTIVE_DISPATCH';
    END IF;
END $$;

-- The trigger is prospective, so activation must also prove that the locked
-- baseline already satisfies both the global cap and the P0 reserve.
DO $$
DECLARE
    active_workers bigint;
    active_normal_workers bigint;
    probe_reservations bigint;
    normal_probe_reservations bigint;
BEGIN
    SELECT count(*), count(*) FILTER (WHERE priority <> 0)
      INTO active_workers, active_normal_workers
      FROM autopilot.task
     WHERE goal_type IN (
         'CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1'
     )
       AND status IN (
         'NEW', 'VALIDATING', 'READY', 'RUNNING',
         'WAITING_EXTERNAL', 'EVALUATING'
       );
    SELECT count(*), count(*) FILTER (WHERE priority <> 0)
      INTO probe_reservations, normal_probe_reservations
      FROM autopilot.project_work_item
     WHERE state IN ('READY', 'BLOCKED')
       AND probe_lease_owner IS NOT NULL
       AND probe_lease_until >= now();

    IF active_workers + probe_reservations > 6
       OR active_normal_workers + normal_probe_reservations > 5 THEN
        RAISE EXCEPTION 'AUTOPILOT_SIX_WORKER_BASELINE_OVER_CAPACITY';
    END IF;
END $$;

-- Retain the exact pre-migration registry.  Rollback restores this snapshot,
-- rather than assuming production still contains only the original seeds.
CREATE TABLE autopilot.role_chat_registry_0331_backup
AS TABLE autopilot.role_chat_registry WITH NO DATA;
ALTER TABLE autopilot.role_chat_registry_0331_backup
    ADD PRIMARY KEY (role_id);
INSERT INTO autopilot.role_chat_registry_0331_backup
SELECT * FROM autopilot.role_chat_registry;
REVOKE ALL ON TABLE autopilot.role_chat_registry_0331_backup
FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;

-- Several role identities intentionally share the one existing event-runtime
-- conversation. ChatGPT webhook automations execute in their durable task
-- conversation rather than in the project control chat, so this must be the
-- conversation_id of the single existing executor automation. A role remains
-- the authorization boundary; chat identity is now a routing target and
-- therefore is no longer one-to-one.
ALTER TABLE autopilot.role_chat_registry
    DROP CONSTRAINT role_chat_registry_chat_id_key,
    DROP CONSTRAINT role_chat_registry_chat_url_key,
    DROP CONSTRAINT role_chat_registry_executor_id_key;

-- The original registry accepted only project-chat URLs. Event-triggered
-- automations use a direct /c/<conversation_id> URL, so admit both exact
-- ChatGPT URL shapes while keeping every other host/path rejected.
ALTER TABLE autopilot.role_chat_registry
    DROP CONSTRAINT role_chat_registry_chat_url_check,
    ADD CONSTRAINT role_chat_registry_chat_url_check CHECK (
        chat_url ~ '^https://chatgpt\.com/(.+/)?c/[0-9a-f-]{36}$'
    );

INSERT INTO autopilot.role_chat_registry(
    role_id, chat_id, chat_name, chat_url, executor_id, enabled, is_dispatcher
)
SELECT role.role_id,
       '6aa6a4c0-4858-83eb-872c-4bc3451edc83',
       'Autopilot role executor',
       'https://chatgpt.com/c/6aa6a4c0-4858-83eb-872c-4bc3451edc83',
       'chat:6aa6a4c0-4858-83eb-872c-4bc3451edc83',
       true,
       false
  FROM autopilot.role_registry AS role
 WHERE role.enabled
   AND role.role_id <> 'PLANNING'
ON CONFLICT (role_id) DO UPDATE
SET chat_id = EXCLUDED.chat_id,
    chat_name = EXCLUDED.chat_name,
    chat_url = EXCLUDED.chat_url,
    executor_id = EXCLUDED.executor_id,
    enabled = true,
    is_dispatcher = false,
    updated_at = now();

CREATE INDEX role_chat_registry_chat_id_idx
    ON autopilot.role_chat_registry(chat_id) WHERE enabled;
CREATE INDEX role_chat_registry_executor_id_idx
    ON autopilot.role_chat_registry(executor_id) WHERE enabled;

ALTER TABLE autopilot.project_planner_state
    ADD COLUMN max_active_workers smallint NOT NULL DEFAULT 6,
    ADD COLUMN max_normal_workers smallint NOT NULL DEFAULT 5,
    ADD CONSTRAINT project_planner_worker_capacity_check CHECK (
        max_active_workers = 6
        AND max_normal_workers = 5
        AND max_normal_workers < max_active_workers
    );

CREATE OR REPLACE FUNCTION autopilot.role_worker_capacity_snapshot()
RETURNS TABLE(
    max_active_workers smallint,
    max_normal_workers smallint,
    active_workers bigint,
    active_normal_workers bigint,
    probe_reservations bigint,
    normal_probe_reservations bigint,
    available_workers bigint
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
WITH policy AS (
    SELECT state.max_active_workers, state.max_normal_workers
      FROM autopilot.project_planner_state AS state
     WHERE state.singleton
), active AS (
    SELECT count(*) AS total,
           count(*) FILTER (WHERE task.priority <> 0) AS normal
      FROM autopilot.task AS task
     WHERE task.goal_type IN (
               'CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1'
           )
       AND task.status IN (
               'NEW', 'VALIDATING', 'READY', 'RUNNING',
               'WAITING_EXTERNAL', 'EVALUATING'
           )
), probes AS (
    SELECT count(*) AS total,
           count(*) FILTER (WHERE item.priority <> 0) AS normal
      FROM autopilot.project_work_item AS item
     WHERE item.state IN ('READY', 'BLOCKED')
       AND item.probe_lease_owner IS NOT NULL
       AND item.probe_lease_until >= now()
)
SELECT policy.max_active_workers,
       policy.max_normal_workers,
       active.total,
       active.normal,
       probes.total,
       probes.normal,
       greatest(policy.max_active_workers::bigint - active.total - probes.total, 0)
  FROM policy CROSS JOIN active CROSS JOIN probes;
$$;

REVOKE ALL ON FUNCTION autopilot.role_worker_capacity_snapshot()
FROM PUBLIC, autopilot_callback;
GRANT EXECUTE ON FUNCTION autopilot.role_worker_capacity_snapshot()
TO autopilot_runtime, autopilot_runtime_principal;

-- Enforce the same limit at task creation so a direct internal caller cannot
-- bypass planner admission.  The materializer atomically releases its exact
-- probe reservation before this trigger admits the replacement task.
CREATE OR REPLACE FUNCTION autopilot.enforce_role_worker_capacity()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    capacity record;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.goal_type IN (
               'CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1'
           )
           AND OLD.status IN (
               'NEW', 'VALIDATING', 'READY', 'RUNNING',
               'WAITING_EXTERNAL', 'EVALUATING'
           ) THEN
            IF OLD.goal_type IS DISTINCT FROM NEW.goal_type
               OR OLD.priority IS DISTINCT FROM NEW.priority THEN
                RAISE EXCEPTION 'AUTOPILOT_ACTIVE_ROLE_IDENTITY_IMMUTABLE';
            END IF;
            IF NEW.status IN (
                'NEW', 'VALIDATING', 'READY', 'RUNNING',
                'WAITING_EXTERNAL', 'EVALUATING'
            ) THEN
                RETURN NEW;
            END IF;
        END IF;
    END IF;

    IF NEW.goal_type NOT IN (
           'CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1'
       )
       OR NEW.status NOT IN (
           'NEW', 'VALIDATING', 'READY', 'RUNNING',
           'WAITING_EXTERNAL', 'EVALUATING'
       )
       THEN
        RETURN NEW;
    END IF;

    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_WORKER_ISOLATION_UNSUPPORTED';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended('autopilot.role-worker-capacity-v1', 0)
    );
    SELECT * INTO capacity FROM autopilot.role_worker_capacity_snapshot();

    IF capacity.active_workers + capacity.probe_reservations
           >= capacity.max_active_workers THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_WORKER_CAPACITY_EXHAUSTED';
    ELSIF NEW.priority <> 0
          AND capacity.active_normal_workers
                + capacity.normal_probe_reservations
              >= capacity.max_normal_workers THEN
        RAISE EXCEPTION 'AUTOPILOT_P0_WORKER_RESERVE_REQUIRED';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER autopilot_role_worker_capacity
BEFORE INSERT OR UPDATE OF goal_type, status, priority
ON autopilot.task
FOR EACH ROW EXECUTE FUNCTION autopilot.enforce_role_worker_capacity();

REVOKE ALL ON FUNCTION autopilot.enforce_role_worker_capacity() FROM PUBLIC;

-- Convert a probe reservation into a task while holding the capacity fence.
-- Clearing the exact lease and inserting the task are one transaction: if
-- admission fails, PostgreSQL restores the lease automatically.  This also
-- closes the legacy NULL-worker/epoch path in the prior materializer.
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
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_WORKER_ISOLATION_UNSUPPORTED';
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended('autopilot.role-worker-capacity-v1', 0)
    );

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

    -- Consume this exact reservation before the task trigger counts capacity.
    UPDATE autopilot.project_work_item
       SET probe_lease_owner = NULL,
           probe_lease_until = NULL,
           updated_at = now()
     WHERE work_item_id = item.work_item_id
       AND probe_lease_owner = p_worker_id
       AND probe_lease_epoch = p_lease_epoch
       AND probe_lease_until >= now();
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_PROBE_FENCED';
    END IF;

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
    capacity record;
BEGIN
    IF length(COALESCE(p_worker_id, '')) NOT BETWEEN 1 AND 256
       OR p_lease_seconds NOT BETWEEN 30 AND 300 THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_PROBE_LEASE_INVALID';
    END IF;
    IF NOT (SELECT enabled FROM autopilot.project_planner_state WHERE singleton) THEN
        RETURN;
    END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_WORKER_ISOLATION_UNSUPPORTED';
    END IF;

    -- Serialize capacity admission.  Valid probe leases are reservations, so
    -- concurrent planner processes cannot both take the sixth slot.
    PERFORM pg_advisory_xact_lock(
        hashtextextended('autopilot.role-worker-capacity-v1', 0)
    );
    SELECT * INTO capacity FROM autopilot.role_worker_capacity_snapshot();

    IF capacity.active_workers + capacity.probe_reservations
           >= capacity.max_active_workers THEN
        UPDATE autopilot.project_planner_state
           SET decision_count = decision_count + 1,
               last_decision_code = 'WAITING_FOR_WORKER_CAPACITY',
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
       AND (
           item.priority = 0
           OR capacity.active_normal_workers
                + capacity.normal_probe_reservations
              < capacity.max_normal_workers
       )
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
                   WHEN capacity.active_normal_workers
                          + capacity.normal_probe_reservations
                        >= capacity.max_normal_workers
                    AND EXISTS (
                        SELECT 1
                          FROM autopilot.project_work_item AS item
                          JOIN autopilot.role_chat_registry AS chat
                            ON chat.role_id = item.role AND chat.enabled
                         WHERE item.state IN ('READY', 'BLOCKED')
                           AND item.priority <> 0
                           AND item.not_before <= now()
                           AND (
                               item.probe_lease_until IS NULL
                               OR item.probe_lease_until < now()
                           )
                    ) THEN 'WAITING_FOR_P0_RESERVED_WORKER'
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
                          AND (
                              item.probe_lease_until IS NULL
                              OR item.probe_lease_until < now()
                          )
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
'Atomically admits project work into six worker slots: five normal plus one P0 reserve; every enabled role routes to the single existing GitHub event-runtime conversation.';
COMMENT ON FUNCTION autopilot.role_worker_capacity_snapshot() IS
'Read-only authoritative capacity snapshot; the dispatcher and project control chat are excluded from the six worker slots.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0331_autopilot_six_worker_capacity')
ON CONFLICT DO NOTHING;

COMMIT;
