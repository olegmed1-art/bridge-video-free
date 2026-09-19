\set ON_ERROR_STOP on
BEGIN;
DO $pre$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migration WHERE migration_key='0348_autopilot_blocker_remediation')
 THEN RAISE EXCEPTION 'AUTOPILOT_REMEDIATION_MATERIALIZER_REQUIRES_0348'; END IF;
END $pre$;

CREATE OR REPLACE FUNCTION autopilot.materialize_blocker_remediation(
 p_origin_task_id uuid,p_result_code text,p_summary text
) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO 'pg_catalog','autopilot'
AS $f$
DECLARE origin_row autopilot.task; action text; followup_id uuid; mode text;
BEGIN
 SELECT * INTO origin_row FROM autopilot.task WHERE task_id=p_origin_task_id;
 IF NOT FOUND OR origin_row.goal_type<>'CHATGPT_ROLE_DISPATCH_V1' THEN RETURN NULL; END IF;
 action:=autopilot.blocker_remediation_action(p_result_code);
 IF action='REPOSITORY_REPAIR' THEN
   RETURN autopilot.materialize_role_repair(p_origin_task_id,p_result_code,p_summary);
 ELSIF action NOT IN ('EVIDENCE_REMEDIATION','RECONCILE_TARGET') THEN
   RETURN NULL;
 END IF;
 mode:=CASE action WHEN 'EVIDENCE_REMEDIATION' THEN 'EVIDENCE' ELSE 'RECONCILE' END;
 SELECT created.task_id INTO followup_id
 FROM autopilot.create_chatgpt_role_followup_task(
   'role-remediation:'||lower(mode)||':'||origin_row.task_id::text,
   jsonb_build_object(
    'repository',origin_row.goal_json->>'repository','mailbox_pr',1637,
    'role',origin_row.goal_json->>'role','target_pr',(origin_row.goal_json->>'target_pr')::integer,
    'expected_head_sha',origin_row.goal_json->>'expected_head_sha',
    'dispatch_epoch',(origin_row.goal_json->>'dispatch_epoch')::bigint+1,
    'mode',mode,'origin_task_id',origin_row.task_id::text,'prior_task_id',origin_row.task_id::text,
    'blocked_result_code',p_result_code,'blocked_summary',p_summary,'repository_mutation',false
   ),origin_row.priority,'AUTOPILOT_REMEDIATION_CONTROLLER','AUTOPILOT_REMEDIATION'
 ) created;
 INSERT INTO autopilot.role_dispatch_followup(parent_task_id,followup_kind,followup_task_id,origin_task_id,trigger_result_code)
 VALUES(origin_row.task_id,mode,followup_id,origin_row.task_id,p_result_code)
 ON CONFLICT(parent_task_id,followup_kind) DO NOTHING;
 RETURN followup_id;
END $f$;

INSERT INTO public.schema_migration(migration_key) VALUES('0349_autopilot_remediation_materializer');
COMMIT;
