\set ON_ERROR_STOP on
BEGIN;

-- Freeze task/outbox admission for the preflight and cutover transaction.
LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;

DO $prerequisite$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0343_autopilot_provider_failure_no_repair'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_REQUIRES_0343';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM autopilot.role_dispatch_outbox
         WHERE status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED')
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_LIVE_DISPATCH_PRESENT';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM autopilot.task
         WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
           AND status NOT IN ('DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED')
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_ACTIVE_ROLE_TASK_PRESENT';
    END IF;
END $prerequisite$;

CREATE TABLE autopilot.role_dispatch_mailbox_registry (
    mailbox_pr integer PRIMARY KEY,
    expected_head_sha text NOT NULL CHECK (expected_head_sha ~ '^[0-9a-f]{40}$'),
    lifecycle text NOT NULL CHECK (lifecycle IN ('ACTIVE','RETAINED')),
    max_dispatches smallint NOT NULL CHECK (max_dispatches BETWEEN 1 AND 100),
    activated_at timestamptz,
    retained_at timestamptz,
    CHECK ((lifecycle='ACTIVE' AND activated_at IS NOT NULL AND retained_at IS NULL)
        OR (lifecycle='RETAINED' AND retained_at IS NOT NULL))
);
CREATE UNIQUE INDEX role_dispatch_mailbox_single_active_idx
    ON autopilot.role_dispatch_mailbox_registry(lifecycle)
    WHERE lifecycle='ACTIVE';

INSERT INTO autopilot.role_dispatch_mailbox_registry(
    mailbox_pr,expected_head_sha,lifecycle,max_dispatches,activated_at,retained_at
) VALUES
    (1150,'2ceb48716988ec9cbd01be438a0ebf8b46836667','RETAINED',40,NULL,clock_timestamp()),
    (1637,'352bdd7d4879d3ca11922ac6d869f0f2dd0afbad','ACTIVE',40,clock_timestamp(),NULL);

ALTER TABLE autopilot.project_work_item
    DROP CONSTRAINT project_work_item_mailbox_pr_check;
ALTER TABLE autopilot.project_work_item
    ADD CONSTRAINT project_work_item_mailbox_pr_check
    CHECK (mailbox_pr IN (1150,1637));
ALTER TABLE autopilot.project_work_item ALTER COLUMN mailbox_pr SET DEFAULT 1637;

ALTER TABLE autopilot.role_dispatch_outbox
    DROP CONSTRAINT role_dispatch_outbox_mailbox_pr_check;
ALTER TABLE autopilot.role_dispatch_outbox
    ADD CONSTRAINT role_dispatch_outbox_mailbox_pr_check
    CHECK (mailbox_pr IN (1150,1637));

-- Preserve terminal history. Only dormant work is repointed; state and receipts
-- are unchanged, so reactivation later cannot publish back into the full v1 PR.
UPDATE autopilot.project_work_item
   SET mailbox_pr=1637, updated_at=clock_timestamp()
 WHERE mailbox_pr=1150 AND state<>'DONE';

CREATE FUNCTION autopilot.enforce_role_dispatch_mailbox_capacity()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'pg_catalog','autopilot'
AS $function$
DECLARE
    mailbox autopilot.role_dispatch_mailbox_registry;
    used integer;
BEGIN
    SELECT * INTO mailbox
      FROM autopilot.role_dispatch_mailbox_registry
     WHERE mailbox_pr=NEW.mailbox_pr
     FOR UPDATE;
    IF mailbox.mailbox_pr IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_UNREGISTERED';
    END IF;
    IF mailbox.lifecycle <> 'ACTIVE' THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_RETAINED';
    END IF;
    SELECT count(*) INTO used
      FROM autopilot.role_dispatch_outbox
     WHERE mailbox_pr=NEW.mailbox_pr;
    IF used >= mailbox.max_dispatches THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_ROTATION_REQUIRED';
    END IF;
    RETURN NEW;
END $function$;

REVOKE ALL ON FUNCTION autopilot.enforce_role_dispatch_mailbox_capacity() FROM PUBLIC;
CREATE TRIGGER role_dispatch_mailbox_capacity_guard
BEFORE INSERT ON autopilot.role_dispatch_outbox
FOR EACH ROW EXECUTE FUNCTION autopilot.enforce_role_dispatch_mailbox_capacity();

DO $patch_functions$
DECLARE
    proc regprocedure;
    original text;
    patched text;
