\set ON_ERROR_STOP on
-- UNAPPLIED CANDIDATE. Requires a separately authorized, drained maintenance
-- window. No role memberships or runtime approvals are enabled by this file.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
DO $pre$
BEGIN
    IF NOT EXISTS (SELECT FROM schema_migration AS q_migration WHERE q_migration.migration_key = '0059_universal_video_terminal_size_guard') THEN
        RAISE EXCEPTION 'SUBSET_REQUIRES_0059';
    END IF;
    IF EXISTS (SELECT FROM pg_prepared_xacts AS q_prepared WHERE q_prepared.database = current_database()) THEN
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
-- Namespaces: p_ inputs, v_ PL/pgSQL locals, q_ SQL relation aliases.
-- Keep public RETURNS TABLE names unchanged; qualify source/output columns.
CREATE FUNCTION video_queue.subset_json(p_value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, video_queue AS $$
DECLARE v_kind text := jsonb_typeof(p_value); v_result text;
BEGIN
    IF v_kind = 'object' THEN
        SELECT '{' || coalesce(string_agg(to_jsonb(q_entry.key)::text || ':' || video_queue.subset_json(q_entry.value), ',' ORDER BY q_entry.key COLLATE "C"), '') || '}'
          INTO v_result FROM jsonb_each(p_value) AS q_entry(key,value);
    ELSIF v_kind = 'array' THEN
        SELECT '[' || coalesce(string_agg(video_queue.subset_json(q_item.value), ',' ORDER BY q_item.ordinality), '') || ']'
          INTO v_result FROM jsonb_array_elements(p_value) WITH ORDINALITY AS q_item(value,ordinality);
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
        (SELECT array_agg(q_actual.key ORDER BY q_actual.key COLLATE "C") FROM jsonb_object_keys(p_value) AS q_actual(key))
        = (SELECT array_agg(q_expected.key ORDER BY q_expected.key COLLATE "C") FROM unnest(p_keys) AS q_expected(key))
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
SELECT 'google_drive:' || q_job.source_file_id, array_agg(q_job.job_id ORDER BY q_job.job_id)
FROM video_queue.job AS q_job GROUP BY q_job.source_file_id ORDER BY q_job.source_file_id COLLATE "C";

CREATE FUNCTION video_queue.subset_validate_contract(p_bytes text) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, video_queue AS $$
DECLARE v_contract jsonb; v_manifest jsonb; v_file jsonb; v_queue_file jsonb; v_revision jsonb; v_source_map jsonb; v_files jsonb := '[]'; v_checksum text;
    v_count integer; v_total bigint := 0; v_last text; v_key text; v_length integer;
BEGIN
    IF p_bytes IS NULL OR octet_length(p_bytes) NOT BETWEEN 1 AND 4194304 THEN RAISE EXCEPTION 'SUBSET_CONTRACT_INVALID'; END IF;
    v_contract := p_bytes::jsonb;
    -- Exact canonical bytes also reject duplicate keys, alternate spellings,
    -- whitespace, negative zero, escaped Unicode and unsupported float numbers.
    IF video_queue.subset_json(v_contract) IS DISTINCT FROM p_bytes THEN RAISE EXCEPTION 'SUBSET_CANONICAL_BYTES_MISMATCH'; END IF;
    FOREACH v_key IN ARRAY ARRAY['schema','plan_sha256','subset_sha256','processing_profile','algorithm_revision','canary_source_file_id','queue_manifest_sha256','contract_sha256'] LOOP
        IF jsonb_typeof(v_contract->v_key) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'SUBSET_CONTRACT_INVALID'; END IF;
    END LOOP;
    IF NOT video_queue.subset_shape(v_contract, string_to_array('schema plan_sha256 subset_sha256 subset_sequence processing_profile algorithm_revision canary_source_file_id files enqueue_manifest queue_manifest_sha256 execution_allowed release_allowed database_persistence_allowed canonical_promotion_allowed contract_sha256', ' '))
       OR v_contract->>'schema' IS DISTINCT FROM 'universal-video-subset-contract-v1'
       OR NOT v_contract @> video_queue.subset_disabled()
       OR v_contract->>'plan_sha256' !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(v_contract->'subset_sequence') IS DISTINCT FROM 'number'
       OR (v_contract->>'subset_sequence')::numeric < 1
       OR video_queue.subset_sha(v_contract - 'contract_sha256') IS DISTINCT FROM v_contract->>'contract_sha256'
       OR video_queue.subset_sha(v_contract->'files') IS DISTINCT FROM v_contract->>'subset_sha256'
       OR video_queue.subset_sha(v_contract->'enqueue_manifest') IS DISTINCT FROM v_contract->>'queue_manifest_sha256' THEN
        RAISE EXCEPTION 'SUBSET_CONTRACT_INVALID';
    END IF;
    v_manifest := v_contract->'enqueue_manifest';
    FOREACH v_key IN ARRAY ARRAY['request_key','source_folder_id','output_folder_id','work_folder_id','processing_profile','algorithm_revision','canary_source_file_id','inventory_sha256','result_mode'] LOOP
        IF jsonb_typeof(v_manifest->v_key) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'SUBSET_MANIFEST_INVALID'; END IF;
    END LOOP;
    IF NOT video_queue.subset_shape(v_manifest, string_to_array('request_key source_folder_id output_folder_id work_folder_id processing_profile algorithm_revision canary_source_file_id inventory_sha256 expected_count total_size_bytes files result_mode canonical_promotion_allowed database_persistence_allowed', ' '))
       OR v_manifest->>'source_folder_id' !~ '^[A-Za-z0-9_-]{10,200}$'
       OR v_manifest->>'output_folder_id' !~ '^[A-Za-z0-9_-]{10,200}$'
       OR v_manifest->>'work_folder_id' !~ '^[A-Za-z0-9_-]{10,200}$'
       OR v_manifest->>'source_folder_id' IN (v_manifest->>'output_folder_id',v_manifest->>'work_folder_id')
       OR v_manifest->>'processing_profile' !~ '^[a-z][a-z0-9_.-]{0,79}$'
       OR v_manifest->>'algorithm_revision' !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'
       OR v_manifest->>'canary_source_file_id' !~ '^[A-Za-z0-9_-]{10,200}$'
       OR v_manifest->>'result_mode' IS DISTINCT FROM 'SHADOW_REVIEW_ONLY'
       OR v_manifest->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR v_manifest->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
       OR jsonb_typeof(v_contract->'files') IS DISTINCT FROM 'array'
       OR jsonb_typeof(v_manifest->'files') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'SUBSET_MANIFEST_INVALID'; END IF;
    v_count := jsonb_array_length(v_contract->'files');
    IF v_count NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'SUBSET_COUNT_INVALID'; END IF;
    FOR v_file IN SELECT q_item.value FROM jsonb_array_elements(v_contract->'files') AS q_item(value) LOOP
        FOREACH v_key IN ARRAY ARRAY['source_identity','file_id','parent','version','modifiedTime','mime_type','name','source_fingerprint'] LOOP
            IF jsonb_typeof(v_file->v_key) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'SUBSET_SOURCE_INVALID'; END IF;
        END LOOP;
        IF jsonb_typeof(v_file->'checksum') NOT IN ('string','null') THEN RAISE EXCEPTION 'SUBSET_CHECKSUM_INVALID'; END IF;
        IF NOT video_queue.subset_shape(v_file, string_to_array('source_identity file_id parent version modifiedTime mime_type size_bytes checksum checksums name trashed source_fingerprint duration_ms estimated_cost_usd', ' '))
           OR v_file->>'file_id' !~ '^[A-Za-z0-9_-]{10,200}$'
           OR jsonb_typeof(v_file->'file_id') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_file->'parent') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_file->'modifiedTime') IS DISTINCT FROM 'string'
           OR jsonb_typeof(v_file->'mime_type') IS DISTINCT FROM 'string'
           OR v_file->>'file_id' IN (v_manifest->>'source_folder_id',v_manifest->>'output_folder_id',v_manifest->>'work_folder_id')
           OR v_file->>'source_identity' IS DISTINCT FROM 'google_drive:' || (v_file->>'file_id')
           OR v_file->>'parent' IS DISTINCT FROM v_manifest->>'source_folder_id'
           OR v_file->'trashed' IS DISTINCT FROM 'false'::jsonb
           OR jsonb_typeof(v_file->'name') IS DISTINCT FROM 'string'
           OR length(btrim(v_file->>'name')) NOT BETWEEN 1 AND 500
           OR v_file->>'name' ~ '^AI_PART_'
           OR v_file->>'mime_type' !~ '^video/[A-Za-z0-9.+_-]{1,120}$'
           OR jsonb_typeof(v_file->'size_bytes') IS DISTINCT FROM 'number'
           OR (v_file->>'size_bytes')::numeric NOT BETWEEN 1048576 AND 68719476736
           OR jsonb_typeof(v_file->'version') IS DISTINCT FROM 'string'
           OR v_file->>'version' !~ '^[1-9][0-9]{0,19}$'
           OR (v_file->>'version')::numeric > 18446744073709551615
           OR v_file->>'modifiedTime' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?(Z|[+-]([01][0-9]|2[0-3]):[0-5][0-9])$'
           OR jsonb_typeof(v_file->'checksums') IS DISTINCT FROM 'object'
           OR (v_file->'duration_ms' <> 'null'::jsonb AND (jsonb_typeof(v_file->'duration_ms') <> 'number' OR (v_file->>'duration_ms')::numeric NOT BETWEEN 1 AND 9223372036854775807))
           OR (v_file->'estimated_cost_usd' <> 'null'::jsonb AND (jsonb_typeof(v_file->'estimated_cost_usd') <> 'number' OR (v_file->>'estimated_cost_usd')::numeric NOT BETWEEN 0 AND 1e308::numeric))
           OR (v_last IS NOT NULL AND (v_file->>'file_id') COLLATE "C" <= v_last COLLATE "C") THEN RAISE EXCEPTION 'SUBSET_SOURCE_INVALID'; END IF;
        PERFORM (v_file->>'modifiedTime')::timestamptz;
        v_checksum := NULL;
        FOR v_key, v_length IN SELECT q_checksum.checksum_key, q_checksum.checksum_length FROM (VALUES ('sha256Checksum',64),('sha1Checksum',40),('md5Checksum',32)) AS q_checksum(checksum_key,checksum_length) LOOP
            IF v_file->'checksums' ? v_key THEN
                IF jsonb_typeof(v_file->'checksums'->v_key) <> 'string' OR v_file->'checksums'->>v_key !~ ('^[0-9a-f]{' || v_length || '}$') THEN RAISE EXCEPTION 'SUBSET_CHECKSUM_INVALID'; END IF;
                v_checksum := coalesce(v_checksum, replace(v_key,'Checksum','') || ':' || (v_file->'checksums'->>v_key));
            END IF;
        END LOOP;
        IF ((v_file->'checksums') - ARRAY['sha256Checksum','sha1Checksum','md5Checksum']) <> '{}'::jsonb OR v_file->>'checksum' IS DISTINCT FROM v_checksum THEN RAISE EXCEPTION 'SUBSET_CHECKSUM_INVALID'; END IF;
        v_revision := v_file - ARRAY['name','trashed','source_fingerprint','duration_ms','estimated_cost_usd'];
        IF video_queue.subset_sha(v_revision) IS DISTINCT FROM v_file->>'source_fingerprint' THEN RAISE EXCEPTION 'SUBSET_FINGERPRINT_INVALID'; END IF;
        v_last := v_file->>'file_id';
        v_total := v_total + (v_file->>'size_bytes')::bigint;
    END LOOP;
    -- Existing Python queue builder owns its natural Unicode name sort. The
    -- trusted approver supplies that exact manifest; SQL verifies its complete
    -- ordered bijection and never substitutes a locale-dependent SQL sort.
    SELECT jsonb_object_agg(q_item.value->>'file_id',q_item.value) INTO v_source_map FROM jsonb_array_elements(v_contract->'files') AS q_item(value);
    FOR v_queue_file IN SELECT q_item.value FROM jsonb_array_elements(v_manifest->'files') AS q_item(value) LOOP
        IF NOT video_queue.subset_shape(v_queue_file, ARRAY['sequence','file_id','name','mime_type','size_bytes','checksum']) THEN RAISE EXCEPTION 'SUBSET_QUEUE_PROJECTION_INVALID'; END IF;
        v_file := v_source_map->(v_queue_file->>'file_id');
        IF v_file IS NULL OR v_queue_file - 'sequence' IS DISTINCT FROM jsonb_build_object('file_id',v_file->'file_id','name',v_file->'name','mime_type',v_file->'mime_type','size_bytes',v_file->'size_bytes','checksum',v_file->'checksum')
           OR v_queue_file->'sequence' IS DISTINCT FROM to_jsonb(jsonb_array_length(v_files) + 1) THEN RAISE EXCEPTION 'SUBSET_QUEUE_PROJECTION_INVALID'; END IF;
        v_files := v_files || jsonb_build_array(v_queue_file);
    END LOOP;
    IF jsonb_array_length(v_files) <> v_count
       OR (SELECT count(DISTINCT q_item.value->>'file_id') FROM jsonb_array_elements(v_files) AS q_item(value)) <> v_count
       OR v_manifest->'expected_count' IS DISTINCT FROM to_jsonb(v_count)
       OR v_manifest->'total_size_bytes' IS DISTINCT FROM to_jsonb(v_total)
       OR v_manifest->>'inventory_sha256' IS DISTINCT FROM video_queue.subset_sha(v_files)
       OR NOT EXISTS (SELECT FROM jsonb_array_elements(v_files) AS q_item(value) WHERE q_item.value->>'file_id' = v_manifest->>'canary_source_file_id')
       OR v_manifest->'processing_profile' IS DISTINCT FROM v_contract->'processing_profile'
       OR v_manifest->'algorithm_revision' IS DISTINCT FROM v_contract->'algorithm_revision'
       OR v_manifest->'canary_source_file_id' IS DISTINCT FROM v_contract->'canary_source_file_id' THEN RAISE EXCEPTION 'SUBSET_MANIFEST_INVALID'; END IF;
    v_revision := jsonb_build_object('plan_sha256',v_contract->'plan_sha256','subset_sha256',v_contract->'subset_sha256','subset_sequence',v_contract->'subset_sequence','processing_profile',v_contract->'processing_profile','algorithm_revision',v_contract->'algorithm_revision','canary_source_file_id',v_contract->'canary_source_file_id');
    IF v_manifest->>'request_key' IS DISTINCT FROM 'subset-v1:' || video_queue.subset_sha(v_revision) THEN RAISE EXCEPTION 'SUBSET_REQUEST_INVALID'; END IF;
    RETURN v_contract;
