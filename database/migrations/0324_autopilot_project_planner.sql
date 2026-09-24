\set ON_ERROR_STOP on
BEGIN;

-- Keep the project moving after every terminal role result.  The durable work
-- catalog is separate from the execution queue: a single bounded planner probe
-- resolves the current public PR head, then materializes one exact-head task.
-- A blocked item is never redispatched on the same head, while unrelated READY
-- items remain eligible.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0323_autopilot_failure_continuation'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_PLANNER_REQUIRES_0323';
    END IF;
END $$;

CREATE TABLE autopilot.project_work_item (
    work_item_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_key text NOT NULL UNIQUE CHECK (
        work_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$'
    ),
    repository text NOT NULL DEFAULT 'olegmed1-art/bridge-video-free' CHECK (
        repository = 'olegmed1-art/bridge-video-free'
    ),
    mailbox_pr integer NOT NULL DEFAULT 1150 CHECK (mailbox_pr = 1150),
    role text NOT NULL CHECK (
        role IN ('RECOGNIZER', 'VIDEO', 'BOOKS', 'KNOWLEDGE')
    ),
    target_pr integer NOT NULL CHECK (target_pr BETWEEN 1 AND 1000000),
    priority smallint NOT NULL DEFAULT 20 CHECK (
        priority IN (0, 10, 20, 30)
    ),
    state text NOT NULL DEFAULT 'READY' CHECK (
        state IN ('READY', 'ACTIVE', 'BLOCKED', 'DONE', 'PAUSED')
    ),
    depends_on_work_item_id uuid REFERENCES autopilot.project_work_item(work_item_id),
    generation integer NOT NULL DEFAULT 0 CHECK (
        generation BETWEEN 0 AND 333332
    ),
    last_observed_head_sha text CHECK (
        last_observed_head_sha IS NULL
        OR last_observed_head_sha ~ '^[0-9a-f]{40}$'
    ),
    last_task_id uuid UNIQUE REFERENCES autopilot.task(task_id),
    result_code text CHECK (
        result_code IS NULL OR result_code ~ '^[A-Z][A-Z0-9_]{0,63}$'
    ),
    result_summary text CHECK (
        result_summary IS NULL OR (
            length(result_summary) BETWEEN 1 AND 160
            AND result_summary !~ '[[:cntrl:]]'
        )
    ),
    not_before timestamptz NOT NULL DEFAULT now(),
    probe_lease_owner text,
    probe_lease_epoch bigint NOT NULL DEFAULT 0 CHECK (probe_lease_epoch >= 0),
    probe_lease_until timestamptz,
    created_by text NOT NULL CHECK (length(created_by) BETWEEN 1 AND 256),
    source text NOT NULL CHECK (length(source) BETWEEN 1 AND 256),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CONSTRAINT project_work_item_not_self_dependent CHECK (
        depends_on_work_item_id IS NULL
        OR depends_on_work_item_id <> work_item_id
    ),
    CONSTRAINT project_work_item_lease_shape CHECK (
        (probe_lease_owner IS NULL AND probe_lease_until IS NULL)
        OR (probe_lease_owner IS NOT NULL AND probe_lease_until IS NOT NULL)
    ),
    CONSTRAINT project_work_item_terminal_shape CHECK (
        (state = 'DONE' AND completed_at IS NOT NULL)
        OR (state <> 'DONE' AND completed_at IS NULL)
    )
);

CREATE INDEX project_work_item_ready_idx
    ON autopilot.project_work_item (
        priority, not_before, created_at
    )
    WHERE state IN ('READY', 'BLOCKED');

