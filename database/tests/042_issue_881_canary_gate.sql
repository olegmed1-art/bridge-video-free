\set ON_ERROR_STOP on
BEGIN;
DO $$
DECLARE
  b record; same record; c record; f record; r record; c2 record; c3 record; bad record;
  out jsonb; bad_out jsonb; manifest jsonb; receipt_core jsonb; receipt jsonb;
  manifest_sha text; evidence_sha text; n integer; old uuid;
BEGIN
  IF video_queue.canonical_json_text('{"z":"δ","a":[true,1,null]}'::jsonb) <> '{"a":[true,1,null],"z":"δ"}' THEN
    RAISE EXCEPTION 'canonical json mismatch';
  END IF;
  IF NOT has_function_privilege('bridge_school_worker', 'video_queue.precanary_idle_snapshot()', 'EXECUTE') THEN
    RAISE EXCEPTION 'worker idle snapshot capability missing';
  END IF;
  SELECT claimable_jobs INTO n FROM video_queue.precanary_idle_snapshot();
  IF n <> 0 THEN RAISE EXCEPTION 'empty idle snapshot mismatch'; END IF;

  SELECT * INTO b FROM video_queue.enqueue_drive_batch(
    'issue881-one-canary','source-folder-123456','output-folder-123456','work-folder-123456',
    'bridge_3_1_free','3.1-free-r25.16','source-file-canary-123456',repeat('a',64),
    jsonb_build_array(
      jsonb_build_object('sequence',1,'file_id','source-file-canary-123456','name','canary.mp4','mime_type','video/mp4','size_bytes',12339062,'checksum','md5:'||repeat('1',32)),
      jsonb_build_object('sequence',2,'file_id','source-file-pending-123456','name','pending.mp4','mime_type','video/mp4','size_bytes',22339062,'checksum','md5:'||repeat('2',32))
    )
  );
  SELECT * INTO same FROM video_queue.enqueue_drive_batch(
    'issue881-one-canary','source-folder-123456','output-folder-123456','work-folder-123456',
    'bridge_3_1_free','3.1-free-r25.16','source-file-canary-123456',repeat('a',64),
    jsonb_build_array(
      jsonb_build_object('sequence',1,'file_id','source-file-canary-123456','name','canary.mp4','mime_type','video/mp4','size_bytes',12339062,'checksum','md5:'||repeat('1',32)),
      jsonb_build_object('sequence',2,'file_id','source-file-pending-123456','name','pending.mp4','mime_type','video/mp4','size_bytes',22339062,'checksum','md5:'||repeat('2',32))
    )
  );
  IF same.batch_id IS DISTINCT FROM b.batch_id THEN RAISE EXCEPTION 'duplicate batch'; END IF;
  SELECT count(*) INTO n FROM video_queue.job WHERE batch_id=b.batch_id;
  IF n<>2 THEN RAISE EXCEPTION 'duplicate jobs'; END IF;

  SELECT * INTO c FROM video_queue.claim_job('worker-issue881',900,'bridge_3_1_free','3.1-free-r25.16');
  IF NOT c.is_canary THEN RAISE EXCEPTION 'not canary'; END IF;

  manifest := jsonb_build_object(
    'schema_version','universal-video-artifact-manifest/v1',
    'job_id',c.stable_job_key,
    'source_file_id',c.source_file_id,
    'algorithm_revision',c.algorithm_revision,
    'result_mode','SHADOW_REVIEW_ONLY',
    'canonical_promotion_allowed',false,
    'database_persistence_allowed',false,
    'publication_state','NOT_PUBLISHED',
    'source_identity',jsonb_build_object(
      'file_id',c.source_file_id,
      'name','canary.mp4',
      'mime_type','video/mp4',
      'size_bytes',12339062,
      'parent_folder_id','source-folder-123456',
      'checksum','md5:'||repeat('1',32)
    ),
    'artifacts',jsonb_build_array(
      jsonb_build_object(
        'kind','master_pdf','locator','gdrive:file:result-pdf-123456','drive_id','result-pdf-123456',
        'name','result.pdf','mime_type','application/pdf','size_bytes',2048,
        'parent_id','output-folder-123456','modified_time','2026-09-03T00:00:00Z','version','101',
        'sha256',repeat('b',64)
      ),
      jsonb_build_object(
        'kind','ai_done','locator','gdrive:file:result-ai-done-123456','drive_id','result-ai-done-123456',
        'name','AI_DONE_'||c.stable_job_key||'.json','mime_type','application/json','size_bytes',1024,
        'parent_id','output-folder-123456','modified_time','2026-09-03T00:00:01Z','version','102',
        'sha256',repeat('c',64)
      )
    )
  );
  manifest_sha := encode(public.digest(convert_to(video_queue.canonical_json_text(manifest),'UTF8'),'sha256'),'hex');

  receipt_core := jsonb_build_object(
    'schema_version','universal-video-terminal-receipt/v1',
    'status','PASS',
    'job_id',c.stable_job_key,
    'source_file_id',c.source_file_id,
    'source_identity_verified',true,
    'drive_readback_verified',true,
    'result_readback_verified',true,
    'checksum_verified',true,
    'artifact_count',2,
    'artifact_manifest_sha256',manifest_sha,
    'canonical_promotion_allowed',false,
    'database_persistence_allowed',false,
    'publication_state','NOT_PUBLISHED'
  );
  evidence_sha := encode(public.digest(convert_to(video_queue.canonical_json_text(receipt_core),'UTF8'),'sha256'),'hex');
  receipt := receipt_core || jsonb_build_object('evidence_sha256',evidence_sha);

  out := jsonb_build_object(
    'result_mode','SHADOW_REVIEW_ONLY',
    'canonical_promotion_allowed',false,
    'database_persistence_allowed',false,
    'publication_state','NOT_PUBLISHED',
    'source_file_id',c.source_file_id,
    'stable_job_key',c.stable_job_key,
    'algorithm_revision',c.algorithm_revision,
    'master_pdf_drive_id','result-pdf-123456',
    'master_pdf_sha256',repeat('b',64),
    'ai_done_drive_id','result-ai-done-123456',
    'ai_done_sha256',repeat('c',64),
    'artifact_locators',jsonb_build_object('master_pdf','result-pdf-123456','ai_done','result-ai-done-123456'),
    'artifact_manifest',manifest,
    'artifact_manifest_sha256',manifest_sha,
    'terminal_receipt',receipt,
    'terminal_evidence_sha256',evidence_sha
  );

  -- Top-level required fields: absent and JSON null must fail closed.
  FOREACH bad_out IN ARRAY ARRAY[
    out - 'publication_state',
    jsonb_set(out,'{publication_state}','null'::jsonb),
    out - 'artifact_locators',
    jsonb_set(out,'{artifact_locators}','null'::jsonb),
    out - 'terminal_receipt',
    jsonb_set(out,'{terminal_receipt}','null'::jsonb),
    out - 'terminal_evidence_sha256',
    jsonb_set(out,'{terminal_evidence_sha256}','null'::jsonb),
    out - 'ai_done_drive_id',
    jsonb_set(out,'{ai_done_drive_id}','null'::jsonb)
  ] LOOP
    BEGIN
      PERFORM * FROM video_queue.finish_job(c.job_id,c.lease_token,'worker-issue881','REVIEW_READY',bad_out,NULL);
      RAISE EXCEPTION 'missing/null required v2 field accepted';
    EXCEPTION WHEN OTHERS THEN
      IF SQLERRM NOT LIKE '%VIDEO_QUEUE_%INVALID%' THEN RAISE; END IF;
    END;
  END LOOP;

  -- Wrong JSON types must fail closed without reaching CANARY_REVIEW.
  FOREACH bad_out IN ARRAY ARRAY[
    jsonb_set(out,'{artifact_locators}','[]'::jsonb),
    jsonb_set(out,'{artifact_manifest,source_identity}','[]'::jsonb),
    jsonb_set(out,'{artifact_manifest,source_identity,size_bytes}',to_jsonb('12339062'::text)),
    jsonb_set(out,'{artifact_manifest,artifacts}',jsonb_build_object('bad',true)),
    jsonb_set(out,'{artifact_manifest,artifacts,0,size_bytes}',to_jsonb('2048'::text)),
    jsonb_set(out,'{artifact_manifest,artifacts,1,size_bytes}',to_jsonb(false)),
    jsonb_set(out,'{terminal_receipt,artifact_count}',to_jsonb('2'::text)),
    jsonb_set(out,'{terminal_receipt,drive_readback_verified}',to_jsonb('true'::text))
  ] LOOP
    BEGIN
      PERFORM * FROM video_queue.finish_job(c.job_id,c.lease_token,'worker-issue881','REVIEW_READY',bad_out,NULL);
      RAISE EXCEPTION 'wrong-type v2 field accepted';
    EXCEPTION WHEN OTHERS THEN
      IF SQLERRM NOT LIKE '%VIDEO_QUEUE_RESULT_CONTRACT_INVALID%' THEN RAISE; END IF;
    END;
  END LOOP;

  -- Correctly rehashed but semantically mismatching evidence must still fail.
  bad_out := jsonb_set(out,'{artifact_manifest,source_identity,name}',to_jsonb('renamed.mp4'::text));
  bad_out := jsonb_set(bad_out,'{artifact_manifest_sha256}',to_jsonb(encode(public.digest(convert_to(video_queue.canonical_json_text(bad_out->'artifact_manifest'),'UTF8'),'sha256'),'hex')));
  BEGIN
    PERFORM * FROM video_queue.finish_job(c.job_id,c.lease_token,'worker-issue881','REVIEW_READY',bad_out,NULL);
    RAISE EXCEPTION 'source identity mismatch accepted';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%VIDEO_QUEUE_RESULT_CONTRACT_INVALID%' THEN RAISE; END IF;
  END;

  bad_out := jsonb_set(out,'{artifact_manifest,artifacts,1,kind}',to_jsonb('master_pdf'::text));
  bad_out := jsonb_set(bad_out,'{artifact_manifest_sha256}',to_jsonb(encode(public.digest(convert_to(video_queue.canonical_json_text(bad_out->'artifact_manifest'),'UTF8'),'sha256'),'hex')));
  BEGIN
    PERFORM * FROM video_queue.finish_job(c.job_id,c.lease_token,'worker-issue881','REVIEW_READY',bad_out,NULL);
    RAISE EXCEPTION 'missing AI_DONE semantic artifact accepted';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%VIDEO_QUEUE_RESULT_CONTRACT_INVALID%' THEN RAISE; END IF;
  END;

  -- Even a correctly rehashed manifest without Drive revisions must fail at
  -- the database boundary so an older/buggy authorized worker cannot bypass
  -- the live revision fence.
  bad_out := jsonb_set(out,'{artifact_manifest}',(out->'artifact_manifest') #- '{artifacts,0,modified_time}');
  bad_out := jsonb_set(bad_out,'{artifact_manifest_sha256}',to_jsonb(encode(public.digest(convert_to(video_queue.canonical_json_text(bad_out->'artifact_manifest'),'UTF8'),'sha256'),'hex')));
  bad_out := jsonb_set(bad_out,'{terminal_receipt,artifact_manifest_sha256}',bad_out->'artifact_manifest_sha256');
  receipt_core := (bad_out->'terminal_receipt') - 'evidence_sha256'::text;
  evidence_sha := encode(public.digest(convert_to(video_queue.canonical_json_text(receipt_core),'UTF8'),'sha256'),'hex');
  bad_out := jsonb_set(bad_out,'{terminal_receipt,evidence_sha256}',to_jsonb(evidence_sha));
  bad_out := jsonb_set(bad_out,'{terminal_evidence_sha256}',to_jsonb(evidence_sha));
  BEGIN
    PERFORM * FROM video_queue.finish_job(c.job_id,c.lease_token,'worker-issue881','REVIEW_READY',bad_out,NULL);
    RAISE EXCEPTION 'revision-less terminal evidence accepted';
  EXCEPTION WHEN check_violation THEN
    NULL;
  END;

  BEGIN
    PERFORM * FROM video_queue.finish_job(
      c.job_id,c.lease_token,'worker-issue881','REVIEW_READY',
      jsonb_set(out,'{terminal_evidence_sha256}',to_jsonb(repeat('d',64))),NULL
    );
    RAISE EXCEPTION 'forged terminal evidence hash accepted';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%VIDEO_QUEUE_RESULT_CONTRACT_INVALID%' THEN RAISE; END IF;
  END;

  SELECT * INTO f FROM video_queue.finish_job(c.job_id,c.lease_token,'worker-issue881','REVIEW_READY',out,NULL);
  IF f.batch_status<>'CANARY_REVIEW' OR f.released_jobs<>0 THEN RAISE EXCEPTION 'automatic release'; END IF;
  SELECT count(*) INTO n FROM video_queue.job WHERE batch_id=b.batch_id AND status='PENDING_CANARY';
  IF n<>1 THEN RAISE EXCEPTION 'pending released'; END IF;
  BEGIN
    PERFORM * FROM video_queue.heartbeat_job(c.job_id,c.lease_token,'worker-issue881',900);
    RAISE EXCEPTION 'old fence accepted';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%VIDEO_QUEUE_LEASE_LOST%' THEN RAISE; END IF;
  END;

  PERFORM video_queue.enqueue_drive_batch(
    'issue881-recovery','source-folder-223456','output-folder-223456','work-folder-223456',
    'bridge_3_1_free','3.1-free-r25.16','source-file-retry-123456',repeat('d',64),
    jsonb_build_array(jsonb_build_object('sequence',1,'file_id','source-file-retry-123456','name','retry.mp4','mime_type','video/mp4','size_bytes',12339062,'checksum','md5:'||repeat('3',32)))
  );
  SELECT * INTO c FROM video_queue.claim_job('worker-recovery',900,'bridge_3_1_free','3.1-free-r25.16'); old:=c.lease_token;
  BEGIN
    PERFORM * FROM video_queue.retry_job(c.job_id,gen_random_uuid(),'worker-recovery','UV_TEST_FAILURE',3,1);
    RAISE EXCEPTION 'wrong fence accepted';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%VIDEO_QUEUE_LEASE_LOST%' THEN RAISE; END IF;
  END;
  SELECT * INTO r FROM video_queue.retry_job(c.job_id,c.lease_token,'worker-recovery','UV_TEST_FAILURE',3,1);
  IF r.job_status<>'QUEUED' THEN RAISE EXCEPTION 'retry false pass'; END IF;
  UPDATE video_queue.job SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE job_id=c.job_id;
  SELECT * INTO c2 FROM video_queue.claim_job('worker-recovery',900,'bridge_3_1_free','3.1-free-r25.16');
  IF c2.lease_token=old OR c2.attempt_count<>2 THEN RAISE EXCEPTION 'retry fencing'; END IF;
  UPDATE video_queue.job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=c2.job_id;
  SELECT * INTO c3 FROM video_queue.claim_job('worker-recovery',900,'bridge_3_1_free','3.1-free-r25.16');
  IF c3.lease_token=c2.lease_token OR c3.attempt_count<>3 THEN RAISE EXCEPTION 'stale reclaim'; END IF;
  UPDATE video_queue.job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=c3.job_id;
  PERFORM * FROM video_queue.claim_job('worker-recovery',900,'bridge_3_1_free','3.1-free-r25.16');
  SELECT count(*) INTO n FROM video_queue.job WHERE job_id=c3.job_id AND status='FAILED' AND error_code='UV_WORKER_CRASH_RETRY_EXHAUSTED';
  IF n<>1 THEN RAISE EXCEPTION 'interrupted worker false pass'; END IF;

  PERFORM video_queue.enqueue_drive_batch(
    'issue881-bad-result','source-folder-323456','output-folder-323456','work-folder-323456',
    'bridge_3_1_free','3.1-free-r25.16','source-file-bad-123456',repeat('e',64),
    jsonb_build_array(jsonb_build_object('sequence',1,'file_id','source-file-bad-123456','name','bad.mp4','mime_type','video/mp4','size_bytes',12339062,'checksum','md5:'||repeat('4',32)))
  );
  SELECT * INTO bad FROM video_queue.claim_job('worker-bad',900,'bridge_3_1_free','3.1-free-r25.16');
  BEGIN
    PERFORM * FROM video_queue.finish_job(
      bad.job_id,bad.lease_token,'worker-bad','REVIEW_READY',
      jsonb_build_object(
        'result_mode','SHADOW_REVIEW_ONLY','canonical_promotion_allowed',false,
        'database_persistence_allowed',false,'publication_state','NOT_PUBLISHED',
        'source_file_id',bad.source_file_id,'stable_job_key',bad.stable_job_key,
        'algorithm_revision',bad.algorithm_revision
      ),NULL
    );
    RAISE EXCEPTION 'missing contract accepted';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%VIDEO_QUEUE_RESULT_CONTRACT_INVALID%' THEN RAISE; END IF;
  END;
  SELECT count(*) INTO n FROM video_queue.job WHERE job_id=bad.job_id AND status='REVIEW_READY';
  IF n<>0 THEN RAISE EXCEPTION 'false PASS'; END IF;
END;
$$;
ROLLBACK;