\set ON_ERROR_STOP on
-- Synthetic fixtures ONLY in the existing isolated PostgreSQL CI/preflight jobs.
-- Never run this lifecycle/callback test against Neon or a production database.
DO $isolation$
BEGIN
    IF current_database() NOT IN ('bridge_school_ci','autopilot_main_ci','bridge_school_preflight')
       OR session_user <> 'bridge_ci_owner' THEN
        RAISE EXCEPTION 'REPAIR_CALLBACK_TEST_REQUIRES_ISOLATED_CI';
    END IF;
END $isolation$;
SET statement_timeout='30s';
SET lock_timeout='2s';

CREATE TEMP TABLE repair_lifecycle_snapshot AS
SELECT pg_get_functiondef(p.oid) AS definition,p.proowner,p.proacl,m.checksum
FROM pg_proc p CROSS JOIN public.schema_migration m
WHERE p.oid='autopilot.materialize_role_repair(uuid,text,text)'::regprocedure
  AND m.migration_key='0337_autopilot_role_repair_admission';
CREATE TEMP TABLE terminal_callback_snapshot AS
SELECT pg_get_functiondef(p.oid) AS definition,p.proowner,p.proacl
FROM pg_proc p
WHERE p.oid='autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure;
DO $installed$
BEGIN
    IF (SELECT count(*) FROM repair_lifecycle_snapshot)<>1
       OR (SELECT strpos(definition,'REPAIR_ADMISSION_V1')
             FROM repair_lifecycle_snapshot)=0
       OR (SELECT count(*) FROM terminal_callback_snapshot)<>1
       OR (SELECT strpos(definition,'TERMINAL_BOUND_ROLE_V1')
             FROM terminal_callback_snapshot)=0 THEN
        RAISE EXCEPTION 'REPAIR_ADMISSION_NOT_INSTALLED';
    END IF;
