\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM autopilot.role_dispatch_outbox WHERE mailbox_pr=1685) THEN RAISE EXCEPTION 'AUTOPILOT_0350_ROLLBACK_NEW_MAILBOX_HAS_HISTORY'; END IF;
END $$;
DO $unpatch$
DECLARE proc regprocedure; original text; restored text;
BEGIN
 original:=pg_get_functiondef('autopilot.register_universal_work_item(text,text,text,text,integer,integer,jsonb,text,text,text)'::regprocedure);
 restored:=replace(original,'p_target_pr integer DEFAULT 1685','p_target_pr integer DEFAULT 1637');
 restored:=replace(restored,'''olegmed1-art/bridge-video-free'',1685,','''olegmed1-art/bridge-video-free'',1637,');
 IF restored=original THEN RAISE EXCEPTION 'AUTOPILOT_0350_ROLLBACK_REGISTRATION_SOURCE_DRIFT'; END IF;
 EXECUTE restored;
 FOREACH proc IN ARRAY ARRAY[
  'autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure,
  'autopilot.accept_role_dispatch_delivery_proof(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
 ] LOOP
  original:=pg_get_functiondef(proc);
  restored:=replace(original,'p_mailbox_pr NOT IN (1150,1637,1685)','p_mailbox_pr NOT IN (1150,1637)');
  IF proc::text LIKE 'autopilot.accept_role_dispatch_callback(%' THEN
   restored:=replace(restored,'''mailbox_pr'', 1685','''mailbox_pr'', 1637');
  END IF;
  IF restored=original THEN RAISE EXCEPTION 'AUTOPILOT_0350_ROLLBACK_INBOUND_SOURCE_DRIFT: %',proc; END IF;
  EXECUTE restored;
 END LOOP;
 FOREACH proc IN ARRAY ARRAY[
  'autopilot.materialize_role_continuation(uuid,text)'::regprocedure,
  'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure,
  'autopilot.materialize_role_verification(uuid,text)'::regprocedure,
  'autopilot.materialize_blocker_remediation(uuid,text,text)'::regprocedure
 ] LOOP
  original:=pg_get_functiondef(proc);
  restored:=replace(original,'''mailbox_pr'', 1685','''mailbox_pr'', 1637');
  IF restored=original AND proc::text='autopilot.materialize_blocker_remediation(uuid,text,text)' THEN
   restored:=replace(original,'''mailbox_pr'',1685','''mailbox_pr'',1637');
  END IF;
  IF restored=original THEN RAISE EXCEPTION 'AUTOPILOT_0350_ROLLBACK_MATERIALIZER_SOURCE_DRIFT: %',proc; END IF;
  EXECUTE restored;
 END LOOP;
 FOREACH proc IN ARRAY ARRAY[
  'autopilot.adopt_project_work_task(text,uuid,text,text)'::regprocedure,
  'autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text)'::regprocedure,
  'autopilot.create_chatgpt_role_followup_task(text,jsonb,integer,text,text)'::regprocedure
 ] LOOP
  original:=pg_get_functiondef(proc);
  restored:=replace(original,$task_row.goal_json->>'mailbox_pr' <> '1685'$,$task_row.goal_json->>'mailbox_pr' <> '1637'$);
  restored:=replace(restored,$p_goal_json->'mailbox_pr' IS DISTINCT FROM '1685'::jsonb$,$p_goal_json->'mailbox_pr' IS DISTINCT FROM '1637'::jsonb$);
  restored:=replace(restored,$'mailbox_pr', 1685$,$'mailbox_pr', 1637$);
  IF restored=original THEN RAISE EXCEPTION 'AUTOPILOT_0350_ROLLBACK_OUTBOUND_SOURCE_DRIFT: %',proc; END IF;
  EXECUTE restored;
 END LOOP;
END $unpatch$;
DELETE FROM autopilot.role_dispatch_mailbox_registry WHERE mailbox_pr=1685;
UPDATE autopilot.role_dispatch_mailbox_registry SET lifecycle='ACTIVE',activated_at=COALESCE(activated_at,clock_timestamp()),retained_at=NULL WHERE mailbox_pr=1637;
ALTER TABLE autopilot.project_work_item DROP CONSTRAINT project_work_item_mailbox_pr_check;
ALTER TABLE autopilot.project_work_item ADD CONSTRAINT project_work_item_mailbox_pr_check CHECK(mailbox_pr IN (1150,1637));
ALTER TABLE autopilot.project_work_item ALTER COLUMN mailbox_pr SET DEFAULT 1637;
ALTER TABLE autopilot.role_dispatch_outbox DROP CONSTRAINT role_dispatch_outbox_mailbox_pr_check;
ALTER TABLE autopilot.role_dispatch_outbox ADD CONSTRAINT role_dispatch_outbox_mailbox_pr_check CHECK(mailbox_pr IN (1150,1637));
DELETE FROM public.schema_migration WHERE migration_key='0350_autopilot_mailbox_v3_rotation';
COMMIT;
