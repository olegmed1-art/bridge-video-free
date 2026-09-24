\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_files jsonb := '[
      {"sequence":1,"file_id":"driveVideo00000001","name":"Lesson 1.mp4","mime_type":"video/mp4","size_bytes":2000000,"checksum":"md5:11111111111111111111111111111111"},
      {"sequence":2,"file_id":"driveVideo00000002","name":"Lesson 2.avi","mime_type":"video/x-msvideo","size_bytes":3000000,"checksum":null},
      {"sequence":3,"file_id":"driveVideo00000003","name":"Lesson 3.mkv","mime_type":"video/x-matroska","size_bytes":4000000,"checksum":"sha256:3333333333333333333333333333333333333333333333333333333333333333"}
    ]'::jsonb;
    v_batch uuid;
    v_again uuid;
    v_job uuid;
    v_second uuid;
    v_third uuid;
    v_token uuid;
    v_second_token uuid;
    v_third_token uuid;
    v_state text;
    v_released integer;
    v_stable_job_key text;
    v_manifest jsonb;
    v_manifest_sha text;
    v_receipt_core jsonb;
    v_receipt jsonb;
    v_evidence_sha text;
    v_output jsonb;
    v_valid_manifest jsonb;
    v_case record;
    v_before_job jsonb;
    v_before_batch jsonb;
    v_before_events jsonb;
    v_constraint text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_migration WHERE migration_key='0056_universal_video_queue') THEN
        RAISE EXCEPTION 'video queue migration is not registered';
    END IF;
    IF has_table_privilege('bridge_school_worker','video_queue.job','INSERT')
       OR has_table_privilege('bridge_school_worker','video_queue.job','UPDATE')
       OR has_table_privilege('bridge_school_worker','video_queue.job','DELETE') THEN
        RAISE EXCEPTION 'worker has forbidden direct job mutation';
    END IF;
    IF has_function_privilege('bridge_school_app','video_queue.claim_job(text,integer,text,text)','EXECUTE') THEN
        RAISE EXCEPTION 'application can claim worker jobs';
    END IF;
    IF NOT has_function_privilege('bridge_school_app','video_queue.enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb)','EXECUTE')
       OR NOT has_function_privilege('bridge_school_worker','video_queue.finish_job(uuid,uuid,text,text,jsonb,text)','EXECUTE') THEN
        RAISE EXCEPTION 'required queue capabilities are missing';
    END IF;

    SELECT e.batch_id INTO v_batch
      FROM video_queue.enqueue_drive_batch(
        'generic-batch-001','sourceFolder000001','outputFolder00001','workFolder0000001',
        'bridge_3_1_free','3.1-free-r25.16','driveVideo00000002',repeat('a',64),v_files
      ) e;
    SELECT e.batch_id INTO v_again
      FROM video_queue.enqueue_drive_batch(
        'generic-batch-001','sourceFolder000001','outputFolder00001','workFolder0000001',
        'bridge_3_1_free','3.1-free-r25.16','driveVideo00000002',repeat('a',64),v_files
      ) e;
    IF v_batch IS NULL OR v_again <> v_batch THEN
        RAISE EXCEPTION 'idempotent enqueue did not return one batch';
    END IF;
    IF (SELECT count(*) FROM video_queue.job WHERE batch_id=v_batch) <> 3
       OR (SELECT count(*) FROM video_queue.job WHERE batch_id=v_batch AND status='QUEUED' AND is_canary) <> 1
       OR (SELECT count(*) FROM video_queue.job WHERE batch_id=v_batch AND status='PENDING_CANARY') <> 2 THEN
        RAISE EXCEPTION 'canary-first queue shape mismatch';
    END IF;

    BEGIN
        PERFORM * FROM video_queue.enqueue_drive_batch(
          'generic-batch-001','sourceFolder000001','outputFolder00001','workFolder0000001',
          'bridge_3_1_free','3.1-free-r25.16','driveVideo00000002',repeat('b',64),v_files
        );
        RAISE EXCEPTION 'conflicting idempotency key was accepted';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'VIDEO_BATCH_REQUEST_CONFLICT' THEN RAISE; END IF;
    END;

    SELECT c.job_id, c.lease_token INTO v_job, v_token
      FROM video_queue.claim_job('worker-1',900,'bridge_3_1_free','3.1-free-r25.16') c;
    IF v_job IS NULL OR NOT (SELECT is_canary FROM video_queue.job WHERE job_id=v_job) THEN
        RAISE EXCEPTION 'first claim is not the canary';
    END IF;
    BEGIN
        PERFORM * FROM video_queue.finish_job(
          v_job, gen_random_uuid(), 'worker-1', 'REVIEW_READY',
          jsonb_build_object(
            'result_mode','SHADOW_REVIEW_ONLY','canonical_promotion_allowed',false,
            'database_persistence_allowed',false,'publication_state','NOT_PUBLISHED',
            'source_file_id','driveVideo00000002',
            'stable_job_key',(SELECT stable_job_key FROM video_queue.job WHERE job_id=v_job),
            'algorithm_revision','3.1-free-r25.16'
          ), NULL
        );
        RAISE EXCEPTION 'wrong fencing token was accepted';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'VIDEO_QUEUE_LEASE_LOST' THEN RAISE; END IF;
    END;

    SELECT stable_job_key INTO v_stable_job_key
      FROM video_queue.job
     WHERE job_id=v_job;

    v_manifest := jsonb_build_object(
      'schema_version','universal-video-artifact-manifest/v1',
      'job_id',v_stable_job_key,
      'source_file_id','driveVideo00000002',
      'algorithm_revision','3.1-free-r25.16',
      'result_mode','SHADOW_REVIEW_ONLY',
      'canonical_promotion_allowed',false,
      'database_persistence_allowed',false,
      'publication_state','NOT_PUBLISHED',
      'source_identity',jsonb_build_object(
        'file_id','driveVideo00000002',
        'name','Lesson 2.avi',
        'mime_type','video/x-msvideo',
        'size_bytes',3000000,
        'parent_folder_id','sourceFolder000001',
        'checksum',NULL
      ),
      'artifacts',jsonb_build_array(
        jsonb_build_object(
          'kind','master_pdf',
          'locator','gdrive:file:resultDrive000001',
          'drive_id','resultDrive000001',
          'name','Lesson 2 review.pdf',
          'mime_type','application/pdf',
          'size_bytes',2048,
          'parent_id','outputFolder00001',
          'modified_time','2026-09-03T00:00:00Z',
          'version','101',
          'sha256',repeat('c',64)
        ),
        jsonb_build_object(
          'kind','ai_done',
          'locator','gdrive:file:resultAiDone000001',
          'drive_id','resultAiDone000001',
          'name','AI_DONE_'||v_stable_job_key||'.json',
          'mime_type','application/json',
          'size_bytes',1024,
          'parent_id','outputFolder00001',
          'modified_time','2026-09-03T00:00:01Z',
          'version','102',
          'sha256',repeat('d',64)
        )
      )
    );
    v_valid_manifest := v_manifest;
    FOR v_case IN SELECT * FROM (VALUES
       (536870912::numeric,8388608::numeric,true),
       (1::numeric,1::numeric,true),
       (536870913::numeric,1024::numeric,false),
       (2048::numeric,8388609::numeric,false),
       (1.5::numeric,1024::numeric,false),
       (2048::numeric,1.5::numeric,false)
    ) AS cases(pdf_size,ai_size,allowed)
    LOOP
        v_manifest := jsonb_set(jsonb_set(v_valid_manifest,
          '{artifacts,0,size_bytes}',to_jsonb(v_case.pdf_size)),
          '{artifacts,1,size_bytes}',to_jsonb(v_case.ai_size));
    v_manifest_sha := encode(
      public.digest(convert_to(video_queue.canonical_json_text(v_manifest),'UTF8'),'sha256'),
      'hex'
    );
    v_receipt_core := jsonb_build_object(
      'schema_version','universal-video-terminal-receipt/v1',
      'status','PASS',
      'job_id',v_stable_job_key,
      'source_file_id','driveVideo00000002',
      'source_identity_verified',true,
      'drive_readback_verified',true,
      'result_readback_verified',true,
      'checksum_verified',true,
      'artifact_count',2,
      'artifact_manifest_sha256',v_manifest_sha,
      'canonical_promotion_allowed',false,
      'database_persistence_allowed',false,
      'publication_state','NOT_PUBLISHED'
    );
    v_evidence_sha := encode(
      public.digest(convert_to(video_queue.canonical_json_text(v_receipt_core),'UTF8'),'sha256'),
      'hex'
    );
    v_receipt := v_receipt_core || jsonb_build_object('evidence_sha256',v_evidence_sha);

    v_output := jsonb_build_object(
          'result_mode','SHADOW_REVIEW_ONLY','canonical_promotion_allowed',false,
          'database_persistence_allowed',false,'publication_state','NOT_PUBLISHED',
          'source_file_id','driveVideo00000002',
          'stable_job_key',v_stable_job_key,
          'algorithm_revision','3.1-free-r25.16',
          'master_pdf_drive_id','resultDrive000001',
          'master_pdf_sha256',repeat('c',64),
          'ai_done_drive_id','resultAiDone000001',
          'ai_done_sha256',repeat('d',64),
          'artifact_locators',jsonb_build_object(
            'master_pdf','resultDrive000001',
            'ai_done','resultAiDone000001'
          ),
          'artifact_manifest',v_manifest,
          'artifact_manifest_sha256',v_manifest_sha,
          'terminal_receipt',v_receipt,
          'terminal_evidence_sha256',v_evidence_sha
        );
        SELECT to_jsonb(j) INTO v_before_job FROM video_queue.job j WHERE job_id=v_job;
        SELECT to_jsonb(b) INTO v_before_batch FROM video_queue.batch b WHERE batch_id=v_batch;
        SELECT coalesce(jsonb_agg(to_jsonb(e) ORDER BY event_id),'[]'::jsonb)
          INTO v_before_events FROM video_queue.job_event e WHERE batch_id=v_batch;
        BEGIN
            PERFORM * FROM video_queue.finish_job(v_job,v_token,'worker-1','REVIEW_READY',v_output,NULL);
            IF NOT v_case.allowed THEN RAISE EXCEPTION 'OVERSIZED_OR_FRACTIONAL_EVIDENCE_ACCEPTED'; END IF;
            -- Undo even the accepted boundary cases, retaining the same lease.
            RAISE EXCEPTION 'ROLLBACK_ACCEPTED_BOUNDARY' USING ERRCODE='P0002';
        EXCEPTION
            WHEN check_violation THEN
                GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
                IF v_case.allowed OR v_constraint <> 'video_job_terminal_size_check' THEN RAISE; END IF;
            WHEN SQLSTATE 'P0002' THEN
                IF NOT v_case.allowed OR SQLERRM <> 'ROLLBACK_ACCEPTED_BOUNDARY' THEN RAISE; END IF;
        END;
        IF (SELECT to_jsonb(j) FROM video_queue.job j WHERE job_id=v_job) IS DISTINCT FROM v_before_job
           OR (SELECT to_jsonb(b) FROM video_queue.batch b WHERE batch_id=v_batch) IS DISTINCT FROM v_before_batch
           OR (SELECT coalesce(jsonb_agg(to_jsonb(e) ORDER BY event_id),'[]'::jsonb)
               FROM video_queue.job_event e WHERE batch_id=v_batch) IS DISTINCT FROM v_before_events THEN
            RAISE EXCEPTION 'SIZE_REJECTION_NOT_ATOMIC';
        END IF;
    END LOOP;
