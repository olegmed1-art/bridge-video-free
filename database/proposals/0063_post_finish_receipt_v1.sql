-- UNAPPLIED SOURCE DRAFT after 0061/0062. Not an installer or ACL rollout.
-- NO issuer, owner enrollment, login creation or EXECUTE grants are supplied.
-- Empty owner registries structurally deny finish-v2/read/ACK-authority checks.
-- Required ownership/default-ACL/retention/capacity review is documented in
-- docs/ops/DIANA20_POST_FINISH_SOURCE_20261020.md. Never run against live SQL.
BEGIN;

CREATE FUNCTION video_queue.post_finish_json_v1(p_value jsonb,p_depth integer DEFAULT 0)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog,video_queue AS $$
DECLARE v_type text:=jsonb_typeof(p_value); v_text text; v_item record; v_parts text[]:='{}';
BEGIN
    IF p_depth<0 OR p_depth>32 THEN RAISE EXCEPTION 'RECEIPT_JSON_DEPTH'; END IF;
    IF v_type='object' THEN
        FOR v_item IN SELECT key,value FROM jsonb_each(p_value) ORDER BY key COLLATE "C" LOOP
            v_parts:=array_append(v_parts,to_json(v_item.key)::text||':'||
                video_queue.post_finish_json_v1(v_item.value,p_depth+1));
        END LOOP;
        RETURN '{'||array_to_string(v_parts,',')||'}';
    ELSIF v_type='array' THEN
        FOR v_item IN SELECT value FROM jsonb_array_elements(p_value) WITH ORDINALITY AS a(value,n) ORDER BY n LOOP
            v_parts:=array_append(v_parts,video_queue.post_finish_json_v1(v_item.value,p_depth+1));
        END LOOP;
        RETURN '['||array_to_string(v_parts,',')||']';
    ELSIF v_type='number' THEN
        v_text:=(p_value::text)::numeric::text;
        IF position('.' IN v_text)>0 THEN v_text:=rtrim(rtrim(v_text,'0'),'.'); END IF;
        IF p_value::text::numeric=0 THEN v_text:='0'; END IF;
        IF length(v_text)>128 OR abs(floor(log(10,abs(nullif(p_value::text::numeric,0)))))>128 THEN
            RAISE EXCEPTION 'RECEIPT_JSON_NUMBER_BOUND';
        END IF;
        RETURN v_text;
    END IF;
    RETURN p_value::text; -- jsonb preserves UTF-8 strings, escaping JSON controls.
END $$;

