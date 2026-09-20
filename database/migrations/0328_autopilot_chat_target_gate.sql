\set ON_ERROR_STOP on
BEGIN;

-- A role may be enabled before an existing ChatGPT chat has been registered
-- for it.  Keep that work durable, but do not materialize a dispatch that can
-- never pass the delivery-proof binding.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0327_autopilot_delivery_proof'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CHAT_TARGET_GATE_REQUIRES_0327';
    END IF;
END $$;

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

INSERT INTO public.schema_migration(migration_key)
VALUES ('0328_autopilot_chat_target_gate')
ON CONFLICT DO NOTHING;

COMMIT;
