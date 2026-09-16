\set ON_ERROR_STOP on
\if :{?persist_accepted}
\else
  \set persist_accepted false
\endif
BEGIN;

-- Synthetic fixtures are for isolated SQL CI only, never a production probe.
DO $$
DECLARE
    work_id uuid;
    task_row record;
    probe record;
    materialized record;
    dispatch record;
    outbox record;
    terminal jsonb;
    candidate jsonb;
    ack jsonb;
    result record;
    published boolean;
    has_ack boolean;
    case_number integer := 0;
    kind text;
    expected_error text;
    delivery_id text;
    event_time text;
    run_suffix text := txid_current()::text;
    backup_row record;
    command_comment_id bigint;
    reaction_id bigint;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0336_autopilot_codex_terminal_implicit_delivery'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_IMPLICIT_DELIVERY_MIGRATION_MISSING';
    END IF;
    SELECT * INTO backup_row
      FROM autopilot.migration_0336_function_backup
     WHERE function_key='accept_role_dispatch_codex_terminal';
    IF NOT FOUND
       OR backup_row.previous_definition IS NULL
       OR backup_row.installed_definition IS NULL
       OR backup_row.previous_owner IS NULL
       OR backup_row.installed_owner IS NULL
       OR backup_row.previous_acl IS NULL
       OR backup_row.installed_acl IS NULL
       OR backup_row.installed_definition IS DISTINCT FROM pg_get_functiondef(
          'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_336_BACKUP_STATE_INVALID';
    END IF;
    IF has_table_privilege(
           'autopilot_callback','autopilot.migration_0336_function_backup','SELECT'
       ) OR has_table_privilege(
           'autopilot_runtime','autopilot.migration_0336_function_backup','SELECT'
       ) OR NOT has_function_privilege(
           'autopilot_callback',
           'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)',
           'EXECUTE'
       ) OR has_function_privilege(
           'autopilot_runtime',
           'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_336_PRIVILEGE_CONTRACT_INVALID';
    END IF;

    FOREACH has_ack IN ARRAY ARRAY[false,true] LOOP
        case_number := case_number+1;
        delivery_id := 'github-codex-result:9900336-'||run_suffix||'-'||case_number;
        command_comment_id := 5669000000 +
            txid_current()*10 + case_number;
        reaction_id := 417000000 +
            txid_current()*10 + case_number;
        SELECT work_item_id INTO work_id
          FROM autopilot.register_universal_work_item(
            'sql-codex-terminal-implicit-336-'||run_suffix||'-'||case_number,
            'AUTOPILOT',
            'CODEX_TERMINAL_IMPLICIT_DELIVERY_TEST',
            'Prove exact-bound pinned terminal acceptance within its deadline, with or without an eyes reaction.',
            1150,0,'{"media":false}'::jsonb,NULL,'database-test','SQL_TEST'
          );
        SELECT * INTO probe
          FROM autopilot.claim_project_work_probe('sql-codex-planner-336',60);
        IF probe.work_item_id IS DISTINCT FROM work_id THEN
            RAISE EXCEPTION 'AUTOPILOT_CODEX_IMPLICIT_WORK_NOT_CLAIMED';
        END IF;
        SELECT * INTO materialized
          FROM autopilot.materialize_project_work_probe(
            work_id,'sql-codex-planner-336',probe.lease_epoch,true,repeat('6',40)
          );
        SELECT * INTO task_row
          FROM autopilot.claim_next_task('sql-codex-worker-336',60);
        SELECT * INTO dispatch
          FROM autopilot.prepare_role_dispatch(
            task_row.task_id,'sql-codex-worker-336',task_row.lease_epoch
          );
        SELECT * INTO outbox
          FROM autopilot.claim_role_dispatch_outbox_v2('sql-codex-publisher-336',60);
        published:=autopilot.mark_role_dispatch_published(
            dispatch.dispatch_id,'sql-codex-publisher-336',outbox.claim_epoch,
            9900336+case_number,repeat('7',64)
        );
        IF NOT published OR (SELECT status FROM autopilot.role_dispatch_outbox
             WHERE dispatch_id=dispatch.dispatch_id)<>'PUBLISHED' THEN
            RAISE EXCEPTION 'AUTOPILOT_CODEX_IMPLICIT_DISPATCH_NOT_PUBLISHED';
        END IF;
        IF EXISTS (
            SELECT 1 FROM autopilot.role_dispatch_codex_delivery_proof
             WHERE dispatch_id=dispatch.dispatch_id
        ) THEN
            RAISE EXCEPTION 'AUTOPILOT_CODEX_IMPLICIT_UNEXPECTED_ACK_PROOF';
        END IF;

        IF has_ack THEN
            event_time:=to_char(clock_timestamp() AT TIME ZONE 'UTC',
                               'YYYY-MM-DD"T"HH24:MI:SS"Z"');
            ack:=jsonb_build_object(
                'dispatch_id',dispatch.dispatch_id::text,
                'dispatch_pr',9900336+case_number,
                'dispatch_epoch',dispatch.dispatch_epoch,
                'role',dispatch.role,
                'task_fingerprint',dispatch.task_fingerprint,
                'target_pr',dispatch.target_pr,
                'expected_head_sha',dispatch.expected_head_sha,
                'mode',outbox.mode,
                'command_pr',1150,
                'command_comment_id',command_comment_id,
                'command_created_at',event_time,
                'ack_reaction_id',reaction_id,
                'ack_created_at',event_time
            );
            SELECT * INTO result FROM autopilot.accept_role_dispatch_codex_ack(
                'github-codex-ack:336-'||run_suffix||'-'||case_number,
                repeat('d',64),true,
                'olegmed1-art/bridge-video-free',1150,
                'olegmed1-art',315099490,'OWNER',
                'chatgpt-codex-connector',1144995,
                'chatgpt-codex-connector[bot]',199175422,ack
            );
            IF NOT result.accepted OR result.resulting_state<>'SENT' THEN
                RAISE EXCEPTION 'AUTOPILOT_CODEX_336_ACK_NOT_RETAINED';
            END IF;
        END IF;

        terminal:=jsonb_build_object(
            'dispatch_id',dispatch.dispatch_id::text,
            'dispatch_epoch',dispatch.dispatch_epoch,
            'role',dispatch.role,
            'task_fingerprint',dispatch.task_fingerprint,
            'target_pr',dispatch.target_pr,
            'status','SUCCEEDED',
            'result_code','CODEX_PINNED_TERMINAL_GREEN',
            'target_head_sha',dispatch.expected_head_sha,
            'summary','Pinned Codex terminal completed inside its bounded window.'
        );

        FOREACH kind IN ARRAY ARRAY[
            'wrong_actor','wrong_actor_id','wrong_app','wrong_app_id',
            'unverified_signature','wrong_repository','wrong_event_pr',
            'wrong_target_pr','wrong_role','wrong_dispatch_id',
            'stale_head','wrong_epoch','wrong_fingerprint',
            'expired_deadline','missing_delivery_deadline'
        ] LOOP
            IF kind='missing_delivery_deadline' AND has_ack THEN
                CONTINUE;
            END IF;
            expected_error := CASE WHEN kind IN (
                'wrong_actor','wrong_actor_id','wrong_app','wrong_app_id',
                'unverified_signature','wrong_repository'
            ) THEN 'AUTOPILOT_CODEX_TERMINAL_IDENTITY_INVALID'
              ELSE 'AUTOPILOT_CODEX_TERMINAL_BINDING_INVALID' END;
            candidate := terminal;
            IF kind='stale_head' THEN
                candidate := terminal||jsonb_build_object('target_head_sha',repeat('9',40));
            ELSIF kind='wrong_epoch' THEN
                candidate := terminal||jsonb_build_object('dispatch_epoch',dispatch.dispatch_epoch+1);
            ELSIF kind='wrong_fingerprint' THEN
                candidate := terminal||jsonb_build_object('task_fingerprint',repeat('9',64));
            ELSIF kind='wrong_target_pr' THEN
                candidate := terminal||jsonb_build_object('target_pr',dispatch.target_pr+1);
            ELSIF kind='wrong_role' THEN
                candidate := terminal||jsonb_build_object('role','VIDEO_QUEUE');
            ELSIF kind='wrong_dispatch_id' THEN
                candidate := terminal||jsonb_build_object(
                    'dispatch_id','00000000-0000-4000-8000-000000000336'
                );
            END IF;
            BEGIN
                -- These changes roll back with the expected exception.
                -- The fixture stays live; reconciliation has not run yet.
                IF kind='expired_deadline' THEN
                    UPDATE autopilot.role_dispatch_outbox
                       SET delivery_deadline_at=CASE WHEN NOT has_ack
                               THEN clock_timestamp()-interval '1 second'
                               ELSE delivery_deadline_at END,
                           callback_deadline_at=CASE WHEN has_ack
                               THEN clock_timestamp()-interval '1 second'
                               ELSE callback_deadline_at END
                     WHERE dispatch_id=dispatch.dispatch_id;
                ELSIF kind='missing_delivery_deadline' THEN
                    UPDATE autopilot.role_dispatch_outbox
                       SET delivery_deadline_at=NULL
                     WHERE dispatch_id=dispatch.dispatch_id;
                END IF;
                PERFORM * FROM autopilot.accept_role_dispatch_codex_terminal(
                    delivery_id||'-'||kind,repeat('8',64),kind<>'unverified_signature',
                    CASE WHEN kind='wrong_repository' THEN 'other/repository'
                         ELSE 'olegmed1-art/bridge-video-free' END,
                    CASE WHEN kind='wrong_event_pr' THEN 1151 ELSE 1150 END,
                    CASE WHEN kind='wrong_actor' THEN 'lookalike[bot]'
                         ELSE 'chatgpt-codex-connector[bot]' END,
                    CASE WHEN kind='wrong_actor_id' THEN 199175423 ELSE 199175422 END,
                    'NONE',
                    CASE WHEN kind='wrong_app' THEN 'lookalike-app'
                         ELSE 'chatgpt-codex-connector' END,
                    CASE WHEN kind='wrong_app_id' THEN 1144996 ELSE 1144995 END,
                    candidate
                );
                RAISE EXCEPTION 'AUTOPILOT_CODEX_336_INVALID_ACCEPTED: % ack=%',kind,has_ack;
            EXCEPTION WHEN OTHERS THEN
                IF SQLERRM IS DISTINCT FROM expected_error THEN
                    RAISE;
                END IF;
            END;
        END LOOP;
        IF EXISTS (SELECT 1 FROM autopilot.role_dispatch_codex_terminal_receipt
                    WHERE dispatch_id=dispatch.dispatch_id)
           OR (SELECT status FROM autopilot.task WHERE task_id=task_row.task_id)<>'WAITING_EXTERNAL'
           OR (SELECT status FROM autopilot.role_dispatch_outbox
                WHERE dispatch_id=dispatch.dispatch_id) IS DISTINCT FROM
              (CASE WHEN has_ack THEN 'SENT' ELSE 'PUBLISHED' END) THEN
            RAISE EXCEPTION 'AUTOPILOT_CODEX_336_REJECTION_CHANGED_STATE';
        END IF;

        SELECT * INTO result FROM autopilot.accept_role_dispatch_codex_terminal(
            delivery_id,repeat('8',64),true,
            'olegmed1-art/bridge-video-free',1150,
            'chatgpt-codex-connector[bot]',199175422,'NONE',
            'chatgpt-codex-connector',1144995,terminal
        );
        IF NOT result.accepted OR result.duplicate
           OR result.resulting_state<>'DONE'
           OR (SELECT status FROM autopilot.role_dispatch_outbox
                WHERE dispatch_id=dispatch.dispatch_id)<>'CALLBACK_ACCEPTED'
           OR (SELECT status FROM autopilot.task WHERE task_id=task_row.task_id)<>'DONE'
           OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=work_id)<>'DONE'
           OR (SELECT count(*) FROM autopilot.role_dispatch_codex_terminal_receipt
                WHERE dispatch_id=dispatch.dispatch_id)<>1
           OR (SELECT count(*) FROM autopilot.role_dispatch_codex_delivery_proof
                WHERE dispatch_id=dispatch.dispatch_id)<>(CASE WHEN has_ack THEN 1 ELSE 0 END)
           OR ((SELECT codex_ack_reaction_id FROM autopilot.role_dispatch_outbox
                 WHERE dispatch_id=dispatch.dispatch_id) IS NOT NULL) IS DISTINCT FROM has_ack
           OR (SELECT delivered_at FROM autopilot.role_dispatch_outbox
                WHERE dispatch_id=dispatch.dispatch_id) IS NULL
           OR (SELECT count(*) FROM autopilot.evidence
                WHERE task_id=task_row.task_id
                  AND evidence_class='CHATGPT_ROLE_DISPATCH_RESULT'
                  AND retained)<>1
           OR NOT EXISTS (
                SELECT 1 FROM autopilot.evidence
                 WHERE task_id=task_row.task_id
                   AND evidence_class='CHATGPT_ROLE_DISPATCH_RESULT'
                   AND metadata_json->>'delivery_proof'=
                       CASE WHEN has_ack THEN 'CODEX_EYES_ACK' ELSE 'PINNED_CODEX_TERMINAL' END
                   AND retained
           ) OR EXISTS (SELECT 1 FROM autopilot.resource_lease
                         WHERE task_id=task_row.task_id AND expires_at>now()) THEN
            RAISE EXCEPTION 'AUTOPILOT_CODEX_IMPLICIT_TERMINAL_NOT_RETAINED ack=%',has_ack;
        END IF;

        -- Accepted redelivery stays idempotent even after its window closes.
        UPDATE autopilot.role_dispatch_outbox
           SET delivery_deadline_at=clock_timestamp()-interval '1 second',
               callback_deadline_at=clock_timestamp()-interval '1 second'
         WHERE dispatch_id=dispatch.dispatch_id;
        SELECT * INTO result FROM autopilot.accept_role_dispatch_codex_terminal(
            delivery_id,repeat('8',64),true,
            'olegmed1-art/bridge-video-free',1150,
            'chatgpt-codex-connector[bot]',199175422,'NONE',
            'chatgpt-codex-connector',1144995,terminal
        );
        IF result.accepted OR NOT result.duplicate THEN
            RAISE EXCEPTION 'AUTOPILOT_CODEX_IMPLICIT_TERMINAL_NOT_IDEMPOTENT';
        END IF;
        SELECT * INTO result FROM autopilot.accept_role_dispatch_codex_terminal(
            delivery_id||'-redelivery',repeat('8',64),true,
            'olegmed1-art/bridge-video-free',1150,
            'chatgpt-codex-connector[bot]',199175422,'NONE',
            'chatgpt-codex-connector',1144995,terminal
        );
        IF result.accepted OR NOT result.duplicate THEN
            RAISE EXCEPTION 'AUTOPILOT_CODEX_336_LOGICAL_DUPLICATE_NOT_IDEMPOTENT';
        END IF;
        BEGIN
            PERFORM * FROM autopilot.accept_role_dispatch_codex_terminal(
                delivery_id,repeat('9',64),true,
                'olegmed1-art/bridge-video-free',1150,
                'chatgpt-codex-connector[bot]',199175422,'NONE',
                'chatgpt-codex-connector',1144995,terminal
            );
            RAISE EXCEPTION 'AUTOPILOT_CODEX_336_CONFLICT_ACCEPTED';
        EXCEPTION WHEN OTHERS THEN
            IF SQLERRM IS DISTINCT FROM 'AUTOPILOT_CODEX_TERMINAL_CONFLICT' THEN
                RAISE;
            END IF;
        END;
        IF (SELECT count(*) FROM autopilot.role_dispatch_codex_terminal_receipt
             WHERE dispatch_id=dispatch.dispatch_id)<>1 THEN
            RAISE EXCEPTION 'AUTOPILOT_CODEX_336_DUPLICATE_CREATED_RECEIPT';
        END IF;
    END LOOP;
END $$;

\if :persist_accepted
COMMIT;
\else
ROLLBACK;
\endif
