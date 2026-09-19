\set ON_ERROR_STOP on
BEGIN;

DO $test$
DECLARE
    active_count integer;
    retained_count integer;
    active_mailbox integer;
    default_value text;
    rejected boolean;
BEGIN
    active_mailbox := CASE WHEN EXISTS(SELECT 1 FROM public.schema_migration WHERE migration_key='0350_autopilot_mailbox_v3_rotation') THEN 1685 ELSE 1637 END;
    SELECT count(*) FILTER (WHERE mailbox_pr=active_mailbox AND lifecycle='ACTIVE' AND max_dispatches=40),
           count(*) FILTER (WHERE mailbox_pr=1150 AND lifecycle='RETAINED')
             + count(*) FILTER (WHERE active_mailbox=1685 AND mailbox_pr=1637 AND lifecycle='RETAINED')
      INTO active_count,retained_count
      FROM autopilot.role_dispatch_mailbox_registry;
    IF active_count<>1 OR retained_count<>(CASE WHEN active_mailbox=1685 THEN 2 ELSE 1 END) THEN
        RAISE EXCEPTION 'MAILBOX_REGISTRY_INVALID';
    END IF;

    SELECT column_default INTO default_value
      FROM information_schema.columns
     WHERE table_schema='autopilot' AND table_name='project_work_item'
       AND column_name='mailbox_pr';
    IF default_value<>active_mailbox::text THEN
        RAISE EXCEPTION 'MAILBOX_DEFAULT_INVALID: %',default_value;
    END IF;

    IF pg_get_functiondef(
        'autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
       ) NOT LIKE (CASE WHEN active_mailbox=1685 THEN '%p_mailbox_pr NOT IN (1150,1637,1685)%' ELSE '%p_mailbox_pr NOT IN (1150,1637)%' END) THEN
        RAISE EXCEPTION 'CALLBACK_DUAL_MAILBOX_GUARD_MISSING';
    END IF;
    IF pg_get_functiondef(
        'autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text)'::regprocedure
       ) NOT LIKE ('%mailbox_pr%IS DISTINCT FROM ''' || active_mailbox::text || '''::jsonb%')
       OR pg_get_functiondef(
        'autopilot.create_chatgpt_role_followup_task(text,jsonb,integer,text,text)'::regprocedure
       ) NOT LIKE ('%mailbox_pr%IS DISTINCT FROM ''' || active_mailbox::text || '''::jsonb%') THEN
        RAISE EXCEPTION 'OUTBOUND_ACTIVE_MAILBOX_GUARD_MISSING';
    END IF;
    IF pg_get_functiondef(
        'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure
       ) NOT LIKE ('%''mailbox_pr'', ' || active_mailbox::text || '%')
       OR pg_get_functiondef(
        'autopilot.materialize_role_verification(uuid,text)'::regprocedure
       ) NOT LIKE ('%''mailbox_pr'', ' || active_mailbox::text || '%') THEN
        RAISE EXCEPTION 'FOLLOWUP_ACTIVE_MAILBOX_ROUTING_MISSING';
    END IF;
    IF pg_get_functiondef(
        'autopilot.register_universal_work_item(text,text,text,text,integer,integer,jsonb,text,text,text)'::regprocedure
       ) NOT LIKE ('%' || active_mailbox::text || '%') THEN
        RAISE EXCEPTION 'UNIVERSAL_WORK_V2_DEFAULT_MISSING';
    END IF;
    IF pg_get_functiondef(
        'autopilot.enforce_role_dispatch_mailbox_capacity()'::regprocedure
       ) NOT LIKE '%FOR UPDATE%'
       OR pg_get_functiondef(
        'autopilot.enforce_role_dispatch_mailbox_capacity()'::regprocedure
       ) NOT LIKE '%AUTOPILOT_MAILBOX_RETAINED%' THEN
        RAISE EXCEPTION 'MAILBOX_CAPACITY_NOT_SERIALIZED';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
         WHERE tgrelid='autopilot.role_dispatch_outbox'::regclass
           AND tgname='role_dispatch_mailbox_capacity_guard' AND NOT tgisinternal
    ) THEN
        RAISE EXCEPTION 'MAILBOX_CAPACITY_GUARD_MISSING';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='autopilot'
           AND indexname='role_dispatch_mailbox_single_active_idx'
    ) THEN
        RAISE EXCEPTION 'MAILBOX_SINGLE_ACTIVE_GUARD_MISSING';
    END IF;

    rejected:=false;
    BEGIN
        PERFORM * FROM autopilot.create_chatgpt_role_dispatch_task(
            'sql-mailbox-v2-retained-rejected-344',
            jsonb_build_object(
                'repository','olegmed1-art/bridge-video-free',
                'mailbox_pr',1150,'role','RECOGNIZER','target_pr',1150,
                'expected_head_sha',repeat('a',40),'dispatch_epoch',1,
                'successor_task_key',NULL,'successor_role',NULL,
                'successor_target_pr',NULL,'successor_expected_head_sha',NULL
            ),0,'database-test','SQL_TEST'
        );
    EXCEPTION WHEN OTHERS THEN
        rejected:=SQLERRM LIKE '%AUTOPILOT_ROLE_GOAL_INVALID%';
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'RETAINED_MAILBOX_ACCEPTED_NEW_TASK';
    END IF;
END $test$;

ROLLBACK;
