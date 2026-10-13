\set ON_ERROR_STOP on
-- UNAPPLIED CANDIDATE. Requires a separately authorized, drained maintenance
-- window. No role memberships or runtime approvals are enabled by this file.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
DO $pre$
BEGIN
    IF NOT EXISTS (SELECT FROM schema_migration WHERE migration_key = '0059_universal_video_terminal_size_guard') THEN
        RAISE EXCEPTION 'SUBSET_REQUIRES_0059';
    END IF;
    IF EXISTS (SELECT FROM pg_prepared_xacts WHERE database = current_database()) THEN
        RAISE EXCEPTION 'SUBSET_PREPARED_TRANSACTIONS_HOLD';
    END IF;
END $pre$;
-- A short-lived table lock alone is not the fence: persistent triggers below
-- reject old/cached entrypoints after this transaction commits as well.
LOCK TABLE video_queue.batch, video_queue.job, video_queue.job_event IN ACCESS EXCLUSIVE MODE;

CREATE ROLE video_subset_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
CREATE ROLE video_subset_approver NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE video_subset_collector NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE video_subset_admitter NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE video_subset_worker_v2 NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT USAGE ON SCHEMA video_queue TO video_subset_owner, video_subset_approver,
    video_subset_collector, video_subset_admitter, video_subset_worker_v2;
GRANT SELECT, INSERT, UPDATE ON video_queue.batch, video_queue.job, video_queue.job_event TO video_subset_owner;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA video_queue TO video_subset_owner;

