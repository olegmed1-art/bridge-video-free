\set ON_ERROR_STOP on
BEGIN;

-- Additive v1.8 retention boundary for a complete, de-identified IBF artifact.
-- Runtime receives no direct table privilege; the only write path is the exact,
-- fenced SECURITY DEFINER function below.
CREATE TABLE autopilot.ibf_structured_artifact (
    artifact_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL UNIQUE REFERENCES autopilot.task(task_id),
    schema_version text NOT NULL
        CHECK (schema_version = 'IBF_STRUCTURED_TOURNAMENT_V1'),
    source_authority text NOT NULL
        CHECK (source_authority = 'ISRAEL_BRIDGE_FEDERATION_OFFICIAL_RESULTS'),
    ibf_player_id text NOT NULL CHECK (ibf_player_id ~ '^[1-9][0-9]{0,9}$'),
    event_id bigint NOT NULL CHECK (event_id BETWEEN 1 AND 9999999999),
    round_id integer NOT NULL CHECK (round_id BETWEEN 1 AND 9999),
    seat text NOT NULL CHECK (seat ~ '^[A-Za-z0-9:-]{1,24}$'),
    board_count smallint NOT NULL CHECK (board_count BETWEEN 1 AND 32),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    content_bytes bytea NOT NULL,
    manifest_json jsonb NOT NULL,
    retained boolean NOT NULL DEFAULT true CHECK (retained),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT autopilot_ibf_artifact_content_bound CHECK (
        octet_length(content_bytes) BETWEEN 128 AND 262144
    ),
    CONSTRAINT autopilot_ibf_artifact_manifest_object CHECK (
        jsonb_typeof(manifest_json) = 'object'
        AND octet_length(manifest_json::text) <= 8192
    )
);

REVOKE ALL ON TABLE autopilot.ibf_structured_artifact FROM PUBLIC;
REVOKE ALL ON TABLE autopilot.ibf_structured_artifact FROM autopilot_runtime;

