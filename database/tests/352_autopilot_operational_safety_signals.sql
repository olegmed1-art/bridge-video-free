\set ON_ERROR_STOP on
BEGIN;
DO $t$
DECLARE r record; e record; planner_def text;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migration WHERE migration_key='0352_autopilot_operational_safety_signals') THEN
   RAISE EXCEPTION 'AUTOPILOT_0352_MIGRATION_MISSING';
 END IF;
 IF autopilot.blocker_remediation_action('AUTOPILOT_UNCLASSIFIED_FAILURE')<>'OWNER_HOLD' THEN
   RAISE EXCEPTION 'AUTOPILOT_0352_UNCLASSIFIED_NOT_OWNER_HOLD';
 END IF;
 SELECT * INTO r FROM autopilot.mailbox_rotation_readiness();
 IF r.mailbox_pr IS NULL OR r.max_dispatches<1 OR r.readiness NOT IN ('NORMAL','PREPARE_ROTATION','ROTATION_REQUIRED') THEN
   RAISE EXCEPTION 'AUTOPILOT_0352_ROTATION_READINESS_INVALID';
 END IF;
 SELECT * INTO e FROM autopilot.mailbox_e2e_acceptance();
 IF e.mailbox_pr IS NULL OR e.state NOT IN ('NO_REAL_E2E_OBSERVED','DISPATCH_OBSERVED','E2E_IN_PROGRESS','E2E_ACCEPTED') THEN
   RAISE EXCEPTION 'AUTOPILOT_0352_E2E_STATE_INVALID';
 END IF;
 planner_def:=pg_get_functiondef('autopilot.claim_project_work_probe(text,integer)'::regprocedure);
 IF position('PROJECT_DONE_WITH_PAUSED_BACKLOG' in planner_def)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0352_PAUSED_WATCHDOG_MISSING';
 END IF;
 IF position('mailbox_rotation_signal' in pg_get_functiondef('autopilot.enforce_role_dispatch_mailbox_capacity()'::regprocedure))=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0352_PRE_ROTATION_SIGNAL_MISSING';
 END IF;
END $t$;
ROLLBACK;
