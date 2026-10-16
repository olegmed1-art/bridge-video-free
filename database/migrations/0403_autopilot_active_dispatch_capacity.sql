\set ON_ERROR_STOP on
BEGIN;

-- Admission is paused by the coordinator for production application. This
-- transaction retains every outbox, task, callback and evidence row in place.
LOCK TABLE autopilot.project_work_item IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_mailbox_registry IN SHARE ROW EXCLUSIVE MODE;

DO $pre$
BEGIN
 IF NOT EXISTS (SELECT FROM public.schema_migration
                WHERE migration_key='0372_autopilot_audit_pass_alias')
    OR NOT EXISTS (SELECT FROM public.schema_migration
                   WHERE migration_key='0362_autopilot_target_pr_codex_context') THEN
   RAISE EXCEPTION 'AUTOPILOT_ACTIVE_QUEUE_PREREQUISITE_MISSING';
 END IF;
 IF EXISTS (
   SELECT FROM autopilot.role_dispatch_mailbox_registry r
   WHERE (SELECT count(*) FROM autopilot.role_dispatch_outbox o
          WHERE o.mailbox_pr=r.mailbox_pr
            AND o.status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED'))>r.max_dispatches
 ) THEN RAISE EXCEPTION 'AUTOPILOT_ACTIVE_QUEUE_BASELINE_OVER_CAPACITY'; END IF;
END $pre$;

CREATE TABLE autopilot.migration_0403_definition_backup (
 object_key text PRIMARY KEY, definition text NOT NULL
);
INSERT INTO autopilot.migration_0403_definition_backup
SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid)
FROM pg_proc p WHERE p.oid IN (
 'autopilot.enforce_role_dispatch_mailbox_capacity()'::regprocedure,
 'autopilot.mailbox_rotation_readiness()'::regprocedure,
 'autopilot.claim_next_task(text,integer)'::regprocedure,
 'autopilot.claim_project_work_probe(text,integer)'::regprocedure
);
INSERT INTO autopilot.migration_0403_definition_backup
VALUES ('health_view',pg_get_viewdef('public.autopilot_operational_health_signal',true));
REVOKE ALL ON autopilot.migration_0403_definition_backup FROM PUBLIC;

-- A logical archive needs no asynchronous move, duplicated record, cleanup
-- transaction or resettable counter. A committed terminal transition removes
-- the row from the working view and preserves the original FK/dedup target.
CREATE VIEW autopilot.role_dispatch_active AS
SELECT * FROM autopilot.role_dispatch_outbox
WHERE status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED');
CREATE VIEW autopilot.role_dispatch_archive AS
SELECT * FROM autopilot.role_dispatch_outbox
WHERE status IN ('CALLBACK_ACCEPTED','FAILED_CLOSED');
REVOKE ALL ON autopilot.role_dispatch_active,autopilot.role_dispatch_archive FROM PUBLIC;
CREATE INDEX role_dispatch_active_mailbox_idx
ON autopilot.role_dispatch_outbox(mailbox_pr)
WHERE status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED');

CREATE FUNCTION autopilot.guard_role_dispatch_history()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,autopilot AS $f$
BEGIN
 IF (NEW.dispatch_id,NEW.task_id,NEW.step_attempt_id,NEW.repository,NEW.mailbox_pr)
    IS DISTINCT FROM
    (OLD.dispatch_id,OLD.task_id,OLD.step_attempt_id,OLD.repository,OLD.mailbox_pr) THEN
   RAISE EXCEPTION 'AUTOPILOT_DISPATCH_IDENTITY_IMMUTABLE';
 END IF;
 IF OLD.status IN ('CALLBACK_ACCEPTED','FAILED_CLOSED')
    AND NEW.status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED') THEN
   RAISE EXCEPTION 'AUTOPILOT_TERMINAL_DISPATCH_REOPEN_FORBIDDEN';
 END IF;
 RETURN NEW;
END $f$;
REVOKE ALL ON FUNCTION autopilot.guard_role_dispatch_history() FROM PUBLIC;
CREATE TRIGGER role_dispatch_history_guard BEFORE UPDATE
ON autopilot.role_dispatch_outbox FOR EACH ROW
EXECUTE FUNCTION autopilot.guard_role_dispatch_history();

CREATE OR REPLACE FUNCTION autopilot.enforce_role_dispatch_mailbox_capacity()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,autopilot AS $f$
DECLARE mailbox autopilot.role_dispatch_mailbox_registry; used integer;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_ISOLATION_UNSUPPORTED';
 END IF;
 SELECT * INTO mailbox FROM autopilot.role_dispatch_mailbox_registry
 WHERE mailbox_pr=NEW.mailbox_pr FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'AUTOPILOT_MAILBOX_UNREGISTERED'; END IF;
 IF mailbox.lifecycle<>'ACTIVE' THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_RETAINED';
 END IF;
 -- Separate statement after the lock wait: READ COMMITTED must see the
 -- preceding admission's commit. AFTER INSERT includes this new row and does
 -- not run for ON CONFLICT DO NOTHING. An exception rolls back this admission.
 SELECT count(*) INTO used FROM autopilot.role_dispatch_active
 WHERE mailbox_pr=NEW.mailbox_pr;
 IF used>mailbox.max_dispatches THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_CAPACITY_REACHED';
 END IF;
 RETURN NEW;
