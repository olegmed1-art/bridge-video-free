\set ON_ERROR_STOP on
BEGIN;

LOCK TABLE autopilot.role_dispatch_mailbox_registry IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.project_work_item IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;

DO $pre$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0356_autopilot_provider_health_recovery'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V4_REQUIRES_0356';
 END IF;
 IF NOT EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_mailbox_registry
   WHERE mailbox_pr=1685
     AND lifecycle='ACTIVE'
     AND expected_head_sha IN (
       '7bfae72289f12ca5c28ce6a3754ccab5383b7f75',
       '5ff5d9abe497a50cac6564d856297b68c7b4a6c0'
     )
     AND EXISTS (
       SELECT 1 FROM public.schema_migration
       WHERE migration_key='0354_autopilot_mailbox_v3_final_head'
     )
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V4_SOURCE_NOT_ACTIVE';
 END IF;
 IF EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_outbox
   WHERE status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED')
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V4_LIVE_DISPATCH_PRESENT';
 END IF;
 IF EXISTS (
   SELECT 1 FROM autopilot.task
   WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
     AND status NOT IN ('OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED')
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V4_ACTIVE_ROLE_TASK_PRESENT';
 END IF;
END $pre$;

CREATE TABLE autopilot.migration_0357_function_backup (
 function_key text PRIMARY KEY,
 definition text NOT NULL
);

INSERT INTO autopilot.migration_0357_function_backup(function_key,definition)
SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid)
FROM pg_proc AS p
JOIN pg_namespace AS n ON n.oid=p.pronamespace
WHERE p.oid IN (
 'autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure,
 'autopilot.accept_role_dispatch_delivery_proof(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure,
 'autopilot.adopt_project_work_task(text,uuid,text,text)'::regprocedure,
 'autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text)'::regprocedure,
 'autopilot.create_chatgpt_role_followup_task(text,jsonb,integer,text,text)'::regprocedure,
 'autopilot.materialize_blocker_remediation(uuid,text,text)'::regprocedure,
 'autopilot.materialize_role_continuation(uuid,text)'::regprocedure,
 'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure,
 'autopilot.materialize_role_verification(uuid,text)'::regprocedure,
 'autopilot.register_universal_work_item(text,text,text,text,integer,integer,jsonb,text,text,text)'::regprocedure
);

DO $backup$
BEGIN
 IF (SELECT count(*) FROM autopilot.migration_0357_function_backup)<>10 THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V4_FUNCTION_BACKUP_INCOMPLETE';
 END IF;
END $backup$;

UPDATE autopilot.role_dispatch_mailbox_registry
SET lifecycle='RETAINED',
    expected_head_sha='5ff5d9abe497a50cac6564d856297b68c7b4a6c0',
    retained_at=clock_timestamp()
WHERE mailbox_pr=1685 AND lifecycle='ACTIVE';

INSERT INTO autopilot.role_dispatch_mailbox_registry(
 mailbox_pr,expected_head_sha,lifecycle,max_dispatches,activated_at,retained_at
) VALUES (
 1703,'ae6def0f14a4a26c58862c96a61c2038bb4a875f','ACTIVE',40,clock_timestamp(),NULL
);

ALTER TABLE autopilot.project_work_item
 DROP CONSTRAINT project_work_item_mailbox_pr_check;
ALTER TABLE autopilot.project_work_item
 ADD CONSTRAINT project_work_item_mailbox_pr_check
 CHECK(mailbox_pr IN (1150,1637,1685,1703));
ALTER TABLE autopilot.project_work_item ALTER COLUMN mailbox_pr SET DEFAULT 1703;

ALTER TABLE autopilot.role_dispatch_outbox
 DROP CONSTRAINT role_dispatch_outbox_mailbox_pr_check;
ALTER TABLE autopilot.role_dispatch_outbox
 ADD CONSTRAINT role_dispatch_outbox_mailbox_pr_check
 CHECK(mailbox_pr IN (1150,1637,1685,1703));

