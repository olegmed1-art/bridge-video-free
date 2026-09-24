\set ON_ERROR_STOP on
BEGIN;
DO $pre$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migration WHERE migration_key='0350_autopilot_mailbox_v3_rotation')
 THEN RAISE EXCEPTION 'AUTOPILOT_PAUSED_RECONCILE_REQUIRES_0350'; END IF;
END $pre$;

CREATE TABLE autopilot.paused_work_reconcile_receipt (
 receipt_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 work_item_id uuid NOT NULL REFERENCES autopilot.project_work_item(work_item_id),
 evidence_token text NOT NULL CHECK(evidence_token ~ '^[0-9a-f]{64}$'),
 action text NOT NULL CHECK(action IN ('CLOSE_SUPERSEDED','REMEDIATE','OWNER_HOLD','NO_CHANGE')),
 blocker_fingerprint text,
 followup_task_id uuid REFERENCES autopilot.task(task_id),
 result_code text,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(work_item_id,evidence_token)
);

CREATE OR REPLACE FUNCTION autopilot.reconcile_paused_project_work(
 p_work_item_id uuid,p_evidence_token text,p_target_disposition text DEFAULT NULL,p_result_code text DEFAULT NULL,p_summary text DEFAULT NULL
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO 'pg_catalog','autopilot'
AS $f$
DECLARE w autopilot.project_work_item; action text; fp text; followup uuid;
BEGIN
 IF p_evidence_token IS NULL OR p_evidence_token !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'AUTOPILOT_PAUSED_RECONCILE_EVIDENCE_REQUIRED'; END IF;
 SELECT * INTO w FROM autopilot.project_work_item WHERE work_item_id=p_work_item_id FOR UPDATE;
 IF NOT FOUND OR w.state<>'PAUSED' THEN RETURN 'NO_CHANGE'; END IF;
 IF EXISTS(SELECT 1 FROM autopilot.paused_work_reconcile_receipt WHERE work_item_id=w.work_item_id AND evidence_token=p_evidence_token) THEN RETURN 'NO_CHANGE'; END IF;
 IF autopilot.role_blocker_requires_owner(COALESCE(p_result_code,w.result_code)) THEN action:='OWNER_HOLD';
 ELSIF p_target_disposition IN ('MERGED','CLOSED','SUPERSEDED','SUBSUMED_BY_MAIN') THEN
   UPDATE autopilot.project_work_item SET state='DONE',result_code='TARGET_SUPERSEDED_BY_CURRENT_MAIN',result_summary=left(COALESCE(p_summary,'Target disposition verified by new evidence.'),160),completed_at=now(),updated_at=now(),hold_reason=NULL,hold_until=NULL WHERE work_item_id=w.work_item_id;
   action:='CLOSE_SUPERSEDED';
 ELSE
   fp:=encode(public.digest(convert_to(COALESCE(p_result_code,w.result_code,'')||'|'||p_evidence_token,'UTF8'),'sha256'),'hex');
   IF w.progress_token IS NOT DISTINCT FROM p_evidence_token OR w.blocker_fingerprint IS NOT DISTINCT FROM fp THEN action:='NO_CHANGE';
   ELSE
     followup:=autopilot.materialize_blocker_remediation(w.last_task_id,COALESCE(p_result_code,w.result_code),COALESCE(p_summary,w.result_summary,'New evidence permits bounded remediation.'));
     IF followup IS NULL THEN action:='NO_CHANGE';
     ELSE
       UPDATE autopilot.project_work_item SET blocker_fingerprint=fp,progress_token=p_evidence_token,hold_reason=NULL,hold_until=NULL,updated_at=now() WHERE work_item_id=w.work_item_id;
       action:='REMEDIATE';
     END IF;
   END IF;
 END IF;
 INSERT INTO autopilot.paused_work_reconcile_receipt(work_item_id,evidence_token,action,blocker_fingerprint,followup_task_id,result_code)
 VALUES(w.work_item_id,p_evidence_token,action,fp,followup,COALESCE(p_result_code,w.result_code));
 RETURN action;
END $f$;
REVOKE ALL ON FUNCTION autopilot.reconcile_paused_project_work(uuid,text,text,text,text) FROM PUBLIC;
INSERT INTO public.schema_migration(migration_key) VALUES('0351_autopilot_paused_evidence_reconcile');
COMMIT;
