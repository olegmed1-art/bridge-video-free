\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    root_item uuid;
    child_item uuid;
    root_task uuid;
    probe record;
    materialized record;
    claimed_task record;
    dispatch record;
    claimed_outbox record;
    callback_body jsonb;
    callback_result record;
    retry_item uuid;
    retry_task_1 uuid;
    retry_task_2 uuid;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0325_autopilot_durable_dependency_wakeup'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DURABLE_WAKEUP_MIGRATION_MISSING';
    END IF;

    SELECT work_item_id INTO root_item
      FROM autopilot.register_universal_work_item(
          'sql-wakeup-root-325', 'AUTOPILOT', 'CONTROL_PLANE_AUDIT',
          'Complete one bounded non-media dispatcher cycle.',
          1150, 0, '{"media":false}'::jsonb, NULL,
          'database-test', 'SQL_TEST'
      );
    SELECT work_item_id INTO child_item
      FROM autopilot.register_universal_work_item(
          'sql-wakeup-child-325', 'SECURITY', 'DEPENDENCY_VERIFY',
          'Wake only after the prerequisite terminal receipt.',
          1150, 0, '{"media":false}'::jsonb, 'sql-wakeup-root-325',
          'database-test', 'SQL_TEST'
      );

    IF (SELECT state FROM autopilot.project_work_item WHERE work_item_id=child_item)
       <> 'WAITING_DEPENDENCY' THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPENDENCY_NOT_DURABLY_WAITING';
    END IF;

    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-wakeup-worker-325',60);
    IF probe.work_item_id <> root_item THEN
        RAISE EXCEPTION 'AUTOPILOT_WAKEUP_ROOT_NOT_CLAIMED';
    END IF;
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
          root_item,'sql-wakeup-worker-325',probe.lease_epoch,true,repeat('3',40)
      );
    root_task := materialized.task_id;

    -- Exercise the complete retained-receipt path without GitHub, media, or
    -- any external mutation: claim -> prepare -> outbox -> callback receipt.
    SELECT * INTO claimed_task
      FROM autopilot.claim_next_task('sql-wakeup-worker-325',60);
    IF claimed_task.task_id <> root_task THEN
        RAISE EXCEPTION 'AUTOPILOT_WAKEUP_ROOT_TASK_NOT_CLAIMED';
    END IF;
    SELECT * INTO dispatch
      FROM autopilot.prepare_role_dispatch(
          root_task,'sql-wakeup-worker-325',claimed_task.lease_epoch
      );
    SELECT * INTO claimed_outbox
      FROM autopilot.claim_role_dispatch_outbox('sql-wakeup-publisher-325',60);
    IF NOT autopilot.mark_role_dispatch_sent(
        dispatch.dispatch_id,'sql-wakeup-publisher-325',
        claimed_outbox.claim_epoch,9900325,repeat('4',64)
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_WAKEUP_OUTBOX_NOT_SENT';
    END IF;
    callback_body := jsonb_build_object(
        'dispatch_id',dispatch.dispatch_id::text,
        'dispatch_epoch',dispatch.dispatch_epoch,
        'role',dispatch.role,
        'task_fingerprint',dispatch.task_fingerprint,
        'target_pr',dispatch.target_pr,
        'status','SUCCEEDED',
        'result_code','NON_MEDIA_CYCLE_GREEN',
        'target_head_sha',dispatch.expected_head_sha,
        'summary','Bounded non-media dispatcher cycle completed.'
    );
    SELECT * INTO callback_result
      FROM autopilot.accept_role_dispatch_callback(
          'delivery-wakeup-325',repeat('5',64),true,
          'olegmed1-art/bridge-video-free',1150,
          'olegmed1-art',315099490,'OWNER',
          'chatgpt-codex-connector',1144995,callback_body
      );
    IF NOT callback_result.accepted OR callback_result.duplicate
       OR callback_result.task_id <> root_task
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.role_dispatch_callback_receipt
            WHERE dispatch_id=dispatch.dispatch_id
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_WAKEUP_TERMINAL_RECEIPT_NOT_RETAINED';
    END IF;

    IF (SELECT state FROM autopilot.project_work_item WHERE work_item_id=root_item)
       <> 'DONE'
       OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=child_item)
       <> 'READY' THEN
        RAISE EXCEPTION 'AUTOPILOT_TERMINAL_RECEIPT_DID_NOT_WAKE_DEPENDENT';
    END IF;

    -- The same registration is idempotent and cannot create a duplicate.
    PERFORM * FROM autopilot.register_universal_work_item(
        'sql-wakeup-child-325', 'SECURITY', 'DEPENDENCY_VERIFY',
        'Wake only after the prerequisite terminal receipt.',
        1150, 0, '{"media":false}'::jsonb, 'sql-wakeup-root-325',
        'database-test', 'SQL_TEST'
    );
    IF (SELECT count(*) FROM autopilot.project_work_item
         WHERE work_key='sql-wakeup-child-325') <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPENDENCY_WAKEUP_DUPLICATED_WORK';
    END IF;

    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-wakeup-worker-325',60);
    IF probe.work_item_id <> child_item THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPENDENT_NOT_CLAIMABLE_AFTER_RECEIPT';
    END IF;

    -- A transport failure may be retried at the same exact head, but receives
    -- a new generation-scoped task key. Semantic blockers are still pinned.
    SELECT work_item_id INTO retry_item
      FROM autopilot.register_universal_work_item(
          'sql-wakeup-transport-325','AUTOPILOT','TRANSPORT_RETRY_TEST',
          'Retry one failed dispatcher delivery without changing the target head.',
          1150,0,'{"media":false}'::jsonb,NULL,'database-test','SQL_TEST'
      );
    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-wakeup-worker-325',60);
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
          retry_item,'sql-wakeup-worker-325',probe.lease_epoch,true,repeat('6',40)
      );
    retry_task_1 := materialized.task_id;
    UPDATE autopilot.task
       SET status='FAILED_CLOSED',
           terminal_reason_code='STALE_RETRY_BUDGET_EXHAUSTED',
           safe_summary_json='{}'::jsonb,completed_at=now()
     WHERE task_id=retry_task_1;
    UPDATE autopilot.project_work_item SET not_before=now()
     WHERE work_item_id=retry_item;

    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-wakeup-worker-325',60);
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
          retry_item,'sql-wakeup-worker-325',probe.lease_epoch,true,repeat('6',40)
      );
    retry_task_2 := materialized.task_id;
    IF retry_task_2 IS NULL OR retry_task_2=retry_task_1
       OR (SELECT task_key FROM autopilot.task WHERE task_id=retry_task_1)
          !~ ':g1$'
       OR (SELECT task_key FROM autopilot.task WHERE task_id=retry_task_2)
          !~ ':g2$'
       OR (SELECT count(*) FROM autopilot.project_work_item
            WHERE work_key='sql-wakeup-transport-325') <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_SAME_HEAD_TRANSPORT_RETRY_INVALID';
    END IF;
END $$;

ROLLBACK;
