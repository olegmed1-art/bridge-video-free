\set ON_ERROR_STOP on
BEGIN;
-- Append-only correction of 0373 health semantics; no admission, ACL or data changes.
DO $pre$
BEGIN
 IF NOT EXISTS (SELECT FROM public.schema_migration
                WHERE migration_key='0373_autopilot_active_dispatch_capacity') THEN
   RAISE EXCEPTION 'AUTOPILOT_QUEUE_HEALTH_REQUIRES_0373';
 END IF;
END $pre$;
CREATE TABLE autopilot.migration_0374_health_backup (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), definition text NOT NULL
);
INSERT INTO autopilot.migration_0374_health_backup(definition)
VALUES (pg_get_viewdef('public.autopilot_operational_health_signal',true));
REVOKE ALL ON autopilot.migration_0374_health_backup FROM PUBLIC;

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
            -- RETRY has a deliberate backoff of up to one hour. Original row
            -- age must not override the next permitted attempt.
            WHEN 'RETRY' THEN o.next_attempt_at IS NULL OR o.next_attempt_at<now()-interval '15 minutes'
            ELSE o.next_attempt_at<now()-interval '15 minutes'
              OR o.created_at<now()-interval '30 minutes'
          END)::integer stalled_dispatches
 FROM autopilot.role_dispatch_mailbox_registry r
 LEFT JOIN autopilot.role_dispatch_outbox o ON o.mailbox_pr=r.mailbox_pr
 WHERE r.lifecycle='ACTIVE' GROUP BY r.mailbox_pr
)
SELECT 'autopilot_planner_backlog'::text signal_key,
       CASE WHEN backlog.open_actionable_count>0
                  AND backlog.last_decision_code='WAITING_FOR_MAILBOX_CAPACITY'
                  AND EXISTS (SELECT FROM mailbox WHERE readiness='CAPACITY_REACHED') THEN 'critical'
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
VALUES ('0374_autopilot_queue_health_semantics');
COMMIT;
