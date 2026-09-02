\set ON_ERROR_STOP on
BEGIN;

-- Project-neutral control plane for video processing. Drive keeps source and
-- result bytes; this schema stores only bounded identities, leases and status.
CREATE SCHEMA IF NOT EXISTS video_queue;
REVOKE ALL ON SCHEMA video_queue FROM PUBLIC;
GRANT USAGE ON SCHEMA video_queue TO
    bridge_school_reader,
    bridge_school_app,
    bridge_school_worker;

CREATE TABLE video_queue.batch (
    batch_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_key text NOT NULL UNIQUE,
    source_kind text NOT NULL DEFAULT 'google_drive_folder',
    source_folder_id text NOT NULL,
    output_folder_id text NOT NULL,
    work_folder_id text NOT NULL,
    processing_profile text NOT NULL,
    algorithm_revision text NOT NULL,
    result_mode text NOT NULL DEFAULT 'SHADOW_REVIEW_ONLY',
    inventory_sha256 text NOT NULL,
    expected_count integer NOT NULL,
    total_size_bytes bigint NOT NULL,
    canary_source_file_id text NOT NULL,
    status text NOT NULL DEFAULT 'QUEUED_CANARY',
    canonical_promotion_allowed boolean NOT NULL DEFAULT false,
    database_persistence_allowed boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    CONSTRAINT video_batch_request_key_check
        CHECK (request_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'),
    CONSTRAINT video_batch_source_kind_check
        CHECK (source_kind = 'google_drive_folder'),
    CONSTRAINT video_batch_drive_ids_check CHECK (
        source_folder_id ~ '^[A-Za-z0-9_-]{10,200}$'
        AND output_folder_id ~ '^[A-Za-z0-9_-]{10,200}$'
        AND work_folder_id ~ '^[A-Za-z0-9_-]{10,200}$'
        AND canary_source_file_id ~ '^[A-Za-z0-9_-]{10,200}$'
        AND source_folder_id <> output_folder_id
        AND source_folder_id <> work_folder_id
    ),
    CONSTRAINT video_batch_profile_check
        CHECK (processing_profile ~ '^[a-z][a-z0-9_.-]{0,79}$'),
    CONSTRAINT video_batch_revision_check
        CHECK (algorithm_revision ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'),
    CONSTRAINT video_batch_result_mode_check
        CHECK (result_mode = 'SHADOW_REVIEW_ONLY'),
    CONSTRAINT video_batch_inventory_sha_check
        CHECK (inventory_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT video_batch_count_check
        CHECK (expected_count BETWEEN 1 AND 1000),
    CONSTRAINT video_batch_size_check
        CHECK (total_size_bytes BETWEEN 1048576 AND 68719476736000),
    CONSTRAINT video_batch_status_check CHECK (
        status IN ('QUEUED_CANARY','RUNNING','CANARY_BLOCKED','REVIEW')
    ),
    CONSTRAINT video_batch_shadow_only_check CHECK (
        NOT canonical_promotion_allowed AND NOT database_persistence_allowed
    ),
    CONSTRAINT video_batch_completion_check CHECK (
        (status IN ('CANARY_BLOCKED','REVIEW')) = (completed_at IS NOT NULL)
    )
);

CREATE TABLE video_queue.job (
    job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id uuid NOT NULL REFERENCES video_queue.batch(batch_id) ON DELETE RESTRICT,
    sequence integer NOT NULL,
    source_file_id text NOT NULL,
    source_name text NOT NULL,
    source_mime_type text NOT NULL,
    source_size_bytes bigint NOT NULL,
    source_checksum text,
    stable_job_key text NOT NULL,
    is_canary boolean NOT NULL,
    status text NOT NULL,
    attempt_count integer NOT NULL DEFAULT 0,
    lease_owner text,
    lease_token uuid,
    lease_expires_at timestamptz,
    next_attempt_at timestamptz NOT NULL DEFAULT '-infinity'::timestamptz,
    error_code text,
    output jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    CONSTRAINT video_job_batch_sequence_key UNIQUE (batch_id, sequence),
    CONSTRAINT video_job_batch_source_key UNIQUE (batch_id, source_file_id),
    CONSTRAINT video_job_batch_stable_key UNIQUE (batch_id, stable_job_key),
    CONSTRAINT video_job_sequence_check CHECK (sequence BETWEEN 1 AND 1000),
    CONSTRAINT video_job_source_id_check
        CHECK (source_file_id ~ '^[A-Za-z0-9_-]{10,200}$'),
    CONSTRAINT video_job_name_check
        CHECK (length(btrim(source_name)) BETWEEN 1 AND 500 AND source_name !~ '^AI_PART_'),
    CONSTRAINT video_job_mime_check
        CHECK (source_mime_type ~ '^video/[A-Za-z0-9.+_-]{1,120}$'),
    CONSTRAINT video_job_size_check
        CHECK (source_size_bytes BETWEEN 1048576 AND 68719476736),
    CONSTRAINT video_job_checksum_check CHECK (
        source_checksum IS NULL
        OR source_checksum ~ '^(md5:[0-9a-f]{32}|sha1:[0-9a-f]{40}|sha256:[0-9a-f]{64})$'
    ),
    CONSTRAINT video_job_stable_key_check
        CHECK (stable_job_key ~ '^[0-9a-f]{32}$'),
    CONSTRAINT video_job_status_check CHECK (
        status IN ('PENDING_CANARY','QUEUED','LEASED','REVIEW_READY','AMBIGUOUS','FAILED')
    ),
    CONSTRAINT video_job_attempt_check CHECK (attempt_count >= 0),
    CONSTRAINT video_job_lease_check CHECK (
        (status = 'LEASED') = (
            lease_owner IS NOT NULL
            AND lease_token IS NOT NULL
            AND lease_expires_at IS NOT NULL
        )
    ),
    CONSTRAINT video_job_completion_check CHECK (
        (status IN ('REVIEW_READY','AMBIGUOUS','FAILED')) = (completed_at IS NOT NULL)
    ),
    CONSTRAINT video_job_error_code_check CHECK (
        error_code IS NULL OR error_code ~ '^UV_[A-Z0-9_]{1,96}$'
    ),
    CONSTRAINT video_job_output_object_check CHECK (jsonb_typeof(output) = 'object'),
    CONSTRAINT video_job_output_shadow_check CHECK (
        output = '{}'::jsonb OR (
            output->>'result_mode' = 'SHADOW_REVIEW_ONLY'
            AND output->'canonical_promotion_allowed' IS NOT DISTINCT FROM 'false'::jsonb
            AND output->'database_persistence_allowed' IS NOT DISTINCT FROM 'false'::jsonb
            AND output->>'publication_state' = 'NOT_PUBLISHED'
        )
    )
);

CREATE INDEX video_job_claim_idx
    ON video_queue.job(status, next_attempt_at, created_at, sequence)
    WHERE status IN ('QUEUED','LEASED');
CREATE INDEX video_job_batch_status_idx
    ON video_queue.job(batch_id, status);
CREATE INDEX video_job_lease_expiry_idx
    ON video_queue.job(lease_expires_at)
    WHERE status = 'LEASED';

CREATE TABLE video_queue.job_event (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id uuid NOT NULL REFERENCES video_queue.batch(batch_id) ON DELETE RESTRICT,
    job_id uuid REFERENCES video_queue.job(job_id) ON DELETE RESTRICT,
    event_type text NOT NULL,
    worker_key text,
    lease_token uuid,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT video_job_event_type_check
        CHECK (event_type ~ '^[A-Z][A-Z0-9_]{0,63}$'),
    CONSTRAINT video_job_event_details_check
        CHECK (jsonb_typeof(details) = 'object')
);

CREATE OR REPLACE FUNCTION video_queue.enqueue_drive_batch(
    p_request_key text,
    p_source_folder_id text,
    p_output_folder_id text,
    p_work_folder_id text,
    p_processing_profile text,
    p_algorithm_revision text,
    p_canary_source_file_id text,
    p_inventory_sha256 text,
    p_files jsonb
)
RETURNS TABLE(batch_id uuid, status text, expected_count integer, total_size_bytes bigint)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, video_queue
AS $$
DECLARE
    v_batch video_queue.batch%ROWTYPE;
    v_count integer;
    v_total bigint;
    v_item jsonb;
    v_ordinal bigint;
BEGIN
    IF p_request_key IS NULL OR p_request_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
       OR p_source_folder_id IS NULL OR p_source_folder_id !~ '^[A-Za-z0-9_-]{10,200}$'
       OR p_output_folder_id IS NULL OR p_output_folder_id !~ '^[A-Za-z0-9_-]{10,200}$'
       OR p_work_folder_id IS NULL OR p_work_folder_id !~ '^[A-Za-z0-9_-]{10,200}$'
       OR p_source_folder_id IN (p_output_folder_id, p_work_folder_id)
       OR p_processing_profile IS NULL OR p_processing_profile !~ '^[a-z][a-z0-9_.-]{0,79}$'
       OR p_algorithm_revision IS NULL OR p_algorithm_revision !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'
       OR p_canary_source_file_id IS NULL OR p_canary_source_file_id !~ '^[A-Za-z0-9_-]{10,200}$'
       OR p_inventory_sha256 IS NULL OR p_inventory_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'VIDEO_BATCH_ARGUMENT_INVALID';
    END IF;
    IF jsonb_typeof(p_files) <> 'array' THEN
        RAISE EXCEPTION 'VIDEO_BATCH_FILES_NOT_ARRAY';
    END IF;

    -- Serialize equal idempotency keys. Without this transaction-scoped lock,
    -- two first-time callers can both miss the SELECT and race at the UNIQUE
    -- insert instead of receiving the same terminal intake receipt.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_request_key, 0));
    v_count := jsonb_array_length(p_files);
    IF v_count NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'VIDEO_BATCH_COUNT_OUT_OF_RANGE';
    END IF;

    FOR v_item, v_ordinal IN
        SELECT value, ordinality FROM jsonb_array_elements(p_files) WITH ORDINALITY
    LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(v_item)) <> 6
           OR NOT (v_item ?& ARRAY['sequence','file_id','name','mime_type','size_bytes','checksum'])
           OR jsonb_typeof(v_item->'sequence') <> 'number'
           OR jsonb_typeof(v_item->'file_id') <> 'string'
           OR jsonb_typeof(v_item->'name') <> 'string'
           OR jsonb_typeof(v_item->'mime_type') <> 'string'
           OR jsonb_typeof(v_item->'size_bytes') <> 'number'
           OR jsonb_typeof(v_item->'checksum') NOT IN ('string','null') THEN
            RAISE EXCEPTION 'VIDEO_BATCH_ITEM_SHAPE_INVALID';
        END IF;
        IF (v_item->>'sequence')::integer <> v_ordinal
           OR v_item->>'file_id' !~ '^[A-Za-z0-9_-]{10,200}$'
           OR length(btrim(v_item->>'name')) NOT BETWEEN 1 AND 500
           OR v_item->>'name' ~ '^AI_PART_'
           OR v_item->>'mime_type' !~ '^video/[A-Za-z0-9.+_-]{1,120}$'
           OR (v_item->>'size_bytes')::bigint NOT BETWEEN 1048576 AND 68719476736
           OR (
                v_item->>'checksum' IS NOT NULL
                AND v_item->>'checksum' !~ '^(md5:[0-9a-f]{32}|sha1:[0-9a-f]{40}|sha256:[0-9a-f]{64})$'
           ) THEN
            RAISE EXCEPTION 'VIDEO_BATCH_ITEM_VALUE_INVALID';
        END IF;
    END LOOP;

    IF (SELECT count(DISTINCT value->>'file_id') FROM jsonb_array_elements(p_files)) <> v_count THEN
        RAISE EXCEPTION 'VIDEO_BATCH_DUPLICATE_SOURCE';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_files) item
         WHERE item->>'file_id' = p_canary_source_file_id
    ) THEN
        RAISE EXCEPTION 'VIDEO_BATCH_CANARY_ABSENT';
    END IF;
    SELECT sum((value->>'size_bytes')::bigint)
      INTO v_total
      FROM jsonb_array_elements(p_files);

    SELECT * INTO v_batch
      FROM video_queue.batch b
     WHERE b.request_key = p_request_key
     FOR UPDATE;
    IF FOUND THEN
        IF v_batch.source_folder_id <> p_source_folder_id
           OR v_batch.output_folder_id <> p_output_folder_id
           OR v_batch.work_folder_id <> p_work_folder_id
           OR v_batch.processing_profile <> p_processing_profile
           OR v_batch.algorithm_revision <> p_algorithm_revision
           OR v_batch.canary_source_file_id <> p_canary_source_file_id
           OR v_batch.inventory_sha256 <> p_inventory_sha256
           OR v_batch.expected_count <> v_count
           OR v_batch.total_size_bytes <> v_total THEN
            RAISE EXCEPTION 'VIDEO_BATCH_REQUEST_CONFLICT';
        END IF;
        RETURN QUERY SELECT v_batch.batch_id, v_batch.status, v_batch.expected_count, v_batch.total_size_bytes;
        RETURN;
    END IF;

    INSERT INTO video_queue.batch(
        request_key, source_folder_id, output_folder_id, work_folder_id,
        processing_profile, algorithm_revision, inventory_sha256,
        expected_count, total_size_bytes, canary_source_file_id
    ) VALUES (
        p_request_key, p_source_folder_id, p_output_folder_id, p_work_folder_id,
        p_processing_profile, p_algorithm_revision, p_inventory_sha256,
        v_count, v_total, p_canary_source_file_id
    ) RETURNING * INTO v_batch;

    INSERT INTO video_queue.job(
        batch_id, sequence, source_file_id, source_name, source_mime_type,
        source_size_bytes, source_checksum, stable_job_key, is_canary, status
    )
    SELECT
        v_batch.batch_id,
        ordinality::integer,
        item->>'file_id',
        item->>'name',
        item->>'mime_type',
        (item->>'size_bytes')::bigint,
        item->>'checksum',
        substr(encode(public.digest(convert_to('bridge-video|drive|' || (item->>'file_id'), 'UTF8'), 'sha256'), 'hex'), 1, 32),
        item->>'file_id' = p_canary_source_file_id,
        CASE WHEN item->>'file_id' = p_canary_source_file_id THEN 'QUEUED' ELSE 'PENDING_CANARY' END
    FROM jsonb_array_elements(p_files) WITH ORDINALITY AS source(item, ordinality);

    INSERT INTO video_queue.job_event(batch_id, job_id, event_type, details)
    SELECT v_batch.batch_id, j.job_id, 'ENQUEUED', jsonb_build_object('status', j.status)
      FROM video_queue.job j
     WHERE j.batch_id = v_batch.batch_id;

    PERFORM pg_notify('video_queue_ready', v_batch.batch_id::text);
    RETURN QUERY SELECT v_batch.batch_id, v_batch.status, v_batch.expected_count, v_batch.total_size_bytes;