END $f$;
DROP TRIGGER role_dispatch_mailbox_capacity_guard ON autopilot.role_dispatch_outbox;
CREATE TRIGGER role_dispatch_mailbox_capacity_guard AFTER INSERT
ON autopilot.role_dispatch_outbox FOR EACH ROW
EXECUTE FUNCTION autopilot.enforce_role_dispatch_mailbox_capacity();

-- No new runtime grants: these helpers are called only by existing definer
-- entry points. Include claimed tasks with no outbox as short reservations,
-- so concurrent claims cannot consume the same last available slot.
CREATE FUNCTION autopilot.role_dispatch_queue_available(p_mailbox_pr integer)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,autopilot AS $f$
 SELECT EXISTS (
   SELECT FROM autopilot.role_dispatch_mailbox_registry r
   WHERE r.mailbox_pr=p_mailbox_pr AND r.lifecycle='ACTIVE'
     AND (SELECT count(*) FROM autopilot.role_dispatch_active o
          WHERE o.mailbox_pr=r.mailbox_pr)
       + (SELECT count(*) FROM autopilot.task t
          WHERE t.goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
            AND t.status='RUNNING' AND t.goal_json->>'mailbox_pr'=r.mailbox_pr::text
            AND NOT EXISTS (SELECT FROM autopilot.role_dispatch_outbox o
                            WHERE o.task_id=t.task_id)) < r.max_dispatches
 )
$f$;
REVOKE ALL ON FUNCTION autopilot.role_dispatch_queue_available(integer) FROM PUBLIC;

DO $patch$
DECLARE source text; revised text;
 anchor text := E'    WITH candidate AS (\n';
 guard text := $guard$
    -- ACTIVE_QUEUE_0403: preserve READY tasks and their attempt budget while
    -- capacity is full. Serialize role claims before taking task row locks.
    IF current_setting('transaction_isolation')<>'read committed' THEN
      RAISE EXCEPTION 'AUTOPILOT_MAILBOX_ISOLATION_UNSUPPORTED';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('autopilot.role-worker-capacity-v1',0));
    PERFORM 1 FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE' FOR UPDATE;
$guard$;
BEGIN
 SELECT definition INTO STRICT source FROM autopilot.migration_0403_definition_backup
 WHERE object_key='autopilot.claim_next_task(text,integer)';
 IF position(anchor in source)=0 OR position('AND t.attempts < t.max_attempts' in source)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_ACTIVE_QUEUE_TASK_SOURCE_DRIFT';
 END IF;
 revised:=replace(source,anchor,guard||anchor);
 revised:=replace(revised,'AND t.attempts < t.max_attempts',
 $filter$AND t.attempts < t.max_attempts
           AND (t.goal_type NOT IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
                OR autopilot.role_dispatch_queue_available((t.goal_json->>'mailbox_pr')::integer))$filter$);
 EXECUTE revised;

 SELECT definition INTO STRICT source FROM autopilot.migration_0403_definition_backup
 WHERE object_key='autopilot.claim_project_work_probe(text,integer)';
 anchor:='SELECT * INTO capacity FROM autopilot.role_worker_capacity_snapshot();';
 IF position(anchor in source)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_ACTIVE_QUEUE_PLANNER_SOURCE_DRIFT';
 END IF;
 revised:=replace(source,anchor,$probe$
 PERFORM 1 FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE' FOR UPDATE;
 IF NOT EXISTS (SELECT FROM autopilot.role_dispatch_mailbox_registry r
                WHERE r.lifecycle='ACTIVE' AND autopilot.role_dispatch_queue_available(r.mailbox_pr)) THEN
   UPDATE autopilot.project_planner_state
   SET decision_count=decision_count+1,last_decision_code='WAITING_FOR_MAILBOX_CAPACITY',
       last_work_item_id=NULL,last_decision_at=now() WHERE singleton;
   RETURN;
 END IF;
 SELECT * INTO capacity FROM autopilot.role_worker_capacity_snapshot();$probe$);
 EXECUTE revised;
END $patch$;

