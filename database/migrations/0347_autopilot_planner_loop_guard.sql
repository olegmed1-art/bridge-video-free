\set ON_ERROR_STOP on
BEGIN;

DO $prerequisite$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.schema_migration
    WHERE migration_key='0346_autopilot_canary_acceptance_guard'
  ) THEN
    RAISE EXCEPTION 'AUTOPILOT_PLANNER_LOOP_GUARD_REQUIRES_0346';
  END IF;
END $prerequisite$;

CREATE TABLE autopilot.migration_0347_function_backup (
  function_key text PRIMARY KEY,
  function_definition text NOT NULL
    CHECK (length(function_definition) BETWEEN 100 AND 100000)
);

INSERT INTO autopilot.migration_0347_function_backup(function_key,function_definition)
SELECT fixture.function_key,pg_get_functiondef(fixture.function_oid)
FROM (VALUES
 ('on_project_work_task_terminal','autopilot.on_project_work_task_terminal()'::regprocedure::oid),
 ('materialize_project_work_probe','autopilot.materialize_project_work_probe(uuid,text,bigint,boolean,text)'::regprocedure::oid)
) AS fixture(function_key,function_oid);

ALTER TABLE autopilot.project_work_item
  ADD COLUMN blocker_fingerprint text,
  ADD COLUMN progress_token text,
  ADD COLUMN hold_reason text,
  ADD COLUMN hold_until timestamptz;

