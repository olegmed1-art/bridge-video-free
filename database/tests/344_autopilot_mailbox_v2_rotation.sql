\set ON_ERROR_STOP on
BEGIN;

DO $test$
DECLARE
    active_count integer;
    retained_count integer;
    default_value text;
BEGIN
    SELECT count(*) FILTER (WHERE mailbox_pr=1637 AND lifecycle='ACTIVE'
                                    AND expected_head_sha='352bdd7d4879d3ca11922ac6d869f0f2dd0afbad'
                                    AND max_dispatches=40),
           count(*) FILTER (WHERE mailbox_pr=1150 AND lifecycle='RETAINED')
      INTO active_count,retained_count
      FROM autopilot.role_dispatch_mailbox_registry;
    IF active_count<>1 OR retained_count<>1 THEN
        RAISE EXCEPTION 'MAILBOX_REGISTRY_INVALID';
    END IF;

    SELECT column_default INTO default_value
      FROM information_schema.columns
     WHERE table_schema='autopilot' AND table_name='project_work_item'
       AND column_name='mailbox_pr';
    IF default_value<>'1637' THEN
        RAISE EXCEPTION 'MAILBOX_DEFAULT_INVALID: %',default_value;
    END IF;

    IF pg_get_functiondef(
        'autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
       ) NOT LIKE '%p_mailbox_pr NOT IN (1150,1637)%' THEN
        RAISE EXCEPTION 'CALLBACK_DUAL_MAILBOX_GUARD_MISSING';
    END IF;
    IF pg_get_functiondef(
        'autopilot.register_universal_work_item(text,text,text,text,integer,integer,jsonb,text,text,text)'::regprocedure
       ) NOT LIKE '%1637%' THEN
        RAISE EXCEPTION 'UNIVERSAL_WORK_V2_DEFAULT_MISSING';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
         WHERE tgrelid='autopilot.role_dispatch_outbox'::regclass
           AND tgname='role_dispatch_mailbox_capacity_guard' AND NOT tgisinternal
    ) THEN
        RAISE EXCEPTION 'MAILBOX_CAPACITY_GUARD_MISSING';
    END IF;
END $test$;

ROLLBACK;
