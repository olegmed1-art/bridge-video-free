\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    unmapped_id uuid;
    mapped_id uuid;
    probe record;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0328_autopilot_chat_target_gate'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CHAT_TARGET_GATE_MIGRATION_MISSING';
    END IF;

    SELECT work_item_id INTO unmapped_id
      FROM autopilot.register_universal_work_item(
        'sql-chat-target-unmapped-328','VIDEO_QUEUE','CHAT_TARGET_GATE_TEST',
        'Retain unmapped work without creating an undeliverable dispatch.',
        1150,0,'{}'::jsonb,NULL,'database-test','SQL_TEST'
      );
    SELECT work_item_id INTO mapped_id
      FROM autopilot.register_universal_work_item(
        'sql-chat-target-mapped-328','AUTOPILOT','CHAT_TARGET_GATE_TEST',
        'Select mapped work even when an unmapped item has higher priority.',
        1150,10,'{}'::jsonb,NULL,'database-test','SQL_TEST'
      );

    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-chat-target-worker-328',60);
    IF probe.work_item_id IS DISTINCT FROM mapped_id OR probe.role <> 'AUTOPILOT' THEN
        RAISE EXCEPTION 'AUTOPILOT_UNMAPPED_ROLE_WAS_CLAIMED';
    END IF;
    IF (SELECT state FROM autopilot.project_work_item WHERE work_item_id=unmapped_id) <> 'READY' THEN
        RAISE EXCEPTION 'AUTOPILOT_UNMAPPED_WORK_NOT_RETAINED';
    END IF;

    PERFORM * FROM autopilot.materialize_project_work_probe(
        mapped_id,'sql-chat-target-worker-328',probe.lease_epoch,false,repeat('a',40)
    );
    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-chat-target-worker-328',60);
    IF FOUND THEN
        RAISE EXCEPTION 'AUTOPILOT_UNMAPPED_ROLE_BECAME_ELIGIBLE';
    END IF;
    IF (SELECT last_decision_code FROM autopilot.project_planner_state WHERE singleton)
       <> 'WAITING_FOR_CHAT_TARGET' THEN
        RAISE EXCEPTION 'AUTOPILOT_CHAT_TARGET_WAIT_REASON_MISSING';
    END IF;
END $$;

ROLLBACK;