-- Codec domain is deliberately narrower than arbitrary Python float JSON:
-- all numbers must be canonical integers. Decimal/exponent cost annotations
-- are explicit errors, not rounded/coerced. NULL costs/durations are retained.
CREATE FUNCTION video_queue.subset_json(p_value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, video_queue AS $$
DECLARE v_kind text := jsonb_typeof(p_value); v_result text;
BEGIN
    IF v_kind = 'object' THEN
        SELECT '{' || coalesce(string_agg(to_jsonb(key)::text || ':' || video_queue.subset_json(value), ',' ORDER BY key COLLATE "C"), '') || '}'
          INTO v_result FROM jsonb_each(p_value);
    ELSIF v_kind = 'array' THEN
        SELECT '[' || coalesce(string_agg(video_queue.subset_json(value), ',' ORDER BY ordinality), '') || ']'
          INTO v_result FROM jsonb_array_elements(p_value) WITH ORDINALITY;
    ELSIF v_kind = 'number' THEN
        IF p_value::text !~ '^-?(0|[1-9][0-9]*)$' THEN RAISE EXCEPTION 'SUBSET_CANONICAL_INTEGER_DOMAIN'; END IF;
        v_result := p_value::text;
    ELSE
        v_result := p_value::text;
    END IF;
    RETURN v_result;
END $$;
CREATE FUNCTION video_queue.subset_sha(p_value jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, video_queue AS $$
    SELECT encode(public.digest(convert_to(video_queue.subset_json(p_value), 'UTF8'), 'sha256'), 'hex')
$$;
CREATE FUNCTION video_queue.subset_shape(p_value jsonb, p_keys text[]) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT coalesce(CASE WHEN jsonb_typeof(p_value) = 'object' THEN
        (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(p_value) key)
        = (SELECT array_agg(key ORDER BY key COLLATE "C") FROM unnest(p_keys) key)
        ELSE false END,false)
$$;
CREATE FUNCTION video_queue.subset_capability(p_role text) RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
BEGIN
    -- current_user here is the definer, not an authenticated caller.
    IF NOT pg_has_role(session_user, p_role, 'MEMBER') THEN RAISE EXCEPTION 'SUBSET_CAPABILITY_DENIED'; END IF;
END $$;
CREATE FUNCTION video_queue.subset_disabled() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT '{"execution_allowed":false,"release_allowed":false,"database_persistence_allowed":false,"canonical_promotion_allowed":false}'::jsonb
$$;

CREATE TABLE video_queue.subset_approval (
    approval_id uuid PRIMARY KEY,
    contract_bytes text NOT NULL CHECK (octet_length(contract_bytes) BETWEEN 1 AND 4194304),
    contract jsonb NOT NULL,
    contract_sha256 text NOT NULL CHECK (contract_sha256 ~ '^[0-9a-f]{64}$'),
    queue_manifest_sha256 text NOT NULL CHECK (queue_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    plan_sha256 text NOT NULL CHECK (plan_sha256 ~ '^[0-9a-f]{64}$'),
    subset_sha256 text NOT NULL CHECK (subset_sha256 ~ '^[0-9a-f]{64}$'),
    request_key text NOT NULL UNIQUE,
    provenance jsonb NOT NULL,
    approved_by name NOT NULL,
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    runtime_enabled boolean NOT NULL DEFAULT false,
    CHECK (expires_at > approved_at),
    CHECK (NOT runtime_enabled OR revoked_at IS NULL)
);
CREATE TABLE video_queue.source_observation (
    observation_id uuid PRIMARY KEY,
    approval_id uuid NOT NULL REFERENCES video_queue.subset_approval,
    contract_sha256 text NOT NULL,
    request_key text NOT NULL,
    phase text NOT NULL CHECK (phase IN ('ADMISSION','ATTEMPT')),
    sources jsonb NOT NULL CHECK (jsonb_typeof(sources) = 'array'),
    observed_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    collected_by name NOT NULL,
    nonce uuid,
    job_id uuid REFERENCES video_queue.job,
    lease_token uuid,
    attempt_count integer,
    worker_key text,
    consumed_at timestamptz,
    CHECK (expires_at = observed_at + interval '30 seconds'),
    CHECK (coalesce((phase = 'ADMISSION' AND nonce IS NOT NULL AND job_id IS NULL AND lease_token IS NULL AND attempt_count IS NULL AND worker_key IS NULL)
        OR (phase = 'ATTEMPT' AND nonce IS NULL AND job_id IS NOT NULL AND lease_token IS NOT NULL AND attempt_count BETWEEN 1 AND 3 AND worker_key IS NOT NULL),false)),
    UNIQUE (approval_id, nonce)
);
CREATE TABLE video_queue.subset_admission (
    request_key text PRIMARY KEY,
    approval_id uuid NOT NULL UNIQUE REFERENCES video_queue.subset_approval,
    observation_id uuid NOT NULL UNIQUE REFERENCES video_queue.source_observation,
    contract_sha256 text NOT NULL,
    queue_manifest_sha256 text NOT NULL,
    plan_sha256 text NOT NULL,
    subset_sha256 text NOT NULL,
    state text NOT NULL CHECK (state IN ('DRAFT','FINALIZED')),
    batch_id uuid UNIQUE REFERENCES video_queue.batch,
    binding jsonb,
    binding_sha256 text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finalized_at timestamptz,
    CHECK ((state = 'DRAFT' AND batch_id IS NULL AND binding IS NULL AND binding_sha256 IS NULL AND finalized_at IS NULL)
        OR (state = 'FINALIZED' AND batch_id IS NOT NULL AND binding IS NOT NULL AND binding_sha256 IS NOT NULL AND finalized_at IS NOT NULL))
);
CREATE TABLE video_queue.source_reservation (
    source_identity text PRIMARY KEY CHECK (source_identity ~ '^google_drive:[A-Za-z0-9_-]{10,200}$'),
    admission_request_key text REFERENCES video_queue.subset_admission,
    source_fingerprint text,
    batch_id uuid REFERENCES video_queue.batch,
    job_id uuid UNIQUE REFERENCES video_queue.job,
    historical_job_ids uuid[],
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (coalesce((admission_request_key IS NULL AND source_fingerprint IS NULL AND batch_id IS NULL AND job_id IS NULL AND cardinality(historical_job_ids) >= 1)
        OR (admission_request_key IS NOT NULL AND source_fingerprint ~ '^[0-9a-f]{64}$' AND historical_job_ids IS NULL),false))
);
-- Every historical status and every duplicate is retained, with no winner.
INSERT INTO video_queue.source_reservation(source_identity, historical_job_ids)
SELECT 'google_drive:' || source_file_id, array_agg(job_id ORDER BY job_id)
FROM video_queue.job GROUP BY source_file_id ORDER BY source_file_id COLLATE "C";

CREATE FUNCTION video_queue.subset_validate_contract(p_bytes text) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, video_queue AS $$
DECLARE c jsonb; m jsonb; f jsonb; q jsonb; r jsonb; source_map jsonb; v_files jsonb := '[]'; v_checksum text;
    v_count integer; v_total bigint := 0; v_last text; v_key text; v_length integer;
BEGIN
    IF p_bytes IS NULL OR octet_length(p_bytes) NOT BETWEEN 1 AND 4194304 THEN RAISE EXCEPTION 'SUBSET_CONTRACT_INVALID'; END IF;
    c := p_bytes::jsonb;
    -- Exact canonical bytes also reject duplicate keys, alternate spellings,
    -- whitespace, negative zero, escaped Unicode and unsupported float numbers.
    IF video_queue.subset_json(c) IS DISTINCT FROM p_bytes THEN RAISE EXCEPTION 'SUBSET_CANONICAL_BYTES_MISMATCH'; END IF;
    FOREACH v_key IN ARRAY ARRAY['schema','plan_sha256','subset_sha256','processing_profile','algorithm_revision','canary_source_file_id','queue_manifest_sha256','contract_sha256'] LOOP
        IF jsonb_typeof(c->v_key) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'SUBSET_CONTRACT_INVALID'; END IF;
    END LOOP;
    IF NOT video_queue.subset_shape(c, string_to_array('schema plan_sha256 subset_sha256 subset_sequence processing_profile algorithm_revision canary_source_file_id files enqueue_manifest queue_manifest_sha256 execution_allowed release_allowed database_persistence_allowed canonical_promotion_allowed contract_sha256', ' '))
       OR c->>'schema' IS DISTINCT FROM 'universal-video-subset-contract-v1'
       OR NOT c @> video_queue.subset_disabled()
       OR c->>'plan_sha256' !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(c->'subset_sequence') IS DISTINCT FROM 'number'
       OR (c->>'subset_sequence')::numeric < 1
       OR video_queue.subset_sha(c - 'contract_sha256') IS DISTINCT FROM c->>'contract_sha256'
       OR video_queue.subset_sha(c->'files') IS DISTINCT FROM c->>'subset_sha256'
       OR video_queue.subset_sha(c->'enqueue_manifest') IS DISTINCT FROM c->>'queue_manifest_sha256' THEN
        RAISE EXCEPTION 'SUBSET_CONTRACT_INVALID';
    END IF;
    m := c->'enqueue_manifest';
    FOREACH v_key IN ARRAY ARRAY['request_key','source_folder_id','output_folder_id','work_folder_id','processing_profile','algorithm_revision','canary_source_file_id','inventory_sha256','result_mode'] LOOP
        IF jsonb_typeof(m->v_key) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'SUBSET_MANIFEST_INVALID'; END IF;
    END LOOP;
    IF NOT video_queue.subset_shape(m, string_to_array('request_key source_folder_id output_folder_id work_folder_id processing_profile algorithm_revision canary_source_file_id inventory_sha256 expected_count total_size_bytes files result_mode canonical_promotion_allowed database_persistence_allowed', ' '))
       OR m->>'source_folder_id' !~ '^[A-Za-z0-9_-]{10,200}$'
       OR m->>'output_folder_id' !~ '^[A-Za-z0-9_-]{10,200}$'
       OR m->>'work_folder_id' !~ '^[A-Za-z0-9_-]{10,200}$'
       OR m->>'source_folder_id' IN (m->>'output_folder_id',m->>'work_folder_id')
       OR m->>'processing_profile' !~ '^[a-z][a-z0-9_.-]{0,79}$'
       OR m->>'algorithm_revision' !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'
       OR m->>'canary_source_file_id' !~ '^[A-Za-z0-9_-]{10,200}$'
       OR m->>'result_mode' IS DISTINCT FROM 'SHADOW_REVIEW_ONLY'
       OR m->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR m->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
       OR jsonb_typeof(c->'files') IS DISTINCT FROM 'array'
       OR jsonb_typeof(m->'files') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'SUBSET_MANIFEST_INVALID'; END IF;
    v_count := jsonb_array_length(c->'files');
    IF v_count NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'SUBSET_COUNT_INVALID'; END IF;
    FOR f IN SELECT value FROM jsonb_array_elements(c->'files') LOOP
        FOREACH v_key IN ARRAY ARRAY['source_identity','file_id','parent','version','modifiedTime','mime_type','name','source_fingerprint'] LOOP
            IF jsonb_typeof(f->v_key) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'SUBSET_SOURCE_INVALID'; END IF;
        END LOOP;
        IF jsonb_typeof(f->'checksum') NOT IN ('string','null') THEN RAISE EXCEPTION 'SUBSET_CHECKSUM_INVALID'; END IF;
        IF NOT video_queue.subset_shape(f, string_to_array('source_identity file_id parent version modifiedTime mime_type size_bytes checksum checksums name trashed source_fingerprint duration_ms estimated_cost_usd', ' '))
           OR f->>'file_id' !~ '^[A-Za-z0-9_-]{10,200}$'
           OR jsonb_typeof(f->'file_id') IS DISTINCT FROM 'string'
           OR jsonb_typeof(f->'parent') IS DISTINCT FROM 'string'
           OR jsonb_typeof(f->'modifiedTime') IS DISTINCT FROM 'string'
           OR jsonb_typeof(f->'mime_type') IS DISTINCT FROM 'string'
           OR f->>'file_id' IN (m->>'source_folder_id',m->>'output_folder_id',m->>'work_folder_id')
           OR f->>'source_identity' IS DISTINCT FROM 'google_drive:' || (f->>'file_id')
           OR f->>'parent' IS DISTINCT FROM m->>'source_folder_id'
           OR f->'trashed' IS DISTINCT FROM 'false'::jsonb
           OR jsonb_typeof(f->'name') IS DISTINCT FROM 'string'
           OR length(btrim(f->>'name')) NOT BETWEEN 1 AND 500
           OR f->>'name' ~ '^AI_PART_'
           OR f->>'mime_type' !~ '^video/[A-Za-z0-9.+_-]{1,120}$'
           OR jsonb_typeof(f->'size_bytes') IS DISTINCT FROM 'number'
           OR (f->>'size_bytes')::numeric NOT BETWEEN 1048576 AND 68719476736
           OR jsonb_typeof(f->'version') IS DISTINCT FROM 'string'
           OR f->>'version' !~ '^[1-9][0-9]{0,19}$'
           OR (f->>'version')::numeric > 18446744073709551615
           OR f->>'modifiedTime' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?(Z|[+-]([01][0-9]|2[0-3]):[0-5][0-9])$'
           OR jsonb_typeof(f->'checksums') IS DISTINCT FROM 'object'
           OR (f->'duration_ms' <> 'null'::jsonb AND (jsonb_typeof(f->'duration_ms') <> 'number' OR (f->>'duration_ms')::numeric NOT BETWEEN 1 AND 9223372036854775807))
           OR (f->'estimated_cost_usd' <> 'null'::jsonb AND (jsonb_typeof(f->'estimated_cost_usd') <> 'number' OR (f->>'estimated_cost_usd')::numeric NOT BETWEEN 0 AND 1e308::numeric))
           OR (v_last IS NOT NULL AND (f->>'file_id') COLLATE "C" <= v_last COLLATE "C") THEN RAISE EXCEPTION 'SUBSET_SOURCE_INVALID'; END IF;
        PERFORM (f->>'modifiedTime')::timestamptz;
        v_checksum := NULL;
        FOR v_key, v_length IN SELECT * FROM (VALUES ('sha256Checksum',64),('sha1Checksum',40),('md5Checksum',32)) x(k,n) LOOP
            IF f->'checksums' ? v_key THEN
                IF jsonb_typeof(f->'checksums'->v_key) <> 'string' OR f->'checksums'->>v_key !~ ('^[0-9a-f]{' || v_length || '}$') THEN RAISE EXCEPTION 'SUBSET_CHECKSUM_INVALID'; END IF;
                v_checksum := coalesce(v_checksum, replace(v_key,'Checksum','') || ':' || (f->'checksums'->>v_key));
            END IF;
        END LOOP;
        IF ((f->'checksums') - ARRAY['sha256Checksum','sha1Checksum','md5Checksum']) <> '{}'::jsonb OR f->>'checksum' IS DISTINCT FROM v_checksum THEN RAISE EXCEPTION 'SUBSET_CHECKSUM_INVALID'; END IF;
        r := f - ARRAY['name','trashed','source_fingerprint','duration_ms','estimated_cost_usd'];
        IF video_queue.subset_sha(r) IS DISTINCT FROM f->>'source_fingerprint' THEN RAISE EXCEPTION 'SUBSET_FINGERPRINT_INVALID'; END IF;
        v_last := f->>'file_id';
        v_total := v_total + (f->>'size_bytes')::bigint;
    END LOOP;
    -- Existing Python queue builder owns its natural Unicode name sort. The
    -- trusted approver supplies that exact manifest; SQL verifies its complete
    -- ordered bijection and never substitutes a locale-dependent SQL sort.
    SELECT jsonb_object_agg(value->>'file_id',value) INTO source_map FROM jsonb_array_elements(c->'files');
    FOR q IN SELECT value FROM jsonb_array_elements(m->'files') LOOP
        IF NOT video_queue.subset_shape(q, ARRAY['sequence','file_id','name','mime_type','size_bytes','checksum']) THEN RAISE EXCEPTION 'SUBSET_QUEUE_PROJECTION_INVALID'; END IF;
        f := source_map->(q->>'file_id');
        IF f IS NULL OR q - 'sequence' IS DISTINCT FROM jsonb_build_object('file_id',f->'file_id','name',f->'name','mime_type',f->'mime_type','size_bytes',f->'size_bytes','checksum',f->'checksum')
           OR q->'sequence' IS DISTINCT FROM to_jsonb(jsonb_array_length(v_files) + 1) THEN RAISE EXCEPTION 'SUBSET_QUEUE_PROJECTION_INVALID'; END IF;
        v_files := v_files || jsonb_build_array(q);
    END LOOP;
    IF jsonb_array_length(v_files) <> v_count
       OR (SELECT count(DISTINCT value->>'file_id') FROM jsonb_array_elements(v_files)) <> v_count
       OR m->'expected_count' IS DISTINCT FROM to_jsonb(v_count)
       OR m->'total_size_bytes' IS DISTINCT FROM to_jsonb(v_total)
       OR m->>'inventory_sha256' IS DISTINCT FROM video_queue.subset_sha(v_files)
       OR NOT EXISTS (SELECT FROM jsonb_array_elements(v_files) WHERE value->>'file_id' = m->>'canary_source_file_id')
       OR m->'processing_profile' IS DISTINCT FROM c->'processing_profile'
       OR m->'algorithm_revision' IS DISTINCT FROM c->'algorithm_revision'
       OR m->'canary_source_file_id' IS DISTINCT FROM c->'canary_source_file_id' THEN RAISE EXCEPTION 'SUBSET_MANIFEST_INVALID'; END IF;
    r := jsonb_build_object('plan_sha256',c->'plan_sha256','subset_sha256',c->'subset_sha256','subset_sequence',c->'subset_sequence','processing_profile',c->'processing_profile','algorithm_revision',c->'algorithm_revision','canary_source_file_id',c->'canary_source_file_id');
    IF m->>'request_key' IS DISTINCT FROM 'subset-v1:' || video_queue.subset_sha(r) THEN RAISE EXCEPTION 'SUBSET_REQUEST_INVALID'; END IF;
    RETURN c;
END $$;

CREATE FUNCTION video_queue.approve_subset(p_approval_id uuid, p_contract_bytes text, p_provenance jsonb, p_expires_at timestamptz) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE c jsonb;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_approver');
    c := video_queue.subset_validate_contract(p_contract_bytes);
    IF p_approval_id IS NULL OR p_expires_at IS NULL OR p_expires_at <= clock_timestamp()
       OR NOT video_queue.subset_shape(p_provenance, ARRAY['approved_by','approval_reference','plan_sha256'])
       OR jsonb_typeof(p_provenance->'approved_by') IS DISTINCT FROM 'string'
       OR jsonb_typeof(p_provenance->'approval_reference') IS DISTINCT FROM 'string'
       OR length(btrim(p_provenance->>'approved_by')) NOT BETWEEN 1 AND 500
       OR length(btrim(p_provenance->>'approval_reference')) NOT BETWEEN 1 AND 500
       OR p_provenance->'plan_sha256' IS DISTINCT FROM c->'plan_sha256' THEN RAISE EXCEPTION 'SUBSET_APPROVAL_INVALID'; END IF;
    INSERT INTO video_queue.subset_approval(approval_id,contract_bytes,contract,contract_sha256,queue_manifest_sha256,plan_sha256,subset_sha256,request_key,provenance,approved_by,expires_at)
    VALUES(p_approval_id,p_contract_bytes,c,c->>'contract_sha256',c->>'queue_manifest_sha256',c->>'plan_sha256',c->>'subset_sha256',c#>>'{enqueue_manifest,request_key}',p_provenance,session_user,p_expires_at);
    RETURN p_approval_id;
END $$;
CREATE FUNCTION video_queue.set_subset_runtime(p_approval_id uuid, p_enabled boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE a video_queue.subset_approval%ROWTYPE;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_approver');
    SELECT * INTO a FROM video_queue.subset_approval WHERE approval_id=p_approval_id FOR UPDATE;
    IF NOT FOUND OR p_enabled IS NULL OR (p_enabled AND (a.revoked_at IS NOT NULL OR a.expires_at <= clock_timestamp())) THEN RAISE EXCEPTION 'SUBSET_APPROVAL_NOT_ACTIVE'; END IF;
    UPDATE video_queue.subset_approval SET runtime_enabled=p_enabled WHERE approval_id=p_approval_id;
END $$;
CREATE FUNCTION video_queue.revoke_subset_approval(p_approval_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
BEGIN
    PERFORM video_queue.subset_capability('video_subset_approver');
    UPDATE video_queue.subset_approval SET revoked_at=coalesce(revoked_at,clock_timestamp()),runtime_enabled=false WHERE approval_id=p_approval_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'SUBSET_APPROVAL_NOT_FOUND'; END IF;
END $$;

CREATE FUNCTION video_queue.subset_observed_sources(p_contract jsonb, p_sources jsonb, p_only_file text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, video_queue AS $$
DECLARE raw jsonb; f jsonb; r jsonb; source_map jsonb; checksums jsonb; checksum text; k text; n integer; v text; result jsonb := '[]'; ids text[] := '{}';
BEGIN
    IF jsonb_typeof(p_sources) IS DISTINCT FROM 'array' OR jsonb_array_length(p_sources) <> CASE WHEN p_only_file IS NULL THEN jsonb_array_length(p_contract->'files') ELSE 1 END THEN RAISE EXCEPTION 'SUBSET_SOURCE_SET_MISMATCH'; END IF;
    SELECT jsonb_object_agg(value->>'file_id',value) INTO source_map FROM jsonb_array_elements(p_contract->'files');
    FOR raw IN SELECT value FROM jsonb_array_elements(p_sources) LOOP
        f := source_map->(raw->>'id');
        IF f IS NULL OR raw->>'id'=ANY(ids) OR (p_only_file IS NOT NULL AND raw->>'id' IS DISTINCT FROM p_only_file)
           OR raw->'parents' IS DISTINCT FROM jsonb_build_array(f->'parent')
           OR raw->'trashed' IS DISTINCT FROM 'false'::jsonb
           OR raw->'name' IS DISTINCT FROM f->'name'
           OR raw->'version' IS DISTINCT FROM f->'version'
           OR raw->'modifiedTime' IS DISTINCT FROM f->'modifiedTime'
           OR raw->'mimeType' IS DISTINCT FROM f->'mime_type'
           OR jsonb_typeof(raw->'size') NOT IN ('string','number')
           OR raw->>'size' !~ '^[0-9]{1,20}$'
           OR (raw->>'size')::numeric IS DISTINCT FROM (f->>'size_bytes')::numeric THEN RAISE EXCEPTION 'SUBSET_SOURCE_REVISION_CHANGED'; END IF;
        checksums := '{}'; checksum := NULL;
        FOR k,n IN SELECT * FROM (VALUES ('sha256Checksum',64),('sha1Checksum',40),('md5Checksum',32)) x(k,n) LOOP
            IF raw->k IS NOT NULL AND raw->k <> 'null'::jsonb THEN
                v := raw->>k;
                IF jsonb_typeof(raw->k) <> 'string' OR v !~ ('^[0-9a-fA-F]{' || n || '}$') THEN RAISE EXCEPTION 'SUBSET_SOURCE_REVISION_CHANGED'; END IF;
                checksums := checksums || jsonb_build_object(k,lower(v));
                checksum := coalesce(checksum,replace(k,'Checksum','') || ':' || lower(v));
            END IF;
        END LOOP;
        r := jsonb_build_object('source_identity',f->'source_identity','file_id',raw->'id','parent',raw->'parents'->0,'version',raw->'version','modifiedTime',raw->'modifiedTime','mime_type',raw->'mimeType','size_bytes',(raw->>'size')::bigint,'checksum',checksum,'checksums',checksums);
        IF video_queue.subset_sha(r) IS DISTINCT FROM f->>'source_fingerprint' THEN RAISE EXCEPTION 'SUBSET_SOURCE_REVISION_CHANGED'; END IF;
        result := result || jsonb_build_array(r || jsonb_build_object('name',raw->'name','source_fingerprint',f->'source_fingerprint','trashed',false));
        ids := array_append(ids,raw->>'id');
    END LOOP;
    SELECT jsonb_agg(value ORDER BY value->>'file_id' COLLATE "C") INTO result FROM jsonb_array_elements(result);
    RETURN result;
END $$;
CREATE FUNCTION video_queue.observe_subset(p_observation_id uuid,p_approval_id uuid,p_phase text,p_sources jsonb,p_observed_at timestamptz,p_nonce uuid DEFAULT NULL,p_job_id uuid DEFAULT NULL,p_lease_token uuid DEFAULT NULL,p_attempt_count integer DEFAULT NULL,p_worker_key text DEFAULT NULL) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE a video_queue.subset_approval%ROWTYPE; j video_queue.job%ROWTYPE; normalized jsonb;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_collector');
    SELECT * INTO a FROM video_queue.subset_approval WHERE approval_id=p_approval_id;
    IF NOT FOUND OR a.revoked_at IS NOT NULL OR a.expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_APPROVAL_NOT_ACTIVE'; END IF;
    IF p_observation_id IS NULL OR p_observed_at IS NULL OR p_observed_at > clock_timestamp() OR p_observed_at + interval '30 seconds' <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_EXPIRED'; END IF;
    IF p_phase='ADMISSION' THEN
        IF p_nonce IS NULL OR p_job_id IS NOT NULL OR p_lease_token IS NOT NULL OR p_attempt_count IS NOT NULL OR p_worker_key IS NOT NULL THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_SCOPE'; END IF;
    ELSIF p_phase='ATTEMPT' THEN
        SELECT * INTO j FROM video_queue.job WHERE job_id=p_job_id;
        IF NOT FOUND OR p_nonce IS NOT NULL OR p_lease_token IS NULL OR p_worker_key IS NULL OR p_attempt_count IS NULL
           OR j.status <> 'LEASED' OR j.lease_token IS DISTINCT FROM p_lease_token OR j.lease_owner IS DISTINCT FROM p_worker_key
           OR j.attempt_count IS DISTINCT FROM p_attempt_count OR j.lease_expires_at <= clock_timestamp()
           OR NOT EXISTS (SELECT FROM video_queue.subset_admission WHERE approval_id=p_approval_id AND batch_id=j.batch_id AND state='FINALIZED') THEN RAISE EXCEPTION 'SUBSET_LEASE_LOST'; END IF;
    ELSE RAISE EXCEPTION 'SUBSET_OBSERVATION_SCOPE'; END IF;
    normalized := video_queue.subset_observed_sources(a.contract,p_sources,j.source_file_id);
    INSERT INTO video_queue.source_observation(observation_id,approval_id,contract_sha256,request_key,phase,sources,observed_at,expires_at,collected_by,nonce,job_id,lease_token,attempt_count,worker_key)
    VALUES(p_observation_id,p_approval_id,a.contract_sha256,a.request_key,p_phase,normalized,p_observed_at,p_observed_at+interval '30 seconds',session_user,p_nonce,p_job_id,p_lease_token,p_attempt_count,p_worker_key);
    RETURN p_observation_id;
END $$;

CREATE FUNCTION video_queue.subset_build_binding(p_request_key text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE a video_queue.subset_approval%ROWTYPE; d video_queue.subset_admission%ROWTYPE; b video_queue.batch%ROWTYPE; j video_queue.job%ROWTYPE; m jsonb; f jsonb; q jsonb; source_map jsonb; jobs jsonb := '[]'; core jsonb; projection jsonb;
BEGIN
    SELECT * INTO d FROM video_queue.subset_admission WHERE request_key=p_request_key;
    SELECT * INTO a FROM video_queue.subset_approval WHERE approval_id=d.approval_id;
    SELECT * INTO b FROM video_queue.batch WHERE request_key=p_request_key;
    IF d.request_key IS NULL OR a.approval_id IS NULL OR b.batch_id IS NULL THEN RAISE EXCEPTION 'SUBSET_BINDING_MISSING_HOLD'; END IF;
    IF video_queue.subset_validate_contract(a.contract_bytes) IS DISTINCT FROM a.contract
       OR a.contract_sha256 IS DISTINCT FROM a.contract->>'contract_sha256'
       OR a.queue_manifest_sha256 IS DISTINCT FROM a.contract->>'queue_manifest_sha256'
       OR a.plan_sha256 IS DISTINCT FROM a.contract->>'plan_sha256'
       OR a.subset_sha256 IS DISTINCT FROM a.contract->>'subset_sha256'
       OR a.request_key IS DISTINCT FROM a.contract#>>'{enqueue_manifest,request_key}' THEN RAISE EXCEPTION 'SUBSET_APPROVAL_CONTENT_HOLD'; END IF;
    m := a.contract->'enqueue_manifest';
    projection := to_jsonb(b) - ARRAY['batch_id','source_kind','status','created_at','updated_at','completed_at'];
    IF b.source_kind <> 'google_drive_folder' OR projection IS DISTINCT FROM m - 'files'
       OR d.contract_sha256 IS DISTINCT FROM a.contract_sha256 OR d.queue_manifest_sha256 IS DISTINCT FROM a.queue_manifest_sha256
       OR d.plan_sha256 IS DISTINCT FROM a.plan_sha256 OR d.subset_sha256 IS DISTINCT FROM a.subset_sha256
       OR d.request_key IS DISTINCT FROM a.request_key
       OR (SELECT count(*) FROM video_queue.job WHERE batch_id=b.batch_id) <> b.expected_count THEN RAISE EXCEPTION 'SUBSET_BINDING_MISMATCH_HOLD'; END IF;
    SELECT jsonb_object_agg(value->>'file_id',value) INTO source_map FROM jsonb_array_elements(a.contract->'files');
    FOR q IN SELECT value FROM jsonb_array_elements(m->'files') LOOP
        SELECT * INTO j FROM video_queue.job WHERE batch_id=b.batch_id AND source_file_id=q->>'file_id';
        f := source_map->(q->>'file_id');
        IF j.job_id IS NULL OR jsonb_build_object('sequence',j.sequence,'file_id',j.source_file_id,'name',j.source_name,'mime_type',j.source_mime_type,'size_bytes',j.source_size_bytes,'checksum',j.source_checksum) IS DISTINCT FROM q
           OR j.stable_job_key IS DISTINCT FROM substr(encode(public.digest(convert_to('bridge-video|drive|' || j.source_file_id,'UTF8'),'sha256'),'hex'),1,32)
           OR j.is_canary IS DISTINCT FROM (j.source_file_id=b.canary_source_file_id) THEN RAISE EXCEPTION 'SUBSET_BINDING_MISMATCH_HOLD'; END IF;
        jobs := jobs || jsonb_build_array(jsonb_build_object('file_id',j.source_file_id,'job_id',j.job_id,'sequence',j.sequence,'source_identity',f->'source_identity','source_fingerprint',f->'source_fingerprint'));
    END LOOP;
    core := jsonb_build_object('schema','universal-video-admission-binding-v1','approval_id',a.approval_id,'request_key',p_request_key,'batch_id',b.batch_id,'plan_sha256',a.plan_sha256,'subset_sha256',a.subset_sha256,'contract_sha256',a.contract_sha256,'queue_manifest_sha256',a.queue_manifest_sha256,'jobs',jobs) || video_queue.subset_disabled();
    RETURN core || jsonb_build_object('binding_sha256',video_queue.subset_sha(core));
END $$;
CREATE FUNCTION video_queue.subset_assert_final(p_request_key text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE d video_queue.subset_admission%ROWTYPE; rebuilt jsonb; x jsonb; o video_queue.source_observation%ROWTYPE;
BEGIN
    SELECT * INTO d FROM video_queue.subset_admission WHERE request_key=p_request_key;
    IF NOT FOUND THEN RAISE EXCEPTION 'SUBSET_BINDING_MISSING_HOLD'; END IF;
    IF d.state <> 'FINALIZED' THEN RAISE EXCEPTION 'SUBSET_UNFINALIZED_HOLD'; END IF;
    rebuilt := video_queue.subset_build_binding(p_request_key);
    IF d.binding IS DISTINCT FROM rebuilt OR d.binding_sha256 IS DISTINCT FROM rebuilt->>'binding_sha256' OR d.batch_id::text IS DISTINCT FROM rebuilt->>'batch_id' THEN RAISE EXCEPTION 'SUBSET_BINDING_MISMATCH_HOLD'; END IF;
    IF (SELECT count(*) FROM video_queue.source_reservation WHERE admission_request_key=p_request_key) <> jsonb_array_length(rebuilt->'jobs') THEN RAISE EXCEPTION 'SUBSET_RESERVATION_MISMATCH_HOLD'; END IF;
    FOR x IN SELECT value FROM jsonb_array_elements(rebuilt->'jobs') LOOP
        IF NOT EXISTS (SELECT FROM video_queue.source_reservation WHERE source_identity=x->>'source_identity' AND admission_request_key=p_request_key AND source_fingerprint=x->>'source_fingerprint' AND batch_id=d.batch_id AND job_id=(x->>'job_id')::uuid AND historical_job_ids IS NULL) THEN RAISE EXCEPTION 'SUBSET_RESERVATION_MISMATCH_HOLD'; END IF;
    END LOOP;
    SELECT * INTO o FROM video_queue.source_observation WHERE observation_id=d.observation_id;
    IF o.approval_id IS DISTINCT FROM d.approval_id OR o.phase IS DISTINCT FROM 'ADMISSION' OR o.contract_sha256 IS DISTINCT FROM d.contract_sha256 OR o.request_key IS DISTINCT FROM d.request_key OR o.consumed_at IS NULL
       OR o.sources IS DISTINCT FROM (SELECT jsonb_agg(f.value-ARRAY['duration_ms','estimated_cost_usd'] ORDER BY f.value->>'file_id' COLLATE "C") FROM video_queue.subset_approval a CROSS JOIN LATERAL jsonb_array_elements(a.contract->'files') f WHERE a.approval_id=d.approval_id) THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_BINDING_HOLD'; END IF;
END $$;
CREATE FUNCTION video_queue.recover_subset_admission(p_request_key text,p_contract_sha256 text,p_queue_manifest_sha256 text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE d video_queue.subset_admission%ROWTYPE;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_admitter');
    SELECT * INTO d FROM video_queue.subset_admission WHERE request_key=p_request_key;
    IF NOT FOUND THEN
        IF EXISTS (SELECT FROM video_queue.batch WHERE request_key=p_request_key) THEN RAISE EXCEPTION 'SUBSET_LEGACY_BINDING_HOLD'; END IF;
        RETURN jsonb_build_object('status','NOT_FOUND') || video_queue.subset_disabled();
    END IF;
    IF d.contract_sha256 IS DISTINCT FROM p_contract_sha256 OR d.queue_manifest_sha256 IS DISTINCT FROM p_queue_manifest_sha256 THEN RAISE EXCEPTION 'SUBSET_DIGEST_MISMATCH_HOLD'; END IF;
    PERFORM video_queue.subset_assert_final(p_request_key);
    RETURN jsonb_build_object('status','RECOVERED_IMMUTABLE_BINDING','binding',d.binding) || video_queue.subset_disabled();
END $$;
CREATE FUNCTION video_queue.admit_approved_subset(p_approval_id uuid,p_request_key text,p_contract_sha256 text,p_queue_manifest_sha256 text,p_observation_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE a video_queue.subset_approval%ROWTYPE; o video_queue.source_observation%ROWTYPE; d video_queue.subset_admission%ROWTYPE; f jsonb; m jsonb; bound jsonb; v_batch_id uuid; v_owner text;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_admitter');
    IF p_request_key IS NULL OR p_request_key !~ '^subset-v1:[0-9a-f]{64}$' OR p_approval_id IS NULL OR p_contract_sha256 IS NULL OR p_queue_manifest_sha256 IS NULL THEN RAISE EXCEPTION 'SUBSET_ARGUMENT_INVALID'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_request_key,0));
    SELECT * INTO d FROM video_queue.subset_admission WHERE request_key=p_request_key;
    IF FOUND THEN
        IF d.approval_id IS DISTINCT FROM p_approval_id THEN RAISE EXCEPTION 'SUBSET_APPROVAL_MISMATCH_HOLD'; END IF;
        RETURN video_queue.recover_subset_admission(p_request_key,p_contract_sha256,p_queue_manifest_sha256);
    END IF;
    IF EXISTS (SELECT FROM video_queue.batch WHERE request_key=p_request_key) THEN RAISE EXCEPTION 'SUBSET_LEGACY_BINDING_HOLD'; END IF;
    SELECT * INTO a FROM video_queue.subset_approval WHERE approval_id=p_approval_id FOR UPDATE;
    IF NOT FOUND OR a.revoked_at IS NOT NULL OR a.expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_APPROVAL_NOT_ACTIVE'; END IF;
    IF a.request_key IS DISTINCT FROM p_request_key OR a.contract_sha256 IS DISTINCT FROM p_contract_sha256 OR a.queue_manifest_sha256 IS DISTINCT FROM p_queue_manifest_sha256 OR video_queue.subset_validate_contract(a.contract_bytes) IS DISTINCT FROM a.contract THEN RAISE EXCEPTION 'SUBSET_DIGEST_MISMATCH_HOLD'; END IF;
    SELECT * INTO o FROM video_queue.source_observation WHERE observation_id=p_observation_id FOR UPDATE;
    IF NOT FOUND OR o.approval_id IS DISTINCT FROM p_approval_id OR o.contract_sha256 IS DISTINCT FROM p_contract_sha256 OR o.request_key IS DISTINCT FROM p_request_key OR o.phase <> 'ADMISSION' OR o.consumed_at IS NOT NULL THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_SCOPE'; END IF;
    INSERT INTO video_queue.subset_admission(request_key,approval_id,observation_id,contract_sha256,queue_manifest_sha256,plan_sha256,subset_sha256,state)
    VALUES(p_request_key,p_approval_id,p_observation_id,p_contract_sha256,p_queue_manifest_sha256,a.plan_sha256,a.subset_sha256,'DRAFT');
    FOR f IN SELECT value FROM jsonb_array_elements(a.contract->'files') ORDER BY value->>'source_identity' COLLATE "C" LOOP
        INSERT INTO video_queue.source_reservation(source_identity,admission_request_key,source_fingerprint)
        VALUES(f->>'source_identity',p_request_key,f->>'source_fingerprint') ON CONFLICT (source_identity) DO NOTHING;
        -- Separate statement = fresh READ COMMITTED snapshot after PK wait.
        SELECT admission_request_key INTO v_owner FROM video_queue.source_reservation WHERE source_identity=f->>'source_identity';
        IF v_owner IS DISTINCT FROM p_request_key THEN RAISE EXCEPTION 'SUBSET_SOURCE_RESERVED_HOLD'; END IF;
    END LOOP;
    IF a.expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_APPROVAL_NOT_ACTIVE'; END IF;
    IF o.observed_at > clock_timestamp() OR o.expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_EXPIRED'; END IF;
    IF o.sources IS DISTINCT FROM (SELECT jsonb_agg(value - ARRAY['duration_ms','estimated_cost_usd'] ORDER BY value->>'file_id' COLLATE "C") FROM jsonb_array_elements(a.contract->'files')) THEN RAISE EXCEPTION 'SUBSET_SOURCE_REVISION_CHANGED'; END IF;
    m := a.contract->'enqueue_manifest';
    SELECT batch_id INTO v_batch_id FROM video_queue.enqueue_drive_batch(p_request_key,m->>'source_folder_id',m->>'output_folder_id',m->>'work_folder_id',m->>'processing_profile',m->>'algorithm_revision',m->>'canary_source_file_id',m->>'inventory_sha256',m->'files');
    bound := video_queue.subset_build_binding(p_request_key);
    UPDATE video_queue.source_reservation r SET batch_id=v_batch_id,job_id=j.job_id FROM video_queue.job j WHERE r.admission_request_key=p_request_key AND j.batch_id=v_batch_id AND r.source_identity='google_drive:' || j.source_file_id;
    UPDATE video_queue.source_observation SET consumed_at=clock_timestamp() WHERE observation_id=p_observation_id;
    UPDATE video_queue.subset_admission SET state='FINALIZED',batch_id=v_batch_id,binding=bound,binding_sha256=bound->>'binding_sha256',finalized_at=clock_timestamp() WHERE request_key=p_request_key;
    PERFORM video_queue.subset_assert_final(p_request_key);
    RETURN jsonb_build_object('status','ADMITTED','binding',bound) || video_queue.subset_disabled();
END $$;

-- Persistent table fences are authority checks, not caller-set GUCs. They also
-- protect against accidentally re-granting a legacy SECURITY DEFINER function.
CREATE FUNCTION video_queue.subset_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE d video_queue.subset_admission%ROWTYPE; a video_queue.subset_approval%ROWTYPE; b video_queue.batch%ROWTYPE; f jsonb; oldj jsonb; newj jsonb;
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
        SELECT * INTO d FROM video_queue.subset_admission WHERE request_key=NEW.admission_request_key;
        IF d.state IS DISTINCT FROM 'DRAFT' OR NEW.historical_job_ids IS NOT NULL
           OR (TG_OP='UPDATE' AND (OLD.batch_id IS NOT NULL OR OLD.job_id IS NOT NULL OR NEW.batch_id IS NULL OR NEW.job_id IS NULL OR to_jsonb(NEW)-ARRAY['batch_id','job_id'] IS DISTINCT FROM to_jsonb(OLD)-ARRAY['batch_id','job_id'])) THEN RAISE EXCEPTION 'SUBSET_RESERVATION_MISMATCH_HOLD'; END IF;
    ELSIF TG_TABLE_NAME='batch' THEN
        SELECT * INTO d FROM video_queue.subset_admission WHERE request_key=NEW.request_key;
        IF TG_OP='INSERT' THEN
            PERFORM video_queue.subset_capability('video_subset_admitter');
            IF d.state IS DISTINCT FROM 'DRAFT' THEN RAISE EXCEPTION 'SUBSET_LEGACY_INSERT_HOLD'; END IF;
            SELECT * INTO a FROM video_queue.subset_approval WHERE approval_id=d.approval_id;
            IF (to_jsonb(NEW)-ARRAY['batch_id','source_kind','status','created_at','updated_at','completed_at']) IS DISTINCT FROM ((a.contract->'enqueue_manifest')-'files') OR NEW.source_kind <> 'google_drive_folder' OR NEW.status <> 'QUEUED_CANARY' THEN RAISE EXCEPTION 'SUBSET_BINDING_MISMATCH_HOLD'; END IF;
        ELSE
            PERFORM video_queue.subset_capability('video_subset_worker_v2');
            IF d.state IS DISTINCT FROM 'FINALIZED' OR to_jsonb(NEW)-ARRAY['status','updated_at','completed_at'] IS DISTINCT FROM to_jsonb(OLD)-ARRAY['status','updated_at','completed_at'] THEN RAISE EXCEPTION 'SUBSET_IMMUTABLE_HOLD'; END IF;
        END IF;
    ELSIF TG_TABLE_NAME='job' THEN
        SELECT * INTO b FROM video_queue.batch WHERE batch_id=NEW.batch_id;
        SELECT * INTO d FROM video_queue.subset_admission WHERE request_key=b.request_key;
        IF TG_OP='INSERT' THEN
            PERFORM video_queue.subset_capability('video_subset_admitter');
            IF d.state IS DISTINCT FROM 'DRAFT' OR NOT EXISTS (SELECT FROM video_queue.source_reservation WHERE source_identity='google_drive:' || NEW.source_file_id AND admission_request_key=d.request_key) THEN RAISE EXCEPTION 'SUBSET_LEGACY_INSERT_HOLD'; END IF;
            SELECT value INTO f FROM video_queue.subset_approval a, jsonb_array_elements(a.contract#>'{enqueue_manifest,files}') WHERE a.approval_id=d.approval_id AND value->>'file_id'=NEW.source_file_id;
            IF f IS NULL OR jsonb_build_object('sequence',NEW.sequence,'file_id',NEW.source_file_id,'name',NEW.source_name,'mime_type',NEW.source_mime_type,'size_bytes',NEW.source_size_bytes,'checksum',NEW.source_checksum) IS DISTINCT FROM f
               OR NEW.attempt_count <> 0 OR NEW.is_canary IS DISTINCT FROM (NEW.source_file_id=b.canary_source_file_id)
               OR NEW.status IS DISTINCT FROM CASE WHEN NEW.is_canary THEN 'QUEUED' ELSE 'PENDING_CANARY' END THEN RAISE EXCEPTION 'SUBSET_BINDING_MISMATCH_HOLD'; END IF;
        ELSE
            PERFORM video_queue.subset_capability('video_subset_worker_v2');
            IF d.state IS DISTINCT FROM 'FINALIZED' THEN RAISE EXCEPTION 'SUBSET_LEGACY_WORKER_HOLD'; END IF;
            oldj := to_jsonb(OLD)-ARRAY['status','attempt_count','lease_owner','lease_token','lease_expires_at','next_attempt_at','error_code','output','updated_at','completed_at'];
            newj := to_jsonb(NEW)-ARRAY['status','attempt_count','lease_owner','lease_token','lease_expires_at','next_attempt_at','error_code','output','updated_at','completed_at'];
            IF oldj IS DISTINCT FROM newj THEN RAISE EXCEPTION 'SUBSET_IMMUTABLE_HOLD'; END IF;
            IF NEW.attempt_count > 3 OR NEW.attempt_count < OLD.attempt_count OR NEW.attempt_count > OLD.attempt_count+1
               OR (NEW.attempt_count=OLD.attempt_count+1 AND (NEW.status <> 'LEASED' OR NEW.lease_token IS NULL OR NEW.lease_token IS NOT DISTINCT FROM OLD.lease_token OR OLD.status NOT IN ('QUEUED','LEASED')))
               OR (NEW.status='LEASED' AND NEW.lease_token IS DISTINCT FROM OLD.lease_token AND NEW.attempt_count <> OLD.attempt_count+1)
               OR (NEW.status='QUEUED' AND NEW.attempt_count >= 3)
               OR (OLD.status='PENDING_CANARY' AND NEW.status <> 'PENDING_CANARY') THEN RAISE EXCEPTION 'SUBSET_ATTEMPT_OR_RELEASE_HOLD'; END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE FUNCTION video_queue.subset_deferred_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE k text; d video_queue.subset_admission%ROWTYPE; r video_queue.source_reservation%ROWTYPE;
    o video_queue.source_observation%ROWTYPE; x jsonb; seq integer;
BEGIN
    IF TG_TABLE_NAME='subset_admission' THEN
        PERFORM video_queue.subset_assert_final(NEW.request_key);
        RETURN NULL;
    ELSIF TG_TABLE_NAME='source_reservation' THEN k := NEW.admission_request_key;
    ELSIF TG_TABLE_NAME='batch' THEN k := NEW.request_key;
    ELSIF TG_TABLE_NAME='job' THEN SELECT request_key INTO k FROM video_queue.batch WHERE batch_id=NEW.batch_id;
    ELSIF TG_TABLE_NAME='source_observation' THEN SELECT request_key INTO k FROM video_queue.subset_admission WHERE observation_id=NEW.observation_id;
    ELSIF TG_TABLE_NAME='subset_approval' THEN SELECT request_key INTO k FROM video_queue.subset_admission WHERE approval_id=NEW.approval_id;
    END IF;
    IF k IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO d FROM video_queue.subset_admission WHERE request_key=k;
    IF d.state IS DISTINCT FROM 'FINALIZED' THEN RAISE EXCEPTION 'SUBSET_UNFINALIZED_HOLD'; END IF;
    -- Full bijection validation runs for both admission mutations. Every other
    -- table schedules an affected-row proof; immutable BEFORE fences prohibit
    -- later edits to the other projection fields. Do not rebuild all 1000 jobs
    -- for each of the 2000 reservation/job triggers (quadratic SQL round trips).
    IF TG_TABLE_NAME='batch' THEN
        IF NEW.batch_id IS DISTINCT FROM d.batch_id THEN RAISE EXCEPTION 'SUBSET_BINDING_MISMATCH_HOLD'; END IF;
    ELSIF TG_TABLE_NAME='job' THEN
        x := (d.binding->'jobs')->(NEW.sequence-1);
        IF NEW.batch_id IS DISTINCT FROM d.batch_id OR x->>'job_id' IS DISTINCT FROM NEW.job_id::text OR x->>'file_id' IS DISTINCT FROM NEW.source_file_id OR x->'sequence' IS DISTINCT FROM to_jsonb(NEW.sequence) THEN RAISE EXCEPTION 'SUBSET_BINDING_MISMATCH_HOLD'; END IF;
    ELSIF TG_TABLE_NAME='source_reservation' THEN
        -- INSERT event images predate mapping; inspect the final persisted row.
        SELECT * INTO r FROM video_queue.source_reservation WHERE source_identity=NEW.source_identity;
        SELECT sequence INTO seq FROM video_queue.job WHERE job_id=r.job_id;
        x := (d.binding->'jobs')->(seq-1);
        IF r.batch_id IS DISTINCT FROM d.batch_id OR x->>'job_id' IS DISTINCT FROM r.job_id::text
           OR x->>'source_identity' IS DISTINCT FROM r.source_identity OR x->>'source_fingerprint' IS DISTINCT FROM r.source_fingerprint THEN RAISE EXCEPTION 'SUBSET_RESERVATION_MISMATCH_HOLD'; END IF;
    ELSIF TG_TABLE_NAME='source_observation' THEN
        SELECT * INTO o FROM video_queue.source_observation WHERE observation_id=NEW.observation_id;
        IF o.approval_id IS DISTINCT FROM d.approval_id OR o.request_key IS DISTINCT FROM k OR o.phase <> 'ADMISSION' OR o.consumed_at IS NULL OR o.contract_sha256 IS DISTINCT FROM d.contract_sha256 THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_BINDING_HOLD'; END IF;
    ELSIF TG_TABLE_NAME='subset_approval' THEN
        IF NEW.approval_id IS DISTINCT FROM d.approval_id OR NEW.contract_sha256 IS DISTINCT FROM d.contract_sha256 OR NEW.queue_manifest_sha256 IS DISTINCT FROM d.queue_manifest_sha256 THEN RAISE EXCEPTION 'SUBSET_APPROVAL_CONTENT_HOLD'; END IF;
    END IF;
    RETURN NULL;
END $$;
DO $triggers$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['subset_approval','source_observation','subset_admission','source_reservation','batch','job'] LOOP
        EXECUTE format('CREATE TRIGGER subset_guard BEFORE INSERT OR UPDATE OR DELETE ON video_queue.%I FOR EACH ROW EXECUTE FUNCTION video_queue.subset_guard()',t);
        EXECUTE format('CREATE TRIGGER subset_no_truncate BEFORE TRUNCATE ON video_queue.%I FOR EACH STATEMENT EXECUTE FUNCTION video_queue.subset_guard()',t);
        EXECUTE format('CREATE CONSTRAINT TRIGGER subset_final AFTER INSERT OR UPDATE ON video_queue.%I DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION video_queue.subset_deferred_guard()',t);
    END LOOP;
END $triggers$;

CREATE FUNCTION video_queue.subset_worker_eligible(p_batch_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
    SELECT EXISTS (SELECT FROM video_queue.subset_admission d JOIN video_queue.subset_approval a USING(approval_id)
        WHERE d.batch_id=p_batch_id AND d.state='FINALIZED' AND a.runtime_enabled AND a.revoked_at IS NULL AND a.expires_at > clock_timestamp())
$$;
CREATE FUNCTION video_queue.subset_lock_lease(p_job_id uuid,p_lease_token uuid,p_worker_key text) RETURNS video_queue.job
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE j video_queue.job%ROWTYPE; a video_queue.subset_approval%ROWTYPE;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_worker_v2');
    SELECT * INTO j FROM video_queue.job WHERE job_id=p_job_id FOR UPDATE;
    IF NOT FOUND OR p_lease_token IS NULL OR p_worker_key IS NULL OR j.status <> 'LEASED'
       OR j.lease_token IS DISTINCT FROM p_lease_token OR j.lease_owner IS DISTINCT FROM p_worker_key
       OR j.attempt_count NOT BETWEEN 1 AND 3 OR j.lease_expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_LEASE_LOST'; END IF;
    SELECT a0.* INTO a FROM video_queue.subset_approval a0 JOIN video_queue.subset_admission d USING(approval_id) WHERE d.batch_id=j.batch_id AND d.state='FINALIZED' FOR SHARE OF a0;
    IF NOT FOUND OR NOT a.runtime_enabled OR a.revoked_at IS NOT NULL OR a.expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_RUNTIME_HOLD'; END IF;
    -- Approval lock may have waited behind revocation; re-read wall clock.
    IF j.lease_expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_LEASE_LOST'; END IF;
    RETURN j;
END $$;
CREATE FUNCTION video_queue.authorize_subset_attempt(p_job_id uuid,p_lease_token uuid,p_worker_key text,p_observation_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE j video_queue.job%ROWTYPE; o video_queue.source_observation%ROWTYPE; a video_queue.subset_approval%ROWTYPE; expected jsonb; checked_at timestamptz;
BEGIN
    j := video_queue.subset_lock_lease(p_job_id,p_lease_token,p_worker_key);
    SELECT a0.* INTO a FROM video_queue.subset_approval a0 JOIN video_queue.subset_admission d USING(approval_id) WHERE d.batch_id=j.batch_id;
    SELECT * INTO o FROM video_queue.source_observation WHERE observation_id=p_observation_id FOR UPDATE;
    IF NOT FOUND OR o.phase <> 'ATTEMPT' OR o.approval_id IS DISTINCT FROM a.approval_id OR o.contract_sha256 IS DISTINCT FROM a.contract_sha256 OR o.request_key IS DISTINCT FROM a.request_key
       OR o.job_id IS DISTINCT FROM j.job_id OR o.lease_token IS DISTINCT FROM j.lease_token OR o.attempt_count IS DISTINCT FROM j.attempt_count OR o.worker_key IS DISTINCT FROM j.lease_owner OR o.consumed_at IS NOT NULL THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_SCOPE'; END IF;
    checked_at := clock_timestamp();
    IF a.expires_at <= checked_at THEN RAISE EXCEPTION 'SUBSET_APPROVAL_NOT_ACTIVE'; END IF;
    IF o.observed_at > checked_at OR o.expires_at <= checked_at OR j.lease_expires_at <= checked_at THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_EXPIRED'; END IF;
    SELECT jsonb_build_array(value-ARRAY['duration_ms','estimated_cost_usd']) INTO expected FROM jsonb_array_elements(a.contract->'files') WHERE value->>'file_id'=j.source_file_id;
    IF o.sources IS DISTINCT FROM expected THEN RAISE EXCEPTION 'SUBSET_SOURCE_REVISION_CHANGED'; END IF;
    UPDATE video_queue.source_observation SET consumed_at=clock_timestamp() WHERE observation_id=p_observation_id;
    RETURN jsonb_build_object('status','SOURCE_AND_LEASE_VERIFIED','job_id',j.job_id,'lease_token',j.lease_token,'attempt_count',j.attempt_count,'observation_id',p_observation_id) || video_queue.subset_disabled();
END $$;
CREATE FUNCTION video_queue.heartbeat_subset_job(p_job_id uuid,p_lease_token uuid,p_worker_key text,p_extend_seconds integer DEFAULT 900) RETURNS timestamptz
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
BEGIN
    PERFORM video_queue.subset_lock_lease(p_job_id,p_lease_token,p_worker_key);
    IF p_extend_seconds IS NULL THEN RAISE EXCEPTION 'SUBSET_ARGUMENT_INVALID'; END IF;
    RETURN video_queue.heartbeat_job(p_job_id,p_lease_token,p_worker_key,p_extend_seconds);
END $$;
CREATE FUNCTION video_queue.retry_subset_job(p_job_id uuid,p_lease_token uuid,p_worker_key text,p_error_code text,p_base_delay_seconds integer DEFAULT 60)
RETURNS TABLE(job_status text,batch_status text,retry_after timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
BEGIN
    PERFORM video_queue.subset_lock_lease(p_job_id,p_lease_token,p_worker_key);
    IF p_base_delay_seconds IS NULL THEN RAISE EXCEPTION 'SUBSET_ARGUMENT_INVALID'; END IF;
    RETURN QUERY SELECT * FROM video_queue.retry_job(p_job_id,p_lease_token,p_worker_key,p_error_code,3,p_base_delay_seconds);
END $$;
CREATE FUNCTION video_queue.finish_subset_job(p_job_id uuid,p_lease_token uuid,p_worker_key text,p_outcome text,p_output jsonb,p_observation_id uuid,p_error_code text DEFAULT NULL)
RETURNS TABLE(job_status text,batch_status text,released_jobs integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
BEGIN
    -- Require a separately consumed pre-work observation for this exact lease.
    -- The post-work observation below must be a distinct, fresh collector row.
    -- This records both boundaries; it cannot prove the timing of external I/O
    -- performed by a malicious holder of the trusted worker credentials.
    PERFORM video_queue.subset_lock_lease(p_job_id,p_lease_token,p_worker_key);
    IF NOT EXISTS (SELECT FROM video_queue.source_observation o JOIN video_queue.job j ON j.job_id=o.job_id
        WHERE o.job_id=p_job_id AND o.lease_token=p_lease_token AND o.worker_key=p_worker_key
          AND o.attempt_count=j.attempt_count AND o.phase='ATTEMPT' AND o.consumed_at IS NOT NULL
          AND o.observation_id IS DISTINCT FROM p_observation_id) THEN RAISE EXCEPTION 'SUBSET_PREWORK_OBSERVATION_REQUIRED'; END IF;
    PERFORM video_queue.authorize_subset_attempt(p_job_id,p_lease_token,p_worker_key,p_observation_id);
    RETURN QUERY SELECT * FROM video_queue.finish_job(p_job_id,p_lease_token,p_worker_key,p_outcome,p_output,p_error_code);
END $$;

-- Existing 0056 claim API/lease algorithm, with bounded managed eligibility.
CREATE OR REPLACE FUNCTION video_queue.claim_job(
    p_worker_key text,
    p_lease_seconds integer,
    p_processing_profile text,
    p_algorithm_revision text
)
RETURNS TABLE(
    job_id uuid,
    batch_id uuid,
    lease_token uuid,
    sequence integer,
    source_folder_id text,
    output_folder_id text,
    work_folder_id text,
    processing_profile text,
    algorithm_revision text,
    source_file_id text,
    source_name text,
    source_mime_type text,
    source_size_bytes bigint,
    source_checksum text,
    stable_job_key text,
    is_canary boolean,
    attempt_count integer
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, video_queue
AS $$
DECLARE
    v_job video_queue.job%ROWTYPE;
    v_previous_status text;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_worker_v2');
    IF p_lease_seconds IS NULL THEN RAISE EXCEPTION 'SUBSET_ARGUMENT_INVALID'; END IF;
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
         WHERE video_queue.subset_worker_eligible(j.batch_id) AND ((j.status='LEASED' AND j.lease_expires_at <= clock_timestamp()) OR j.status='QUEUED')
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
     WHERE video_queue.subset_worker_eligible(j.batch_id) AND j.attempt_count < 3 AND (
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
    -- Claim selection and runtime revocation serialize on the approval row.
    -- Keep the existing job -> batch lock order; approval revocation takes no
    -- job locks. Recheck after any approval-lock wait.
    PERFORM 1 FROM video_queue.subset_approval a
    JOIN video_queue.subset_admission d USING(approval_id)
    WHERE d.batch_id=v_job.batch_id FOR SHARE OF a;
    IF NOT video_queue.subset_worker_eligible(v_job.batch_id) THEN
        RAISE EXCEPTION 'SUBSET_RUNTIME_HOLD';
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
$$;

-- New capability roles receive function EXECUTE only, never table DML or
-- membership in a definer/legacy owner role. Login provisioning is a later,
-- separately reviewed operation. Session identities remain independently
-- attributable even through a SECURITY DEFINER call chain.
ALTER TABLE video_queue.subset_approval OWNER TO video_subset_owner;
ALTER TABLE video_queue.source_observation OWNER TO video_subset_owner;
ALTER TABLE video_queue.subset_admission OWNER TO video_subset_owner;
ALTER TABLE video_queue.source_reservation OWNER TO video_subset_owner;
DO $privileges$
DECLARE f record; acl record; t record;
BEGIN
    -- Cut off every extant raw table writer, including per-column grants.
    -- Definer ownership is a trusted migration boundary, never a runtime role.
    FOR t IN SELECT c.oid,c.oid::regclass AS relation,c.relowner FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='video_queue'
        AND c.relname IN ('batch','job','job_event') LOOP
        EXECUTE format('REVOKE ALL ON TABLE %s FROM PUBLIC',t.relation);
        FOR acl IN SELECT DISTINCT x.grantee,r.rolname FROM pg_class c,
          LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) x
          JOIN pg_roles r ON r.oid=x.grantee WHERE c.oid=t.oid AND x.grantee<>t.relowner AND r.rolname<>'video_subset_owner' LOOP
            EXECUTE format('REVOKE ALL ON TABLE %s FROM %I',t.relation,acl.rolname);
        END LOOP;
        FOR acl IN SELECT DISTINCT a.attname,x.grantee,r.rolname FROM pg_attribute a,
          LATERAL aclexplode(a.attacl) x LEFT JOIN pg_roles r ON r.oid=x.grantee
          WHERE a.attrelid=t.oid AND x.grantee<>t.relowner AND (r.rolname IS NULL OR r.rolname<>'video_subset_owner') LOOP
            EXECUTE format('REVOKE ALL (%I) ON TABLE %s FROM %s',acl.attname,t.relation,
                CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE quote_ident(acl.rolname) END);
        END LOOP;
    END LOOP;
    -- Revoke every extant grantee, not only the three known legacy roles.
    FOR f IN SELECT p.oid, p.oid::regprocedure AS signature, p.proowner
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='video_queue' AND p.proname IN ('enqueue_drive_batch','claim_job','heartbeat_job','retry_job','finish_job') LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
        FOR acl IN SELECT DISTINCT x.grantee, r.rolname FROM pg_proc p,
            LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x
            JOIN pg_roles r ON r.oid=x.grantee WHERE p.oid=f.oid AND x.grantee<>f.proowner LOOP
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',f.signature,acl.rolname);
        END LOOP;
    END LOOP;
    FOR f IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='video_queue' AND (p.proname LIKE 'subset_%' OR p.proname IN
       ('approve_subset','set_subset_runtime','revoke_subset_approval','observe_subset','recover_subset_admission','admit_approved_subset','authorize_subset_attempt','heartbeat_subset_job','retry_subset_job','finish_subset_job','claim_job')) LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO video_subset_owner',f.signature);
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
    END LOOP;
END $privileges$;
REVOKE ALL ON video_queue.subset_approval,video_queue.source_observation,video_queue.subset_admission,video_queue.source_reservation
    FROM PUBLIC,bridge_school_app,bridge_school_worker,bridge_school_reader,
         video_subset_approver,video_subset_collector,video_subset_admitter,video_subset_worker_v2;
GRANT EXECUTE ON FUNCTION video_queue.enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb),
    video_queue.heartbeat_job(uuid,uuid,text,integer),video_queue.retry_job(uuid,uuid,text,text,integer,integer),
    video_queue.finish_job(uuid,uuid,text,text,jsonb,text) TO video_subset_owner;
GRANT EXECUTE ON FUNCTION video_queue.approve_subset(uuid,text,jsonb,timestamptz),
    video_queue.set_subset_runtime(uuid,boolean),video_queue.revoke_subset_approval(uuid) TO video_subset_approver;
GRANT EXECUTE ON FUNCTION video_queue.observe_subset(uuid,uuid,text,jsonb,timestamptz,uuid,uuid,uuid,integer,text) TO video_subset_collector;
GRANT EXECUTE ON FUNCTION video_queue.admit_approved_subset(uuid,text,text,text,uuid),
    video_queue.recover_subset_admission(text,text,text) TO video_subset_admitter;
GRANT EXECUTE ON FUNCTION video_queue.claim_job(text,integer,text,text),
    video_queue.authorize_subset_attempt(uuid,uuid,text,uuid),video_queue.heartbeat_subset_job(uuid,uuid,text,integer),
    video_queue.retry_subset_job(uuid,uuid,text,text,integer),video_queue.finish_subset_job(uuid,uuid,text,text,jsonb,uuid,text) TO video_subset_worker_v2;
COMMENT ON TABLE video_queue.subset_approval IS 'Trusted exact fixed-subset approval; integer-only canonical numeric domain; runtime HOLD by default';
COMMENT ON TABLE video_queue.source_reservation IS 'Permanent global Drive source exclusion, including all historical jobs/statuses';
COMMENT ON FUNCTION video_queue.admit_approved_subset(uuid,text,text,text,uuid) IS 'Tentative immutable binding; caller must commit exactly once before reporting admission';
INSERT INTO schema_migration(migration_key) VALUES ('0060_fixed_subset_admission');
COMMIT;
