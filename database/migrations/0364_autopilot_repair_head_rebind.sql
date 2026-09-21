\set ON_ERROR_STOP on
BEGIN;

LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.project_work_item IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.project_work_task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_followup IN SHARE ROW EXCLUSIVE MODE;

DO $pre$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0363_autopilot_audit_findings_repair_progression'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_REPAIR_HEAD_REBIND_REQUIRES_0363';
 END IF;
 IF EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0364_autopilot_repair_head_rebind'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_REPAIR_HEAD_REBIND_ALREADY_APPLIED';
 END IF;
 IF to_regprocedure(
      'autopilot.materialize_role_repair_rebound(uuid,text,text,text)'
    ) IS NOT NULL THEN
   RAISE EXCEPTION 'AUTOPILOT_REPAIR_HEAD_REBIND_FUNCTION_ALREADY_PRESENT';
 END IF;
 IF EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_outbox
   WHERE status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED')
 ) OR EXISTS (
   SELECT 1 FROM autopilot.task
   WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
     AND status NOT IN (
       'OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED'
     )
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_REPAIR_HEAD_REBIND_ACTIVE_ROLE_WORK_PRESENT';
 END IF;
END $pre$;

CREATE OR REPLACE FUNCTION autopilot.materialize_role_repair_rebound(
 p_origin_task_id uuid,
 p_result_code text,
 p_summary text,
 p_expected_head_sha text
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,autopilot
AS $function$
DECLARE
 origin_row autopilot.task;
 work_row autopilot.project_work_item;
 existing_followup autopilot.task;
 inserted autopilot.task;
 followup_id uuid;
 followup_goal jsonb;
BEGIN
 SELECT * INTO origin_row
 FROM autopilot.task AS task
 WHERE task.task_id=p_origin_task_id
 FOR UPDATE;
 IF NOT FOUND
    OR origin_row.goal_type<>'CHATGPT_ROLE_DISPATCH_V1'
    OR origin_row.status<>'DONE'
    OR p_result_code<>'AUDIT_FINDINGS_REPORTED'
    OR origin_row.safe_summary_json->>'result_code' IS DISTINCT FROM p_result_code
    OR origin_row.safe_summary_json->>'summary' IS DISTINCT FROM p_summary
    OR origin_row.goal_json->>'repository' IS DISTINCT FROM
       'olegmed1-art/bridge-video-free'
    OR origin_row.goal_json->>'mailbox_pr' IS DISTINCT FROM '1703'
    OR COALESCE(origin_row.goal_json->>'expected_head_sha','')
       !~ '^[0-9a-f]{40}$'
    OR COALESCE(origin_row.goal_json->>'target_pr','')
       !~ '^[1-9][0-9]{0,6}$'
    OR COALESCE(origin_row.goal_json->>'dispatch_epoch','')
       !~ '^[1-9][0-9]{0,9}$'
    OR (origin_row.goal_json->>'dispatch_epoch')::bigint NOT BETWEEN 1 AND 999999
    OR COALESCE(p_expected_head_sha,'')!~'^[0-9a-f]{40}$'
    OR p_expected_head_sha IS NOT DISTINCT FROM
       origin_row.goal_json->>'expected_head_sha'
    OR length(COALESCE(p_summary,'')) NOT BETWEEN 1 AND 160
    OR p_summary~'[[:cntrl:]]' THEN
   RETURN NULL;
 END IF;

 SELECT work.* INTO work_row
 FROM autopilot.project_work_task AS mapping
 JOIN autopilot.project_work_item AS work
   ON work.work_item_id=mapping.work_item_id
 WHERE mapping.task_id=origin_row.task_id
   AND mapping.run_kind='AUDIT'
 FOR UPDATE OF work;
 IF NOT FOUND THEN
   RETURN NULL;
 END IF;

 -- The lineage row is the durable idempotency boundary.  Once the rebound
 -- successor exists, replay must return it even though the followup trigger
 -- has already moved the work item back to ACTIVE.
 SELECT task.* INTO existing_followup
 FROM autopilot.role_dispatch_followup AS lineage
 JOIN autopilot.task AS task ON task.task_id=lineage.followup_task_id
 WHERE lineage.parent_task_id=origin_row.task_id
   AND lineage.followup_kind='REPAIR';
 IF FOUND THEN
   IF existing_followup.goal_json->>'expected_head_sha'=p_expected_head_sha
      AND EXISTS (
        SELECT 1
        FROM autopilot.role_dispatch_followup AS lineage
        WHERE lineage.parent_task_id=origin_row.task_id
          AND lineage.followup_kind='REPAIR'
          AND lineage.trigger_result_code=p_result_code
      ) THEN
     RETURN existing_followup.task_id;
   END IF;
   RAISE EXCEPTION 'AUTOPILOT_REPAIR_HEAD_REBIND_LINEAGE_CONFLICT';
 END IF;

 IF work_row.state<>'DONE'
    OR work_row.last_task_id IS DISTINCT FROM origin_row.task_id
    OR work_row.result_code IS DISTINCT FROM p_result_code
    OR work_row.result_summary IS DISTINCT FROM p_summary
    OR work_row.last_observed_head_sha IS DISTINCT FROM
       origin_row.goal_json->>'expected_head_sha'
    OR work_row.repository IS DISTINCT FROM origin_row.goal_json->>'repository'
    OR work_row.mailbox_pr IS DISTINCT FROM
       (origin_row.goal_json->>'mailbox_pr')::integer
    OR work_row.role IS DISTINCT FROM origin_row.goal_json->>'role'
    OR work_row.target_pr IS DISTINCT FROM
       (origin_row.goal_json->>'target_pr')::integer
    OR COALESCE(work_row.task_spec_json ? 'canary',false)
    OR COALESCE(work_row.task_spec_json->>'repair_policy','')='DISABLED' THEN
   RETURN NULL;
 END IF;

 PERFORM 1
 FROM autopilot.role_registry AS permitted_role
 WHERE permitted_role.role_id=origin_row.goal_json->>'role'
   AND permitted_role.enabled
   AND permitted_role.execution_scope='REPOSITORY'
   AND permitted_role.can_repair
 FOR SHARE;
 IF NOT FOUND THEN
   RETURN NULL;
 END IF;

 IF EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_outbox
   WHERE status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED')
 ) OR EXISTS (
   SELECT 1 FROM autopilot.task
   WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
     AND status NOT IN (
       'OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED'
     )
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_REPAIR_HEAD_REBIND_ACTIVE_ROLE_WORK_PRESENT';
 END IF;

 followup_goal:=jsonb_build_object(
   'repository',origin_row.goal_json->>'repository',
   'mailbox_pr',1703,
   'role',origin_row.goal_json->>'role',
   'target_pr',(origin_row.goal_json->>'target_pr')::integer,
   'expected_head_sha',p_expected_head_sha,
   'dispatch_epoch',(origin_row.goal_json->>'dispatch_epoch')::bigint+1,
   'mode','REPAIR',
   'repair_attempt',1,
   'origin_task_id',origin_row.task_id::text,
   'prior_task_id',origin_row.task_id::text,
   'blocked_result_code',p_result_code,
   'blocked_summary',p_summary
 );

 INSERT INTO autopilot.task(
   task_key,goal_type,goal_json,status,current_step_key,
   acceptance_contract_json,allowed_capabilities_json,priority,
   model_turn_cap,cost_cap_microusd,created_by,source
 ) VALUES (
   'role-repair-rebound:'||origin_row.task_id::text,
   'CHATGPT_ROLE_FOLLOWUP_V1',followup_goal,'READY',
   'github.chatgpt.role.dispatch',
   jsonb_build_object(
     'retained_evidence_required',true,
     'production_mutation',false,
     'exact_head_required',true,
     'mailbox_pr',1703,
     'mode','REPAIR',
     'repair_attempt_cap',1,
     'cost_actual_microusd',0
   ),
   '["github.chatgpt.role.dispatch"]'::jsonb,
   origin_row.priority,0,0,
   'AUTOPILOT_REBOUND_CONTROLLER','AUTOPILOT_REPAIR_REBOUND'
 )
 RETURNING * INTO inserted;
 followup_id:=inserted.task_id;

 PERFORM autopilot.record_event(
   followup_id,'TASK_READY','NEW','READY',
   jsonb_build_object(
     'goal_type','CHATGPT_ROLE_FOLLOWUP_V1',
     'mode','REPAIR',
     'origin_task_id',origin_row.task_id::text,
     'rebound_from_head_sha',origin_row.goal_json->>'expected_head_sha',
     'expected_head_sha',p_expected_head_sha
   ),
   'SYSTEM','autopilot-rebound-controller',
   'create:role-repair-rebound:'||origin_row.task_id::text
 );

 INSERT INTO autopilot.role_dispatch_followup(
   parent_task_id,followup_kind,followup_task_id,
   origin_task_id,trigger_result_code
 ) VALUES (
   origin_row.task_id,'REPAIR',followup_id,
   origin_row.task_id,p_result_code
 );

 UPDATE autopilot.project_work_item
 SET last_observed_head_sha=p_expected_head_sha,
     completed_at=NULL,
     updated_at=now()
 WHERE work_item_id=work_row.work_item_id;
 RETURN followup_id;
END;
$function$;

REVOKE ALL ON FUNCTION autopilot.materialize_role_repair_rebound(
 uuid,text,text,text
) FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

COMMENT ON FUNCTION autopilot.materialize_role_repair_rebound(
 uuid,text,text,text
) IS 'Owner-only exact-head rebind for one lost AUDIT_FINDINGS_REPORTED repair successor; origin task remains immutable.';

INSERT INTO public.schema_migration(migration_key)
VALUES('0364_autopilot_repair_head_rebind');

COMMIT;