BEGIN
    -- Inbound receipts remain valid for both retained and active mailboxes so
    -- authenticated late provider replies can still close old dispatches.
    FOREACH proc IN ARRAY ARRAY[
        'autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure,
        'autopilot.accept_role_dispatch_delivery_proof(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
    ] LOOP
        original := pg_get_functiondef(proc);
        patched := original;
        patched := replace(patched,
            'p_mailbox_pr IS DISTINCT FROM 1150',
            'p_mailbox_pr NOT IN (1150,1637)');
        patched := replace(patched,
            'p_mailbox_pr IS DISTINCT FROM 1150 OR',
            'p_mailbox_pr NOT IN (1150,1637) OR');
        IF proc::text LIKE 'autopilot.accept_role_dispatch_callback(%' THEN
            patched := replace(patched,
                $$'mailbox_pr', (task_row.goal_json->>'mailbox_pr')::integer$$,
                $$'mailbox_pr', 1637$$);
        END IF;
        IF patched=original OR strpos(patched,'1637')=0 THEN
            RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_FUNCTION_SOURCE_DRIFT: %',proc;
        END IF;
        EXECUTE patched;
    END LOOP;

    -- Any continuation synthesized from a late v1 receipt is routed to the
    -- active mailbox. The retained mailbox never receives new commands.
    FOREACH proc IN ARRAY ARRAY[
        'autopilot.materialize_role_continuation(uuid,text)'::regprocedure,
        'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure,
        'autopilot.materialize_role_verification(uuid,text)'::regprocedure
    ] LOOP
        original := pg_get_functiondef(proc);
        patched := replace(original,
            $$'mailbox_pr', (parent_row.goal_json->>'mailbox_pr')::integer$$,
            $$'mailbox_pr', 1637$$);
        patched := replace(patched,
            $$'mailbox_pr', (origin_row.goal_json->>'mailbox_pr')::integer$$,
            $$'mailbox_pr', 1637$$);
        patched := replace(patched,
            $$'mailbox_pr', (repair_row.goal_json->>'mailbox_pr')::integer$$,
            $$'mailbox_pr', 1637$$);
        IF patched=original OR strpos(patched,$$'mailbox_pr', 1637$$)=0 THEN
            RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_MATERIALIZER_SOURCE_DRIFT: %',proc;
        END IF;
        EXECUTE patched;
    END LOOP;

    -- Outbound task adoption and creation are active-mailbox-only. Retained
    -- #1150 is accepted exclusively by the two inbound receipt functions.
    FOREACH proc IN ARRAY ARRAY[
        'autopilot.adopt_project_work_task(text,uuid,text,text)'::regprocedure,
        'autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text)'::regprocedure,
        'autopilot.create_chatgpt_role_followup_task(text,jsonb,integer,text,text)'::regprocedure
    ] LOOP
        original := pg_get_functiondef(proc);
        patched := replace(original,
            $$task_row.goal_json->>'mailbox_pr' <> '1150'$$,
            $$task_row.goal_json->>'mailbox_pr' <> '1637'$$);
        patched := replace(patched,
            $$p_goal_json->'mailbox_pr' IS DISTINCT FROM '1150'::jsonb$$,
            $$p_goal_json->'mailbox_pr' IS DISTINCT FROM '1637'::jsonb$$);
        patched := replace(patched,$$'mailbox_pr', 1150$$,$$'mailbox_pr', 1637$$);
        IF patched=original OR strpos(patched,'1637')=0 THEN
            RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_OUTBOUND_SOURCE_DRIFT: %',proc;
        END IF;
        EXECUTE patched;
    END LOOP;

    proc := 'autopilot.register_universal_work_item(text,text,text,text,integer,integer,jsonb,text,text,text)'::regprocedure;
    original := pg_get_functiondef(proc);
    patched := replace(original,'p_target_pr integer DEFAULT 1150','p_target_pr integer DEFAULT 1637');
    patched := replace(patched,
        $$p_work_key,'olegmed1-art/bridge-video-free',1150,$$,
        $$p_work_key,'olegmed1-art/bridge-video-free',1637,$$);
    IF patched=original OR strpos(patched,'1637')=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_REGISTRATION_SOURCE_DRIFT';
    END IF;
    EXECUTE patched;
END $patch_functions$;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0344_autopilot_mailbox_v2_rotation');
COMMIT;
