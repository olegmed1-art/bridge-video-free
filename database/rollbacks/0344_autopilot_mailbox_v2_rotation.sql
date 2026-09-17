\set ON_ERROR_STOP on
BEGIN;

DO $guard$
BEGIN
    IF EXISTS (SELECT 1 FROM autopilot.role_dispatch_outbox WHERE mailbox_pr=1637) THEN
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
        'autopilot.accept_role_dispatch_delivery_proof(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure,
        'autopilot.adopt_project_work_task(text,uuid,text,text)'::regprocedure,
        'autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text)'::regprocedure,
        'autopilot.create_chatgpt_role_followup_task(text,jsonb,integer,text,text)'::regprocedure,
        'autopilot.register_universal_work_item(text,text,text,text,integer,integer,jsonb,text,text,text)'::regprocedure
    ] LOOP
        original := pg_get_functiondef(proc);
        restored := original;
        restored := replace(restored,'p_mailbox_pr NOT IN (1150,1637)','p_mailbox_pr IS DISTINCT FROM 1150');
        restored := replace(restored,
            $$task_row.goal_json->>'mailbox_pr' NOT IN ('1150','1637')$$,
            $$task_row.goal_json->>'mailbox_pr' <> '1150'$$);
        restored := replace(restored,
            $$COALESCE(p_goal_json->>'mailbox_pr','') NOT IN ('1150','1637')$$,
            $$p_goal_json->'mailbox_pr' IS DISTINCT FROM '1150'::jsonb$$);
        restored := replace(restored,
            $$'mailbox_pr', (p_goal_json->>'mailbox_pr')::integer$$,
            $$'mailbox_pr', 1150$$);
        IF proc::text LIKE '%register_universal_work_item(%' THEN
            restored := replace(restored,'p_target_pr integer DEFAULT 1637','p_target_pr integer DEFAULT 1150');
            restored := replace(restored,
                $$p_work_key,'olegmed1-art/bridge-video-free',1637,$$,
                $$p_work_key,'olegmed1-art/bridge-video-free',1150,$$);
        END IF;
        IF restored=original OR strpos(restored,'1637')>0 THEN
            RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_ROLLBACK_SOURCE_DRIFT: %',proc;
        END IF;
        EXECUTE restored;
    END LOOP;
END $restore_functions$;

DROP TABLE autopilot.role_dispatch_mailbox_registry;
DELETE FROM public.schema_migration
 WHERE migration_key='0344_autopilot_mailbox_v2_rotation';
COMMIT;
