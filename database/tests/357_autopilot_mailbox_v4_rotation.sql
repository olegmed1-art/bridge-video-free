\set ON_ERROR_STOP on
BEGIN;

DO $test$
DECLARE
 active_count integer;
 allowed text;
 default_expr text;
 proc regprocedure;
 definition text;
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0357_autopilot_mailbox_v4_rotation'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0357_MIGRATION_MISSING';
 END IF;

 SELECT count(*) INTO active_count
 FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE';
 IF active_count<>1 THEN
   RAISE EXCEPTION 'AUTOPILOT_0357_ACTIVE_COUNT_INVALID';
 END IF;
 IF NOT EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_mailbox_registry
   WHERE mailbox_pr=1685 AND lifecycle='RETAINED'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0357_1685_NOT_RETAINED';
 END IF;
 IF NOT EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_mailbox_registry
   WHERE mailbox_pr=1703 AND lifecycle='ACTIVE' AND max_dispatches=40
     AND expected_head_sha='ae6def0f14a4a26c58862c96a61c2038bb4a875f'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0357_1703_NOT_ACTIVE';
 END IF;

 SELECT pg_get_constraintdef(oid) INTO allowed
 FROM pg_constraint
 WHERE conrelid='autopilot.role_dispatch_outbox'::regclass
   AND conname='role_dispatch_outbox_mailbox_pr_check';
 IF position('1150' in allowed)=0 OR position('1637' in allowed)=0
    OR position('1685' in allowed)=0 OR position('1703' in allowed)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0357_HISTORY_CONSTRAINT_INVALID';
 END IF;

 SELECT c.column_default INTO default_expr
 FROM information_schema.columns
 AS c
 WHERE table_schema='autopilot' AND table_name='project_work_item'
   AND column_name='mailbox_pr';
 IF default_expr IS DISTINCT FROM '1703' THEN
   RAISE EXCEPTION 'AUTOPILOT_0357_DEFAULT_NOT_ROTATED: %',default_expr;
 END IF;

 FOREACH proc IN ARRAY ARRAY[
  'autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure,
  'autopilot.accept_role_dispatch_delivery_proof(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
 ] LOOP
  definition:=pg_get_functiondef(proc);
  IF position('p_mailbox_pr NOT IN (1150,1637,1685,1703)' in definition)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0357_HISTORY_CALLBACK_INVALID: %',proc;
  END IF;
 END LOOP;

 FOREACH proc IN ARRAY ARRAY[
  'autopilot.adopt_project_work_task(text,uuid,text,text)'::regprocedure,
  'autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text)'::regprocedure,
  'autopilot.create_chatgpt_role_followup_task(text,jsonb,integer,text,text)'::regprocedure,
  'autopilot.materialize_blocker_remediation(uuid,text,text)'::regprocedure,
  'autopilot.materialize_role_continuation(uuid,text)'::regprocedure,
  'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure,
  'autopilot.materialize_role_verification(uuid,text)'::regprocedure,
  'autopilot.register_universal_work_item(text,text,text,text,integer,integer,jsonb,text,text,text)'::regprocedure
 ] LOOP
  definition:=pg_get_functiondef(proc);
  IF position('1703' in definition)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0357_OUTBOUND_NOT_ROTATED: %',proc;
  END IF;
 END LOOP;

 IF EXISTS (
   SELECT 1 FROM autopilot.project_work_item
   WHERE state<>'DONE' AND mailbox_pr=1685
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0357_UNFINISHED_WORK_NOT_ROTATED';
 END IF;
END $test$;

ROLLBACK;