END;
$$;

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
$$;

CREATE OR REPLACE FUNCTION video_queue.heartbeat_job(
    p_job_id uuid,
    p_lease_token uuid,
    p_worker_key text,
    p_extend_seconds integer DEFAULT 900
)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, video_queue
AS $$
DECLARE
    v_expiry timestamptz;
BEGIN
    IF p_extend_seconds NOT BETWEEN 60 AND 3600 THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_HEARTBEAT_ARGUMENT_INVALID';
    END IF;
    UPDATE video_queue.job
       SET lease_expires_at = clock_timestamp() + make_interval(secs => p_extend_seconds),
           updated_at = clock_timestamp()
     WHERE job_id = p_job_id
       AND status = 'LEASED'
       AND lease_token = p_lease_token
       AND lease_owner = p_worker_key
       AND lease_expires_at > clock_timestamp()
     RETURNING lease_expires_at INTO v_expiry;
    IF v_expiry IS NULL THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_LEASE_LOST';
    END IF;
    RETURN v_expiry;
END;
$$;

CREATE OR REPLACE FUNCTION video_queue.retry_job(
    p_job_id uuid,
    p_lease_token uuid,
    p_worker_key text,
    p_error_code text,
    p_max_attempts integer DEFAULT 3,
    p_base_delay_seconds integer DEFAULT 60
)
RETURNS TABLE(job_status text, batch_status text, retry_after timestamptz)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, video_queue
AS $$
DECLARE
    v_job video_queue.job%ROWTYPE;
    v_status text;
    v_batch_status text;
    v_retry_after timestamptz;