END $$;

CREATE FUNCTION video_queue.approve_subset(p_approval_id uuid, p_contract_bytes text, p_provenance jsonb, p_expires_at timestamptz) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE v_contract jsonb;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_approver');
    v_contract := video_queue.subset_validate_contract(p_contract_bytes);
    IF p_approval_id IS NULL OR p_expires_at IS NULL OR p_expires_at <= clock_timestamp()
       OR NOT video_queue.subset_shape(p_provenance, ARRAY['approved_by','approval_reference','plan_sha256'])
       OR jsonb_typeof(p_provenance->'approved_by') IS DISTINCT FROM 'string'
       OR jsonb_typeof(p_provenance->'approval_reference') IS DISTINCT FROM 'string'
       OR length(btrim(p_provenance->>'approved_by')) NOT BETWEEN 1 AND 500
       OR length(btrim(p_provenance->>'approval_reference')) NOT BETWEEN 1 AND 500
       OR p_provenance->'plan_sha256' IS DISTINCT FROM v_contract->'plan_sha256' THEN RAISE EXCEPTION 'SUBSET_APPROVAL_INVALID'; END IF;
    INSERT INTO video_queue.subset_approval(approval_id,contract_bytes,contract,contract_sha256,queue_manifest_sha256,plan_sha256,subset_sha256,request_key,provenance,approved_by,expires_at)
    VALUES(p_approval_id,p_contract_bytes,v_contract,v_contract->>'contract_sha256',v_contract->>'queue_manifest_sha256',v_contract->>'plan_sha256',v_contract->>'subset_sha256',v_contract#>>'{enqueue_manifest,request_key}',p_provenance,session_user,p_expires_at);
    RETURN p_approval_id;
END $$;
CREATE FUNCTION video_queue.set_subset_runtime(p_approval_id uuid, p_enabled boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE v_approval video_queue.subset_approval%ROWTYPE;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_approver');
    SELECT q_approval.* INTO v_approval FROM video_queue.subset_approval AS q_approval WHERE q_approval.approval_id=p_approval_id FOR UPDATE;
    IF NOT FOUND OR p_enabled IS NULL OR (p_enabled AND (v_approval.revoked_at IS NOT NULL OR v_approval.expires_at <= clock_timestamp())) THEN RAISE EXCEPTION 'SUBSET_APPROVAL_NOT_ACTIVE'; END IF;
    UPDATE video_queue.subset_approval AS q_approval SET runtime_enabled=p_enabled WHERE q_approval.approval_id=p_approval_id;
END $$;
CREATE FUNCTION video_queue.revoke_subset_approval(p_approval_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
BEGIN
    PERFORM video_queue.subset_capability('video_subset_approver');
    UPDATE video_queue.subset_approval AS q_approval SET revoked_at=coalesce(q_approval.revoked_at,clock_timestamp()),runtime_enabled=false WHERE q_approval.approval_id=p_approval_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'SUBSET_APPROVAL_NOT_FOUND'; END IF;
END $$;

CREATE FUNCTION video_queue.subset_observed_sources(p_contract jsonb, p_sources jsonb, p_only_file text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, video_queue AS $$
DECLARE v_raw jsonb; v_file jsonb; v_revision jsonb; v_source_map jsonb; v_checksums jsonb; v_checksum text; v_checksum_key text; v_checksum_length integer; v_checksum_value text; v_result jsonb := '[]'; v_ids text[] := '{}';
BEGIN
    IF jsonb_typeof(p_sources) IS DISTINCT FROM 'array' OR jsonb_array_length(p_sources) <> (CASE WHEN p_only_file IS NULL THEN jsonb_array_length(p_contract->'files') ELSE 1 END) THEN RAISE EXCEPTION 'SUBSET_SOURCE_SET_MISMATCH'; END IF;
    SELECT jsonb_object_agg(q_item.value->>'file_id',q_item.value) INTO v_source_map FROM jsonb_array_elements(p_contract->'files') AS q_item(value);
    FOR v_raw IN SELECT q_item.value FROM jsonb_array_elements(p_sources) AS q_item(value) LOOP
        v_file := v_source_map->(v_raw->>'id');
        IF v_file IS NULL OR v_raw->>'id'=ANY(v_ids) OR (p_only_file IS NOT NULL AND v_raw->>'id' IS DISTINCT FROM p_only_file)
           OR v_raw->'parents' IS DISTINCT FROM jsonb_build_array(v_file->'parent')
           OR v_raw->'trashed' IS DISTINCT FROM 'false'::jsonb
           OR v_raw->'name' IS DISTINCT FROM v_file->'name'
           OR v_raw->'version' IS DISTINCT FROM v_file->'version'
           OR v_raw->'modifiedTime' IS DISTINCT FROM v_file->'modifiedTime'
           OR v_raw->'mimeType' IS DISTINCT FROM v_file->'mime_type'
           OR jsonb_typeof(v_raw->'size') NOT IN ('string','number')
           OR v_raw->>'size' !~ '^[0-9]{1,20}$'
           OR (v_raw->>'size')::numeric IS DISTINCT FROM (v_file->>'size_bytes')::numeric THEN RAISE EXCEPTION 'SUBSET_SOURCE_REVISION_CHANGED'; END IF;
        v_checksums := '{}'; v_checksum := NULL;
        FOR v_checksum_key,v_checksum_length IN SELECT q_checksum.checksum_key, q_checksum.checksum_length FROM (VALUES ('sha256Checksum',64),('sha1Checksum',40),('md5Checksum',32)) AS q_checksum(checksum_key,checksum_length) LOOP
            IF v_raw->v_checksum_key IS NOT NULL AND v_raw->v_checksum_key <> 'null'::jsonb THEN
                v_checksum_value := v_raw->>v_checksum_key;
                IF jsonb_typeof(v_raw->v_checksum_key) <> 'string' OR v_checksum_value !~ ('^[0-9a-fA-F]{' || v_checksum_length || '}$') THEN RAISE EXCEPTION 'SUBSET_SOURCE_REVISION_CHANGED'; END IF;
                v_checksums := v_checksums || jsonb_build_object(v_checksum_key,lower(v_checksum_value));
                v_checksum := coalesce(v_checksum,replace(v_checksum_key,'Checksum','') || ':' || lower(v_checksum_value));
            END IF;
        END LOOP;
        v_revision := jsonb_build_object('source_identity',v_file->'source_identity','file_id',v_raw->'id','parent',v_raw->'parents'->0,'version',v_raw->'version','modifiedTime',v_raw->'modifiedTime','mime_type',v_raw->'mimeType','size_bytes',(v_raw->>'size')::bigint,'checksum',v_checksum,'checksums',v_checksums);
        IF video_queue.subset_sha(v_revision) IS DISTINCT FROM v_file->>'source_fingerprint' THEN RAISE EXCEPTION 'SUBSET_SOURCE_REVISION_CHANGED'; END IF;
        v_result := v_result || jsonb_build_array(v_revision || jsonb_build_object('name',v_raw->'name','source_fingerprint',v_file->'source_fingerprint','trashed',false));
        v_ids := array_append(v_ids,v_raw->>'id');
    END LOOP;
    SELECT jsonb_agg(q_item.value ORDER BY q_item.value->>'file_id' COLLATE "C") INTO v_result FROM jsonb_array_elements(v_result) AS q_item(value);
    RETURN v_result;
END $$;
CREATE FUNCTION video_queue.observe_subset(p_observation_id uuid,p_approval_id uuid,p_phase text,p_sources jsonb,p_observed_at timestamptz,p_nonce uuid DEFAULT NULL,p_job_id uuid DEFAULT NULL,p_lease_token uuid DEFAULT NULL,p_attempt_count integer DEFAULT NULL,p_worker_key text DEFAULT NULL) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE v_approval video_queue.subset_approval%ROWTYPE; v_job video_queue.job%ROWTYPE; v_normalized jsonb;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_collector');
    SELECT q_approval.* INTO v_approval FROM video_queue.subset_approval AS q_approval WHERE q_approval.approval_id=p_approval_id;
    IF NOT FOUND OR v_approval.revoked_at IS NOT NULL OR v_approval.expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_APPROVAL_NOT_ACTIVE'; END IF;
    IF p_observation_id IS NULL OR p_observed_at IS NULL OR p_observed_at > clock_timestamp() OR p_observed_at + interval '30 seconds' <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_EXPIRED'; END IF;
    IF p_phase='ADMISSION' THEN
        IF p_nonce IS NULL OR p_job_id IS NOT NULL OR p_lease_token IS NOT NULL OR p_attempt_count IS NOT NULL OR p_worker_key IS NOT NULL THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_SCOPE'; END IF;
    ELSIF p_phase='ATTEMPT' THEN
        SELECT q_job.* INTO v_job FROM video_queue.job AS q_job WHERE q_job.job_id=p_job_id;
        IF NOT FOUND OR p_nonce IS NOT NULL OR p_lease_token IS NULL OR p_worker_key IS NULL OR p_attempt_count IS NULL
           OR v_job.status <> 'LEASED' OR v_job.lease_token IS DISTINCT FROM p_lease_token OR v_job.lease_owner IS DISTINCT FROM p_worker_key
           OR v_job.attempt_count IS DISTINCT FROM p_attempt_count OR v_job.lease_expires_at <= clock_timestamp()
           OR NOT EXISTS (SELECT FROM video_queue.subset_admission AS q_admission WHERE q_admission.approval_id=p_approval_id AND q_admission.batch_id=v_job.batch_id AND q_admission.state='FINALIZED') THEN RAISE EXCEPTION 'SUBSET_LEASE_LOST'; END IF;
    ELSE RAISE EXCEPTION 'SUBSET_OBSERVATION_SCOPE'; END IF;
    v_normalized := video_queue.subset_observed_sources(v_approval.contract,p_sources,v_job.source_file_id);
    INSERT INTO video_queue.source_observation(observation_id,approval_id,contract_sha256,request_key,phase,sources,observed_at,expires_at,collected_by,nonce,job_id,lease_token,attempt_count,worker_key)
    VALUES(p_observation_id,p_approval_id,v_approval.contract_sha256,v_approval.request_key,p_phase,v_normalized,p_observed_at,p_observed_at+interval '30 seconds',session_user,p_nonce,p_job_id,p_lease_token,p_attempt_count,p_worker_key);
    RETURN p_observation_id;
END $$;

CREATE FUNCTION video_queue.subset_build_binding(p_request_key text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE v_approval video_queue.subset_approval%ROWTYPE; v_admission video_queue.subset_admission%ROWTYPE; v_batch video_queue.batch%ROWTYPE; v_job video_queue.job%ROWTYPE; v_manifest jsonb; v_file jsonb; v_queue_file jsonb; v_source_map jsonb; v_jobs jsonb := '[]'; v_core jsonb; v_projection jsonb;
BEGIN
    SELECT q_admission.* INTO v_admission FROM video_queue.subset_admission AS q_admission WHERE q_admission.request_key=p_request_key;
    SELECT q_approval.* INTO v_approval FROM video_queue.subset_approval AS q_approval WHERE q_approval.approval_id=v_admission.approval_id;
    SELECT q_batch.* INTO v_batch FROM video_queue.batch AS q_batch WHERE q_batch.request_key=p_request_key;
    IF v_admission.request_key IS NULL OR v_approval.approval_id IS NULL OR v_batch.batch_id IS NULL THEN RAISE EXCEPTION 'SUBSET_BINDING_MISSING_HOLD'; END IF;
    IF video_queue.subset_validate_contract(v_approval.contract_bytes) IS DISTINCT FROM v_approval.contract
       OR v_approval.contract_sha256 IS DISTINCT FROM v_approval.contract->>'contract_sha256'
       OR v_approval.queue_manifest_sha256 IS DISTINCT FROM v_approval.contract->>'queue_manifest_sha256'
       OR v_approval.plan_sha256 IS DISTINCT FROM v_approval.contract->>'plan_sha256'
       OR v_approval.subset_sha256 IS DISTINCT FROM v_approval.contract->>'subset_sha256'
       OR v_approval.request_key IS DISTINCT FROM v_approval.contract#>>'{enqueue_manifest,request_key}' THEN RAISE EXCEPTION 'SUBSET_APPROVAL_CONTENT_HOLD'; END IF;
    v_manifest := v_approval.contract->'enqueue_manifest';
    v_projection := to_jsonb(v_batch) - ARRAY['batch_id','source_kind','status','created_at','updated_at','completed_at'];
    IF v_batch.source_kind <> 'google_drive_folder' OR v_projection IS DISTINCT FROM v_manifest - 'files'
       OR v_admission.contract_sha256 IS DISTINCT FROM v_approval.contract_sha256 OR v_admission.queue_manifest_sha256 IS DISTINCT FROM v_approval.queue_manifest_sha256
       OR v_admission.plan_sha256 IS DISTINCT FROM v_approval.plan_sha256 OR v_admission.subset_sha256 IS DISTINCT FROM v_approval.subset_sha256
       OR v_admission.request_key IS DISTINCT FROM v_approval.request_key
       OR (SELECT count(*) FROM video_queue.job AS q_job WHERE q_job.batch_id=v_batch.batch_id) <> v_batch.expected_count THEN RAISE EXCEPTION 'SUBSET_BINDING_MISMATCH_HOLD'; END IF;
    SELECT jsonb_object_agg(q_item.value->>'file_id',q_item.value) INTO v_source_map FROM jsonb_array_elements(v_approval.contract->'files') AS q_item(value);
    FOR v_queue_file IN SELECT q_item.value FROM jsonb_array_elements(v_manifest->'files') AS q_item(value) LOOP
        SELECT q_job.* INTO v_job FROM video_queue.job AS q_job WHERE q_job.batch_id=v_batch.batch_id AND q_job.source_file_id=v_queue_file->>'file_id';
        v_file := v_source_map->(v_queue_file->>'file_id');
        IF v_job.job_id IS NULL OR jsonb_build_object('sequence',v_job.sequence,'file_id',v_job.source_file_id,'name',v_job.source_name,'mime_type',v_job.source_mime_type,'size_bytes',v_job.source_size_bytes,'checksum',v_job.source_checksum) IS DISTINCT FROM v_queue_file
           OR v_job.stable_job_key IS DISTINCT FROM substr(encode(public.digest(convert_to('bridge-video|drive|' || v_job.source_file_id,'UTF8'),'sha256'),'hex'),1,32)
           OR v_job.is_canary IS DISTINCT FROM (v_job.source_file_id=v_batch.canary_source_file_id) THEN RAISE EXCEPTION 'SUBSET_BINDING_MISMATCH_HOLD'; END IF;
        v_jobs := v_jobs || jsonb_build_array(jsonb_build_object('file_id',v_job.source_file_id,'job_id',v_job.job_id,'sequence',v_job.sequence,'source_identity',v_file->'source_identity','source_fingerprint',v_file->'source_fingerprint'));
    END LOOP;
    v_core := jsonb_build_object('schema','universal-video-admission-binding-v1','approval_id',v_approval.approval_id,'request_key',p_request_key,'batch_id',v_batch.batch_id,'plan_sha256',v_approval.plan_sha256,'subset_sha256',v_approval.subset_sha256,'contract_sha256',v_approval.contract_sha256,'queue_manifest_sha256',v_approval.queue_manifest_sha256,'jobs',v_jobs) || video_queue.subset_disabled();
    RETURN v_core || jsonb_build_object('binding_sha256',video_queue.subset_sha(v_core));
END $$;
CREATE FUNCTION video_queue.subset_assert_final(p_request_key text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE v_admission video_queue.subset_admission%ROWTYPE; v_rebuilt jsonb; v_bound_job jsonb; v_observation video_queue.source_observation%ROWTYPE;
BEGIN
    SELECT q_admission.* INTO v_admission FROM video_queue.subset_admission AS q_admission WHERE q_admission.request_key=p_request_key;
    IF NOT FOUND THEN RAISE EXCEPTION 'SUBSET_BINDING_MISSING_HOLD'; END IF;
    IF v_admission.state <> 'FINALIZED' THEN RAISE EXCEPTION 'SUBSET_UNFINALIZED_HOLD'; END IF;
    v_rebuilt := video_queue.subset_build_binding(p_request_key);
    IF v_admission.binding IS DISTINCT FROM v_rebuilt OR v_admission.binding_sha256 IS DISTINCT FROM v_rebuilt->>'binding_sha256' OR v_admission.batch_id::text IS DISTINCT FROM v_rebuilt->>'batch_id' THEN RAISE EXCEPTION 'SUBSET_BINDING_MISMATCH_HOLD'; END IF;
    IF (SELECT count(*) FROM video_queue.source_reservation AS q_reservation WHERE q_reservation.admission_request_key=p_request_key) <> jsonb_array_length(v_rebuilt->'jobs') THEN RAISE EXCEPTION 'SUBSET_RESERVATION_MISMATCH_HOLD'; END IF;
    FOR v_bound_job IN SELECT q_item.value FROM jsonb_array_elements(v_rebuilt->'jobs') AS q_item(value) LOOP
        IF NOT EXISTS (SELECT FROM video_queue.source_reservation AS q_reservation WHERE q_reservation.source_identity=v_bound_job->>'source_identity' AND q_reservation.admission_request_key=p_request_key AND q_reservation.source_fingerprint=v_bound_job->>'source_fingerprint' AND q_reservation.batch_id=v_admission.batch_id AND q_reservation.job_id=(v_bound_job->>'job_id')::uuid AND q_reservation.historical_job_ids IS NULL) THEN RAISE EXCEPTION 'SUBSET_RESERVATION_MISMATCH_HOLD'; END IF;
    END LOOP;
    SELECT q_observation.* INTO v_observation FROM video_queue.source_observation AS q_observation WHERE q_observation.observation_id=v_admission.observation_id;
    IF v_observation.approval_id IS DISTINCT FROM v_admission.approval_id OR v_observation.phase IS DISTINCT FROM 'ADMISSION' OR v_observation.contract_sha256 IS DISTINCT FROM v_admission.contract_sha256 OR v_observation.request_key IS DISTINCT FROM v_admission.request_key OR v_observation.consumed_at IS NULL
       OR v_observation.sources IS DISTINCT FROM (SELECT jsonb_agg(q_file.value-ARRAY['duration_ms','estimated_cost_usd'] ORDER BY q_file.value->>'file_id' COLLATE "C") FROM video_queue.subset_approval AS q_approval CROSS JOIN LATERAL jsonb_array_elements(q_approval.contract->'files') AS q_file(value) WHERE q_approval.approval_id=v_admission.approval_id) THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_BINDING_HOLD'; END IF;
END $$;
CREATE FUNCTION video_queue.recover_subset_admission(p_request_key text,p_contract_sha256 text,p_queue_manifest_sha256 text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE v_admission video_queue.subset_admission%ROWTYPE;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_admitter');
    SELECT q_admission.* INTO v_admission FROM video_queue.subset_admission AS q_admission WHERE q_admission.request_key=p_request_key;
    IF NOT FOUND THEN
        IF EXISTS (SELECT FROM video_queue.batch AS q_batch WHERE q_batch.request_key=p_request_key) THEN RAISE EXCEPTION 'SUBSET_LEGACY_BINDING_HOLD'; END IF;
        RETURN jsonb_build_object('status','NOT_FOUND') || video_queue.subset_disabled();
    END IF;
    IF v_admission.contract_sha256 IS DISTINCT FROM p_contract_sha256 OR v_admission.queue_manifest_sha256 IS DISTINCT FROM p_queue_manifest_sha256 THEN RAISE EXCEPTION 'SUBSET_DIGEST_MISMATCH_HOLD'; END IF;
    PERFORM video_queue.subset_assert_final(p_request_key);
    RETURN jsonb_build_object('status','RECOVERED_IMMUTABLE_BINDING','binding',v_admission.binding) || video_queue.subset_disabled();
END $$;
CREATE FUNCTION video_queue.admit_approved_subset(p_approval_id uuid,p_request_key text,p_contract_sha256 text,p_queue_manifest_sha256 text,p_observation_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE v_approval video_queue.subset_approval%ROWTYPE; v_observation video_queue.source_observation%ROWTYPE; v_admission video_queue.subset_admission%ROWTYPE; v_file jsonb; v_manifest jsonb; v_bound jsonb; v_batch_id uuid; v_owner text;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_admitter');
    IF p_request_key IS NULL OR p_request_key !~ '^subset-v1:[0-9a-f]{64}$' OR p_approval_id IS NULL OR p_contract_sha256 IS NULL OR p_queue_manifest_sha256 IS NULL THEN RAISE EXCEPTION 'SUBSET_ARGUMENT_INVALID'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_request_key,0));
    SELECT q_admission.* INTO v_admission FROM video_queue.subset_admission AS q_admission WHERE q_admission.request_key=p_request_key;
    IF FOUND THEN
        IF v_admission.approval_id IS DISTINCT FROM p_approval_id THEN RAISE EXCEPTION 'SUBSET_APPROVAL_MISMATCH_HOLD'; END IF;
        RETURN video_queue.recover_subset_admission(p_request_key,p_contract_sha256,p_queue_manifest_sha256);
    END IF;
    IF EXISTS (SELECT FROM video_queue.batch AS q_batch WHERE q_batch.request_key=p_request_key) THEN RAISE EXCEPTION 'SUBSET_LEGACY_BINDING_HOLD'; END IF;
    SELECT q_approval.* INTO v_approval FROM video_queue.subset_approval AS q_approval WHERE q_approval.approval_id=p_approval_id FOR UPDATE;
    IF NOT FOUND OR v_approval.revoked_at IS NOT NULL OR v_approval.expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_APPROVAL_NOT_ACTIVE'; END IF;
    IF v_approval.request_key IS DISTINCT FROM p_request_key OR v_approval.contract_sha256 IS DISTINCT FROM p_contract_sha256 OR v_approval.queue_manifest_sha256 IS DISTINCT FROM p_queue_manifest_sha256 OR video_queue.subset_validate_contract(v_approval.contract_bytes) IS DISTINCT FROM v_approval.contract THEN RAISE EXCEPTION 'SUBSET_DIGEST_MISMATCH_HOLD'; END IF;
    SELECT q_observation.* INTO v_observation FROM video_queue.source_observation AS q_observation WHERE q_observation.observation_id=p_observation_id FOR UPDATE;
    IF NOT FOUND OR v_observation.approval_id IS DISTINCT FROM p_approval_id OR v_observation.contract_sha256 IS DISTINCT FROM p_contract_sha256 OR v_observation.request_key IS DISTINCT FROM p_request_key OR v_observation.phase <> 'ADMISSION' OR v_observation.consumed_at IS NOT NULL THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_SCOPE'; END IF;
    INSERT INTO video_queue.subset_admission(request_key,approval_id,observation_id,contract_sha256,queue_manifest_sha256,plan_sha256,subset_sha256,state)
    VALUES(p_request_key,p_approval_id,p_observation_id,p_contract_sha256,p_queue_manifest_sha256,v_approval.plan_sha256,v_approval.subset_sha256,'DRAFT');
    FOR v_file IN SELECT q_item.value FROM jsonb_array_elements(v_approval.contract->'files') AS q_item(value) ORDER BY q_item.value->>'source_identity' COLLATE "C" LOOP
        INSERT INTO video_queue.source_reservation(source_identity,admission_request_key,source_fingerprint)
        VALUES(v_file->>'source_identity',p_request_key,v_file->>'source_fingerprint') ON CONFLICT (source_identity) DO NOTHING;
        -- Separate statement = fresh READ COMMITTED snapshot after PK wait.
        SELECT q_reservation.admission_request_key INTO v_owner FROM video_queue.source_reservation AS q_reservation WHERE q_reservation.source_identity=v_file->>'source_identity';
        IF v_owner IS DISTINCT FROM p_request_key THEN RAISE EXCEPTION 'SUBSET_SOURCE_RESERVED_HOLD'; END IF;
    END LOOP;
    IF v_approval.expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_APPROVAL_NOT_ACTIVE'; END IF;
    IF v_observation.observed_at > clock_timestamp() OR v_observation.expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_EXPIRED'; END IF;
    IF v_observation.sources IS DISTINCT FROM (SELECT jsonb_agg(q_item.value - ARRAY['duration_ms','estimated_cost_usd'] ORDER BY q_item.value->>'file_id' COLLATE "C") FROM jsonb_array_elements(v_approval.contract->'files') AS q_item(value)) THEN RAISE EXCEPTION 'SUBSET_SOURCE_REVISION_CHANGED'; END IF;
    v_manifest := v_approval.contract->'enqueue_manifest';
    SELECT q_enqueued.batch_id INTO v_batch_id FROM video_queue.enqueue_drive_batch(p_request_key,v_manifest->>'source_folder_id',v_manifest->>'output_folder_id',v_manifest->>'work_folder_id',v_manifest->>'processing_profile',v_manifest->>'algorithm_revision',v_manifest->>'canary_source_file_id',v_manifest->>'inventory_sha256',v_manifest->'files') AS q_enqueued;
    v_bound := video_queue.subset_build_binding(p_request_key);
    UPDATE video_queue.source_reservation AS q_reservation SET batch_id=v_batch_id,job_id=q_job.job_id FROM video_queue.job AS q_job WHERE q_reservation.admission_request_key=p_request_key AND q_job.batch_id=v_batch_id AND q_reservation.source_identity='google_drive:' || q_job.source_file_id;
    UPDATE video_queue.source_observation AS q_observation SET consumed_at=clock_timestamp() WHERE q_observation.observation_id=p_observation_id;
    UPDATE video_queue.subset_admission AS q_admission SET state='FINALIZED',batch_id=v_batch_id,binding=v_bound,binding_sha256=v_bound->>'binding_sha256',finalized_at=clock_timestamp() WHERE q_admission.request_key=p_request_key;
    PERFORM video_queue.subset_assert_final(p_request_key);
    RETURN jsonb_build_object('status','ADMITTED','binding',v_bound) || video_queue.subset_disabled();
END $$;

-- Persistent table fences are authority checks, not caller-set GUCs. They also
-- protect against accidentally re-granting a legacy SECURITY DEFINER function.
CREATE FUNCTION video_queue.subset_guard() RETURNS trigger
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
CREATE FUNCTION video_queue.subset_deferred_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE v_request_key text; v_admission video_queue.subset_admission%ROWTYPE; v_reservation video_queue.source_reservation%ROWTYPE;
    v_observation video_queue.source_observation%ROWTYPE; v_bound_job jsonb; v_sequence integer;
BEGIN
    IF TG_TABLE_NAME='subset_admission' THEN
        PERFORM video_queue.subset_assert_final(NEW.request_key);
        RETURN NULL;
    ELSIF TG_TABLE_NAME='source_reservation' THEN v_request_key := NEW.admission_request_key;
    ELSIF TG_TABLE_NAME='batch' THEN v_request_key := NEW.request_key;
    ELSIF TG_TABLE_NAME='job' THEN SELECT q_batch.request_key INTO v_request_key FROM video_queue.batch AS q_batch WHERE q_batch.batch_id=NEW.batch_id;
    ELSIF TG_TABLE_NAME='source_observation' THEN SELECT q_admission.request_key INTO v_request_key FROM video_queue.subset_admission AS q_admission WHERE q_admission.observation_id=NEW.observation_id;
    ELSIF TG_TABLE_NAME='subset_approval' THEN SELECT q_admission.request_key INTO v_request_key FROM video_queue.subset_admission AS q_admission WHERE q_admission.approval_id=NEW.approval_id;
    END IF;
    IF v_request_key IS NULL THEN RETURN NULL; END IF;
    SELECT q_admission.* INTO v_admission FROM video_queue.subset_admission AS q_admission WHERE q_admission.request_key=v_request_key;
    IF v_admission.state IS DISTINCT FROM 'FINALIZED' THEN RAISE EXCEPTION 'SUBSET_UNFINALIZED_HOLD'; END IF;
    -- Full bijection validation runs for both admission mutations. Every other
    -- table schedules an affected-row proof; immutable BEFORE fences prohibit
    -- later edits to the other projection fields. Do not rebuild all 1000 jobs
    -- for each of the 2000 reservation/job triggers (quadratic SQL round trips).
    IF TG_TABLE_NAME='batch' THEN
        IF NEW.batch_id IS DISTINCT FROM v_admission.batch_id THEN RAISE EXCEPTION 'SUBSET_BINDING_MISMATCH_HOLD'; END IF;
    ELSIF TG_TABLE_NAME='job' THEN
        v_bound_job := (v_admission.binding->'jobs')->(NEW.sequence-1);
        IF NEW.batch_id IS DISTINCT FROM v_admission.batch_id OR v_bound_job->>'job_id' IS DISTINCT FROM NEW.job_id::text OR v_bound_job->>'file_id' IS DISTINCT FROM NEW.source_file_id OR v_bound_job->'sequence' IS DISTINCT FROM to_jsonb(NEW.sequence) THEN RAISE EXCEPTION 'SUBSET_BINDING_MISMATCH_HOLD'; END IF;
    ELSIF TG_TABLE_NAME='source_reservation' THEN
        -- INSERT event images predate mapping; inspect the final persisted row.
        SELECT q_reservation.* INTO v_reservation FROM video_queue.source_reservation AS q_reservation WHERE q_reservation.source_identity=NEW.source_identity;
        SELECT q_job.sequence INTO v_sequence FROM video_queue.job AS q_job WHERE q_job.job_id=v_reservation.job_id;
        v_bound_job := (v_admission.binding->'jobs')->(v_sequence-1);
        IF v_reservation.batch_id IS DISTINCT FROM v_admission.batch_id OR v_bound_job->>'job_id' IS DISTINCT FROM v_reservation.job_id::text
           OR v_bound_job->>'source_identity' IS DISTINCT FROM v_reservation.source_identity OR v_bound_job->>'source_fingerprint' IS DISTINCT FROM v_reservation.source_fingerprint THEN RAISE EXCEPTION 'SUBSET_RESERVATION_MISMATCH_HOLD'; END IF;
    ELSIF TG_TABLE_NAME='source_observation' THEN
        SELECT q_observation.* INTO v_observation FROM video_queue.source_observation AS q_observation WHERE q_observation.observation_id=NEW.observation_id;
        IF v_observation.approval_id IS DISTINCT FROM v_admission.approval_id OR v_observation.request_key IS DISTINCT FROM v_request_key OR v_observation.phase <> 'ADMISSION' OR v_observation.consumed_at IS NULL OR v_observation.contract_sha256 IS DISTINCT FROM v_admission.contract_sha256 THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_BINDING_HOLD'; END IF;
    ELSIF TG_TABLE_NAME='subset_approval' THEN
        IF NEW.approval_id IS DISTINCT FROM v_admission.approval_id OR NEW.contract_sha256 IS DISTINCT FROM v_admission.contract_sha256 OR NEW.queue_manifest_sha256 IS DISTINCT FROM v_admission.queue_manifest_sha256 THEN RAISE EXCEPTION 'SUBSET_APPROVAL_CONTENT_HOLD'; END IF;
    END IF;
    RETURN NULL;
END $$;
DO $triggers$
DECLARE v_table_name text;
BEGIN
    FOREACH v_table_name IN ARRAY ARRAY['subset_approval','source_observation','subset_admission','source_reservation','batch','job'] LOOP
        EXECUTE format('CREATE TRIGGER subset_guard BEFORE INSERT OR UPDATE OR DELETE ON video_queue.%I FOR EACH ROW EXECUTE FUNCTION video_queue.subset_guard()',v_table_name);
        EXECUTE format('CREATE TRIGGER subset_no_truncate BEFORE TRUNCATE ON video_queue.%I FOR EACH STATEMENT EXECUTE FUNCTION video_queue.subset_guard()',v_table_name);
        EXECUTE format('CREATE CONSTRAINT TRIGGER subset_final AFTER INSERT OR UPDATE ON video_queue.%I DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION video_queue.subset_deferred_guard()',v_table_name);
    END LOOP;
END $triggers$;

CREATE FUNCTION video_queue.subset_worker_eligible(p_batch_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
    SELECT EXISTS (SELECT FROM video_queue.subset_admission AS q_admission JOIN video_queue.subset_approval AS q_approval ON q_approval.approval_id=q_admission.approval_id
        WHERE q_admission.batch_id=p_batch_id AND q_admission.state='FINALIZED' AND q_approval.runtime_enabled AND q_approval.revoked_at IS NULL AND q_approval.expires_at > clock_timestamp())
$$;
CREATE FUNCTION video_queue.subset_lock_lease(p_job_id uuid,p_lease_token uuid,p_worker_key text) RETURNS video_queue.job
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE v_job video_queue.job%ROWTYPE; v_approval video_queue.subset_approval%ROWTYPE;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_worker_v2');
    SELECT q_job.* INTO v_job FROM video_queue.job AS q_job WHERE q_job.job_id=p_job_id FOR UPDATE;
    IF NOT FOUND OR p_lease_token IS NULL OR p_worker_key IS NULL OR v_job.status <> 'LEASED'
       OR v_job.lease_token IS DISTINCT FROM p_lease_token OR v_job.lease_owner IS DISTINCT FROM p_worker_key
       OR v_job.attempt_count NOT BETWEEN 1 AND 3 OR v_job.lease_expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_LEASE_LOST'; END IF;
    SELECT q_approval.* INTO v_approval FROM video_queue.subset_approval AS q_approval JOIN video_queue.subset_admission AS q_admission ON q_admission.approval_id=q_approval.approval_id WHERE q_admission.batch_id=v_job.batch_id AND q_admission.state='FINALIZED' FOR SHARE OF q_approval;
    IF NOT FOUND OR NOT v_approval.runtime_enabled OR v_approval.revoked_at IS NOT NULL OR v_approval.expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_RUNTIME_HOLD'; END IF;
    -- Approval lock may have waited behind revocation; re-read wall clock.
    IF v_job.lease_expires_at <= clock_timestamp() THEN RAISE EXCEPTION 'SUBSET_LEASE_LOST'; END IF;
    RETURN v_job;
END $$;
CREATE FUNCTION video_queue.authorize_subset_attempt(p_job_id uuid,p_lease_token uuid,p_worker_key text,p_observation_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE v_job video_queue.job%ROWTYPE; v_observation video_queue.source_observation%ROWTYPE; v_approval video_queue.subset_approval%ROWTYPE; v_expected jsonb; v_checked_at timestamptz;
BEGIN
    v_job := video_queue.subset_lock_lease(p_job_id,p_lease_token,p_worker_key);
    SELECT q_approval.* INTO v_approval FROM video_queue.subset_approval AS q_approval JOIN video_queue.subset_admission AS q_admission ON q_admission.approval_id=q_approval.approval_id WHERE q_admission.batch_id=v_job.batch_id;
    SELECT q_observation.* INTO v_observation FROM video_queue.source_observation AS q_observation WHERE q_observation.observation_id=p_observation_id FOR UPDATE;
    IF NOT FOUND OR v_observation.phase <> 'ATTEMPT' OR v_observation.approval_id IS DISTINCT FROM v_approval.approval_id OR v_observation.contract_sha256 IS DISTINCT FROM v_approval.contract_sha256 OR v_observation.request_key IS DISTINCT FROM v_approval.request_key
       OR v_observation.job_id IS DISTINCT FROM v_job.job_id OR v_observation.lease_token IS DISTINCT FROM v_job.lease_token OR v_observation.attempt_count IS DISTINCT FROM v_job.attempt_count OR v_observation.worker_key IS DISTINCT FROM v_job.lease_owner OR v_observation.consumed_at IS NOT NULL THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_SCOPE'; END IF;
    v_checked_at := clock_timestamp();
    IF v_approval.expires_at <= v_checked_at THEN RAISE EXCEPTION 'SUBSET_APPROVAL_NOT_ACTIVE'; END IF;
    IF v_observation.observed_at > v_checked_at OR v_observation.expires_at <= v_checked_at OR v_job.lease_expires_at <= v_checked_at THEN RAISE EXCEPTION 'SUBSET_OBSERVATION_EXPIRED'; END IF;
    SELECT jsonb_build_array(q_item.value-ARRAY['duration_ms','estimated_cost_usd']) INTO v_expected FROM jsonb_array_elements(v_approval.contract->'files') AS q_item(value) WHERE q_item.value->>'file_id'=v_job.source_file_id;
    IF v_observation.sources IS DISTINCT FROM v_expected THEN RAISE EXCEPTION 'SUBSET_SOURCE_REVISION_CHANGED'; END IF;
    UPDATE video_queue.source_observation AS q_observation SET consumed_at=clock_timestamp() WHERE q_observation.observation_id=p_observation_id;
    RETURN jsonb_build_object('status','SOURCE_AND_LEASE_VERIFIED','job_id',v_job.job_id,'lease_token',v_job.lease_token,'attempt_count',v_job.attempt_count,'observation_id',p_observation_id) || video_queue.subset_disabled();
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
    RETURN QUERY SELECT q_retry.job_status,q_retry.batch_status,q_retry.retry_after FROM video_queue.retry_job(p_job_id,p_lease_token,p_worker_key,p_error_code,3,p_base_delay_seconds) AS q_retry;
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
    IF NOT EXISTS (SELECT FROM video_queue.source_observation AS q_observation JOIN video_queue.job AS q_job ON q_job.job_id=q_observation.job_id
        WHERE q_observation.job_id=p_job_id AND q_observation.lease_token=p_lease_token AND q_observation.worker_key=p_worker_key
          AND q_observation.attempt_count=q_job.attempt_count AND q_observation.phase='ATTEMPT' AND q_observation.consumed_at IS NOT NULL
          AND q_observation.observation_id IS DISTINCT FROM p_observation_id) THEN RAISE EXCEPTION 'SUBSET_PREWORK_OBSERVATION_REQUIRED'; END IF;
    PERFORM video_queue.authorize_subset_attempt(p_job_id,p_lease_token,p_worker_key,p_observation_id);
    RETURN QUERY SELECT q_finish.job_status,q_finish.batch_status,q_finish.released_jobs FROM video_queue.finish_job(p_job_id,p_lease_token,p_worker_key,p_outcome,p_output,p_error_code) AS q_finish;
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
        UPDATE video_queue.job AS q_job
           SET status='FAILED', lease_owner=NULL, lease_token=NULL,
               lease_expires_at=NULL, error_code='UV_WORKER_CRASH_RETRY_EXHAUSTED',
               updated_at=clock_timestamp(), completed_at=clock_timestamp()
         WHERE video_queue.subset_worker_eligible(q_job.batch_id) AND ((q_job.status='LEASED' AND q_job.lease_expires_at <= clock_timestamp()) OR q_job.status='QUEUED')
           AND q_job.attempt_count >= 3
         RETURNING q_job.*
    LOOP
        INSERT INTO video_queue.job_event(batch_id,job_id,event_type,details)
        VALUES (v_job.batch_id,v_job.job_id,'FAILED',
                jsonb_build_object('error_code','UV_WORKER_CRASH_RETRY_EXHAUSTED',
                                   'attempt',v_job.attempt_count));
        IF v_job.is_canary THEN
            UPDATE video_queue.batch AS q_batch
               SET status='CANARY_BLOCKED',updated_at=clock_timestamp(),completed_at=clock_timestamp()
             WHERE q_batch.batch_id=v_job.batch_id;
        ELSIF NOT EXISTS (
            SELECT 1 FROM video_queue.job AS q_pending WHERE q_pending.batch_id=v_job.batch_id
              AND q_pending.status IN ('PENDING_CANARY','QUEUED','LEASED')
        ) THEN
            UPDATE video_queue.batch AS q_batch
               SET status='REVIEW',updated_at=clock_timestamp(),completed_at=clock_timestamp()
             WHERE q_batch.batch_id=v_job.batch_id;
        END IF;
    END LOOP;

    SELECT q_job.* INTO v_job
      FROM video_queue.job AS q_job
      JOIN video_queue.batch AS q_batch ON q_batch.batch_id = q_job.batch_id
     WHERE video_queue.subset_worker_eligible(q_job.batch_id) AND q_job.attempt_count < 3 AND (
            (q_job.status = 'QUEUED' AND q_job.next_attempt_at <= clock_timestamp())
            OR (q_job.status = 'LEASED' AND q_job.lease_expires_at <= clock_timestamp() AND q_job.attempt_count < 3)
        )
       AND q_batch.status IN ('QUEUED_CANARY','RUNNING')
       AND q_batch.processing_profile = p_processing_profile
       AND q_batch.algorithm_revision = p_algorithm_revision
     ORDER BY q_batch.created_at, q_job.is_canary DESC, q_job.sequence
     FOR UPDATE OF q_job SKIP LOCKED
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    -- Claim selection and runtime revocation serialize on the approval row.
    -- Keep the existing job -> batch lock order; approval revocation takes no
    -- job locks. Recheck after any approval-lock wait.
    PERFORM 1 FROM video_queue.subset_approval AS q_approval
    JOIN video_queue.subset_admission AS q_admission ON q_admission.approval_id=q_approval.approval_id
    WHERE q_admission.batch_id=v_job.batch_id FOR SHARE OF q_approval;
    IF NOT video_queue.subset_worker_eligible(v_job.batch_id) THEN
        RAISE EXCEPTION 'SUBSET_RUNTIME_HOLD';
    END IF;
    v_previous_status := v_job.status;
    UPDATE video_queue.job AS q_job
       SET status = 'LEASED',
           attempt_count = q_job.attempt_count + 1,
           lease_owner = p_worker_key,
           lease_token = gen_random_uuid(),
           lease_expires_at = clock_timestamp() + make_interval(secs => p_lease_seconds),
           updated_at = clock_timestamp()
     WHERE q_job.job_id = v_job.job_id
     RETURNING q_job.* INTO v_job;
    UPDATE video_queue.batch AS q_batch
       SET status = 'RUNNING', updated_at = clock_timestamp()
     WHERE q_batch.batch_id = v_job.batch_id
       AND q_batch.status = 'QUEUED_CANARY';
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
        q_batch.source_folder_id,
        q_batch.output_folder_id,
        q_batch.work_folder_id,
        q_batch.processing_profile,
        q_batch.algorithm_revision,
        v_job.source_file_id,
        v_job.source_name,
        v_job.source_mime_type,
        v_job.source_size_bytes,
        v_job.source_checksum,
        v_job.stable_job_key,
        v_job.is_canary,
        v_job.attempt_count
      FROM video_queue.batch AS q_batch
     WHERE q_batch.batch_id = v_job.batch_id;
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
DECLARE v_function record; v_acl record; v_table record;
BEGIN
    -- Cut off every extant raw table writer, including per-column grants.
    -- Definer ownership is a trusted migration boundary, never a runtime role.
    FOR v_table IN SELECT q_class.oid,q_class.oid::regclass AS relation,q_class.relowner FROM pg_class AS q_class
      JOIN pg_namespace AS q_namespace ON q_namespace.oid=q_class.relnamespace WHERE q_namespace.nspname='video_queue'
        AND q_class.relname IN ('batch','job','job_event') LOOP
        EXECUTE format('REVOKE ALL ON TABLE %s FROM PUBLIC',v_table.relation);
        FOR v_acl IN SELECT DISTINCT q_acl.grantee,q_role.rolname FROM pg_class AS q_class,
          LATERAL aclexplode(coalesce(q_class.relacl,acldefault('r',q_class.relowner))) AS q_acl
          JOIN pg_roles AS q_role ON q_role.oid=q_acl.grantee WHERE q_class.oid=v_table.oid AND q_acl.grantee<>v_table.relowner AND q_role.rolname<>'video_subset_owner' LOOP
            EXECUTE format('REVOKE ALL ON TABLE %s FROM %I',v_table.relation,v_acl.rolname);
        END LOOP;
        FOR v_acl IN SELECT DISTINCT q_attribute.attname,q_acl.grantee,q_role.rolname FROM pg_attribute AS q_attribute,
          LATERAL aclexplode(q_attribute.attacl) AS q_acl LEFT JOIN pg_roles AS q_role ON q_role.oid=q_acl.grantee
          WHERE q_attribute.attrelid=v_table.oid AND q_acl.grantee<>v_table.relowner AND (q_role.rolname IS NULL OR q_role.rolname<>'video_subset_owner') LOOP
            EXECUTE format('REVOKE ALL (%I) ON TABLE %s FROM %s',v_acl.attname,v_table.relation,
                CASE WHEN v_acl.grantee=0 THEN 'PUBLIC' ELSE quote_ident(v_acl.rolname) END);
        END LOOP;
    END LOOP;
    -- Revoke every extant grantee, not only the three known legacy roles.
    FOR v_function IN SELECT q_proc.oid, q_proc.oid::regprocedure AS signature, q_proc.proowner
      FROM pg_proc AS q_proc JOIN pg_namespace AS q_namespace ON q_namespace.oid=q_proc.pronamespace
      WHERE q_namespace.nspname='video_queue' AND q_proc.proname IN ('enqueue_drive_batch','claim_job','heartbeat_job','retry_job','finish_job') LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',v_function.signature);
        FOR v_acl IN SELECT DISTINCT q_acl.grantee, q_role.rolname FROM pg_proc AS q_proc,
            LATERAL aclexplode(coalesce(q_proc.proacl,acldefault('f',q_proc.proowner))) AS q_acl
            JOIN pg_roles AS q_role ON q_role.oid=q_acl.grantee WHERE q_proc.oid=v_function.oid AND q_acl.grantee<>v_function.proowner LOOP
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',v_function.signature,v_acl.rolname);
        END LOOP;
    END LOOP;
    FOR v_function IN SELECT q_proc.oid::regprocedure AS signature FROM pg_proc AS q_proc JOIN pg_namespace AS q_namespace ON q_namespace.oid=q_proc.pronamespace
      WHERE q_namespace.nspname='video_queue' AND (q_proc.proname LIKE 'subset_%' OR q_proc.proname IN
       ('approve_subset','set_subset_runtime','revoke_subset_approval','observe_subset','recover_subset_admission','admit_approved_subset','authorize_subset_attempt','heartbeat_subset_job','retry_subset_job','finish_subset_job','claim_job')) LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO video_subset_owner',v_function.signature);
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',v_function.signature);
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
