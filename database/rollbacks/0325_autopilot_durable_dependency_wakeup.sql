\set ON_ERROR_STOP on
BEGIN;

DROP TRIGGER IF EXISTS autopilot_project_work_dependency_release
    ON autopilot.project_work_item;
DROP TRIGGER IF EXISTS autopilot_project_work_dependency_state
    ON autopilot.project_work_item;
DROP FUNCTION IF EXISTS autopilot.release_project_work_dependents();
DROP FUNCTION IF EXISTS autopilot.set_project_work_dependency_state();

UPDATE autopilot.project_work_item
   SET state = 'READY', updated_at = now()
 WHERE state = 'WAITING_DEPENDENCY';

ALTER TABLE autopilot.project_work_item
    DROP CONSTRAINT project_work_item_state_check;
ALTER TABLE autopilot.project_work_item
    ADD CONSTRAINT project_work_item_state_check CHECK (
        state IN ('READY', 'ACTIVE', 'BLOCKED', 'DONE', 'PAUSED')
    );

-- Restore the 0324 decision semantics.  Dependency eligibility remains
-- enforced by the join, so rollback does not weaken the safety gate.
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
         WHERE task.goal_type IN ('CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1')
           AND task.status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')
    ) THEN
        UPDATE autopilot.project_planner_state
           SET decision_count=decision_count+1,
               last_decision_code='WAITING_FOR_ACTIVE_ROLE_TASK',
               last_work_item_id=NULL,last_decision_at=now()
         WHERE singleton;
        RETURN;
    END IF;
    SELECT item.* INTO selected
      FROM autopilot.project_work_item AS item
      LEFT JOIN autopilot.project_work_item AS dependency
        ON dependency.work_item_id=item.depends_on_work_item_id
     WHERE item.state IN ('READY','BLOCKED') AND item.not_before<=now()
       AND (item.probe_lease_until IS NULL OR item.probe_lease_until<now())
       AND (item.depends_on_work_item_id IS NULL OR dependency.state='DONE')
     ORDER BY CASE item.state WHEN 'READY' THEN 0 ELSE 1 END,
              item.priority,item.not_before,item.created_at
     FOR UPDATE OF item SKIP LOCKED LIMIT 1;
    IF NOT FOUND THEN
        UPDATE autopilot.project_planner_state
           SET decision_count=decision_count+1,
               last_decision_code=CASE
                   WHEN NOT EXISTS (SELECT 1 FROM autopilot.project_work_item)
                       THEN 'IDLE_NO_REGISTERED_WORK'
                   WHEN EXISTS (SELECT 1 FROM autopilot.project_work_item
                                 WHERE state IN ('READY','BLOCKED','ACTIVE'))
                       THEN 'IDLE_NO_ELIGIBLE_TASK'
                   ELSE 'PROJECT_DONE' END,
               last_work_item_id=NULL,last_decision_at=now()
         WHERE singleton;
        RETURN;
    END IF;
    UPDATE autopilot.project_work_item AS item
       SET probe_lease_owner=p_worker_id,
           probe_lease_epoch=item.probe_lease_epoch+1,
           probe_lease_until=now()+make_interval(secs=>p_lease_seconds),
           updated_at=now()
     WHERE item.work_item_id=selected.work_item_id RETURNING * INTO selected;
    UPDATE autopilot.project_planner_state
       SET decision_count=decision_count+1,last_decision_code='PROBE_CLAIMED',
           last_work_item_id=selected.work_item_id,last_decision_at=now()
     WHERE singleton;
    RETURN QUERY SELECT selected.work_item_id,selected.work_key,selected.repository,
        selected.role,selected.target_pr,selected.state,
        selected.last_observed_head_sha,selected.probe_lease_epoch;
END;
$$;

DROP FUNCTION IF EXISTS autopilot.project_work_transport_retryable(text);

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

DELETE FROM public.schema_migration
 WHERE migration_key = '0325_autopilot_durable_dependency_wakeup';

COMMIT;
