\set ON_ERROR_STOP on
BEGIN;

DO $pre$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migration WHERE migration_key='0352_autopilot_operational_safety_signals')
 THEN RAISE EXCEPTION 'AUTOPILOT_PAUSED_RECONCILE_RUNNER_REQUIRES_0352'; END IF;
END $pre$;

CREATE TABLE autopilot.migration_0353_function_backup (
 function_key text PRIMARY KEY,
 function_definition text NOT NULL CHECK(length(function_definition) BETWEEN 100 AND 100000)
);

INSERT INTO autopilot.migration_0353_function_backup(function_key,function_definition)
VALUES (
 'reconcile_paused_project_work',
 pg_get_functiondef('autopilot.reconcile_paused_project_work(uuid,text,text,text,text)'::regprocedure)
);

CREATE OR REPLACE FUNCTION autopilot.paused_reconcile_candidates(p_limit integer DEFAULT 10)
RETURNS TABLE(
 work_item_id uuid,
 work_key text,
 role text,
 task_kind text,
 target_pr integer,
 result_code text,
 last_observed_head_sha text,
 progress_token text,
 hold_reason text,
 blocker_action text,
 owner_gated boolean,
 provider_state text,
 provider_last_success_at timestamptz,
 updated_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'pg_catalog','autopilot'
AS $f$
BEGIN
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 50 THEN
   RAISE EXCEPTION 'AUTOPILOT_PAUSED_RECONCILE_LIMIT_INVALID';
 END IF;
 RETURN QUERY
 SELECT w.work_item_id,w.work_key,w.role,w.task_kind,w.target_pr,w.result_code,
        w.last_observed_head_sha,w.progress_token,w.hold_reason,
        autopilot.blocker_remediation_action(w.result_code),
        EXISTS (
          SELECT 1 FROM autopilot.role_registry rr
          WHERE rr.role_id=w.role AND rr.execution_scope='OWNER_GATED'
        ),
        (SELECT pcs.state FROM autopilot.provider_circuit_state pcs WHERE pcs.provider_id='CODEX'),
        (SELECT pcs.last_success_at FROM autopilot.provider_circuit_state pcs WHERE pcs.provider_id='CODEX'),
        w.updated_at
 FROM autopilot.project_work_item w
 WHERE w.state='PAUSED'
 ORDER BY
   CASE autopilot.blocker_remediation_action(w.result_code)
     WHEN 'RECONCILE_TARGET' THEN 0
     WHEN 'REPOSITORY_REPAIR' THEN 1
     WHEN 'EVIDENCE_REMEDIATION' THEN 2
     WHEN 'TRANSPORT_REMEDIATION' THEN 3
     WHEN 'OWNER_HOLD' THEN 4
     WHEN 'PROVIDER_HOLD' THEN 5
     ELSE 6
   END,
   w.priority,w.updated_at,w.created_at
 LIMIT p_limit;
END $f$;

REVOKE ALL ON FUNCTION autopilot.paused_reconcile_candidates(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.paused_reconcile_candidates(integer) TO bridge_school_worker;

CREATE OR REPLACE FUNCTION autopilot.reconcile_paused_project_work(
 p_work_item_id uuid,p_evidence_token text,p_target_disposition text DEFAULT NULL,p_result_code text DEFAULT NULL,p_summary text DEFAULT NULL
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO 'pg_catalog','autopilot'
AS $f$
DECLARE
 w autopilot.project_work_item;
 decision_action text;
 blocker_action text;
 fp text;
 effective_code text;
BEGIN
 IF p_evidence_token IS NULL OR p_evidence_token !~ '^[0-9a-f]{64}$' THEN
   RAISE EXCEPTION 'AUTOPILOT_PAUSED_RECONCILE_EVIDENCE_REQUIRED';
 END IF;

 SELECT * INTO w
 FROM autopilot.project_work_item
 WHERE work_item_id=p_work_item_id
 FOR UPDATE;

 IF NOT FOUND OR w.state<>'PAUSED' THEN
   RETURN 'NO_CHANGE';
 END IF;

 IF EXISTS(
   SELECT 1 FROM autopilot.paused_work_reconcile_receipt
   WHERE work_item_id=w.work_item_id AND evidence_token=p_evidence_token
 ) THEN
   RETURN 'NO_CHANGE';
 END IF;

 effective_code:=COALESCE(p_result_code,w.result_code);
 blocker_action:=autopilot.blocker_remediation_action(effective_code);
 fp:=encode(public.digest(convert_to(COALESCE(effective_code,'')||'|'||p_evidence_token,'UTF8'),'sha256'),'hex');

 IF autopilot.role_blocker_requires_owner(effective_code)
    OR EXISTS (
      SELECT 1
      FROM autopilot.role_registry rr
      WHERE rr.role_id=w.role
        AND rr.execution_scope='OWNER_GATED'
    ) THEN
   UPDATE autopilot.project_work_item
      SET hold_reason='OWNER_HOLD',
          progress_token=p_evidence_token,
          blocker_fingerprint=fp,
          result_summary=left(COALESCE(p_summary,w.result_summary,'Owner decision required by new evidence.'),160),
          updated_at=now()
    WHERE work_item_id=w.work_item_id;
   decision_action:='OWNER_HOLD';

 ELSIF p_target_disposition IN ('MERGED','SUPERSEDED','SUBSUMED_BY_MAIN') THEN
   UPDATE autopilot.project_work_item
      SET state='DONE',
          result_code='TARGET_SUPERSEDED_BY_CURRENT_MAIN',
          result_summary=left(COALESCE(p_summary,'Target disposition verified by new evidence.'),160),
          completed_at=now(),
          updated_at=now(),
          hold_reason=NULL,
          hold_until=NULL,
          progress_token=p_evidence_token,
          blocker_fingerprint=fp
    WHERE work_item_id=w.work_item_id;
   decision_action:='CLOSE_SUPERSEDED';

 ELSIF blocker_action IN (
   'RECONCILE_TARGET','REPOSITORY_REPAIR','EVIDENCE_REMEDIATION','TRANSPORT_REMEDIATION'
 )
 OR (
   blocker_action='PROVIDER_HOLD'
   AND EXISTS (
     SELECT 1
     FROM autopilot.provider_circuit_state pcs
     WHERE pcs.provider_id='CODEX'
       AND pcs.state='CLOSED'
       AND pcs.last_success_at IS NOT NULL
       AND pcs.last_success_at>w.updated_at
   )
 ) THEN
   IF w.progress_token IS NOT DISTINCT FROM p_evidence_token
      OR w.blocker_fingerprint IS NOT DISTINCT FROM fp THEN
     decision_action:='NO_CHANGE';
   ELSE
     UPDATE autopilot.project_work_item
        SET state='READY',
            not_before=now(),
            hold_reason=NULL,
            hold_until=NULL,
            progress_token=p_evidence_token,
            blocker_fingerprint=fp,
            result_code=left(effective_code,64),
            result_summary=left(COALESCE(p_summary,w.result_summary,'New evidence permits one bounded planner attempt.'),160),
            probe_lease_owner=NULL,
            probe_lease_until=NULL,
            completed_at=NULL,
            updated_at=now()
      WHERE work_item_id=w.work_item_id;
     UPDATE autopilot.project_planner_state
        SET decision_count=decision_count+1,
            last_decision_code='PAUSED_EVIDENCE_REMEDIATION_READY',
            last_work_item_id=w.work_item_id,
            last_decision_at=now()
      WHERE singleton;
     PERFORM pg_notify('autopilot_ready','paused-evidence-remediation');
     decision_action:='REMEDIATE';
   END IF;

 ELSE
   decision_action:='NO_CHANGE';
 END IF;

 INSERT INTO autopilot.paused_work_reconcile_receipt(
   work_item_id,evidence_token,action,blocker_fingerprint,followup_task_id,result_code
 )
 VALUES(w.work_item_id,p_evidence_token,decision_action,fp,NULL,effective_code);

 RETURN decision_action;
END $f$;

REVOKE ALL ON FUNCTION autopilot.reconcile_paused_project_work(uuid,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.reconcile_paused_project_work(uuid,text,text,text,text) TO bridge_school_worker;

INSERT INTO public.schema_migration(migration_key)
VALUES('0353_autopilot_paused_reconcile_runner');

COMMIT;