BEGIN
    IF p_worker_key IS NULL OR p_worker_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
       OR p_error_code IS NULL OR p_error_code !~ '^UV_[A-Z0-9_]{1,96}$'
       OR p_max_attempts NOT BETWEEN 1 AND 10
       OR p_base_delay_seconds NOT BETWEEN 1 AND 3600 THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_RETRY_ARGUMENT_INVALID';
    END IF;
    SELECT * INTO v_job FROM video_queue.job j WHERE j.job_id=p_job_id FOR UPDATE;
    IF NOT FOUND OR v_job.status <> 'LEASED' OR v_job.lease_token <> p_lease_token
       OR v_job.lease_owner <> p_worker_key OR v_job.lease_expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_LEASE_LOST';
    END IF;

    IF v_job.attempt_count >= p_max_attempts THEN
        v_status := 'FAILED';
        v_retry_after := NULL;
    ELSE
        v_status := 'QUEUED';
        v_retry_after := clock_timestamp()
            + make_interval(secs => least(3600, p_base_delay_seconds * (1 << (v_job.attempt_count - 1))));
    END IF;
    UPDATE video_queue.job
       SET status=v_status, lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL,
           next_attempt_at=coalesce(v_retry_after, '-infinity'::timestamptz),
           error_code=p_error_code, updated_at=clock_timestamp(),
           completed_at=CASE WHEN v_status='FAILED' THEN clock_timestamp() ELSE NULL END
     WHERE job_id=p_job_id;
    INSERT INTO video_queue.job_event(batch_id,job_id,event_type,worker_key,lease_token,details)
    VALUES (v_job.batch_id,v_job.job_id,
            CASE WHEN v_status='QUEUED' THEN 'RETRY_SCHEDULED' ELSE 'FAILED' END,
            p_worker_key,p_lease_token,
            jsonb_build_object('error_code',p_error_code,'attempt',v_job.attempt_count,
                               'retry_after',v_retry_after));

    IF v_status='FAILED' AND v_job.is_canary THEN
        UPDATE video_queue.batch SET status='CANARY_BLOCKED',updated_at=clock_timestamp(),completed_at=clock_timestamp()
         WHERE batch_id=v_job.batch_id;
    ELSIF v_status='FAILED' AND NOT EXISTS (
        SELECT 1 FROM video_queue.job WHERE batch_id=v_job.batch_id
          AND status IN ('PENDING_CANARY','QUEUED','LEASED')
    ) THEN
        UPDATE video_queue.batch SET status='REVIEW',updated_at=clock_timestamp(),completed_at=clock_timestamp()
         WHERE batch_id=v_job.batch_id;
    END IF;
    SELECT b.status INTO v_batch_status FROM video_queue.batch b WHERE b.batch_id=v_job.batch_id;
    RETURN QUERY SELECT v_status,v_batch_status,v_retry_after;
