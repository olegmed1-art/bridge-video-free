\set ON_ERROR_STOP on
BEGIN;

DO $replay$
DECLARE
  action text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.schema_migration
    WHERE migration_key='0347_autopilot_planner_loop_guard'
  ) THEN RAISE EXCEPTION 'AUTOPILOT_0347_REPLAY_MIGRATION_MISSING'; END IF;

  -- Night replay: unchanged semantic blocker must never authorize a generation.
  action := autopilot.project_work_blocker_action('PRODUCTION_READINESS_EVIDENCE_INCOMPLETE');
  IF action <> 'NO_PROGRESS_HOLD' THEN
    RAISE EXCEPTION 'AUTOPILOT_0347_REPLAY_G2_G3_INVALID action=%',action;
  END IF;

  -- Provider failures are classified as provider hold, never repository repair.
  IF autopilot.project_work_blocker_action('CODEX_ACK_DEADLINE_EXCEEDED') <> 'PROVIDER_HOLD'
     OR autopilot.project_work_blocker_action('CODEX_RESULT_DEADLINE_EXCEEDED') <> 'PROVIDER_HOLD'
     OR autopilot.project_work_blocker_action('CODEX_PROVIDER_GENERIC_FAILURE') <> 'PROVIDER_HOLD' THEN
    RAISE EXCEPTION 'AUTOPILOT_0347_REPLAY_PROVIDER_CLASS_INVALID';
  END IF;

  -- Circuit contract: one bounded failure, second opens; open circuit holds.
  IF autopilot.provider_circuit_decision('CLOSED',0,'CODEX_ACK_DEADLINE_EXCEEDED',NULL,now())
       <> 'BOUNDED_RETRY_ONE'
     OR autopilot.provider_circuit_decision('CLOSED',1,'CODEX_ACK_DEADLINE_EXCEEDED',NULL,now())
       <> 'OPEN_CIRCUIT'
     OR autopilot.provider_circuit_decision('OPEN',2,'CODEX_ACK_DEADLINE_EXCEEDED',now()+interval '1 hour',now())
       <> 'HOLD' THEN
    RAISE EXCEPTION 'AUTOPILOT_0347_REPLAY_CIRCUIT_INVALID';
  END IF;

  -- A real transient outside Codex remains retryable.
  IF NOT autopilot.project_work_transport_retryable('GITHUB_API_TRANSIENT_ERROR') THEN
    RAISE EXCEPTION 'AUTOPILOT_0347_REPLAY_TRANSIENT_RETRY_LOST';
  END IF;

  -- Exact function backup is present for rollback.
  IF (SELECT count(*) FROM autopilot.migration_0347_function_backup) <> 2 THEN
    RAISE EXCEPTION 'AUTOPILOT_0347_ROLLBACK_BACKUP_INVALID';
  END IF;
  IF strpos(pg_get_functiondef(
       'autopilot.materialize_project_work_probe(uuid,text,bigint,boolean,text)'::regprocedure
     ),'PLANNER_LOOP_GUARD_V2') = 0 THEN
    RAISE EXCEPTION 'AUTOPILOT_0347_LOOP_GUARD_NOT_INSTALLED';
  END IF;
END $replay$;

ROLLBACK;