CREATE FUNCTION video_queue.post_finish_sha_v1(p_value jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog,video_queue AS $$
 SELECT encode(public.digest(convert_to(video_queue.post_finish_json_v1($1),'UTF8'),'sha256'),'hex')
$$;

CREATE FUNCTION video_queue.post_finish_assignment_v1(p_text text) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog,video_queue AS $$
DECLARE a jsonb; v jsonb; k text;
BEGIN
    IF octet_length(p_text) NOT BETWEEN 2 AND 16384 THEN RAISE EXCEPTION 'ASSIGNMENT_SIZE'; END IF;
    a:=p_text::jsonb; v:=a->'video';
    IF video_queue.post_finish_json_v1(a) IS DISTINCT FROM p_text
       OR a->>'schema' IS DISTINCT FROM 'diana20-owner-assignment/v1'
       OR a->>'unit' IS DISTINCT FROM 'school-autopilot-production-light.service'
       OR jsonb_typeof(a) IS DISTINCT FROM 'object'
       OR (SELECT count(*) FROM jsonb_object_keys(a))<>16
       OR NOT a ?& ARRAY['schema','unit','autopilot','video','admission_id','policy_id','policy_epoch',
          'source_sha256','context_sha256','request_sha256','generation_sha256','manifest_sha256',
          'source_fingerprint','production_source_sha256','unit_sha256','binding_sha256'] THEN
        RAISE EXCEPTION 'ASSIGNMENT_CANONICAL_FIELDS';
    END IF;
    -- The exact canonical bytes must ALSO equal the owner-enrolled row below.
    FOREACH k IN ARRAY ARRAY['source_sha256','context_sha256','request_sha256','generation_sha256',
         'manifest_sha256','source_fingerprint','production_source_sha256','unit_sha256','binding_sha256'] LOOP
        IF jsonb_typeof(a->k) IS DISTINCT FROM 'string'
           OR NOT coalesce((a->>k)~'^[0-9a-f]{64}$',false) THEN RAISE EXCEPTION 'ASSIGNMENT_PIN'; END IF;
    END LOOP;
    IF jsonb_typeof(v) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(v))<>9
       OR NOT v ?& ARRAY['job_id','batch_id','source_file_id','lease_token','attempt_count','worker_key',
                         'stable_job_key','algorithm_revision','output_folder_id']
       OR jsonb_typeof(a->'autopilot') IS DISTINCT FROM 'object'
       OR (SELECT count(*) FROM jsonb_object_keys(a->'autopilot'))<>4
       OR NOT (a->'autopilot') ?& ARRAY['task_id','worker_id','lease_epoch','intake_source_revision']
       OR NOT coalesce((v->>'attempt_count')~'^[1-3]$',false)
       OR jsonb_typeof(v->'attempt_count') IS DISTINCT FROM 'number'
       OR jsonb_typeof(a#>'{autopilot,lease_epoch}') IS DISTINCT FROM 'number'
       OR jsonb_typeof(a->'policy_epoch') IS DISTINCT FROM 'number'
       OR NOT coalesce((a#>>'{autopilot,lease_epoch}')~'^[1-9][0-9]*$',false)
       OR NOT coalesce((a->>'policy_epoch')~'^[1-9][0-9]*$',false)
       OR NOT coalesce((v->>'stable_job_key')~'^[0-9a-f]{32}$',false)
       OR jsonb_typeof(v->'stable_job_key') IS DISTINCT FROM 'string'
       OR jsonb_typeof(v->'source_file_id') IS DISTINCT FROM 'string'
       OR jsonb_typeof(v->'output_folder_id') IS DISTINCT FROM 'string'
       OR NOT coalesce((v->>'source_file_id')~'^[A-Za-z0-9_-]{10,200}$',false)
       OR NOT coalesce((v->>'output_folder_id')~'^[A-Za-z0-9_-]{10,200}$',false) THEN
        RAISE EXCEPTION 'ASSIGNMENT_NAMESPACES';
    END IF;
    FOREACH k IN ARRAY ARRAY['job_id','batch_id','lease_token'] LOOP
        IF jsonb_typeof(v->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'VIDEO_UUID_TYPE'; END IF;
    END LOOP;
    IF jsonb_typeof(a->'admission_id') IS DISTINCT FROM 'string'
       OR jsonb_typeof(a#>'{autopilot,task_id}') IS DISTINCT FROM 'string' THEN
        RAISE EXCEPTION 'ASSIGNMENT_UUID_TYPE'; END IF;
    FOREACH k IN ARRAY ARRAY['worker_key','algorithm_revision'] LOOP
        IF jsonb_typeof(v->k) IS DISTINCT FROM 'string' OR length(v->>k) NOT BETWEEN 1 AND 200 THEN
            RAISE EXCEPTION 'VIDEO_IDENTITY'; END IF;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['worker_id','intake_source_revision'] LOOP
        IF jsonb_typeof((a->'autopilot')->k) IS DISTINCT FROM 'string'
           OR length((a->'autopilot')->>k) NOT BETWEEN 1 AND 200 THEN
            RAISE EXCEPTION 'AUTOPILOT_IDENTITY'; END IF;
    END LOOP;
    IF jsonb_typeof(a->'policy_id') IS DISTINCT FROM 'string' OR length(a->>'policy_id') NOT BETWEEN 1 AND 200
       OR (v->>'job_id')::uuid::text IS DISTINCT FROM v->>'job_id'
       OR (v->>'batch_id')::uuid::text IS DISTINCT FROM v->>'batch_id'
       OR (v->>'lease_token')::uuid::text IS DISTINCT FROM v->>'lease_token'
       OR (a->>'admission_id')::uuid::text IS DISTINCT FROM a->>'admission_id'
       OR (a#>>'{autopilot,task_id}')::uuid::text IS DISTINCT FROM a#>>'{autopilot,task_id}' THEN
        RAISE EXCEPTION 'ASSIGNMENT_POLICY_OR_UUID'; END IF;
    PERFORM (v->>'job_id')::uuid,(v->>'batch_id')::uuid,(v->>'lease_token')::uuid,
            (a->>'admission_id')::uuid,(a#>>'{autopilot,task_id}')::uuid;
    RETURN a;
END $$;

CREATE FUNCTION video_queue.post_finish_selector_v1(p_text text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog,video_queue AS $$
 SELECT video_queue.post_finish_sha_v1(jsonb_build_object(
    'schema','diana20-terminal-selector/v1','assignment_sha256',video_queue.post_finish_sha_v1(a),
    'job_id',a#>>'{video,job_id}','attempt_count',a#>'{video,attempt_count}',
    'former_video_lease_token',a#>>'{video,lease_token}','generation_sha256',a->>'generation_sha256'))
 FROM (SELECT video_queue.post_finish_assignment_v1($1) AS a) AS q
$$;

-- Owner-only future enrollment records. Hashes/receipts cannot insert these.
CREATE TABLE video_queue.terminal_assignment_v1 (
    selector_sha256 text PRIMARY KEY CHECK(selector_sha256~'^[0-9a-f]{64}$'),
    assignment_sha256 text NOT NULL UNIQUE CHECK(assignment_sha256~'^[0-9a-f]{64}$'),
    assignment_text text NOT NULL CHECK(octet_length(assignment_text) BETWEEN 2 AND 16384),
    principal name NOT NULL,
    issued_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK(expires_at>issued_at),
    revoked_at timestamptz,
    CHECK(selector_sha256=video_queue.post_finish_selector_v1(assignment_text)),
    CHECK(assignment_sha256=video_queue.post_finish_sha_v1(assignment_text::jsonb)),
    CHECK(revoked_at IS NULL OR revoked_at>=issued_at)
);
CREATE TABLE video_queue.terminal_read_grant_v1 (
    read_grant_id uuid PRIMARY KEY,
    selector_sha256 text NOT NULL REFERENCES video_queue.terminal_assignment_v1,
    principal name NOT NULL,
    purpose text NOT NULL CHECK(purpose='READ_RESULT'),
    issued_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK(expires_at>issued_at AND expires_at<=issued_at+interval '5 minutes'),
    revoked_at timestamptz CHECK(revoked_at IS NULL OR revoked_at>=issued_at)
);
CREATE UNIQUE INDEX terminal_one_unrevoked_read_grant_v1
    ON video_queue.terminal_read_grant_v1(selector_sha256,principal) WHERE revoked_at IS NULL;

CREATE FUNCTION video_queue.post_finish_registry_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,video_queue AS $$
BEGIN
    IF TG_OP='UPDATE' AND OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL
       AND NEW.revoked_at>=OLD.issued_at
       AND to_jsonb(OLD)-'revoked_at'=to_jsonb(NEW)-'revoked_at' THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'POST_FINISH_REGISTRY_IMMUTABLE';
END $$;
CREATE TRIGGER terminal_assignment_immutable_v1 BEFORE UPDATE OR DELETE ON video_queue.terminal_assignment_v1
    FOR EACH ROW EXECUTE FUNCTION video_queue.post_finish_registry_immutable_v1();
CREATE TRIGGER terminal_read_grant_immutable_v1 BEFORE UPDATE OR DELETE ON video_queue.terminal_read_grant_v1
    FOR EACH ROW EXECUTE FUNCTION video_queue.post_finish_registry_immutable_v1();
CREATE FUNCTION video_queue.post_finish_no_truncate_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,video_queue AS $$
BEGIN RAISE EXCEPTION 'POST_FINISH_REPLAY_FENCE_REQUIRED'; END $$;
CREATE TRIGGER terminal_assignment_no_truncate_v1 BEFORE TRUNCATE ON video_queue.terminal_assignment_v1
    EXECUTE FUNCTION video_queue.post_finish_no_truncate_v1();
CREATE TRIGGER terminal_read_grant_no_truncate_v1 BEFORE TRUNCATE ON video_queue.terminal_read_grant_v1
    EXECUTE FUNCTION video_queue.post_finish_no_truncate_v1();

-- Common lock order: job -> batch -> assignment -> READ_RESULT grant -> event.
-- Future revoker updates this SAME grant row. No locks are inferred from GUCs.
CREATE FUNCTION video_queue.check_terminal_assignment_v1(p_selector text,p_assignment text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,video_queue AS $$
DECLARE a jsonb; r video_queue.terminal_assignment_v1%ROWTYPE;
        j video_queue.job%ROWTYPE; b video_queue.batch%ROWTYPE;
BEGIN
    a:=video_queue.post_finish_assignment_v1(p_assignment);
    IF p_selector IS DISTINCT FROM video_queue.post_finish_selector_v1(p_assignment) THEN
        RAISE EXCEPTION 'POST_FINISH_SELECTOR'; END IF;
    SELECT * INTO j FROM video_queue.job WHERE job_id=(a#>>'{video,job_id}')::uuid FOR SHARE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION 'POST_FINISH_JOB_MISSING'; END IF;
    SELECT * INTO b FROM video_queue.batch WHERE batch_id=j.batch_id FOR SHARE NOWAIT;
    IF NOT FOUND OR j.batch_id::text IS DISTINCT FROM a#>>'{video,batch_id}'
       OR j.source_file_id IS DISTINCT FROM a#>>'{video,source_file_id}'
       OR j.stable_job_key IS DISTINCT FROM a#>>'{video,stable_job_key}'
       OR b.algorithm_revision IS DISTINCT FROM a#>>'{video,algorithm_revision}'
       OR b.output_folder_id IS DISTINCT FROM a#>>'{video,output_folder_id}' THEN
        RAISE EXCEPTION 'POST_FINISH_JOB_SOURCE_BINDING'; END IF;
    SELECT * INTO r FROM video_queue.terminal_assignment_v1
        WHERE selector_sha256=p_selector FOR SHARE NOWAIT;
    IF NOT FOUND OR r.assignment_text IS DISTINCT FROM p_assignment
       OR r.assignment_sha256 IS DISTINCT FROM video_queue.post_finish_sha_v1(a)
       OR r.principal IS DISTINCT FROM session_user::name OR r.revoked_at IS NOT NULL
       OR r.issued_at>clock_timestamp() OR r.expires_at<=clock_timestamp() THEN
        RAISE EXCEPTION 'POST_FINISH_ASSIGNMENT_UNBOUND'; END IF;
    RETURN a;
END $$;

CREATE FUNCTION video_queue.check_terminal_stage_assignment_v1(p_selector text,p_assignment text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,video_queue AS $$
DECLARE a jsonb;
BEGIN
    a:=video_queue.post_finish_assignment_v1(p_assignment);
    --0062's stage AND pre-finish bundle reader later request job FOR UPDATE.
    -- Take EX first, never acquire SH then upgrade under a competing reader.
    PERFORM 1 FROM video_queue.job WHERE job_id=(a#>>'{video,job_id}')::uuid FOR UPDATE NOWAIT;
    IF NOT FOUND THEN RAISE EXCEPTION 'POST_FINISH_JOB_MISSING'; END IF;
    RETURN video_queue.check_terminal_assignment_v1(p_selector,p_assignment);
END $$;

CREATE FUNCTION video_queue.assert_terminal_read_authority_v1(p_selector text,p_assignment text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,video_queue AS $$
DECLARE g video_queue.terminal_read_grant_v1%ROWTYPE;
BEGIN
    PERFORM video_queue.check_terminal_assignment_v1(p_selector,p_assignment);
    SELECT * INTO g FROM video_queue.terminal_read_grant_v1
        WHERE selector_sha256=p_selector AND principal=session_user::name
          AND purpose='READ_RESULT' AND revoked_at IS NULL FOR SHARE NOWAIT;
    IF NOT FOUND OR g.issued_at>clock_timestamp() OR g.expires_at<=clock_timestamp() THEN
        RAISE EXCEPTION 'POST_FINISH_READ_GRANT_UNBOUND'; END IF;
END $$;

-- Store ONE bounded receipt in the existing terminal event, in the same
-- transaction as the unchanged0062 finish. No copied observations/preimages.
CREATE FUNCTION video_queue.finish_one_job_private_result_v2(
    p_admission_id uuid,p_job_id uuid,p_lease_token uuid,p_worker_key text,
    p_attempt_count integer,p_source_fingerprint text,p_receipt_id uuid,
    p_postwork_observation_id uuid,p_output jsonb,p_assignment text,p_selected text)
RETURNS TABLE(job_status text,batch_status text,released_jobs integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,video_queue AS $$
DECLARE a jsonb; s jsonb; q jsonb; b jsonb; v_bundle jsonb; v_text text;
        v_event video_queue.job_event%ROWTYPE; v_job video_queue.job%ROWTYPE; v_result video_queue.one_job_private_result_v1%ROWTYPE;
BEGIN
    -- Take the strongest job lock first, before registry/share and0062 locks.
    SELECT * INTO STRICT v_job FROM video_queue.job WHERE job_id=p_job_id FOR UPDATE NOWAIT;
    PERFORM 1 FROM video_queue.batch WHERE batch_id=v_job.batch_id FOR UPDATE NOWAIT;
    a:=video_queue.check_terminal_assignment_v1(video_queue.post_finish_selector_v1(p_assignment),p_assignment);
    IF p_selected IS NULL OR octet_length(p_selected) NOT BETWEEN 2 AND 131072
       OR p_output IS NULL OR octet_length(p_output::text)>131072 THEN RAISE EXCEPTION 'SELECTED_SIZE'; END IF;
    s:=p_selected::jsonb;
    IF video_queue.post_finish_json_v1(s) IS DISTINCT FROM p_selected
       OR a#>>'{video,job_id}' IS DISTINCT FROM p_job_id::text
       OR a#>>'{video,batch_id}' IS DISTINCT FROM v_job.batch_id::text
       OR a#>>'{video,lease_token}' IS DISTINCT FROM p_lease_token::text
       OR a#>>'{video,worker_key}' IS DISTINCT FROM p_worker_key
       OR a#>'{video,attempt_count}' IS DISTINCT FROM to_jsonb(p_attempt_count)
       OR a->>'admission_id' IS DISTINCT FROM p_admission_id::text
       OR a->>'source_fingerprint' IS DISTINCT FROM p_source_fingerprint
       OR a#>>'{video,source_file_id}' IS DISTINCT FROM v_job.source_file_id
       OR s->>'result_mode' IS DISTINCT FROM 'SHADOW_REVIEW_ONLY'
       OR s->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
       OR s->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR s ? 'private_staging' OR NOT s ? 'saved_frame_observations'
       OR s#>>'{saved_frame_observations,source_sha256}' IS DISTINCT FROM a->>'source_sha256' THEN
        RAISE EXCEPTION 'POST_FINISH_ASSIGNMENT_OUTPUT'; END IF;
    SELECT * INTO STRICT v_result FROM video_queue.one_job_private_result_v1 WHERE receipt_id=p_receipt_id FOR SHARE;
    v_bundle:=jsonb_build_object('status','PRIVATE_RESULT_STAGED','receipt_id',p_receipt_id,
        'bundle_sha256',v_result.bundle_sha256,'master_json_sha256',v_result.master_json_sha256,'ai_done_sha256',v_result.ai_done_sha256);
    IF p_output IS DISTINCT FROM s||jsonb_build_object('result_mode','PRIVATE_RESULT_STAGING_ONLY',
          'database_persistence_allowed',true,'private_staging',v_bundle) THEN
        RAISE EXCEPTION 'POST_FINISH_CLOSED_PROJECTION'; END IF;
    SELECT to_jsonb(x) INTO STRICT q FROM video_queue.finish_one_job_private_result_v1(
        p_admission_id,p_job_id,p_lease_token,p_worker_key,p_attempt_count,p_source_fingerprint,
        p_receipt_id,p_postwork_observation_id,p_output) AS x;
    IF q->>'job_status' IS DISTINCT FROM 'REVIEW_READY' OR q->>'batch_status' IS NULL
       OR q->>'batch_status' NOT IN ('RUNNING','REVIEW')
       OR q->'released_jobs' IS DISTINCT FROM '0'::jsonb THEN RAISE EXCEPTION 'POST_FINISH_QUEUE_RESULT'; END IF;
    SELECT * INTO STRICT v_event FROM video_queue.job_event
        WHERE job_id=p_job_id AND lease_token=p_lease_token AND worker_key=p_worker_key
          AND event_type='REVIEW_READY' AND details->>'private_receipt_id'=p_receipt_id::text
          AND details->>'attempt'=p_attempt_count::text FOR UPDATE;
    b:=jsonb_build_object('schema','diana20-terminal-finish/v1','encoding','diana20-canonical-json/v1',
        'selector_sha256',video_queue.post_finish_selector_v1(p_assignment),
        'assignment_sha256',video_queue.post_finish_sha_v1(a),'job_id',p_job_id,'attempt_count',p_attempt_count,
        'former_video_lease_token',p_lease_token,'source_file_id',v_job.source_file_id,
        'generation_sha256',a->>'generation_sha256','terminal_event_id',v_event.event_id,
        'selected_terminal_sha256',video_queue.post_finish_sha_v1(s),
        'persisted_output_sha256',video_queue.post_finish_sha_v1(p_output),'queue_result',q,
        'queue_result_sha256',video_queue.post_finish_sha_v1(q),'private_bundle',v_bundle,
        'artifact',jsonb_build_object('ai_done_drive_id',s->'ai_done_drive_id','ai_done_sha256',s->'ai_done_sha256',
            'master_pdf_drive_id',s->'master_pdf_drive_id','master_pdf_sha256',s->'master_pdf_sha256',
            'artifact_manifest_sha256',s->'artifact_manifest_sha256','terminal_evidence_sha256',s->'terminal_evidence_sha256'));
    v_text:=video_queue.post_finish_json_v1(b);
    IF octet_length(v_text)>65536 THEN RAISE EXCEPTION 'POST_FINISH_RECEIPT_SIZE'; END IF;
    UPDATE video_queue.job_event SET details=details||jsonb_build_object('terminal_finish_v1',jsonb_build_object(
        'receipt_text',v_text,'receipt_sha256',video_queue.post_finish_sha_v1(b),
        'selector_sha256',b->'selector_sha256','attempt_count',p_attempt_count,'writer_backend_pid',pg_backend_pid()))
        WHERE event_id=v_event.event_id AND NOT details ? 'terminal_finish_v1';
    IF NOT FOUND THEN RAISE EXCEPTION 'POST_FINISH_RECEIPT_REPLAY'; END IF;
    PERFORM video_queue.check_terminal_assignment_v1(b->>'selector_sha256',p_assignment);
    RETURN QUERY SELECT q->>'job_status',q->>'batch_status',(q->>'released_jobs')::integer;
END $$;

CREATE UNIQUE INDEX terminal_finish_one_attempt_v1 ON video_queue.job_event(job_id,
    ((details#>>'{terminal_finish_v1,attempt_count}')::integer)) WHERE details ? 'terminal_finish_v1';
CREATE UNIQUE INDEX terminal_finish_one_selector_v1 ON video_queue.job_event(
    (details#>>'{terminal_finish_v1,selector_sha256}')) WHERE details ? 'terminal_finish_v1';
CREATE FUNCTION video_queue.post_finish_event_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,video_queue AS $$
BEGIN
    IF OLD.details ? 'terminal_finish_v1' THEN RAISE EXCEPTION 'POST_FINISH_RECEIPT_IMMUTABLE'; END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER terminal_finish_event_immutable_v1 BEFORE UPDATE OR DELETE ON video_queue.job_event
    FOR EACH ROW EXECUTE FUNCTION video_queue.post_finish_event_immutable_v1();
CREATE FUNCTION video_queue.post_finish_event_no_truncate_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,video_queue AS $$
BEGIN
    IF EXISTS(SELECT 1 FROM video_queue.job_event WHERE details ? 'terminal_finish_v1') THEN
        RAISE EXCEPTION 'POST_FINISH_REPLAY_FENCE_REQUIRED'; END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER terminal_finish_event_no_truncate_v1 BEFORE TRUNCATE ON video_queue.job_event
    EXECUTE FUNCTION video_queue.post_finish_event_no_truncate_v1();

CREATE FUNCTION video_queue.read_terminal_receipt_v1(p_selector text,p_assignment text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,video_queue AS $$
DECLARE a jsonb; b jsonb; envelope jsonb; s jsonb; q jsonb; response jsonb;
        j video_queue.job%ROWTYPE; e video_queue.job_event%ROWTYPE;
BEGIN
    PERFORM video_queue.assert_terminal_read_authority_v1(p_selector,p_assignment);
    a:=video_queue.post_finish_assignment_v1(p_assignment);
    SELECT * INTO STRICT j FROM video_queue.job WHERE job_id=(a#>>'{video,job_id}')::uuid;
    SELECT * INTO STRICT e FROM video_queue.job_event WHERE job_id=j.job_id
        AND details#>>'{terminal_finish_v1,selector_sha256}'=p_selector FOR SHARE;
    envelope:=e.details->'terminal_finish_v1';
    IF octet_length(envelope->>'receipt_text') NOT BETWEEN 2 AND 65536 THEN RAISE EXCEPTION 'POST_FINISH_RECEIPT_SIZE'; END IF;
    b:=(envelope->>'receipt_text')::jsonb;q:=b->'queue_result';
    s:=(j.output-'private_staging')||jsonb_build_object('result_mode','SHADOW_REVIEW_ONLY','database_persistence_allowed',false);
    IF j.status IS DISTINCT FROM 'REVIEW_READY' OR j.lease_token IS NOT NULL
       OR jsonb_typeof(b) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(b))<>16
       OR jsonb_typeof(q) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(q))<>3
       OR j.lease_owner IS NOT NULL OR j.lease_expires_at IS NOT NULL
       OR j.attempt_count IS DISTINCT FROM (a#>>'{video,attempt_count}')::integer
       OR j.source_file_id IS DISTINCT FROM a#>>'{video,source_file_id}'
       OR j.batch_id::text IS DISTINCT FROM a#>>'{video,batch_id}'
       OR j.stable_job_key IS DISTINCT FROM a#>>'{video,stable_job_key}'
       OR (envelope->>'writer_backend_pid')::integer=pg_backend_pid()
       OR video_queue.post_finish_json_v1(b) IS DISTINCT FROM envelope->>'receipt_text'
       OR video_queue.post_finish_sha_v1(b) IS DISTINCT FROM envelope->>'receipt_sha256'
       OR b->>'schema' IS DISTINCT FROM 'diana20-terminal-finish/v1'
       OR b->>'encoding' IS DISTINCT FROM 'diana20-canonical-json/v1'
       OR b->>'selector_sha256' IS DISTINCT FROM p_selector
       OR b->>'assignment_sha256' IS DISTINCT FROM video_queue.post_finish_sha_v1(a)
       OR b->>'job_id' IS DISTINCT FROM j.job_id::text
       OR b->'attempt_count' IS DISTINCT FROM to_jsonb(j.attempt_count)
       OR b->>'source_file_id' IS DISTINCT FROM j.source_file_id
       OR b->>'former_video_lease_token' IS DISTINCT FROM a#>>'{video,lease_token}'
       OR b->>'generation_sha256' IS DISTINCT FROM a->>'generation_sha256'
       OR b->'terminal_event_id' IS DISTINCT FROM to_jsonb(e.event_id)
       OR e.event_type IS DISTINCT FROM 'REVIEW_READY' OR e.batch_id IS DISTINCT FROM j.batch_id
       OR e.lease_token::text IS DISTINCT FROM a#>>'{video,lease_token}'
       OR e.worker_key IS DISTINCT FROM a#>>'{video,worker_key}'
       OR b->>'persisted_output_sha256' IS DISTINCT FROM video_queue.post_finish_sha_v1(j.output)
       OR b->>'selected_terminal_sha256' IS DISTINCT FROM video_queue.post_finish_sha_v1(s)
       OR b->'private_bundle' IS DISTINCT FROM j.output->'private_staging'
       OR b->'artifact' IS DISTINCT FROM jsonb_build_object(
          'ai_done_drive_id',j.output->'ai_done_drive_id','ai_done_sha256',j.output->'ai_done_sha256',
          'master_pdf_drive_id',j.output->'master_pdf_drive_id','master_pdf_sha256',j.output->'master_pdf_sha256',
          'artifact_manifest_sha256',j.output->'artifact_manifest_sha256','terminal_evidence_sha256',j.output->'terminal_evidence_sha256')
       OR e.details->>'private_receipt_id' IS DISTINCT FROM b#>>'{private_bundle,receipt_id}'
       OR e.details->>'bundle_sha256' IS DISTINCT FROM b#>>'{private_bundle,bundle_sha256}'
       OR q->>'job_status' IS DISTINCT FROM 'REVIEW_READY' OR q->>'batch_status' IS NULL
       OR q->>'batch_status' NOT IN ('RUNNING','REVIEW')
       OR q->'released_jobs' IS DISTINCT FROM '0'::jsonb
       OR b->>'queue_result_sha256' IS DISTINCT FROM video_queue.post_finish_sha_v1(q) THEN
        RAISE EXCEPTION 'POST_FINISH_TERMINAL_BINDING'; END IF;
    -- Check expiry again after receipt construction while SAME grant lock is held.
    PERFORM video_queue.assert_terminal_read_authority_v1(p_selector,p_assignment);
    response:=jsonb_build_object('status','COMMITTED_TERMINAL_RESULT',
        'receipt_text',envelope->>'receipt_text','receipt_sha256',envelope->>'receipt_sha256');
    IF octet_length(response::text)>65536 THEN RAISE EXCEPTION 'POST_FINISH_RESPONSE_SIZE'; END IF;
    RETURN response;
END $$;

REVOKE ALL ON video_queue.terminal_assignment_v1,video_queue.terminal_read_grant_v1 FROM PUBLIC;
REVOKE ALL ON FUNCTION video_queue.post_finish_json_v1(jsonb,integer),
 video_queue.post_finish_sha_v1(jsonb),video_queue.post_finish_assignment_v1(text),
 video_queue.post_finish_selector_v1(text),video_queue.post_finish_registry_immutable_v1(),
 video_queue.post_finish_no_truncate_v1(),video_queue.post_finish_event_no_truncate_v1(),
 video_queue.check_terminal_assignment_v1(text,text),video_queue.assert_terminal_read_authority_v1(text,text),
 video_queue.check_terminal_stage_assignment_v1(text,text),
 video_queue.finish_one_job_private_result_v2(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb,text,text),
 video_queue.post_finish_event_immutable_v1(),video_queue.read_terminal_receipt_v1(text,text) FROM PUBLIC;
COMMIT;
