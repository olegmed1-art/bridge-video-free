-- LOCAL UNEXECUTED RECOVERY CANDIDATE. Run only in the guarded connector
-- transaction envelope; never as standalone SQL. Preserves0059 and all history.
-- The envelope provides target/receipt/drain checks, temporary owner membership,
-- before/after data digests, postconditions and registry checksum insertion.
DROP TRIGGER subset_guard ON video_queue.batch;

DROP TRIGGER subset_no_truncate ON video_queue.batch;

DROP TRIGGER subset_final ON video_queue.batch;

DROP TRIGGER subset_guard ON video_queue.job;

DROP TRIGGER subset_no_truncate ON video_queue.job;

DROP TRIGGER subset_final ON video_queue.job;

CREATE OR REPLACE FUNCTION video_queue.claim_job(p_worker_key text, p_lease_seconds integer, p_processing_profile text, p_algorithm_revision text)
 RETURNS TABLE(job_id uuid, batch_id uuid, lease_token uuid, sequence integer, source_folder_id text, output_folder_id text, work_folder_id text, processing_profile text, algorithm_revision text, source_file_id text, source_name text, source_mime_type text, source_size_bytes bigint, source_checksum text, stable_job_key text, is_canary boolean, attempt_count integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'video_queue'
AS $function$
DECLARE
    v_job video_queue.job%ROWTYPE;
    v_previous_status text;
BEGIN
    IF p_worker_key IS NULL OR p_worker_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
       OR p_lease_seconds NOT BETWEEN 60 AND 3600
       OR p_processing_profile IS NULL OR p_processing_profile !~ '^[a-z][a-z0-9_.-]{0,79}$'
       OR p_algorithm_revision IS NULL OR p_algorithm_revision !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$' THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_CLAIM_ARGUMENT_INVALID';
    END IF;

    -- A worker may disappear before it can call retry_job. Terminalize an
    -- expired third lease here so crash recovery is bounded as well.
    FOR v_job IN
        UPDATE video_queue.job j
           SET status='FAILED', lease_owner=NULL, lease_token=NULL,
               lease_expires_at=NULL, error_code='UV_WORKER_CRASH_RETRY_EXHAUSTED',
               updated_at=clock_timestamp(), completed_at=clock_timestamp()
         WHERE j.status='LEASED' AND j.lease_expires_at <= clock_timestamp()
           AND j.attempt_count >= 3
         RETURNING j.*
    LOOP
        INSERT INTO video_queue.job_event(batch_id,job_id,event_type,details)
        VALUES (v_job.batch_id,v_job.job_id,'FAILED',
                jsonb_build_object('error_code','UV_WORKER_CRASH_RETRY_EXHAUSTED',
                                   'attempt',v_job.attempt_count));
        IF v_job.is_canary THEN
            UPDATE video_queue.batch b
               SET status='CANARY_BLOCKED',updated_at=clock_timestamp(),completed_at=clock_timestamp()
             WHERE b.batch_id=v_job.batch_id;
        ELSIF NOT EXISTS (
            SELECT 1 FROM video_queue.job pending WHERE pending.batch_id=v_job.batch_id
              AND pending.status IN ('PENDING_CANARY','QUEUED','LEASED')
        ) THEN
            UPDATE video_queue.batch b
               SET status='REVIEW',updated_at=clock_timestamp(),completed_at=clock_timestamp()
             WHERE b.batch_id=v_job.batch_id;
        END IF;
    END LOOP;

    SELECT j.* INTO v_job
      FROM video_queue.job j
      JOIN video_queue.batch b ON b.batch_id = j.batch_id
     WHERE (
            (j.status = 'QUEUED' AND j.next_attempt_at <= clock_timestamp())
            OR (j.status = 'LEASED' AND j.lease_expires_at <= clock_timestamp() AND j.attempt_count < 3)
        )
       AND b.status IN ('QUEUED_CANARY','RUNNING')
       AND b.processing_profile = p_processing_profile
       AND b.algorithm_revision = p_algorithm_revision
     ORDER BY b.created_at, j.is_canary DESC, j.sequence
     FOR UPDATE OF j SKIP LOCKED
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    v_previous_status := v_job.status;
    UPDATE video_queue.job j
       SET status = 'LEASED',
           attempt_count = j.attempt_count + 1,
           lease_owner = p_worker_key,
           lease_token = gen_random_uuid(),
           lease_expires_at = clock_timestamp() + make_interval(secs => p_lease_seconds),
           updated_at = clock_timestamp()
     WHERE j.job_id = v_job.job_id
     RETURNING * INTO v_job;
    UPDATE video_queue.batch b
       SET status = 'RUNNING', updated_at = clock_timestamp()
     WHERE b.batch_id = v_job.batch_id
       AND b.status = 'QUEUED_CANARY';
    INSERT INTO video_queue.job_event(batch_id, job_id, event_type, worker_key, lease_token, details)
    VALUES (
        v_job.batch_id,
        v_job.job_id,
        CASE WHEN v_previous_status = 'LEASED' THEN 'RECLAIMED' ELSE 'CLAIMED' END,
        p_worker_key,
        v_job.lease_token,
        jsonb_build_object('attempt', v_job.attempt_count)
    );
    RETURN QUERY
    SELECT
        v_job.job_id,
        v_job.batch_id,
        v_job.lease_token,
        v_job.sequence,
        b.source_folder_id,
        b.output_folder_id,
        b.work_folder_id,
        b.processing_profile,
        b.algorithm_revision,
        v_job.source_file_id,
        v_job.source_name,
        v_job.source_mime_type,
        v_job.source_size_bytes,
        v_job.source_checksum,
        v_job.stable_job_key,
        v_job.is_canary,
        v_job.attempt_count
      FROM video_queue.batch b
     WHERE b.batch_id = v_job.batch_id;
END;
$function$;

ALTER FUNCTION video_queue.claim_job(text,integer,text,text) OWNER TO neondb_owner;

REVOKE ALL ON FUNCTION video_queue.claim_job(text,integer,text,text) FROM PUBLIC, video_subset_owner, video_subset_approver, video_subset_collector, video_subset_admitter, video_subset_worker_v2, bridge_school_app, bridge_school_worker;

GRANT EXECUTE ON FUNCTION video_queue.claim_job(text,integer,text,text) TO bridge_school_worker GRANTED BY CURRENT_USER;

REVOKE ALL ON FUNCTION video_queue.enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb) FROM PUBLIC, video_subset_owner, video_subset_approver, video_subset_collector, video_subset_admitter, video_subset_worker_v2, bridge_school_app, bridge_school_worker;