UPDATE autopilot.project_work_item
SET mailbox_pr=1703,updated_at=clock_timestamp()
WHERE mailbox_pr=1685 AND state<>'DONE';

DO $patch$
DECLARE proc regprocedure; original text; patched text;
BEGIN
 proc:='autopilot.register_universal_work_item(text,text,text,text,integer,integer,jsonb,text,text,text)'::regprocedure;
 SELECT definition INTO STRICT original
 FROM autopilot.migration_0357_function_backup WHERE function_key=proc::text;
 patched:=replace(original,'p_target_pr integer DEFAULT 1685','p_target_pr integer DEFAULT 1703');
 patched:=replace(patched,'''olegmed1-art/bridge-video-free'',1685,','''olegmed1-art/bridge-video-free'',1703,');
 IF patched=original OR position('1703' in patched)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V4_REGISTRATION_SOURCE_DRIFT';
 END IF;
 EXECUTE patched;

 FOREACH proc IN ARRAY ARRAY[
  'autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure,
  'autopilot.accept_role_dispatch_delivery_proof(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
 ] LOOP
  SELECT definition INTO STRICT original
  FROM autopilot.migration_0357_function_backup WHERE function_key=proc::text;
  patched:=replace(original,
    'p_mailbox_pr NOT IN (1150,1637,1685)',
    'p_mailbox_pr NOT IN (1150,1637,1685,1703)');
  IF proc::text LIKE 'autopilot.accept_role_dispatch_callback(%' THEN
   patched:=replace(patched,'''mailbox_pr'', 1685','''mailbox_pr'', 1703');
   patched:=replace(patched,'''mailbox_pr'',1685','''mailbox_pr'',1703');
  END IF;
  IF patched=original OR position('1703' in patched)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V4_INBOUND_SOURCE_DRIFT: %',proc;
  END IF;
  EXECUTE patched;
 END LOOP;

 FOREACH proc IN ARRAY ARRAY[
  'autopilot.materialize_role_continuation(uuid,text)'::regprocedure,
  'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure,
  'autopilot.materialize_role_verification(uuid,text)'::regprocedure,
  'autopilot.materialize_blocker_remediation(uuid,text,text)'::regprocedure
 ] LOOP
  SELECT definition INTO STRICT original
  FROM autopilot.migration_0357_function_backup WHERE function_key=proc::text;
  patched:=replace(original,'''mailbox_pr'', 1685','''mailbox_pr'', 1703');
  patched:=replace(patched,'''mailbox_pr'',1685','''mailbox_pr'',1703');
  IF patched=original THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V4_MATERIALIZER_SOURCE_DRIFT: %',proc;
  END IF;
  EXECUTE patched;
 END LOOP;

 FOREACH proc IN ARRAY ARRAY[
  'autopilot.adopt_project_work_task(text,uuid,text,text)'::regprocedure,
  'autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text)'::regprocedure,
  'autopilot.create_chatgpt_role_followup_task(text,jsonb,integer,text,text)'::regprocedure
 ] LOOP
  SELECT definition INTO STRICT original
  FROM autopilot.migration_0357_function_backup WHERE function_key=proc::text;
  patched:=replace(original,
    $$task_row.goal_json->>'mailbox_pr' <> '1685'$$,
    $$task_row.goal_json->>'mailbox_pr' <> '1703'$$);
  patched:=replace(patched,
    $$p_goal_json->'mailbox_pr' IS DISTINCT FROM '1685'::jsonb$$,
    $$p_goal_json->'mailbox_pr' IS DISTINCT FROM '1703'::jsonb$$);
  patched:=replace(patched,$$'mailbox_pr', 1685$$,$$'mailbox_pr', 1703$$);
  patched:=replace(patched,$$'mailbox_pr',1685$$,$$'mailbox_pr',1703$$);
  IF patched=original OR position('1703' in patched)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V4_OUTBOUND_SOURCE_DRIFT: %',proc;
  END IF;
  EXECUTE patched;
 END LOOP;
END $patch$;

INSERT INTO public.schema_migration(migration_key)
VALUES('0357_autopilot_mailbox_v4_rotation');

COMMIT;