CREATE OR REPLACE FUNCTION autopilot.store_ibf_structured_artifact(
    p_task_id uuid,
    p_worker_id text,
    p_lease_epoch bigint,
    p_schema_version text,
    p_content_sha256 text,
    p_content_bytes bytea,
    p_manifest jsonb
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    task_goal_json jsonb;
    task_step_key text;
    artifact_json jsonb;
    manifest_keys text[];
    artifact_keys text[];
    teaching_keys text[];
    existing autopilot.ibf_structured_artifact;
BEGIN
    IF p_schema_version <> 'IBF_STRUCTURED_TOURNAMENT_V1'
       OR p_content_sha256 !~ '^[0-9a-f]{64}$'
       OR p_content_bytes IS NULL
       OR octet_length(p_content_bytes) NOT BETWEEN 128 AND 262144
       OR p_content_sha256 <> encode(public.digest(p_content_bytes, 'sha256'), 'hex')
       OR jsonb_typeof(COALESCE(p_manifest, '{}'::jsonb)) <> 'object'
       OR octet_length(COALESCE(p_manifest, '{}'::jsonb)::text) > 8192 THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_INVALID';
    END IF;

    SELECT array_agg(key ORDER BY key) INTO manifest_keys
      FROM jsonb_object_keys(p_manifest) AS keys(key);
    IF manifest_keys IS DISTINCT FROM ARRAY[
           'analysis_scope', 'artifact_bytes', 'artifact_schema_version',
           'artifact_sha256', 'board_count', 'event_id', 'ibf_player_id',
           'methodology_or_canon_applied', 'model_calls', 'production_mutation',
           'round_id', 'seat', 'source_authority'
       ]
       OR p_manifest->>'analysis_scope' <> 'STRUCTURED_SOURCE_AND_REVIEW_CANDIDATES'
       OR p_manifest->>'artifact_schema_version' <> p_schema_version
       OR p_manifest->>'artifact_sha256' <> p_content_sha256
       OR p_manifest->'artifact_bytes' IS DISTINCT FROM to_jsonb(octet_length(p_content_bytes))
       OR p_manifest->>'source_authority'
          <> 'ISRAEL_BRIDGE_FEDERATION_OFFICIAL_RESULTS'
       OR p_manifest->'methodology_or_canon_applied' IS DISTINCT FROM 'false'::jsonb
       OR p_manifest->'model_calls' IS DISTINCT FROM '0'::jsonb
       OR p_manifest->'production_mutation' IS DISTINCT FROM 'false'::jsonb
       OR COALESCE(p_manifest->>'ibf_player_id', '') !~ '^[1-9][0-9]{0,9}$'
       OR COALESCE(p_manifest->>'event_id', '') !~ '^[1-9][0-9]{0,9}$'
       OR COALESCE(p_manifest->>'round_id', '') !~ '^[1-9][0-9]{0,3}$'
       OR COALESCE(p_manifest->>'seat', '') !~ '^[A-Za-z0-9:-]{1,24}$'
       OR COALESCE(p_manifest->>'board_count', '') !~ '^[1-9][0-9]{0,1}$'
       OR (p_manifest->>'board_count')::integer > 32 THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_MANIFEST_INVALID';
    END IF;

    BEGIN
        artifact_json := convert_from(p_content_bytes, 'UTF8')::jsonb;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_JSON_INVALID';
    END;

    SELECT array_agg(key ORDER BY key) INTO artifact_keys
      FROM jsonb_object_keys(artifact_json) AS keys(key);
    IF artifact_keys IS DISTINCT FROM ARRAY[
           'board_count', 'boards', 'cost_actual_microusd', 'ibf_player_id',
           'latest_participation', 'model_calls', 'production_mutation',
           'schema_version', 'source_authority', 'teaching_analysis'
       ]
       OR artifact_json->>'schema_version' <> p_schema_version
       OR artifact_json->>'source_authority' <> p_manifest->>'source_authority'
       OR artifact_json->>'ibf_player_id' <> p_manifest->>'ibf_player_id'
       OR artifact_json->'board_count' IS DISTINCT FROM p_manifest->'board_count'
       OR artifact_json->'production_mutation' IS DISTINCT FROM 'false'::jsonb
       OR artifact_json->'model_calls' IS DISTINCT FROM '0'::jsonb
       OR artifact_json->'cost_actual_microusd' IS DISTINCT FROM '0'::jsonb
       OR jsonb_typeof(artifact_json->'latest_participation') <> 'object'
       OR artifact_json->'latest_participation'->'event_id'
          IS DISTINCT FROM p_manifest->'event_id'
       OR artifact_json->'latest_participation'->'round_id'
          IS DISTINCT FROM p_manifest->'round_id'
       OR artifact_json->'latest_participation'->'seat'
          IS DISTINCT FROM p_manifest->'seat'
       OR jsonb_typeof(artifact_json->'boards') <> 'array'
       OR jsonb_array_length(artifact_json->'boards')
          <> (p_manifest->>'board_count')::integer
       OR jsonb_typeof(artifact_json->'teaching_analysis') <> 'object' THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_CONTENT_INVALID';
    END IF;

    SELECT array_agg(key ORDER BY key) INTO teaching_keys
      FROM jsonb_object_keys(artifact_json->'teaching_analysis') AS keys(key);
    IF teaching_keys IS DISTINCT FROM ARRAY[
           'causal_attribution', 'methodology_or_canon_applied',
           'missing_source_dimensions', 'review_order'
       ]
       OR artifact_json->'teaching_analysis'->>'causal_attribution'
          <> 'NOT_DEMONSTRATED_BY_SCORE_OR_DOUBLE_DUMMY_ALONE'
       OR artifact_json->'teaching_analysis'->'methodology_or_canon_applied'
          IS DISTINCT FROM 'false'::jsonb
       OR artifact_json->'teaching_analysis'->'missing_source_dimensions'
          IS DISTINCT FROM '["AUCTION", "PLAY_RECORD"]'::jsonb
       OR jsonb_typeof(artifact_json->'teaching_analysis'->'review_order') <> 'array'
       OR jsonb_array_length(artifact_json->'teaching_analysis'->'review_order')
          <> (p_manifest->>'board_count')::integer
       OR EXISTS (
           SELECT 1
             FROM jsonb_array_elements(
                  artifact_json->'teaching_analysis'->'review_order'
             ) AS reviews(item)
            WHERE (SELECT array_agg(key ORDER BY key)
                     FROM jsonb_object_keys(item) AS keys(key)) IS DISTINCT FROM ARRAY[
                      'board_number', 'percentage', 'player_error_demonstrated'
                  ]
               OR COALESCE(item->>'board_number', '') !~ '^[1-9][0-9]{0,2}$'
               OR jsonb_typeof(item->'percentage') <> 'number'
               OR (item->>'percentage')::numeric NOT BETWEEN 0 AND 100
               OR item->'player_error_demonstrated' IS DISTINCT FROM 'false'::jsonb
       ) OR (
           SELECT count(DISTINCT item->>'board_number')
             FROM jsonb_array_elements(
                  artifact_json->'teaching_analysis'->'review_order'
             ) AS reviews(item)
       ) <> (p_manifest->>'board_count')::integer THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_ANALYSIS_INVALID';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM jsonb_array_elements(artifact_json->'boards') AS boards(item)
         WHERE (SELECT array_agg(key ORDER BY key)
                  FROM jsonb_object_keys(item) AS keys(key)) IS DISTINCT FROM ARRAY[
                   'board_number', 'board_page_sha256', 'dds_source_url_sha256',
                   'dealer', 'double_dummy_tricks', 'field_results', 'hands',
                   'observability', 'par_score', 'target_result', 'vulnerability'
               ]
            OR COALESCE(item->>'board_number', '') !~ '^[1-9][0-9]{0,2}$'
            OR COALESCE(item->>'board_page_sha256', '') !~ '^[0-9a-f]{64}$'
            OR COALESCE(item->>'dds_source_url_sha256', '') !~ '^[0-9a-f]{64}$'
            OR item->>'dealer' NOT IN ('N', 'E', 'S', 'W')
            OR jsonb_typeof(item->'hands') <> 'object'
            OR jsonb_typeof(item->'double_dummy_tricks') <> 'object'
            OR jsonb_typeof(item->'field_results') <> 'array'
            OR jsonb_array_length(item->'field_results') NOT BETWEEN 1 AND 200
            OR jsonb_typeof(item->'observability') <> 'object'
            OR jsonb_typeof(item->'target_result') <> 'object'
            OR jsonb_typeof(item->'par_score') <> 'number'
            OR abs((item->>'par_score')::integer) > 10000
            OR jsonb_typeof(item->'vulnerability') <> 'string'
            OR length(item->>'vulnerability') NOT BETWEEN 1 AND 16
    ) OR (
        SELECT count(DISTINCT item->>'board_number')
          FROM jsonb_array_elements(artifact_json->'boards') AS boards(item)
    ) <> (p_manifest->>'board_count')::integer THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_BOARDS_INVALID';
    END IF;

    SELECT goal_json, current_step_key
      INTO task_goal_json, task_step_key
      FROM autopilot.task
     WHERE task_id = p_task_id
       AND status = 'RUNNING'
       AND goal_type = 'IBF_READ_ONLY_ANALYSIS'
       AND lease_owner = p_worker_id
       AND lease_epoch = p_lease_epoch
       AND cost_cap_microusd = 0
     FOR UPDATE;
    IF NOT FOUND THEN RETURN false; END IF;
    IF task_step_key <> 'ibf.read_only_analysis'
       OR task_goal_json->>'ibf_player_id' <> p_manifest->>'ibf_player_id'
       OR task_goal_json->>'source_authority' <> p_manifest->>'source_authority'
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.step_attempt
            WHERE task_id = p_task_id
              AND lease_epoch = p_lease_epoch
              AND status = 'RUNNING'
              AND capability_name = 'ibf.read_only_analysis'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_TASK_MISMATCH';
    END IF;

    SELECT * INTO existing
      FROM autopilot.ibf_structured_artifact
     WHERE task_id = p_task_id;
    IF FOUND THEN
        IF existing.schema_version <> p_schema_version
           OR existing.content_sha256 <> p_content_sha256
           OR existing.content_bytes <> p_content_bytes
           OR existing.manifest_json <> p_manifest THEN
            RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN true;
    END IF;

    INSERT INTO autopilot.ibf_structured_artifact (
        task_id, schema_version, source_authority, ibf_player_id,
        event_id, round_id, seat, board_count,
        content_sha256, content_bytes, manifest_json
    ) VALUES (
        p_task_id,
        p_schema_version,
        p_manifest->>'source_authority',
        p_manifest->>'ibf_player_id',
        (p_manifest->>'event_id')::bigint,
        (p_manifest->>'round_id')::integer,
        p_manifest->>'seat',
        (p_manifest->>'board_count')::smallint,
        p_content_sha256,
        p_content_bytes,
        p_manifest
    );
    RETURN true;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.store_ibf_structured_artifact(
    uuid,text,bigint,text,text,bytea,jsonb
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.store_ibf_structured_artifact(
    uuid,text,bigint,text,text,bytea,jsonb
) TO autopilot_runtime;

COMMENT ON FUNCTION autopilot.store_ibf_structured_artifact(
    uuid,text,bigint,text,text,bytea,jsonb
) IS 'Stores one exact, bounded and fenced de-identified IBF structured artifact.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0308_autopilot_ibf_structured_artifact')
ON CONFLICT DO NOTHING;

COMMIT;
