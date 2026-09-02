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
          'sha256',repeat('d',64)
        )
      )
    );
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

    SELECT f.batch_status, f.released_jobs INTO v_state, v_released
      FROM video_queue.finish_job(
        v_job, v_token, 'worker-1', 'REVIEW_READY',
        jsonb_build_object(
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
        ), NULL
      ) f;
    IF v_state <> 'CANARY_REVIEW' OR v_released <> 0
       OR (SELECT count(*) FROM video_queue.job WHERE batch_id=v_batch AND status='PENDING_CANARY') <> 2 THEN
        RAISE EXCEPTION 'successful canary escaped explicit review gate';
    END IF;

    -- Simulate a separate, explicit Director release so the generic queue
    -- concurrency and terminalization invariants remain covered. finish_job
    -- itself must never perform these mutations.
    UPDATE video_queue.batch
       SET status='RUNNING', updated_at=clock_timestamp()
     WHERE batch_id=v_batch AND status='CANARY_REVIEW';
    UPDATE video_queue.job
       SET status='QUEUED', updated_at=clock_timestamp()
     WHERE batch_id=v_batch AND status='PENDING_CANARY';

    SELECT c.job_id, c.lease_token INTO v_second, v_second_token
      FROM video_queue.claim_job('worker-2',900,'bridge_3_1_free','3.1-free-r25.16') c;
    SELECT c.job_id, c.lease_token INTO v_third, v_third_token
      FROM video_queue.claim_job('worker-3',900,'bridge_3_1_free','3.1-free-r25.16') c;
    IF v_second IS NULL OR v_third IS NULL OR v_second=v_third THEN
        RAISE EXCEPTION 'independent claims were not fenced';
    END IF;
    PERFORM video_queue.heartbeat_job(v_second,v_second_token,'worker-2',900);
    PERFORM * FROM video_queue.finish_job(
      v_second,v_second_token,'worker-2','AMBIGUOUS',
      jsonb_build_object(
        'result_mode','SHADOW_REVIEW_ONLY','canonical_promotion_allowed',false,
        'database_persistence_allowed',false,'publication_state','NOT_PUBLISHED',
        'source_file_id',(SELECT source_file_id FROM video_queue.job WHERE job_id=v_second),
        'stable_job_key',(SELECT stable_job_key FROM video_queue.job WHERE job_id=v_second),
        'algorithm_revision','3.1-free-r25.16'
      ),'UV_CONTENT_AMBIGUOUS'
    );
    SELECT f.batch_status INTO v_state
      FROM video_queue.finish_job(
        v_third,v_third_token,'worker-3','FAILED',
        jsonb_build_object(
          'result_mode','SHADOW_REVIEW_ONLY','canonical_promotion_allowed',false,
          'database_persistence_allowed',false,'publication_state','NOT_PUBLISHED',
          'source_file_id',(SELECT source_file_id FROM video_queue.job WHERE job_id=v_third),
          'stable_job_key',(SELECT stable_job_key FROM video_queue.job WHERE job_id=v_third),
          'algorithm_revision','3.1-free-r25.16'
        ),'UV_ITEM_FAILED'
      ) f;
    IF v_state <> 'REVIEW' THEN
        RAISE EXCEPTION 'batch did not terminate at REVIEW';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM video_queue.batch_status
         WHERE batch_id=v_batch AND review_ready=1 AND ambiguous=1 AND failed=1
           AND NOT canonical_promotion_allowed AND NOT database_persistence_allowed
    ) THEN
        RAISE EXCEPTION 'batch summary mismatch';
    END IF;
    IF EXISTS (SELECT 1 FROM video_queue.job WHERE batch_id=v_batch AND output->>'result_mode'<>'SHADOW_REVIEW_ONLY') THEN
        RAISE EXCEPTION 'non-review result escaped';
    END IF;
END $$;

ROLLBACK;
