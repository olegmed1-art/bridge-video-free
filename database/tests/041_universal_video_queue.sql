\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_files jsonb := '[
      {"sequence":1,"file_id":"driveVideo00000001","name":"Lesson 1.mp4","mime_type":"video/mp4","size_bytes":2000000,"checksum":"md5:11111111111111111111111111111111"},
      {"sequence":2,"file_id":"driveVideo00000002","name":"Lesson 2.avi","mime_type":"video/x-msvideo","size_bytes":3000000,"checksum":"md5:22222222222222222222222222222222"},
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
    v_manifest jsonb;
    v_receipt jsonb;
    v_output jsonb;
    v_invalid jsonb;
    v_path text[];
    v_variant text;
    v_jobs_before jsonb;
    v_batch_before jsonb;
    v_events_before bigint;
    v_rejected integer := 0;
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
        'generic-batch-001',
        'sourceFolder000001',
        'outputFolder00001',
        'workFolder0000001',
        'bridge_3_1_free',
        '3.1-free-r25.16',
        'driveVideo00000002',
        repeat('a',64),
        v_files
      ) e;
    SELECT e.batch_id INTO v_again
      FROM video_queue.enqueue_drive_batch(
        'generic-batch-001',
        'sourceFolder000001',
        'outputFolder00001',
        'workFolder0000001',
        'bridge_3_1_free',
        '3.1-free-r25.16',
        'driveVideo00000002',
        repeat('a',64),
        v_files
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
    SELECT jsonb_agg(to_jsonb(j) ORDER BY j.job_id) INTO v_jobs_before
      FROM video_queue.job j WHERE batch_id=v_batch;
    SELECT to_jsonb(b) INTO v_batch_before FROM video_queue.batch b WHERE batch_id=v_batch;
    SELECT count(*) INTO v_events_before FROM video_queue.job_event WHERE batch_id=v_batch;
    BEGIN
      PERFORM * FROM video_queue.finish_job(
        v_job, v_token, 'worker-1', 'REVIEW_READY', jsonb_build_object(
          'result_mode','SHADOW_REVIEW_ONLY','canonical_promotion_allowed',false,
          'database_persistence_allowed',false,'publication_state','NOT_PUBLISHED',
          'source_file_id','driveVideo00000002',
          'stable_job_key',(SELECT stable_job_key FROM video_queue.job WHERE job_id=v_job),
          'algorithm_revision','3.1-free-r25.16'
        ), NULL);
      RAISE EXCEPTION 'minimal terminal evidence was accepted';
    EXCEPTION WHEN raise_exception THEN
      IF SQLERRM <> 'VIDEO_QUEUE_TERMINAL_EVIDENCE_INVALID' THEN RAISE; END IF;
    END;
    IF (SELECT jsonb_agg(to_jsonb(j) ORDER BY j.job_id) FROM video_queue.job j WHERE batch_id=v_batch) IS DISTINCT FROM v_jobs_before
       OR (SELECT to_jsonb(b) FROM video_queue.batch b WHERE batch_id=v_batch) IS DISTINCT FROM v_batch_before
       OR (SELECT count(*) FROM video_queue.job_event WHERE batch_id=v_batch) <> v_events_before THEN
      RAISE EXCEPTION 'failed terminal evidence mutated queue state';
    END IF;

    v_manifest := jsonb_build_object(
      'schema','universal-video-terminal-manifest-v1',
      'job_id',(SELECT stable_job_key FROM video_queue.job WHERE job_id=v_job),
      'source_file_id','driveVideo00000002',
      'source_identity',jsonb_build_object('file_id','driveVideo00000002','name','Lesson 2.avi','mime_type','video/x-msvideo','size_bytes',3000000,'parent_folder_id','sourceFolder000001','checksum','md5:'||repeat('2',32)),
      'algorithm_revision','3.1-free-r25.16','result_mode','SHADOW_REVIEW_ONLY','publication_state','NOT_PUBLISHED',
      'canonical_promotion_allowed',false,'database_persistence_allowed',false,
      'artifacts',jsonb_build_array(
        jsonb_build_object('kind','master_pdf','drive_file_id','masterPdf0000001','name','master.pdf','mime_type','application/pdf','size_bytes',100,'sha256',repeat('1',64),'md5',repeat('1',32),'parent_folder_id','outputFolder00001'),
        jsonb_build_object('kind','ai_done','drive_file_id','aiDoneJson000001','name','AI_DONE.json','mime_type','application/json','size_bytes',200,'sha256',repeat('2',64),'md5',repeat('2',32),'parent_folder_id','outputFolder00001')));
    v_receipt := jsonb_build_object(
      'schema','universal-video-terminal-receipt-v1','job_id',(SELECT stable_job_key FROM video_queue.job WHERE job_id=v_job),
      'source_file_id','driveVideo00000002','source_identity',v_manifest->'source_identity','source_identity_verified',true,
      'route_readback_verified',true,'result_readback_verified',true,'checksum_verified',true,
      'manifest_sha256',encode(public.digest(convert_to(video_queue.canonical_json(v_manifest),'UTF8'),'sha256'),'hex'),
      'artifact_count',2,'publication_state','NOT_PUBLISHED','canonical_promotion_allowed',false,
      'database_persistence_allowed',false,'media_execution_evidence_only',true);
    v_receipt := v_receipt || jsonb_build_object('evidence_sha256',encode(public.digest(convert_to(video_queue.canonical_json(v_receipt),'UTF8'),'sha256'),'hex'));
    v_output := jsonb_build_object(
      'result_mode','SHADOW_REVIEW_ONLY','canonical_promotion_allowed',false,'database_persistence_allowed',false,'publication_state','NOT_PUBLISHED',
      'source_file_id','driveVideo00000002','stable_job_key',(SELECT stable_job_key FROM video_queue.job WHERE job_id=v_job),'algorithm_revision','3.1-free-r25.16',
      'manifest',v_manifest,'artifact_locators',jsonb_build_object('master_pdf_drive_id','masterPdf0000001','ai_done_drive_id','aiDoneJson000001'),
      'terminal_receipt',v_receipt,'terminal_evidence_sha256',v_receipt->>'evidence_sha256');

    BEGIN
      PERFORM * FROM video_queue.finish_job(v_job,v_token,'worker-1','REVIEW_READY',jsonb_set(v_output,'{manifest,artifacts,1,mime_type}','"text/plain"'),NULL);
      RAISE EXCEPTION 'contradictory artifact evidence was accepted';
    EXCEPTION WHEN raise_exception THEN
      IF SQLERRM <> 'VIDEO_QUEUE_TERMINAL_ARTIFACT_INVALID' THEN RAISE; END IF;
    END;
    IF (SELECT status FROM video_queue.job WHERE job_id=v_job) <> 'LEASED' THEN RAISE EXCEPTION 'contradictory evidence mutated job'; END IF;

    -- Recompute the dependent hashes so these vectors test required fields,
    -- rather than accidentally passing only because a stale digest rejected them.
    FOR v_path IN
      SELECT string_to_array(path, '.') FROM unnest(ARRAY[
        'result_mode','publication_state','source_file_id','stable_job_key','algorithm_revision',
        'manifest.schema','manifest.job_id','manifest.source_file_id','manifest.algorithm_revision',
        'manifest.result_mode','manifest.publication_state','manifest.artifacts',
        'manifest.artifacts.0.drive_file_id','manifest.artifacts.0.mime_type','manifest.artifacts.0.parent_folder_id',
        'manifest.artifacts.1.drive_file_id','manifest.artifacts.1.mime_type','manifest.artifacts.1.parent_folder_id',
        'artifact_locators.master_pdf_drive_id','artifact_locators.ai_done_drive_id',
        'terminal_receipt.schema','terminal_receipt.job_id','terminal_receipt.source_file_id',
        'terminal_receipt.publication_state','terminal_receipt.manifest_sha256',
        'terminal_receipt.evidence_sha256','terminal_evidence_sha256'
      ]) AS required(path)
    LOOP
      FOREACH v_variant IN ARRAY ARRAY['missing','null'] LOOP
        v_invalid := CASE WHEN v_variant='missing' THEN v_output #- v_path
                          ELSE jsonb_set(v_output,v_path,'null'::jsonb) END;
        IF v_path[1]='manifest' THEN
          v_invalid := jsonb_set(v_invalid,'{terminal_receipt,manifest_sha256}',
            to_jsonb(encode(public.digest(convert_to(video_queue.canonical_json(v_invalid->'manifest'),'UTF8'),'sha256'),'hex')));
        END IF;
        IF v_path <> ARRAY['terminal_receipt','evidence_sha256'] AND v_path <> ARRAY['terminal_evidence_sha256'] THEN
          v_receipt := (v_invalid->'terminal_receipt') - 'evidence_sha256';
          v_receipt := v_receipt || jsonb_build_object('evidence_sha256',
            encode(public.digest(convert_to(video_queue.canonical_json(v_receipt),'UTF8'),'sha256'),'hex'));
          v_invalid := jsonb_set(jsonb_set(v_invalid,'{terminal_receipt}',v_receipt),
            '{terminal_evidence_sha256}',v_receipt->'evidence_sha256');
        END IF;
        BEGIN
          PERFORM * FROM video_queue.finish_job(v_job,v_token,'worker-1','REVIEW_READY',v_invalid,NULL);
          RAISE EXCEPTION 'incomplete rehashed evidence accepted: % %',v_path,v_variant;
        EXCEPTION WHEN raise_exception THEN
          IF SQLERRM NOT IN ('VIDEO_QUEUE_FINISH_ARGUMENT_INVALID','VIDEO_QUEUE_RESULT_IDENTITY_MISMATCH',
            'VIDEO_QUEUE_TERMINAL_EVIDENCE_INVALID','VIDEO_QUEUE_TERMINAL_ARTIFACT_INVALID','VIDEO_QUEUE_TERMINAL_RECEIPT_INVALID') THEN RAISE; END IF;
        END;
        v_rejected := v_rejected + 1;
        IF (SELECT jsonb_agg(to_jsonb(j) ORDER BY j.job_id) FROM video_queue.job j WHERE batch_id=v_batch) IS DISTINCT FROM v_jobs_before
           OR (SELECT to_jsonb(b) FROM video_queue.batch b WHERE batch_id=v_batch) IS DISTINCT FROM v_batch_before
           OR (SELECT count(*) FROM video_queue.job_event WHERE batch_id=v_batch) <> v_events_before THEN
          RAISE EXCEPTION 'invalid rehashed evidence mutated queue state';
        END IF;
      END LOOP;
    END LOOP;
    IF v_rejected <> 54 THEN RAISE EXCEPTION 'required-field rejection coverage incomplete'; END IF;
    BEGIN
      PERFORM * FROM video_queue.finish_job(v_job,NULL,'worker-1','REVIEW_READY',v_output,NULL);
      RAISE EXCEPTION 'null fencing token accepted';
    EXCEPTION WHEN raise_exception THEN
      IF SQLERRM <> 'VIDEO_QUEUE_LEASE_LOST' THEN RAISE; END IF;
    END;

    SELECT f.batch_status, f.released_jobs INTO v_state, v_released
      FROM video_queue.finish_job(v_job,v_token,'worker-1','REVIEW_READY',v_output,NULL) f;
    IF v_state <> 'RUNNING' OR v_released <> 2
       OR (SELECT count(*) FROM video_queue.job WHERE batch_id=v_batch AND status='QUEUED') <> 2 THEN
        RAISE EXCEPTION 'successful canary did not release remaining jobs';
    END IF;

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