CREATE TABLE autopilot.provider_circuit_state (
  provider_id text PRIMARY KEY,
  state text NOT NULL CHECK (state IN ('CLOSED','OPEN','HALF_OPEN')),
  consecutive_failures integer NOT NULL DEFAULT 0 CHECK (consecutive_failures BETWEEN 0 AND 1000),
  opened_at timestamptz,
  hold_until timestamptz,
  last_failure_code text,
  last_success_at timestamptz,
  probe_in_flight boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO autopilot.provider_circuit_state(provider_id,state) VALUES ('CODEX','CLOSED');

CREATE OR REPLACE FUNCTION autopilot.project_work_blocker_action(p_result_code text)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$
 SELECT CASE
  WHEN p_result_code IS NULL THEN 'NO_PROGRESS_HOLD'
  WHEN autopilot.role_blocker_requires_owner(p_result_code) THEN 'OWNER_HOLD'
  WHEN p_result_code IN ('CODEX_PROVIDER_GENERIC_FAILURE','CODEX_ACK_DEADLINE_EXCEEDED','CODEX_RESULT_DEADLINE_EXCEEDED') THEN 'PROVIDER_HOLD'
  WHEN p_result_code ~ '(^|_)(SUPERSEDED|OBSOLETE|STALE_TARGET)(_|$)' OR p_result_code='TARGET_SUPERSEDED_BY_CURRENT_MAIN' THEN 'RECONCILE_TARGET'
  WHEN p_result_code IN ('SERVER_EVIDENCE_INCOMPLETE','SERVER_PARITY_EVIDENCE_INCOMPLETE','PRODUCTION_ACCEPTANCE_PLAN_INCOMPLETE') THEN 'EVIDENCE_REMEDIATION'
  WHEN p_result_code IN ('ROLE_DISPATCH_RESPONSE_INVALID','ROLE_DISPATCH_CLAIM_EXPIRED','ROLE_DISPATCH_DELIVERY_EXHAUSTED','ROLE_CALLBACK_DEADLINE_EXCEEDED','STALE_RETRY_BUDGET_EXHAUSTED','AUTOPILOT_TRANSIENT_DATABASE_ERROR','GITHUB_API_TRANSIENT_ERROR') THEN 'TRANSPORT_REMEDIATION'
  WHEN p_result_code ~ '(^|_)(TEST|CI|QA|GATE|DEFECT|BUG|REGRESSION)(_|$)' THEN 'DIAGNOSE_REPAIR'
  ELSE 'NO_PROGRESS_HOLD'
 END
$$;

CREATE OR REPLACE FUNCTION autopilot.project_work_progress_token(
 p_head_sha text,p_result_code text,p_task_spec jsonb,p_remediation_marker text DEFAULT NULL
) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$
 SELECT encode(public.digest(convert_to(
  coalesce(p_head_sha,'')||'|'||coalesce(p_result_code,'')||'|'||
  coalesce(p_task_spec,'{}'::jsonb)::text||'|'||coalesce(p_remediation_marker,'')
 ,'UTF8'),'sha256'),'hex')
$$;

-- Guard is intentionally fail-closed: a provider failure is never evidence that
-- repository work made progress. One retry is allowed; the second opens circuit.
CREATE OR REPLACE FUNCTION autopilot.provider_circuit_decision(
 p_state text,p_consecutive_failures integer,p_result_code text,p_hold_until timestamptz,p_now timestamptz
) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$
 SELECT CASE
  WHEN p_state='OPEN' AND p_hold_until IS NOT NULL AND p_now<p_hold_until THEN 'HOLD'
  WHEN p_state='OPEN' THEN 'PROBE_ONE'
  WHEN p_state='HALF_OPEN' THEN 'HOLD'
  WHEN p_result_code IN ('CODEX_ACK_DEADLINE_EXCEEDED','CODEX_RESULT_DEADLINE_EXCEEDED','CODEX_PROVIDER_GENERIC_FAILURE')
       AND p_consecutive_failures>=1 THEN 'OPEN_CIRCUIT'
  WHEN p_result_code IN ('CODEX_ACK_DEADLINE_EXCEEDED','CODEX_RESULT_DEADLINE_EXCEEDED','CODEX_PROVIDER_GENERIC_FAILURE')
       THEN 'BOUNDED_RETRY_ONE'
  ELSE 'CONTINUE'
 END
$$;

-- Hard loop guard. It runs before the legacy BLOCKED_HEAD_UNCHANGED guard and
-- before generation+1. It does not create remediation itself; existing failure
-- controller owns REPAIR/VERIFY lineage.
DO $patch$
DECLARE
 original text;
 anchor text := $anchor$    IF item.state = 'BLOCKED'
       AND item.last_observed_head_sha = p_observed_head_sha
       AND NOT autopilot.project_work_transport_retryable(item.result_code)$anchor$;
 guard_sql text := $guard$
    -- PLANNER_LOOP_GUARD_V2
    IF item.state='BLOCKED' AND item.result_code IN (
       'CODEX_ACK_DEADLINE_EXCEEDED','CODEX_RESULT_DEADLINE_EXCEEDED','CODEX_PROVIDER_GENERIC_FAILURE'
    ) THEN
      UPDATE autopilot.project_work_item
         SET state='PAUSED',hold_reason='PROVIDER_HOLD',
             probe_lease_owner=NULL,probe_lease_until=NULL,updated_at=now()
       WHERE work_item_id=item.work_item_id;
      UPDATE autopilot.project_planner_state
         SET decision_count=decision_count+1,last_decision_code='PROVIDER_HOLD',
             last_work_item_id=item.work_item_id,last_decision_at=now()
       WHERE singleton;
      RETURN QUERY SELECT NULL::uuid,'PAUSED'::text,false;
      RETURN;
    END IF;

    IF item.state='BLOCKED'
       AND item.last_observed_head_sha=p_observed_head_sha
       AND NOT autopilot.project_work_transport_retryable(item.result_code)
       AND NOT EXISTS (
         SELECT 1 FROM autopilot.role_dispatch_followup f
         JOIN autopilot.project_work_task m ON m.task_id=f.followup_task_id
         JOIN autopilot.task t ON t.task_id=f.followup_task_id
         WHERE m.work_item_id=item.work_item_id
           AND t.status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')
       ) THEN
      UPDATE autopilot.project_work_item
         SET state='PAUSED',hold_reason='NO_PROGRESS_NO_RETRY',
             probe_lease_owner=NULL,probe_lease_until=NULL,updated_at=now()
       WHERE work_item_id=item.work_item_id;
      UPDATE autopilot.project_planner_state
         SET decision_count=decision_count+1,last_decision_code='NO_PROGRESS_NO_RETRY',
             last_work_item_id=item.work_item_id,last_decision_at=now()
       WHERE singleton;
      RETURN QUERY SELECT NULL::uuid,'PAUSED'::text,false;
      RETURN;
    END IF;

$guard$;
BEGIN
 original := pg_get_functiondef('autopilot.materialize_project_work_probe(uuid,text,bigint,boolean,text)'::regprocedure);
 IF original IS NULL OR strpos(original,'PLANNER_LOOP_GUARD_V2')>0 OR strpos(original,anchor)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_PLANNER_LOOP_GUARD_SOURCE_DRIFT';
 END IF;
 EXECUTE replace(original,anchor,guard_sql||anchor);
END $patch$;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0347_autopilot_planner_loop_guard');

COMMIT;
