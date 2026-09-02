\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_batch_id uuid;
    v_jobs integer;
BEGIN
    SELECT batch_id INTO v_batch_id
      FROM video_queue.batch
     WHERE request_key = 'issue881-rollback-preserve';
    IF v_batch_id IS NULL THEN
        RAISE EXCEPTION 'rollback fixture batch was lost';
    END IF;
    IF (SELECT status FROM video_queue.batch WHERE batch_id = v_batch_id) <> 'CANARY_BLOCKED' THEN
        RAISE EXCEPTION 'CANARY_REVIEW was not safely terminalized';
    END IF;
    IF (SELECT completed_at FROM video_queue.batch WHERE batch_id = v_batch_id) IS NULL THEN
        RAISE EXCEPTION 'rollback terminal timestamp missing';
    END IF;
    SELECT count(*) INTO v_jobs FROM video_queue.job WHERE batch_id = v_batch_id;
    IF v_jobs <> 2 THEN RAISE EXCEPTION 'rollback did not preserve jobs'; END IF;
    IF EXISTS (
        SELECT 1 FROM schema_migration
         WHERE migration_key = '0057_universal_video_canary_review_gate'
    ) THEN
        RAISE EXCEPTION 'rollback migration ledger mismatch';
    END IF;
    IF to_regprocedure('video_queue.precanary_idle_snapshot()') IS NOT NULL
       OR to_regprocedure('video_queue.canonical_json_text(jsonb)') IS NOT NULL THEN
        RAISE EXCEPTION 'rollback helper functions remain';
    END IF;
END;
$$;

ROLLBACK;