END;
$$;

CREATE OR REPLACE FUNCTION video_queue.finish_job(
    p_job_id uuid,
    p_lease_token uuid,
    p_worker_key text,
    p_outcome text,
    p_output jsonb,
    p_error_code text DEFAULT NULL
)
RETURNS TABLE(job_status text, batch_status text, released_jobs integer)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, video_queue
AS $$
DECLARE
    v_job video_queue.job%ROWTYPE;
    v_batch video_queue.batch%ROWTYPE;
    v_released integer := 0;
    v_batch_status text;
BEGIN
    IF p_outcome NOT IN ('REVIEW_READY','AMBIGUOUS','FAILED')
       OR p_worker_key IS NULL OR p_worker_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
       OR p_output IS NULL OR jsonb_typeof(p_output) <> 'object'
       OR length(p_output::text) > 65536
       OR p_output->>'result_mode' <> 'SHADOW_REVIEW_ONLY'
       OR p_output->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR p_output->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
       OR p_output->>'publication_state' <> 'NOT_PUBLISHED'
       OR (p_error_code IS NOT NULL AND p_error_code !~ '^UV_[A-Z0-9_]{1,96}$') THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_FINISH_ARGUMENT_INVALID';
    END IF;
    SELECT * INTO v_job
      FROM video_queue.job j
     WHERE j.job_id = p_job_id
     FOR UPDATE;
    IF NOT FOUND
       OR v_job.status <> 'LEASED'
       OR v_job.lease_token <> p_lease_token
       OR v_job.lease_owner <> p_worker_key
       OR v_job.lease_expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_LEASE_LOST';
    END IF;
    SELECT * INTO v_batch
      FROM video_queue.batch b
     WHERE b.batch_id = v_job.batch_id
     FOR UPDATE;
    IF p_output->>'source_file_id' <> v_job.source_file_id
       OR p_output->>'stable_job_key' <> v_job.stable_job_key
       OR p_output->>'algorithm_revision' <> v_batch.algorithm_revision THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_RESULT_IDENTITY_MISMATCH';
    END IF;

    UPDATE video_queue.job
       SET status = p_outcome,
           lease_owner = NULL,
           lease_token = NULL,
           lease_expires_at = NULL,
           error_code = p_error_code,
           output = p_output,
           updated_at = clock_timestamp(),
           completed_at = clock_timestamp()
     WHERE job_id = p_job_id;
    INSERT INTO video_queue.job_event(batch_id, job_id, event_type, worker_key, lease_token, details)
    VALUES (
        v_job.batch_id,
        v_job.job_id,
        p_outcome,
        p_worker_key,
        p_lease_token,
        jsonb_build_object('error_code', p_error_code)
    );

    IF v_job.is_canary AND p_outcome = 'REVIEW_READY' THEN
        UPDATE video_queue.job
           SET status = 'QUEUED', updated_at = clock_timestamp()
         WHERE batch_id = v_job.batch_id AND status = 'PENDING_CANARY';
        GET DIAGNOSTICS v_released = ROW_COUNT;
        UPDATE video_queue.batch
           SET status = 'RUNNING', updated_at = clock_timestamp()
         WHERE batch_id = v_job.batch_id;
        IF v_released > 0 THEN
            PERFORM pg_notify('video_queue_ready', v_job.batch_id::text);
        END IF;
    ELSIF v_job.is_canary THEN
        UPDATE video_queue.batch
           SET status = 'CANARY_BLOCKED',
               updated_at = clock_timestamp(),
               completed_at = clock_timestamp()
         WHERE batch_id = v_job.batch_id;
    END IF;

    IF NOT v_job.is_canary OR p_outcome = 'REVIEW_READY' THEN
        IF NOT EXISTS (
            SELECT 1 FROM video_queue.job
             WHERE batch_id = v_job.batch_id
               AND status IN ('PENDING_CANARY','QUEUED','LEASED')
        ) THEN
            UPDATE video_queue.batch
               SET status = 'REVIEW',
                   updated_at = clock_timestamp(),
                   completed_at = clock_timestamp()
             WHERE batch_id = v_job.batch_id;
        END IF;
    END IF;
    SELECT b.status INTO v_batch_status
      FROM video_queue.batch b
     WHERE b.batch_id = v_job.batch_id;
    RETURN QUERY SELECT p_outcome, v_batch_status, v_released;