END $$;
ROLLBACK;

-- Exercise rollback/reapply on the ephemeral CI database, never production.
CREATE TEMP TABLE size_guard_before AS
SELECT (SELECT count(*) FROM video_queue.job) AS jobs,
       (SELECT count(*) FROM video_queue.batch) AS batches,
       (SELECT count(*) FROM video_queue.job_event) AS events;
\ir ../rollbacks/0059_universal_video_terminal_size_guard.sql
DO $$
BEGIN
 IF EXISTS (SELECT FROM pg_constraint WHERE conrelid='video_queue.job'::regclass
             AND conname='video_job_terminal_size_check')
    OR EXISTS (SELECT FROM schema_migration WHERE migration_key='0059_universal_video_terminal_size_guard')
    OR NOT EXISTS (SELECT FROM schema_migration WHERE migration_key='0058_universal_video_terminal_v2_gate') THEN
   RAISE EXCEPTION 'SIZE_GUARD_ROLLBACK_SCOPE_INVALID';
 END IF;
END $$;
\ir ../migrations/0059_universal_video_terminal_size_guard.sql
DO $$
BEGIN
 IF NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid='video_queue.job'::regclass
                AND conname='video_job_terminal_size_check' AND NOT convalidated)
    OR (SELECT jobs FROM size_guard_before) <> (SELECT count(*) FROM video_queue.job)
    OR (SELECT batches FROM size_guard_before) <> (SELECT count(*) FROM video_queue.batch)
    OR (SELECT events FROM size_guard_before) <> (SELECT count(*) FROM video_queue.job_event) THEN
   RAISE EXCEPTION 'SIZE_GUARD_REAPPLY_CHANGED_HISTORY';
 END IF;
END $$;
DROP TABLE size_guard_before;