-- Keep this existing signature/ACL for old clients. Values now describe the
-- working queue; rotation is an optional transport maintenance operation.
CREATE OR REPLACE FUNCTION autopilot.mailbox_rotation_readiness()
RETURNS TABLE(mailbox_pr integer,used_dispatches integer,max_dispatches integer,remaining integer,readiness text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,autopilot AS $f$
 SELECT r.mailbox_pr,count(o.dispatch_id)::integer,r.max_dispatches::integer,
        (r.max_dispatches-count(o.dispatch_id))::integer,
        CASE WHEN count(o.dispatch_id)>=r.max_dispatches THEN 'CAPACITY_REACHED'
             WHEN count(o.dispatch_id)>=ceil(r.max_dispatches*0.8)::integer THEN 'NEAR_CAPACITY'
             ELSE 'NORMAL' END
 FROM autopilot.role_dispatch_mailbox_registry r
 LEFT JOIN autopilot.role_dispatch_active o ON o.mailbox_pr=r.mailbox_pr
 WHERE r.lifecycle='ACTIVE' GROUP BY r.mailbox_pr,r.max_dispatches
$f$;

-- The old rotation-signal table is retained as historical observations. No
-- current reader or new admission treats its stale peak as live capacity.
CREATE OR REPLACE VIEW public.autopilot_operational_health_signal AS
WITH backlog AS (
 SELECT ps.last_decision_code,
        count(w.work_item_id) FILTER(WHERE w.state='PAUSED')::integer paused_count,
        count(w.work_item_id) FILTER(WHERE w.state IN ('READY','BLOCKED','ACTIVE','WAITING_DEPENDENCY'))::integer open_actionable_count
 FROM autopilot.project_planner_state ps LEFT JOIN autopilot.project_work_item w ON true
 WHERE ps.singleton GROUP BY ps.last_decision_code
), mailbox AS (SELECT * FROM autopilot.mailbox_rotation_readiness()),
e2e AS (SELECT * FROM autopilot.mailbox_e2e_acceptance()),
queue AS (
 SELECT r.mailbox_pr,r.activated_at,r.max_dispatches,
        count(o.dispatch_id) FILTER(WHERE o.status IN ('CALLBACK_ACCEPTED','FAILED_CLOSED'))::integer archived_dispatches,
        count(o.dispatch_id) FILTER(WHERE o.codex_command_pr=o.mailbox_pr)::integer legacy_mailbox_commands,
        count(o.dispatch_id) FILTER(WHERE o.status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED') AND
          CASE o.status
            WHEN 'CLAIMED' THEN o.claim_until IS NULL OR o.claim_until<now()
            WHEN 'SENT' THEN o.callback_deadline_at IS NULL OR o.callback_deadline_at<now()
            WHEN 'PUBLISHED' THEN o.delivery_deadline_at IS NULL OR o.delivery_deadline_at<now()
            WHEN 'DELIVERY_FAILED' THEN o.delivery_deadline_at IS NULL OR o.delivery_deadline_at<now()
            ELSE o.next_attempt_at<now()-interval '15 minutes'
              OR o.created_at<now()-interval '30 minutes'
          END)::integer stalled_dispatches
 FROM autopilot.role_dispatch_mailbox_registry r
 LEFT JOIN autopilot.role_dispatch_outbox o ON o.mailbox_pr=r.mailbox_pr
 WHERE r.lifecycle='ACTIVE' GROUP BY r.mailbox_pr
)
SELECT 'autopilot_planner_backlog'::text signal_key,
       CASE WHEN backlog.open_actionable_count>0 THEN 'critical'
            WHEN backlog.paused_count>0 THEN 'warning' ELSE 'ok' END::text severity,
       jsonb_build_object('planner_decision',backlog.last_decision_code,'paused_count',backlog.paused_count,'open_actionable_count',backlog.open_actionable_count) details,
       clock_timestamp() observed_at FROM backlog
UNION ALL
SELECT 'autopilot_mailbox_capacity',
       CASE mailbox.readiness WHEN 'CAPACITY_REACHED' THEN 'critical' WHEN 'NEAR_CAPACITY' THEN 'warning' ELSE 'ok' END,
       jsonb_build_object('mailbox_pr',mailbox.mailbox_pr,'used_dispatches',mailbox.used_dispatches,'max_dispatches',mailbox.max_dispatches,'remaining',mailbox.remaining,'readiness',mailbox.readiness,'archived_dispatches',queue.archived_dispatches),
       clock_timestamp() FROM mailbox JOIN queue USING(mailbox_pr)
UNION ALL
SELECT 'autopilot_mailbox_e2e',
       CASE WHEN queue.stalled_dispatches>0 THEN 'critical'
            WHEN e2e.state='NO_REAL_E2E_OBSERVED' AND queue.activated_at<now()-interval '2 hours' THEN 'warning' ELSE 'ok' END,
       jsonb_build_object('mailbox_pr',e2e.mailbox_pr,'state',e2e.state,'dispatch_count',e2e.dispatch_count,'published_count',e2e.published_count,'callback_count',e2e.callback_count,'retained_evidence_count',e2e.retained_evidence_count,'first_dispatch_at',e2e.first_dispatch_at,'last_callback_at',e2e.last_callback_at,'stalled_dispatches',queue.stalled_dispatches),
       clock_timestamp() FROM e2e JOIN queue USING(mailbox_pr)
UNION ALL
SELECT 'autopilot_transport_history',
       CASE WHEN legacy_mailbox_commands>=max_dispatches THEN 'warning' ELSE 'ok' END,
       jsonb_build_object('mailbox_pr',mailbox_pr,'legacy_mailbox_commands',legacy_mailbox_commands,'warning_threshold',max_dispatches),
       clock_timestamp() FROM queue;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0403_autopilot_active_dispatch_capacity');
COMMIT;
