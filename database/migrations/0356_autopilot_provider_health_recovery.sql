\set ON_ERROR_STOP on
BEGIN;

DO $precondition$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0355_autopilot_light_v3_compatibility_fence'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_PROVIDER_HEALTH_RECOVERY_REQUIRES_0355';
    END IF;
END $precondition$;

CREATE TABLE autopilot.migration_0356_provider_backup (
    provider_id text PRIMARY KEY,
    state text NOT NULL,
    consecutive_failures integer NOT NULL,
    opened_at timestamptz,
    hold_until timestamptz,
    last_failure_code text,
    last_success_at timestamptz,
    probe_in_flight boolean NOT NULL,
    updated_at timestamptz NOT NULL,
    backed_up_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

INSERT INTO autopilot.migration_0356_provider_backup (
    provider_id,state,consecutive_failures,opened_at,hold_until,
    last_failure_code,last_success_at,probe_in_flight,updated_at
)
SELECT provider_id,state,consecutive_failures,opened_at,hold_until,
       last_failure_code,last_success_at,probe_in_flight,updated_at
  FROM autopilot.provider_circuit_state
 WHERE provider_id='CODEX';

CREATE TABLE autopilot.migration_0356_view_backup (
    view_key text PRIMARY KEY,
    view_definition text NOT NULL CHECK (length(view_definition) BETWEEN 100 AND 100000),
    owner_name text NOT NULL,
    backed_up_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

INSERT INTO autopilot.migration_0356_view_backup(view_key,view_definition,owner_name)
SELECT 'public.autopilot_operational_health_signal',
       pg_get_viewdef('public.autopilot_operational_health_signal'::regclass,true),
       pg_get_userbyid(c.relowner)
  FROM pg_class c
 WHERE c.oid='public.autopilot_operational_health_signal'::regclass;

CREATE OR REPLACE FUNCTION autopilot.on_role_dispatch_provider_success()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'pg_catalog','autopilot'
AS $function$
BEGIN
    IF NEW.status='CALLBACK_ACCEPTED'
       AND OLD.status IS DISTINCT FROM NEW.status THEN
        UPDATE autopilot.provider_circuit_state
           SET state='CLOSED',
               consecutive_failures=0,
               opened_at=NULL,
               hold_until=NULL,
               last_failure_code=NULL,
               last_success_at=COALESCE(NEW.completed_at,clock_timestamp()),
               probe_in_flight=false,
               updated_at=clock_timestamp()
         WHERE provider_id='CODEX';
    END IF;
    RETURN NEW;
END
$function$;

REVOKE ALL ON FUNCTION autopilot.on_role_dispatch_provider_success() FROM PUBLIC;

CREATE TRIGGER role_dispatch_provider_success
AFTER UPDATE OF status ON autopilot.role_dispatch_outbox
FOR EACH ROW
WHEN (NEW.status='CALLBACK_ACCEPTED' AND OLD.status IS DISTINCT FROM NEW.status)
EXECUTE FUNCTION autopilot.on_role_dispatch_provider_success();

-- A callback accepted after the circuit opened is already durable recovery evidence.
UPDATE autopilot.provider_circuit_state circuit
   SET state='CLOSED',
       consecutive_failures=0,
       opened_at=NULL,
       hold_until=NULL,
       last_failure_code=NULL,
       last_success_at=accepted.last_success_at,
       probe_in_flight=false,
       updated_at=clock_timestamp()
  FROM (
      SELECT max(completed_at) AS last_success_at
        FROM autopilot.role_dispatch_outbox
       WHERE status='CALLBACK_ACCEPTED'
  ) accepted
 WHERE circuit.provider_id='CODEX'
   AND accepted.last_success_at IS NOT NULL
   AND (circuit.opened_at IS NULL OR accepted.last_success_at>circuit.opened_at);

CREATE OR REPLACE VIEW public.autopilot_operational_health_signal AS
WITH backlog AS (
 SELECT ps.last_decision_code,
        count(*) FILTER(WHERE w.state='PAUSED')::integer AS paused_count,
        count(*) FILTER(WHERE w.state IN ('READY','BLOCKED','ACTIVE','WAITING_DEPENDENCY'))::integer AS open_actionable_count
 FROM autopilot.project_planner_state ps
 LEFT JOIN autopilot.project_work_item w ON true
 WHERE ps.singleton
 GROUP BY ps.last_decision_code
),
mailbox AS (
 SELECT * FROM autopilot.mailbox_rotation_readiness()
),
e2e AS (
 SELECT * FROM autopilot.mailbox_e2e_acceptance()
),
active AS (
 SELECT mailbox_pr,activated_at FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE'
)
SELECT 'autopilot_planner_backlog'::text AS signal_key,
       CASE
         WHEN backlog.open_actionable_count>0 THEN 'critical'
         WHEN backlog.paused_count>0 THEN 'warning'
         ELSE 'ok'
       END::text AS severity,
       jsonb_build_object('planner_decision',backlog.last_decision_code,'paused_count',backlog.paused_count,'open_actionable_count',backlog.open_actionable_count) AS details,
       clock_timestamp() AS observed_at
FROM backlog
UNION ALL
SELECT 'autopilot_mailbox_capacity',
       CASE mailbox.readiness WHEN 'ROTATION_REQUIRED' THEN 'critical' WHEN 'PREPARE_ROTATION' THEN 'warning' ELSE 'ok' END,
       jsonb_build_object('mailbox_pr',mailbox.mailbox_pr,'used_dispatches',mailbox.used_dispatches,'max_dispatches',mailbox.max_dispatches,'remaining',mailbox.remaining,'readiness',mailbox.readiness),
       clock_timestamp()
FROM mailbox
UNION ALL
SELECT 'autopilot_mailbox_e2e',
       CASE
         WHEN EXISTS(
           SELECT 1 FROM autopilot.role_dispatch_outbox o
           WHERE o.mailbox_pr=e2e.mailbox_pr
             AND o.status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED')
             AND COALESCE(o.callback_deadline_at,o.delivery_deadline_at,now()+interval '1 hour')<now()
         ) THEN 'critical'
         WHEN e2e.state='NO_REAL_E2E_OBSERVED' AND active.activated_at<now()-interval '2 hours' THEN 'warning'
         ELSE 'ok'
       END,
       jsonb_build_object('mailbox_pr',e2e.mailbox_pr,'state',e2e.state,'dispatch_count',e2e.dispatch_count,'published_count',e2e.published_count,'callback_count',e2e.callback_count,'retained_evidence_count',e2e.retained_evidence_count,'first_dispatch_at',e2e.first_dispatch_at,'last_callback_at',e2e.last_callback_at),
       clock_timestamp()
FROM e2e JOIN active USING(mailbox_pr);

REVOKE ALL ON public.autopilot_operational_health_signal FROM PUBLIC;
GRANT SELECT ON public.autopilot_operational_health_signal TO bridge_school_health;

-- Reuse the already-correct production callback DSN for the scheduled read-only
-- mailbox-capacity probe instead of the stale general worker DSN.
GRANT EXECUTE ON FUNCTION autopilot.mailbox_rotation_readiness() TO autopilot_callback;

INSERT INTO public.schema_migration(migration_key)
VALUES('0356_autopilot_provider_health_recovery');

COMMIT;
