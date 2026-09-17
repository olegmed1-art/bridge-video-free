\set ON_ERROR_STOP on
BEGIN;

LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;

DO $guard$
BEGIN
    IF EXISTS (SELECT 1 FROM autopilot.role_dispatch_outbox WHERE mailbox_pr=1637)
       OR EXISTS (
           SELECT 1
             FROM autopilot.task
            WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
              AND goal_json->>'mailbox_pr'='1637'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_ROLLBACK_REQUIRES_FORWARD_RECOVERY';
    END IF;
END $guard$;

DROP TRIGGER role_dispatch_mailbox_capacity_guard ON autopilot.role_dispatch_outbox;
DROP FUNCTION autopilot.enforce_role_dispatch_mailbox_capacity();

UPDATE autopilot.project_work_item
   SET mailbox_pr=1150, updated_at=clock_timestamp()
 WHERE mailbox_pr=1637;

ALTER TABLE autopilot.project_work_item
    DROP CONSTRAINT project_work_item_mailbox_pr_check;
ALTER TABLE autopilot.project_work_item
    ADD CONSTRAINT project_work_item_mailbox_pr_check CHECK (mailbox_pr=1150);
ALTER TABLE autopilot.project_work_item ALTER COLUMN mailbox_pr SET DEFAULT 1150;
ALTER TABLE autopilot.role_dispatch_outbox
    DROP CONSTRAINT role_dispatch_outbox_mailbox_pr_check;
ALTER TABLE autopilot.role_dispatch_outbox
    ADD CONSTRAINT role_dispatch_outbox_mailbox_pr_check CHECK (mailbox_pr=1150);

DO $restore_functions$
DECLARE
    proc regprocedure;
    original text;
    restored text;
BEGIN
    FOREACH proc IN ARRAY ARRAY[
        'autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure,
        'autopilot.accept_role_dispatch_delivery_proof(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
    ] LOOP
        original := pg_get_functiondef(proc);
        restored := original;
        restored := replace(restored,'p_mailbox_pr NOT IN (1150,1637)','p_mailbox_pr IS DISTINCT FROM 1150');
        IF proc::text LIKE 'autopilot.accept_role_dispatch_callback(%' THEN
            restored := replace(restored,
                $$'mailbox_pr', 1637$$,
                $$'mailbox_pr', (task_row.goal_json->>'mailbox_pr')::integer$$);
        END IF;
        IF restored=original OR strpos(restored,'1637')>0 THEN
            RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_ROLLBACK_SOURCE_DRIFT: %',proc;
        END IF;
        EXECUTE restored;
    END LOOP;

    FOREACH proc IN ARRAY ARRAY[
        'autopilot.materialize_role_continuation(uuid,text)'::regprocedure,
        'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure,
        'autopilot.materialize_role_verification(uuid,text)'::regprocedure
    ] LOOP
        original := pg_get_functiondef(proc);
        restored := original;
        IF proc::text LIKE 'autopilot.materialize_role_continuation(%' THEN
            restored := replace(restored,$$'mailbox_pr', 1637$$,
                $$'mailbox_pr', (parent_row.goal_json->>'mailbox_pr')::integer$$);
        ELSIF proc::text LIKE 'autopilot.materialize_role_repair(%' THEN
            restored := replace(restored,$$'mailbox_pr', 1637$$,
                $$'mailbox_pr', (origin_row.goal_json->>'mailbox_pr')::integer$$);
        ELSE
            restored := replace(restored,$$'mailbox_pr', 1637$$,
                $$'mailbox_pr', (repair_row.goal_json->>'mailbox_pr')::integer$$);
        END IF;
        IF restored=original OR strpos(restored,$$'mailbox_pr', 1637$$)>0 THEN
            RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_MATERIALIZER_ROLLBACK_SOURCE_DRIFT: %',proc;
        END IF;
        EXECUTE restored;
    END LOOP;

    FOREACH proc IN ARRAY ARRAY[
        'autopilot.adopt_project_work_task(text,uuid,text,text)'::regprocedure,
        'autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text)'::regprocedure,
        'autopilot.create_chatgpt_role_followup_task(text,jsonb,integer,text,text)'::regprocedure
    ] LOOP
        original := pg_get_functiondef(proc);
        restored := replace(original,
            $$task_row.goal_json->>'mailbox_pr' <> '1637'$$,
            $$task_row.goal_json->>'mailbox_pr' <> '1150'$$);
        restored := replace(restored,
            $$p_goal_json->'mailbox_pr' IS DISTINCT FROM '1637'::jsonb$$,
            $$p_goal_json->'mailbox_pr' IS DISTINCT FROM '1150'::jsonb$$);
        restored := replace(restored,$$'mailbox_pr', 1637$$,$$'mailbox_pr', 1150$$);
        IF restored=original OR strpos(restored,'1637')>0 THEN
            RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_OUTBOUND_ROLLBACK_SOURCE_DRIFT: %',proc;
        END IF;
        EXECUTE restored;
    END LOOP;

    proc := 'autopilot.register_universal_work_item(text,text,text,text,integer,integer,jsonb,text,text,text)'::regprocedure;
    original := pg_get_functiondef(proc);
    restored := replace(original,'p_target_pr integer DEFAULT 1637','p_target_pr integer DEFAULT 1150');
    restored := replace(restored,
        $$p_work_key,'olegmed1-art/bridge-video-free',1637,$$,
        $$p_work_key,'olegmed1-art/bridge-video-free',1150,$$);
    IF restored=original OR strpos(restored,'1637')>0 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_REGISTRATION_ROLLBACK_SOURCE_DRIFT';
    END IF;
    EXECUTE restored;
END $restore_functions$;

DROP TABLE autopilot.role_dispatch_mailbox_registry;
DELETE FROM public.schema_migration
 WHERE migration_key='0344_autopilot_mailbox_v2_rotation';
COMMIT;