GRANT EXECUTE ON FUNCTION video_queue.enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb) TO bridge_school_app GRANTED BY CURRENT_USER;

REVOKE ALL ON FUNCTION video_queue.heartbeat_job(uuid,uuid,text,integer) FROM PUBLIC, video_subset_owner, video_subset_approver, video_subset_collector, video_subset_admitter, video_subset_worker_v2, bridge_school_app, bridge_school_worker;

GRANT EXECUTE ON FUNCTION video_queue.heartbeat_job(uuid,uuid,text,integer) TO bridge_school_worker GRANTED BY CURRENT_USER;

REVOKE ALL ON FUNCTION video_queue.retry_job(uuid,uuid,text,text,integer,integer) FROM PUBLIC, video_subset_owner, video_subset_approver, video_subset_collector, video_subset_admitter, video_subset_worker_v2, bridge_school_app, bridge_school_worker;

GRANT EXECUTE ON FUNCTION video_queue.retry_job(uuid,uuid,text,text,integer,integer) TO bridge_school_worker GRANTED BY CURRENT_USER;

REVOKE ALL ON FUNCTION video_queue.finish_job(uuid,uuid,text,text,jsonb,text) FROM PUBLIC, video_subset_owner, video_subset_approver, video_subset_collector, video_subset_admitter, video_subset_worker_v2, bridge_school_app, bridge_school_worker;

GRANT EXECUTE ON FUNCTION video_queue.finish_job(uuid,uuid,text,text,jsonb,text) TO bridge_school_worker GRANTED BY CURRENT_USER;

REVOKE ALL ON TABLE video_queue.batch FROM video_subset_owner;

REVOKE ALL ON TABLE video_queue.job FROM video_subset_owner;

REVOKE ALL ON TABLE video_queue.job_event FROM video_subset_owner;

REVOKE ALL ON SEQUENCE video_queue.job_event_event_id_seq FROM video_subset_owner;

REVOKE ALL ON SCHEMA video_queue FROM video_subset_owner, video_subset_approver, video_subset_collector, video_subset_admitter, video_subset_worker_v2;

DO $disable$
DECLARE f record;
BEGIN
 FOR f IN SELECT p.oid::regprocedure AS signature FROM pg_proc p
 JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='video_queue' AND p.proowner='video_subset_owner'::regrole LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC, video_subset_approver, video_subset_collector, video_subset_admitter, video_subset_worker_v2',f.signature);
 END LOOP;
END $disable$;