CREATE TABLE autopilot.project_work_task (
    work_item_id uuid NOT NULL REFERENCES autopilot.project_work_item(work_item_id),
    task_id uuid NOT NULL UNIQUE REFERENCES autopilot.task(task_id),
    run_kind text NOT NULL CHECK (run_kind IN ('AUDIT', 'REPAIR', 'VERIFY')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (work_item_id, task_id)
);

CREATE TABLE autopilot.project_planner_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    enabled boolean NOT NULL DEFAULT true,
    decision_count bigint NOT NULL DEFAULT 0 CHECK (decision_count >= 0),
    last_decision_code text NOT NULL DEFAULT 'IDLE_NO_REGISTERED_WORK' CHECK (
        last_decision_code ~ '^[A-Z][A-Z0-9_]{0,63}$'
    ),
    last_work_item_id uuid REFERENCES autopilot.project_work_item(work_item_id),
    last_decision_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO autopilot.project_planner_state(singleton) VALUES (true);

REVOKE ALL ON TABLE autopilot.project_work_item FROM PUBLIC;
REVOKE ALL ON TABLE autopilot.project_work_task FROM PUBLIC;
REVOKE ALL ON TABLE autopilot.project_planner_state FROM PUBLIC;
REVOKE ALL ON TABLE autopilot.project_work_item,
    autopilot.project_work_task, autopilot.project_planner_state
    FROM autopilot_runtime, autopilot_runtime_principal, autopilot_callback;

CREATE OR REPLACE FUNCTION autopilot.register_project_work_item(
    p_work_key text,
    p_role text,
    p_target_pr integer,
    p_priority integer DEFAULT 20,
    p_depends_on_work_key text DEFAULT NULL,
    p_created_by text DEFAULT 'CHATGPT_DIRECTOR',
    p_source text DEFAULT 'SCHOOL_WORKING_BOARD'
)
RETURNS TABLE(work_item_id uuid, state text, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    existing autopilot.project_work_item;
    inserted autopilot.project_work_item;
    dependency_id uuid;
BEGIN
    IF p_work_key IS NULL
       OR p_work_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$'
       OR p_role IS NULL
       OR p_role NOT IN ('RECOGNIZER', 'VIDEO', 'BOOKS', 'KNOWLEDGE')
       OR p_target_pr IS NULL
       OR p_target_pr NOT BETWEEN 1 AND 1000000
       OR p_priority IS NULL
       OR p_priority NOT IN (0, 10, 20, 30)
       OR length(COALESCE(p_created_by, '')) NOT BETWEEN 1 AND 256
       OR length(COALESCE(p_source, '')) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_WORK_ITEM_INVALID';
    END IF;
    IF p_depends_on_work_key IS NOT NULL THEN
        IF p_depends_on_work_key = p_work_key THEN
            RAISE EXCEPTION 'AUTOPILOT_PROJECT_WORK_SELF_DEPENDENCY';
        END IF;
        SELECT item.work_item_id INTO dependency_id
          FROM autopilot.project_work_item AS item
         WHERE item.work_key = p_depends_on_work_key;
        IF dependency_id IS NULL THEN
            RAISE EXCEPTION 'AUTOPILOT_PROJECT_WORK_DEPENDENCY_UNKNOWN';
        END IF;
    END IF;

    SELECT * INTO existing
      FROM autopilot.project_work_item AS item
     WHERE item.work_key = p_work_key;
    IF FOUND THEN
        IF existing.role <> p_role
           OR existing.target_pr <> p_target_pr
           OR existing.priority <> p_priority::smallint
           OR existing.depends_on_work_item_id IS DISTINCT FROM dependency_id
           OR existing.created_by <> p_created_by
           OR existing.source <> p_source THEN
            RAISE EXCEPTION 'AUTOPILOT_PROJECT_WORK_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN QUERY SELECT existing.work_item_id, existing.state, false;
        RETURN;
    END IF;

    INSERT INTO autopilot.project_work_item (
        work_key, role, target_pr, priority, depends_on_work_item_id,
        created_by, source
    ) VALUES (
        p_work_key, p_role, p_target_pr, p_priority::smallint, dependency_id,
        p_created_by, p_source
    )
    RETURNING * INTO inserted;

    UPDATE autopilot.project_planner_state
       SET decision_count = decision_count + 1,
           last_decision_code = 'WORK_REGISTERED',
           last_work_item_id = inserted.work_item_id,
           last_decision_at = now()
     WHERE singleton;
    PERFORM pg_notify('autopilot_ready', 'project-work-registered');
    RETURN QUERY SELECT inserted.work_item_id, inserted.state, true;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.register_project_work_item(
    text,text,integer,integer,text,text,text
) FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;

CREATE OR REPLACE FUNCTION autopilot.adopt_project_work_task(
    p_work_key text,
    p_task_id uuid,
    p_created_by text DEFAULT 'CHATGPT_TECHNICAL_SUPERVISOR',
    p_source text DEFAULT 'PROJECT_STATE_RECONCILIATION'
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    task_row autopilot.task;
    existing_id uuid;
    item_id uuid;
    item_state text;
    item_result_code text;
    item_result_summary text;
BEGIN
    SELECT * INTO task_row FROM autopilot.task AS task
     WHERE task.task_id = p_task_id;
    IF NOT FOUND
       OR task_row.goal_type <> 'CHATGPT_ROLE_DISPATCH_V1'
       OR task_row.status NOT IN ('DONE', 'FAILED_CLOSED', 'OWNER_REQUIRED')
       OR task_row.goal_json->>'repository' <> 'olegmed1-art/bridge-video-free'
       OR task_row.goal_json->>'mailbox_pr' <> '1150' THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_ADOPTION_TASK_INVALID';
    END IF;

    SELECT work_item_id INTO existing_id
      FROM autopilot.project_work_task
     WHERE task_id = p_task_id;
    IF existing_id IS NOT NULL THEN
        RETURN existing_id;
    END IF;

    SELECT registered.work_item_id INTO item_id
      FROM autopilot.register_project_work_item(
          p_work_key,
          task_row.goal_json->>'role',
          (task_row.goal_json->>'target_pr')::integer,
          task_row.priority,
          NULL,
          p_created_by,
          p_source
      ) AS registered;

    IF task_row.status = 'DONE' THEN
        item_state := 'DONE';
        item_result_code := COALESCE(
            task_row.safe_summary_json->>'result_code',
            task_row.terminal_reason_code,
            'TASK_DONE'
        );
        item_result_summary := COALESCE(
            task_row.safe_summary_json->>'summary',
            'Existing project task completed.'
        );
    ELSE
        item_state := 'BLOCKED';
        item_result_code := COALESCE(
            task_row.safe_summary_json->>'result_code',
            task_row.terminal_reason_code,
            'TASK_BLOCKED'
        );
        item_result_summary := COALESCE(
            task_row.safe_summary_json->>'summary',
            'Existing project task is blocked.'
        );
    END IF;

    INSERT INTO autopilot.project_work_task(work_item_id, task_id, run_kind)
    VALUES (item_id, p_task_id, 'AUDIT');
    UPDATE autopilot.project_work_item
       SET state = item_state,
           generation = 1,
           last_observed_head_sha = task_row.goal_json->>'expected_head_sha',
           last_task_id = p_task_id,
           result_code = left(item_result_code, 64),
           result_summary = left(item_result_summary, 160),
           not_before = CASE
               WHEN item_state = 'BLOCKED' THEN now() + interval '15 minutes'
               ELSE not_before
           END,
           completed_at = CASE WHEN item_state = 'DONE' THEN now() ELSE NULL END,
           updated_at = now()
     WHERE work_item_id = item_id;
    PERFORM pg_notify('autopilot_ready', 'project-work-adopted');
    RETURN item_id;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.adopt_project_work_task(text,uuid,text,text)
    FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;

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

    -- The project planner never fans out role work.  It waits until the current
    -- audit/repair/verification has reached a terminal state, then advances.
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
      LEFT JOIN autopilot.project_work_item AS dependency
        ON dependency.work_item_id = item.depends_on_work_item_id
     WHERE item.state IN ('READY', 'BLOCKED')
       AND item.not_before <= now()
       AND (item.probe_lease_until IS NULL OR item.probe_lease_until < now())
       AND (
           item.depends_on_work_item_id IS NULL
           OR dependency.state = 'DONE'
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
                   WHEN NOT EXISTS (
                       SELECT 1 FROM autopilot.project_work_item
                   ) THEN 'IDLE_NO_REGISTERED_WORK'
                   WHEN EXISTS (
                       SELECT 1 FROM autopilot.project_work_item
                        WHERE state IN ('READY', 'BLOCKED', 'ACTIVE')
                   ) THEN 'IDLE_NO_ELIGIBLE_TASK'
                   ELSE 'PROJECT_DONE'
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

REVOKE ALL ON FUNCTION autopilot.claim_project_work_probe(text,integer)
    FROM PUBLIC, autopilot_callback;
GRANT EXECUTE ON FUNCTION autopilot.claim_project_work_probe(text,integer)
    TO autopilot_runtime, autopilot_runtime_principal;

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

    IF item.state = 'BLOCKED'
       AND item.last_observed_head_sha = p_observed_head_sha THEN
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

    IF item.generation > 333332 THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_GENERATION_EXHAUSTED';
    END IF;
    next_task_key := 'project-work:' || item.work_key || ':' ||
        substr(p_observed_head_sha, 1, 12);
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
           last_decision_code = 'TASK_MATERIALIZED',
           last_work_item_id = item.work_item_id,
           last_decision_at = now()
     WHERE singleton;
    RETURN QUERY SELECT created_task.task_id, 'ACTIVE'::text, created_task.created;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.materialize_project_work_probe(
    uuid,text,bigint,boolean,text
) FROM PUBLIC, autopilot_callback;
GRANT EXECUTE ON FUNCTION autopilot.materialize_project_work_probe(
    uuid,text,bigint,boolean,text
) TO autopilot_runtime, autopilot_runtime_principal;

CREATE OR REPLACE FUNCTION autopilot.fail_project_work_probe(
    p_work_item_id uuid,
    p_worker_id text,
    p_lease_epoch bigint,
    p_error_code text,
    p_retryable boolean
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    item autopilot.project_work_item;
    new_state text;
BEGIN
    IF p_error_code !~ '^[A-Z][A-Z0-9_]{0,63}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_PROBE_ERROR_INVALID';
    END IF;
    SELECT * INTO item FROM autopilot.project_work_item AS work
     WHERE work.work_item_id = p_work_item_id FOR UPDATE;
    IF NOT FOUND
       OR item.probe_lease_owner IS DISTINCT FROM p_worker_id
       OR item.probe_lease_epoch <> p_lease_epoch THEN
        RETURN 'FENCED';
    END IF;
    new_state := CASE WHEN p_retryable THEN item.state ELSE 'PAUSED' END;
    UPDATE autopilot.project_work_item
       SET state = new_state,
           result_code = p_error_code,
           result_summary = CASE
               WHEN p_retryable THEN 'Project head probe will retry.'
               ELSE 'Project head probe failed closed.'
           END,
           not_before = CASE
               WHEN p_retryable THEN now() + interval '60 seconds'
               ELSE not_before
           END,
           probe_lease_owner = NULL,
           probe_lease_until = NULL,
           updated_at = now()
     WHERE work_item_id = item.work_item_id;
    UPDATE autopilot.project_planner_state
       SET decision_count = decision_count + 1,
           last_decision_code = CASE
               WHEN p_retryable THEN 'PROBE_RETRY_SCHEDULED'
               ELSE 'PROBE_FAILED_CLOSED'
           END,
           last_work_item_id = item.work_item_id,
           last_decision_at = now()
     WHERE singleton;
    PERFORM pg_notify('autopilot_ready', 'project-probe-finished');
    RETURN new_state;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.fail_project_work_probe(
    uuid,text,bigint,text,boolean
) FROM PUBLIC, autopilot_callback;
GRANT EXECUTE ON FUNCTION autopilot.fail_project_work_probe(
    uuid,text,bigint,text,boolean
) TO autopilot_runtime, autopilot_runtime_principal;

CREATE OR REPLACE FUNCTION autopilot.on_project_work_followup()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    mapped_item_id uuid;
    mapped_kind text;
BEGIN
    SELECT mapping.work_item_id INTO mapped_item_id
      FROM autopilot.project_work_task AS mapping
     WHERE mapping.task_id = NEW.parent_task_id;
    IF mapped_item_id IS NULL THEN
        RETURN NEW;
    END IF;
    mapped_kind := CASE NEW.followup_kind
        WHEN 'REPAIR' THEN 'REPAIR'
        WHEN 'VERIFY' THEN 'VERIFY'
        ELSE 'AUDIT'
    END;
    INSERT INTO autopilot.project_work_task(work_item_id, task_id, run_kind)
    VALUES (mapped_item_id, NEW.followup_task_id, mapped_kind)
    ON CONFLICT ON CONSTRAINT project_work_task_task_id_key DO NOTHING;
    UPDATE autopilot.project_work_item
       SET state = 'ACTIVE',
           last_task_id = NEW.followup_task_id,
           result_code = NULL,
           result_summary = NULL,
           updated_at = now()
     WHERE work_item_id = mapped_item_id;
    RETURN NEW;
END;
$$;

CREATE TRIGGER autopilot_project_work_followup
AFTER INSERT ON autopilot.role_dispatch_followup
FOR EACH ROW EXECUTE FUNCTION autopilot.on_project_work_followup();

REVOKE ALL ON FUNCTION autopilot.on_project_work_followup() FROM PUBLIC;

CREATE OR REPLACE FUNCTION autopilot.on_project_work_task_terminal()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    mapping autopilot.project_work_task;
    active_task_id uuid;
    terminal_code text;
    terminal_summary text;
BEGIN
    IF OLD.status = NEW.status
       OR NEW.status NOT IN (
           'DONE', 'FAILED_CLOSED', 'OWNER_REQUIRED',
           'BUDGET_STOP', 'CANCELLED'
       ) THEN
        RETURN NEW;
    END IF;
    SELECT * INTO mapping FROM autopilot.project_work_task AS link
     WHERE link.task_id = NEW.task_id;
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    -- The 0323 trigger runs first and may already have created REPAIR/VERIFY.
    SELECT task.task_id INTO active_task_id
      FROM autopilot.project_work_task AS link
      JOIN autopilot.task AS task ON task.task_id = link.task_id
     WHERE link.work_item_id = mapping.work_item_id
       AND task.status IN (
           'NEW', 'VALIDATING', 'READY', 'RUNNING',
           'WAITING_EXTERNAL', 'EVALUATING'
       )
     ORDER BY link.created_at DESC
     LIMIT 1;

    IF active_task_id IS NOT NULL THEN
        UPDATE autopilot.project_work_item
           SET state = 'ACTIVE',
               last_task_id = active_task_id,
               updated_at = now()
         WHERE work_item_id = mapping.work_item_id;
        RETURN NEW;
    END IF;

    terminal_code := COALESCE(
        NEW.safe_summary_json->>'result_code',
        NEW.terminal_reason_code,
        CASE WHEN NEW.status = 'DONE' THEN 'TASK_DONE' ELSE 'TASK_BLOCKED' END
    );
    terminal_summary := COALESCE(
        NEW.safe_summary_json->>'summary',
        CASE
            WHEN NEW.status = 'DONE' THEN 'Project work completed.'
            ELSE 'Project work stopped at a retained terminal result.'
        END
    );

    IF NEW.status = 'DONE' AND mapping.run_kind IN ('AUDIT', 'VERIFY') THEN
        UPDATE autopilot.project_work_item
           SET state = 'DONE',
               last_task_id = NEW.task_id,
               result_code = left(terminal_code, 64),
               result_summary = left(terminal_summary, 160),
               completed_at = now(),
               updated_at = now()
         WHERE work_item_id = mapping.work_item_id;
    ELSE
        UPDATE autopilot.project_work_item
           SET state = 'BLOCKED',
               last_task_id = NEW.task_id,
               result_code = left(terminal_code, 64),
               result_summary = left(terminal_summary, 160),
               not_before = now() + interval '15 minutes',
               completed_at = NULL,
               updated_at = now()
         WHERE work_item_id = mapping.work_item_id;
    END IF;

    UPDATE autopilot.project_planner_state
       SET decision_count = decision_count + 1,
           last_decision_code = CASE
               WHEN NEW.status = 'DONE' AND mapping.run_kind IN ('AUDIT', 'VERIFY')
                   THEN 'WORK_ITEM_DONE'
               ELSE 'WORK_ITEM_BLOCKED_CONTINUE'
           END,
           last_work_item_id = mapping.work_item_id,
           last_decision_at = now()
     WHERE singleton;
    PERFORM pg_notify('autopilot_ready', 'project-work-terminal');
    RETURN NEW;
END;
$$;

-- PostgreSQL fires same-event triggers in name order.  The zz prefix ensures
-- 0323 has materialized and mapped any repair/verification before we decide
-- whether the work item is still active or terminally blocked.
CREATE TRIGGER zz_autopilot_project_work_task_terminal
AFTER UPDATE OF status ON autopilot.task
FOR EACH ROW EXECUTE FUNCTION autopilot.on_project_work_task_terminal();

REVOKE ALL ON FUNCTION autopilot.on_project_work_task_terminal() FROM PUBLIC;

COMMENT ON TABLE autopilot.project_work_item IS
'Durable bounded project backlog; blocked heads do not stop independent work.';
COMMENT ON FUNCTION autopilot.claim_project_work_probe(text,integer) IS
'Selects one dependency-eligible lane only when no role task is active.';
COMMENT ON FUNCTION autopilot.materialize_project_work_probe(uuid,text,bigint,boolean,text) IS
'Creates one exact-head role task or retains an unchanged blocked lane.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0324_autopilot_project_planner')
ON CONFLICT DO NOTHING;

COMMIT;