END;
$$;

CREATE OR REPLACE VIEW video_queue.batch_status AS
SELECT
    b.batch_id,
    b.request_key,
    b.processing_profile,
    b.algorithm_revision,
    b.result_mode,
    b.inventory_sha256,
    b.expected_count,
    b.total_size_bytes,
    b.status,
    count(*) FILTER (WHERE j.status = 'PENDING_CANARY')::integer AS pending_canary,
    count(*) FILTER (WHERE j.status = 'QUEUED')::integer AS queued,
    count(*) FILTER (WHERE j.status = 'LEASED')::integer AS running,
    count(*) FILTER (WHERE j.status = 'REVIEW_READY')::integer AS review_ready,
    count(*) FILTER (WHERE j.status = 'AMBIGUOUS')::integer AS ambiguous,
    count(*) FILTER (WHERE j.status = 'FAILED')::integer AS failed,
    b.canonical_promotion_allowed,
    b.database_persistence_allowed,
    b.created_at,
    b.updated_at,
    b.completed_at
FROM video_queue.batch b
JOIN video_queue.job j ON j.batch_id = b.batch_id
GROUP BY b.batch_id;

CREATE OR REPLACE VIEW video_queue.job_status AS
SELECT
    j.job_id,
    j.batch_id,
    j.sequence,
    j.source_file_id,
    j.source_name,
    j.source_size_bytes,
    j.stable_job_key,
    j.is_canary,
    j.status,
    j.attempt_count,
    j.error_code,
    j.created_at,
    j.updated_at,
    j.completed_at
