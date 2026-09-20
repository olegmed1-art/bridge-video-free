\set ON_ERROR_STOP on
BEGIN;
DO $t$
DECLARE active_count integer; allowed text; def text; active_mailbox integer;
BEGIN
 SELECT mailbox_pr INTO STRICT active_mailbox
 FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE';
 SELECT count(*) INTO active_count FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE';
 IF active_count<>1 THEN RAISE EXCEPTION 'AUTOPILOT_0350_ACTIVE_COUNT_INVALID'; END IF;
 IF NOT EXISTS(SELECT 1 FROM autopilot.role_dispatch_mailbox_registry WHERE mailbox_pr=1637 AND lifecycle='RETAINED') THEN RAISE EXCEPTION 'AUTOPILOT_0350_1637_NOT_RETAINED'; END IF;
 IF NOT EXISTS(SELECT 1 FROM autopilot.role_dispatch_mailbox_registry WHERE mailbox_pr=1685 AND lifecycle IN ('ACTIVE','RETAINED') AND max_dispatches=40 AND expected_head_sha='5ff5d9abe497a50cac6564d856297b68c7b4a6c0') THEN RAISE EXCEPTION 'AUTOPILOT_0350_1685_HISTORY_INVALID'; END IF;
 SELECT pg_get_constraintdef(oid) INTO allowed FROM pg_constraint WHERE conrelid='autopilot.role_dispatch_outbox'::regclass AND conname='role_dispatch_outbox_mailbox_pr_check';
 IF position('1150' in allowed)=0 OR position('1637' in allowed)=0 OR position('1685' in allowed)=0 OR position(active_mailbox::text in allowed)=0 THEN RAISE EXCEPTION 'AUTOPILOT_0350_HISTORY_CONSTRAINT_INVALID'; END IF;
 SELECT pg_get_functiondef('autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure) INTO def;
 IF position('1150' in def)=0 OR position('1637' in def)=0 OR position('1685' in def)=0 OR position(active_mailbox::text in def)=0 THEN RAISE EXCEPTION 'AUTOPILOT_0350_CALLBACK_HISTORY_GUARD_INVALID'; END IF;
 SELECT pg_get_functiondef('autopilot.materialize_blocker_remediation(uuid,text,text)'::regprocedure) INTO def;
 IF position(active_mailbox::text in def)=0 THEN RAISE EXCEPTION 'AUTOPILOT_0350_0349_ROUTE_NOT_CURRENT'; END IF;
 SELECT pg_get_functiondef('autopilot.create_chatgpt_role_followup_task(text,jsonb,integer,text,text)'::regprocedure) INTO def;
 IF position(active_mailbox::text in def)=0 THEN RAISE EXCEPTION 'AUTOPILOT_0350_FOLLOWUP_ADMISSION_NOT_CURRENT'; END IF;
END $t$;
ROLLBACK;
