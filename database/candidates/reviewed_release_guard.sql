CREATE OR REPLACE FUNCTION video_queue.subset_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE v_admission video_queue.subset_admission%ROWTYPE; v_approval video_queue.subset_approval%ROWTYPE; v_batch video_queue.batch%ROWTYPE; v_file jsonb; v_old_job jsonb; v_new_job jsonb;
BEGIN
    IF TG_OP IN ('DELETE','TRUNCATE') THEN RAISE EXCEPTION 'SUBSET_IMMUTABLE_HOLD'; END IF;
    IF TG_TABLE_NAME='subset_approval' THEN
        PERFORM video_queue.subset_capability('video_subset_approver');
        IF TG_OP='UPDATE' AND (to_jsonb(NEW)-ARRAY['revoked_at','runtime_enabled'] IS DISTINCT FROM to_jsonb(OLD)-ARRAY['revoked_at','runtime_enabled'] OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)) THEN RAISE EXCEPTION 'SUBSET_IMMUTABLE_HOLD'; END IF;
    ELSIF TG_TABLE_NAME='source_observation' THEN
        IF TG_OP='INSERT' THEN PERFORM video_queue.subset_capability('video_subset_collector');
        ELSIF NOT (pg_has_role(session_user,'video_subset_admitter','MEMBER') OR pg_has_role(session_user,'video_subset_worker_v2','MEMBER'))
           OR to_jsonb(NEW)-'consumed_at' IS DISTINCT FROM to_jsonb(OLD)-'consumed_at' OR OLD.consumed_at IS NOT NULL OR NEW.consumed_at IS NULL THEN RAISE EXCEPTION 'SUBSET_IMMUTABLE_HOLD'; END IF;
    ELSIF TG_TABLE_NAME='subset_admission' THEN
        PERFORM video_queue.subset_capability('video_subset_admitter');
        IF (TG_OP='INSERT' AND NEW.state <> 'DRAFT') OR (TG_OP='UPDATE' AND (OLD.state <> 'DRAFT' OR NEW.state <> 'FINALIZED'
            OR to_jsonb(NEW)-ARRAY['state','batch_id','binding','binding_sha256','finalized_at'] IS DISTINCT FROM to_jsonb(OLD)-ARRAY['state','batch_id','binding','binding_sha256','finalized_at'])) THEN RAISE EXCEPTION 'SUBSET_IMMUTABLE_HOLD'; END IF;
    ELSIF TG_TABLE_NAME='source_reservation' THEN
        PERFORM video_queue.subset_capability('video_subset_admitter');
        SELECT q_admission.* INTO v_admission FROM video_queue.subset_admission AS q_admission WHERE q_admission.request_key=NEW.admission_request_key;
        IF v_admission.state IS DISTINCT FROM 'DRAFT' OR NEW.historical_job_ids IS NOT NULL
           OR (TG_OP='UPDATE' AND (OLD.batch_id IS NOT NULL OR OLD.job_id IS NOT NULL OR NEW.batch_id IS NULL OR NEW.job_id IS NULL OR to_jsonb(NEW)-ARRAY['batch_id','job_id'] IS DISTINCT FROM to_jsonb(OLD)-ARRAY['batch_id','job_id'])) THEN RAISE EXCEPTION 'SUBSET_RESERVATION_MISMATCH_HOLD'; END IF;
    ELSIF TG_TABLE_NAME='batch' THEN
        SELECT q_admission.* INTO v_admission FROM video_queue.subset_admission AS q_admission WHERE q_admission.request_key=NEW.request_key;
        IF TG_OP='UPDATE' THEN
            IF OLD.status='CANARY_REVIEW' AND NEW.status='RUNNING'
               AND to_jsonb(NEW)-ARRAY['status','updated_at'] IS NOT DISTINCT FROM to_jsonb(OLD)-ARRAY['status','updated_at']
               AND EXISTS (SELECT FROM video_queue.subset_reviewed_release r
                   WHERE r.batch_id=NEW.batch_id AND r.approval_id=v_admission.approval_id
                     AND r.release_xid=txid_current() AND r.released_at IS NULL
                     AND r.reviewed_by=session_user::name)
            THEN
                PERFORM video_queue.subset_capability('video_subset_approver');
                RETURN NEW;
            END IF;
        END IF;
        IF TG_OP='INSERT' THEN
            PERFORM video_queue.subset_capability('video_subset_admitter');
            IF v_admission.state IS DISTINCT FROM 'DRAFT' THEN RAISE EXCEPTION 'SUBSET_LEGACY_INSERT_HOLD'; END IF;
            SELECT q_approval.* INTO v_approval FROM video_queue.subset_approval AS q_approval WHERE q_approval.approval_id=v_admission.approval_id;
            IF (to_jsonb(NEW)-ARRAY['batch_id','source_kind','status','created_at','updated_at','completed_at']) IS DISTINCT FROM ((v_approval.contract->'enqueue_manifest')-'files') OR NEW.source_kind <> 'google_drive_folder' OR NEW.status <> 'QUEUED_CANARY' THEN RAISE EXCEPTION 'SUBSET_BINDING_MISMATCH_HOLD'; END IF;
        ELSE
            PERFORM video_queue.subset_capability('video_subset_worker_v2');
            IF v_admission.state IS DISTINCT FROM 'FINALIZED' OR to_jsonb(NEW)-ARRAY['status','updated_at','completed_at'] IS DISTINCT FROM to_jsonb(OLD)-ARRAY['status','updated_at','completed_at'] THEN RAISE EXCEPTION 'SUBSET_IMMUTABLE_HOLD'; END IF;
        END IF;
    ELSIF TG_TABLE_NAME='job' THEN
        SELECT q_batch.* INTO v_batch FROM video_queue.batch AS q_batch WHERE q_batch.batch_id=NEW.batch_id;
        SELECT q_admission.* INTO v_admission FROM video_queue.subset_admission AS q_admission WHERE q_admission.request_key=v_batch.request_key;
        IF TG_OP='UPDATE' THEN
            IF OLD.status='PENDING_CANARY' AND NEW.status='QUEUED'
               AND to_jsonb(NEW)-ARRAY['status','updated_at'] IS NOT DISTINCT FROM to_jsonb(OLD)-ARRAY['status','updated_at']
               AND EXISTS (SELECT FROM video_queue.subset_reviewed_release r
                   WHERE r.batch_id=NEW.batch_id AND r.approval_id=v_admission.approval_id
                     AND r.release_xid=txid_current() AND r.released_at IS NULL
                     AND r.reviewed_by=session_user::name)
            THEN
                PERFORM video_queue.subset_capability('video_subset_approver');
                RETURN NEW;
            END IF;
        END IF;
        IF TG_OP='INSERT' THEN
            PERFORM video_queue.subset_capability('video_subset_admitter');
            IF v_admission.state IS DISTINCT FROM 'DRAFT' OR NOT EXISTS (SELECT FROM video_queue.source_reservation AS q_reservation WHERE q_reservation.source_identity='google_drive:' || NEW.source_file_id AND q_reservation.admission_request_key=v_admission.request_key) THEN RAISE EXCEPTION 'SUBSET_LEGACY_INSERT_HOLD'; END IF;
            SELECT q_file.value INTO v_file FROM video_queue.subset_approval AS q_approval CROSS JOIN LATERAL jsonb_array_elements(q_approval.contract#>'{enqueue_manifest,files}') AS q_file(value) WHERE q_approval.approval_id=v_admission.approval_id AND q_file.value->>'file_id'=NEW.source_file_id;
            IF v_file IS NULL OR jsonb_build_object('sequence',NEW.sequence,'file_id',NEW.source_file_id,'name',NEW.source_name,'mime_type',NEW.source_mime_type,'size_bytes',NEW.source_size_bytes,'checksum',NEW.source_checksum) IS DISTINCT FROM v_file
               OR NEW.attempt_count <> 0 OR NEW.is_canary IS DISTINCT FROM (NEW.source_file_id=v_batch.canary_source_file_id)
               OR NEW.status IS DISTINCT FROM (CASE WHEN NEW.is_canary THEN 'QUEUED' ELSE 'PENDING_CANARY' END) THEN RAISE EXCEPTION 'SUBSET_BINDING_MISMATCH_HOLD'; END IF;
        ELSE
            PERFORM video_queue.subset_capability('video_subset_worker_v2');
            IF v_admission.state IS DISTINCT FROM 'FINALIZED' THEN RAISE EXCEPTION 'SUBSET_LEGACY_WORKER_HOLD'; END IF;
            v_old_job := to_jsonb(OLD)-ARRAY['status','attempt_count','lease_owner','lease_token','lease_expires_at','next_attempt_at','error_code','output','updated_at','completed_at'];
            v_new_job := to_jsonb(NEW)-ARRAY['status','attempt_count','lease_owner','lease_token','lease_expires_at','next_attempt_at','error_code','output','updated_at','completed_at'];
            IF v_old_job IS DISTINCT FROM v_new_job THEN RAISE EXCEPTION 'SUBSET_IMMUTABLE_HOLD'; END IF;
            IF NEW.attempt_count > 3 OR NEW.attempt_count < OLD.attempt_count OR NEW.attempt_count > OLD.attempt_count+1
               OR (NEW.attempt_count=OLD.attempt_count+1 AND (NEW.status <> 'LEASED' OR NEW.lease_token IS NULL OR NEW.lease_token IS NOT DISTINCT FROM OLD.lease_token OR OLD.status NOT IN ('QUEUED','LEASED')))
               OR (NEW.status='LEASED' AND NEW.lease_token IS DISTINCT FROM OLD.lease_token AND NEW.attempt_count <> OLD.attempt_count+1)
               OR (NEW.status='QUEUED' AND NEW.attempt_count >= 3)
               OR (OLD.status='PENDING_CANARY' AND NEW.status <> 'PENDING_CANARY') THEN RAISE EXCEPTION 'SUBSET_ATTEMPT_OR_RELEASE_HOLD'; END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
