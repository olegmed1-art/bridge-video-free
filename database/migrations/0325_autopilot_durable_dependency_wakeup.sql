\set ON_ERROR_STOP on
BEGIN;

-- Make dependency wake-up durable.  LISTEN/NOTIFY remains a latency hint, but
-- the queue state itself is now sufficient for recovery polling after a missed
-- notification or resident restart.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0324_autopilot_project_planner'
    ) OR to_regclass('autopilot.role_registry') IS NULL
       OR to_regprocedure(
           'autopilot.register_universal_work_item(text,text,text,text,integer,integer,jsonb,text,text,text)'
       ) IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_DURABLE_WAKEUP_REQUIRES_DYNAMIC_PROJECT_PLANNER';
    END IF;
END $$;

ALTER TABLE autopilot.project_work_item
    DROP CONSTRAINT project_work_item_state_check;
ALTER TABLE autopilot.project_work_item
    ADD CONSTRAINT project_work_item_state_check CHECK (
        state IN (
            'READY', 'WAITING_DEPENDENCY', 'ACTIVE',
            'BLOCKED', 'DONE', 'PAUSED'
        )
    );

CREATE OR REPLACE FUNCTION autopilot.set_project_work_dependency_state()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    dependency_state text;
BEGIN
    IF NEW.depends_on_work_item_id IS NULL THEN
        IF NEW.state = 'WAITING_DEPENDENCY' THEN
            NEW.state := 'READY';
            NEW.not_before := now();
        END IF;
        RETURN NEW;
    END IF;

    SELECT item.state INTO dependency_state
      FROM autopilot.project_work_item AS item
     WHERE item.work_item_id = NEW.depends_on_work_item_id;

    IF dependency_state IS NULL THEN
        -- Preserve the foreign-key error as the authoritative rejection.
        RETURN NEW;
    ELSIF dependency_state = 'DONE' THEN
        IF NEW.state = 'WAITING_DEPENDENCY' THEN
            NEW.state := 'READY';
            NEW.not_before := now();
        END IF;
    ELSIF TG_OP = 'INSERT' OR NEW.depends_on_work_item_id IS DISTINCT FROM OLD.depends_on_work_item_id THEN
        NEW.state := 'WAITING_DEPENDENCY';
        NEW.completed_at := NULL;
        NEW.probe_lease_owner := NULL;
        NEW.probe_lease_until := NULL;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER autopilot_project_work_dependency_state
BEFORE INSERT OR UPDATE OF depends_on_work_item_id
ON autopilot.project_work_item
FOR EACH ROW EXECUTE FUNCTION autopilot.set_project_work_dependency_state();

CREATE OR REPLACE FUNCTION autopilot.release_project_work_dependents()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    released_count integer;
BEGIN
    IF OLD.state IS DISTINCT FROM 'DONE' AND NEW.state = 'DONE' THEN
        UPDATE autopilot.project_work_item AS child
           SET state = 'READY',
               not_before = now(),
               probe_lease_owner = NULL,
               probe_lease_until = NULL,
               updated_at = now()
         WHERE child.depends_on_work_item_id = NEW.work_item_id
           AND child.state = 'WAITING_DEPENDENCY';
        GET DIAGNOSTICS released_count = ROW_COUNT;

        IF released_count > 0 THEN
            UPDATE autopilot.project_planner_state
               SET decision_count = decision_count + 1,
                   last_decision_code = 'DEPENDENT_WORK_READY',
                   last_work_item_id = NEW.work_item_id,
                   last_decision_at = now()
             WHERE singleton;
            PERFORM pg_notify('autopilot_ready', 'project-work-dependent-ready');
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER autopilot_project_work_dependency_release
AFTER UPDATE OF state ON autopilot.project_work_item
FOR EACH ROW EXECUTE FUNCTION autopilot.release_project_work_dependents();

-- Reconcile legacy rows that looked READY while their prerequisite was not.
UPDATE autopilot.project_work_item AS child
   SET state = 'WAITING_DEPENDENCY',
       probe_lease_owner = NULL,
       probe_lease_until = NULL,
       completed_at = NULL,
       updated_at = now()
  FROM autopilot.project_work_item AS dependency
 WHERE child.depends_on_work_item_id = dependency.work_item_id
   AND dependency.state <> 'DONE'
   AND child.state IN ('READY', 'BLOCKED');

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

CREATE OR REPLACE FUNCTION autopilot.project_work_transport_retryable(
    p_result_code text
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT COALESCE(p_result_code IN (
        'STALE_RETRY_BUDGET_EXHAUSTED',
        'ROLE_DISPATCH_CLAIM_EXPIRED',
        'ROLE_DISPATCH_DELIVERY_EXHAUSTED',
        'ROLE_CALLBACK_DEADLINE_EXCEEDED',
        'AUTOPILOT_TRANSIENT_DATABASE_ERROR'
    ), false);
$$;

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

    -- A semantic/safety blocker remains pinned to its exact head.  Only
    -- transport failures may be retried on an unchanged head after the
    -- bounded cooldown; the generation suffix prevents task-key aliasing.
    IF item.state = 'BLOCKED'
       AND item.last_observed_head_sha = p_observed_head_sha
       AND NOT autopilot.project_work_transport_retryable(item.result_code) THEN
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
               WHEN item.state = 'BLOCKED' THEN 'TRANSPORT_RETRY_MATERIALIZED'
               ELSE 'TASK_MATERIALIZED'
           END,
           last_work_item_id = item.work_item_id,
           last_decision_at = now()
     WHERE singleton;
    RETURN QUERY SELECT created_task.task_id, 'ACTIVE'::text, created_task.created;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.set_project_work_dependency_state() FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.release_project_work_dependents() FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.project_work_transport_retryable(text) FROM PUBLIC;

COMMENT ON FUNCTION autopilot.release_project_work_dependents() IS
'Durably promotes dependency-blocked work to READY after the prerequisite reaches DONE; NOTIFY is only a latency hint.';
COMMENT ON FUNCTION autopilot.claim_project_work_probe(text,integer) IS
'Claims one durable eligible lane and records precise waiting reasons instead of conflating cooldowns and dependencies with idle.';
COMMENT ON FUNCTION autopilot.materialize_project_work_probe(uuid,text,bigint,boolean,text) IS
'Creates generation-scoped exact-head tasks; unchanged-head retries are limited to explicit transport failures.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0325_autopilot_durable_dependency_wakeup')
ON CONFLICT DO NOTHING;

COMMIT;