FROM video_queue.job j;

REVOKE ALL ON ALL TABLES IN SCHEMA video_queue FROM
    PUBLIC,
    bridge_school_reader,
    bridge_school_app,
    bridge_school_worker;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA video_queue FROM
    PUBLIC,
    bridge_school_reader,
    bridge_school_app,
    bridge_school_worker;
GRANT SELECT ON video_queue.batch_status, video_queue.job_status TO bridge_school_reader;

REVOKE ALL ON FUNCTION video_queue.enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION video_queue.claim_job(text,integer,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION video_queue.heartbeat_job(uuid,uuid,text,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION video_queue.retry_job(uuid,uuid,text,text,integer,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION video_queue.finish_job(uuid,uuid,text,text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION video_queue.enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb)
    TO bridge_school_app;
GRANT EXECUTE ON FUNCTION video_queue.claim_job(text,integer,text,text)
    TO bridge_school_worker;
GRANT EXECUTE ON FUNCTION video_queue.heartbeat_job(uuid,uuid,text,integer)
    TO bridge_school_worker;
GRANT EXECUTE ON FUNCTION video_queue.retry_job(uuid,uuid,text,text,integer,integer)
    TO bridge_school_worker;
GRANT EXECUTE ON FUNCTION video_queue.finish_job(uuid,uuid,text,text,jsonb,text)
    TO bridge_school_worker;

COMMENT ON SCHEMA video_queue IS
    'Project-neutral SHADOW/REVIEW video control plane; never stores media bytes';
COMMENT ON TABLE video_queue.batch IS
    'Hash-bound Drive batch with canary gate and no production promotion';
COMMENT ON TABLE video_queue.job IS
    'Independently leased video job; result identity is fail-closed';
COMMENT ON TABLE video_queue.job_event IS
    'Append-only queue lifecycle evidence';

INSERT INTO schema_migration(migration_key)
VALUES ('0056_universal_video_queue')
ON CONFLICT DO NOTHING;

COMMIT;
