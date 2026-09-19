\set ON_ERROR_STOP on
BEGIN;
LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;

DO $pre$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migration WHERE migration_key='0349_autopilot_remediation_materializer') THEN RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V3_REQUIRES_0349'; END IF;
 IF (SELECT lifecycle FROM autopilot.role_dispatch_mailbox_registry WHERE mailbox_pr=1637) <> 'ACTIVE' THEN RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V3_SOURCE_NOT_ACTIVE'; END IF;
 IF (SELECT count(*) FROM autopilot.role_dispatch_outbox WHERE mailbox_pr=1637) < (SELECT max_dispatches FROM autopilot.role_dispatch_mailbox_registry WHERE mailbox_pr=1637) THEN RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V3_SOURCE_NOT_FULL'; END IF;
 IF EXISTS(SELECT 1 FROM autopilot.role_dispatch_outbox WHERE status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED')) THEN RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V3_LIVE_DISPATCH_PRESENT'; END IF;
 IF EXISTS(SELECT 1 FROM autopilot.task WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1') AND status NOT IN ('OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED')) THEN RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V3_ACTIVE_ROLE_TASK_PRESENT'; END IF;
END $pre$;

UPDATE autopilot.role_dispatch_mailbox_registry SET lifecycle='RETAINED',retained_at=clock_timestamp() WHERE mailbox_pr=1637 AND lifecycle='ACTIVE';
INSERT INTO autopilot.role_dispatch_mailbox_registry(mailbox_pr,expected_head_sha,lifecycle,max_dispatches,activated_at,retained_at)
VALUES(1685,'7bfae72289f12ca5c28ce6a3754ccab5383b7f75','ACTIVE',40,clock_timestamp(),NULL);

ALTER TABLE autopilot.project_work_item DROP CONSTRAINT project_work_item_mailbox_pr_check;
ALTER TABLE autopilot.project_work_item ADD CONSTRAINT project_work_item_mailbox_pr_check CHECK(mailbox_pr IN (1150,1637,1685));
ALTER TABLE autopilot.project_work_item ALTER COLUMN mailbox_pr SET DEFAULT 1685;
ALTER TABLE autopilot.role_dispatch_outbox DROP CONSTRAINT role_dispatch_outbox_mailbox_pr_check;
ALTER TABLE autopilot.role_dispatch_outbox ADD CONSTRAINT role_dispatch_outbox_mailbox_pr_check CHECK(mailbox_pr IN (1150,1637,1685));
UPDATE autopilot.project_work_item SET mailbox_pr=1685,updated_at=clock_timestamp() WHERE mailbox_pr=1637 AND state<>'DONE';

DO $patch$
DECLARE proc regprocedure; original text; patched text;
BEGIN
 FOREACH proc IN ARRAY ARRAY[
  'autopilot.materialize_role_continuation(uuid,text)'::regprocedure,
  'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure,
  'autopilot.materialize_role_verification(uuid,text)'::regprocedure,
  'autopilot.materialize_blocker_remediation(uuid,text,text)'::regprocedure
 ] LOOP
  original:=pg_get_functiondef(proc); patched:=replace(original,$$'mailbox_pr', 1637$$,$$'mailbox_pr', 1685$$);
  IF patched=original THEN RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V3_MATERIALIZER_SOURCE_DRIFT: %',proc; END IF; EXECUTE patched;
 END LOOP;
 FOREACH proc IN ARRAY ARRAY[
  'autopilot.adopt_project_work_task(text,uuid,text,text)'::regprocedure,
  'autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text)'::regprocedure,
  'autopilot.create_chatgpt_role_followup_task(text,jsonb,integer,text,text)'::regprocedure
 ] LOOP
  original:=pg_get_functiondef(proc); patched:=replace(original,$$task_row.goal_json->>'mailbox_pr' <> '1637'$$,$$task_row.goal_json->>'mailbox_pr' <> '1685'$$);
  patched:=replace(patched,$$p_goal_json->'mailbox_pr' IS DISTINCT FROM '1637'::jsonb$$,$$p_goal_json->'mailbox_pr' IS DISTINCT FROM '1685'::jsonb$$);
  patched:=replace(patched,$$'mailbox_pr', 1637$$,$$'mailbox_pr', 1685$$);
  IF patched=original THEN RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V3_OUTBOUND_SOURCE_DRIFT: %',proc; END IF; EXECUTE patched;
 END LOOP;
END $patch$;
INSERT INTO public.schema_migration(migration_key) VALUES('0350_autopilot_mailbox_v3_rotation');
COMMIT;