END $installed$;
\ir ../rollbacks/0337_autopilot_role_repair_admission.sql
DO $rolled_back$
BEGIN
    IF EXISTS(SELECT 1 FROM public.schema_migration
              WHERE migration_key='0337_autopilot_role_repair_admission')
       OR strpos(pg_get_functiondef(
           'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure),
           'REPAIR_ADMISSION_V1')>0
       OR strpos(pg_get_functiondef(
           'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure),
           'TERMINAL_BOUND_ROLE_V1')>0 THEN
        RAISE EXCEPTION 'REPAIR_ADMISSION_ROLLBACK_NOT_EXACT';
    END IF;
END $rolled_back$;
\ir ../migrations/0337_autopilot_role_repair_admission.sql
DO $reapplied$
BEGIN
    IF NOT EXISTS(
        SELECT 1 FROM pg_proc p CROSS JOIN repair_lifecycle_snapshot s
        WHERE p.oid='autopilot.materialize_role_repair(uuid,text,text)'::regprocedure
          AND pg_get_functiondef(p.oid)=s.definition
          AND p.proowner=s.proowner AND p.proacl IS NOT DISTINCT FROM s.proacl
    ) OR NOT EXISTS(
        SELECT 1 FROM pg_proc p CROSS JOIN terminal_callback_snapshot s
        WHERE p.oid='autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
          AND pg_get_functiondef(p.oid)=s.definition
          AND p.proowner=s.proowner AND p.proacl IS NOT DISTINCT FROM s.proacl
    ) THEN
        RAISE EXCEPTION 'REPAIR_ADMISSION_REAPPLY_BODY_OR_ACL_DRIFT';
    END IF;
END $reapplied$;
-- Preserve the migration runner's checksum across this isolated lifecycle test.
UPDATE public.schema_migration SET checksum=(SELECT checksum FROM repair_lifecycle_snapshot)
WHERE migration_key='0337_autopilot_role_repair_admission';
\echo REPAIR_ADMISSION_LIFECYCLE_PASS

BEGIN;
-- Exercise actual functions, tables and triggers, not an instrumented copy.
DO $callbacks$
DECLARE
    c record;
    work_id uuid;
    task_row record;
    probe record;
    dispatch record;
    publication record;
    result record;
    ack jsonb;
    terminal jsonb;
    event_time text;
    expected_state text;
    repair_id uuid;
    receipt_key text;
BEGIN
    FOR c IN SELECT * FROM (VALUES
        (1,'INFRA','BLOCKED','BOUNDED_REPOSITORY_DEFECT',false,false,false),
        (2,'PLANNING','BLOCKED','BOUNDED_REPOSITORY_DEFECT',false,false,false),
        (3,'AUTOPILOT','BLOCKED','TARGET_PR_SUPERSEDED_AND_CHECKS_INCOMPLETE',false,false,false),
        (4,'AUTOPILOT','BLOCKED','TARGET_PR_OBSOLETE',false,false,false),
        (5,'AUTOPILOT','BLOCKED','OWNER_REQUIRED',false,false,false),
        (6,'AUTOPILOT','BLOCKED','BOUNDED_REPOSITORY_DEFECT',false,true,false),
        (7,'AUTOPILOT','SUCCEEDED','READ_ONLY_AUDIT_COMPLETE',false,false,false),
        (8,'AUTOPILOT','BLOCKED','BOUNDED_REPOSITORY_DEFECT',false,false,true),
        (9,'AUTOPILOT','BLOCKED','BOUNDED_REPOSITORY_DEFECT',true,false,false)
    ) AS cases(id,role_id,terminal_status,result_code,allow_repair,revoke_after_ack,implicit_after_revoke)
    ORDER BY id LOOP
        SELECT work_item_id INTO work_id FROM autopilot.register_universal_work_item(
            'sql-repair-callback-337-'||c.id,c.role_id,'REPOSITORY_AUDIT',
            'Verify one isolated callback admission and retained outcome.',
            1150,0,'{}'::jsonb,NULL,'database-test','SQL_TEST');
        SELECT * INTO probe FROM autopilot.claim_project_work_probe('sql-repair-planner-337',60);
        IF probe.work_item_id IS DISTINCT FROM work_id THEN
            RAISE EXCEPTION 'REPAIR_ADMISSION_NEXT_INDEPENDENT_ITEM_NOT_CLAIMED case %',c.id;
        END IF;
        PERFORM * FROM autopilot.materialize_project_work_probe(
            work_id,'sql-repair-planner-337',probe.lease_epoch,true,repeat('a',40));
        SELECT * INTO task_row FROM autopilot.claim_next_task('sql-repair-worker-337',60);
        IF task_row.task_id IS DISTINCT FROM
           (SELECT last_task_id FROM autopilot.project_work_item WHERE work_item_id=work_id) THEN
            RAISE EXCEPTION 'REPAIR_ADMISSION_WRONG_TASK_CLAIMED case %',c.id;
        END IF;
        SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
            task_row.task_id,'sql-repair-worker-337',task_row.lease_epoch);
        SELECT * INTO publication FROM autopilot.claim_role_dispatch_outbox_v2('sql-repair-publisher-337',60);
        IF publication.dispatch_id IS DISTINCT FROM dispatch.dispatch_id
           OR NOT autopilot.mark_role_dispatch_published(
               dispatch.dispatch_id,'sql-repair-publisher-337',publication.claim_epoch,
               900370+c.id,repeat('b',64)) THEN
            RAISE EXCEPTION 'REPAIR_ADMISSION_PUBLICATION_FAILED case %',c.id;
        END IF;
        event_time:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"');
        ack:=jsonb_build_object(
            'dispatch_id',dispatch.dispatch_id::text,'dispatch_pr',900370+c.id,
            'dispatch_epoch',dispatch.dispatch_epoch,'role',dispatch.role,
            'task_fingerprint',dispatch.task_fingerprint,'target_pr',dispatch.target_pr,
            'expected_head_sha',dispatch.expected_head_sha,'mode',publication.mode,
            'command_pr',1150,'command_comment_id',990033700+c.id,
            'command_created_at',event_time,'ack_reaction_id',99003370+c.id,
            'ack_created_at',event_time);
        IF NOT c.implicit_after_revoke THEN
            SELECT * INTO result FROM autopilot.accept_role_dispatch_codex_ack(
                'github-codex-ack:'||(99003370+c.id),repeat('c',64),true,
                'olegmed1-art/bridge-video-free',1150,'olegmed1-art',315099490,'OWNER',
                'chatgpt-codex-connector',1144995,'chatgpt-codex-connector[bot]',199175422,ack);
            IF NOT result.accepted OR result.duplicate OR result.resulting_state<>'SENT' THEN
                RAISE EXCEPTION 'REPAIR_ADMISSION_ACK_FAILED case %',c.id;
            END IF;
        END IF;
        IF c.revoke_after_ack OR c.implicit_after_revoke THEN
            UPDATE autopilot.role_registry SET enabled=false,can_repair=false WHERE role_id=c.role_id;
        END IF;
        terminal:=jsonb_build_object(
            'dispatch_id',dispatch.dispatch_id::text,'dispatch_epoch',dispatch.dispatch_epoch,
            'role',dispatch.role,'task_fingerprint',dispatch.task_fingerprint,
            'target_pr',dispatch.target_pr,'status',c.terminal_status,'result_code',c.result_code,
            'target_head_sha',dispatch.expected_head_sha,'summary',
            CASE WHEN c.terminal_status='SUCCEEDED'
                 THEN 'Task completed. See execution details above.'
                 ELSE 'Task blocked. See execution details above.' END);
        receipt_key:='github-codex-result:'||(9900337000::bigint+c.id);
        IF c.implicit_after_revoke THEN
            BEGIN
                PERFORM * FROM autopilot.accept_role_dispatch_codex_terminal(
                    receipt_key,repeat('d',64),true,'olegmed1-art/bridge-video-free',1150,
                    'chatgpt-codex-connector[bot]',199175422,'NONE',
                    'chatgpt-codex-connector',1144995,terminal);
                RAISE EXCEPTION 'REPAIR_ADMISSION_REVOKED_IMPLICIT_ACCEPTED';
            EXCEPTION WHEN OTHERS THEN
                IF SQLERRM<>'AUTOPILOT_CODEX_TERMINAL_BINDING_INVALID' THEN
                    RAISE;
                END IF;
            END;
            IF EXISTS(SELECT 1 FROM autopilot.role_dispatch_codex_terminal_receipt
                      WHERE dispatch_id=dispatch.dispatch_id)
               OR EXISTS(SELECT 1 FROM autopilot.evidence
                         WHERE task_id=task_row.task_id
                           AND evidence_class='CHATGPT_ROLE_DISPATCH_RESULT')
               OR (SELECT status FROM autopilot.role_dispatch_outbox
                   WHERE dispatch_id=dispatch.dispatch_id)<>'PUBLISHED'
               OR (SELECT status FROM autopilot.task
                   WHERE task_id=task_row.task_id)<>'WAITING_EXTERNAL' THEN
                RAISE EXCEPTION 'REPAIR_ADMISSION_REVOKED_IMPLICIT_CHANGED_STATE';
            END IF;
            UPDATE autopilot.role_registry SET enabled=true,can_repair=true
             WHERE role_id=c.role_id;
            CONTINUE;
        END IF;
        IF c.id=9 THEN
            BEGIN
                PERFORM * FROM autopilot.accept_role_dispatch_codex_terminal(
                    receipt_key||'-null-role',repeat('e',64),true,
                    'olegmed1-art/bridge-video-free',1150,
                    'chatgpt-codex-connector[bot]',199175422,'NONE',
                    'chatgpt-codex-connector',1144995,
                    terminal||jsonb_build_object('role',NULL));
                RAISE EXCEPTION 'REPAIR_ADMISSION_NULL_ROLE_ACCEPTED';
            EXCEPTION WHEN OTHERS THEN
                IF SQLERRM<>'AUTOPILOT_CODEX_TERMINAL_BODY_INVALID' THEN
                    RAISE;
                END IF;
            END;
        END IF;
        SELECT * INTO result FROM autopilot.accept_role_dispatch_codex_terminal(
            receipt_key,repeat('d',64),true,'olegmed1-art/bridge-video-free',1150,
            'chatgpt-codex-connector[bot]',199175422,'NONE',
            'chatgpt-codex-connector',1144995,terminal);
        expected_state:=CASE WHEN c.terminal_status='SUCCEEDED' THEN 'DONE' ELSE 'FAILED_CLOSED' END;
        IF result.accepted IS DISTINCT FROM true OR result.duplicate IS DISTINCT FROM false
           OR result.resulting_state IS DISTINCT FROM expected_state
           OR (SELECT status FROM autopilot.task WHERE task_id=task_row.task_id) IS DISTINCT FROM expected_state
           OR (SELECT status FROM autopilot.role_dispatch_outbox WHERE dispatch_id=dispatch.dispatch_id)
               IS DISTINCT FROM 'CALLBACK_ACCEPTED'
           OR (SELECT count(*) FROM autopilot.role_dispatch_codex_terminal_receipt
               WHERE dispatch_id=dispatch.dispatch_id)<>1
           OR (SELECT count(*) FROM autopilot.evidence WHERE task_id=task_row.task_id
               AND evidence_class='CHATGPT_ROLE_DISPATCH_RESULT' AND retained)<>1
           OR EXISTS(SELECT 1 FROM autopilot.resource_lease WHERE task_id=task_row.task_id) THEN
            RAISE EXCEPTION 'REPAIR_ADMISSION_CALLBACK_OR_RELEASE_FAILED case %',c.id;
        END IF;
        SELECT followup_task_id INTO repair_id FROM autopilot.role_dispatch_followup
        WHERE parent_task_id=task_row.task_id AND followup_kind='REPAIR';
        IF (repair_id IS NOT NULL) IS DISTINCT FROM c.allow_repair THEN
            RAISE EXCEPTION 'REPAIR_ADMISSION_WRONG_FOLLOWUP case %',c.id;
        END IF;
        IF NOT c.allow_repair AND (SELECT state FROM autopilot.project_work_item WHERE work_item_id=work_id)
           IS DISTINCT FROM (CASE WHEN c.terminal_status='SUCCEEDED' THEN 'DONE' ELSE 'BLOCKED' END) THEN
            RAISE EXCEPTION 'REPAIR_ADMISSION_WORK_ITEM_NOT_TERMINAL case %',c.id;
        END IF;
        SELECT * INTO result FROM autopilot.accept_role_dispatch_codex_terminal(
            receipt_key,repeat('d',64),true,'olegmed1-art/bridge-video-free',1150,
            'chatgpt-codex-connector[bot]',199175422,'NONE',
            'chatgpt-codex-connector',1144995,terminal);
        IF result.accepted IS DISTINCT FROM false OR result.duplicate IS DISTINCT FROM true
           OR (SELECT count(*) FROM autopilot.role_dispatch_followup
               WHERE parent_task_id=task_row.task_id AND followup_kind='REPAIR')
               <>(CASE WHEN c.allow_repair THEN 1 ELSE 0 END) THEN
            RAISE EXCEPTION 'REPAIR_ADMISSION_CALLBACK_REPLAY_FAILED case %',c.id;
        END IF;
        IF c.revoke_after_ack THEN
            UPDATE autopilot.role_registry SET enabled=true,can_repair=true WHERE role_id=c.role_id;
        END IF;
        RAISE NOTICE 'REPAIR_CALLBACK_CASE_PASS id=% role=% status=% repair=%',
            c.id,c.role_id,c.terminal_status,c.allow_repair;
    END LOOP;
END $callbacks$;
ROLLBACK;
\echo REPAIR_ADMISSION_CALLBACK_9_CASES_PASS
