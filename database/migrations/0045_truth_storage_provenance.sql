\set ON_ERROR_STOP on
BEGIN;

-- META Truth Layer hardening.
-- Registration establishes identity/provenance only; it does not promote quality status.

CREATE UNIQUE INDEX IF NOT EXISTS algorithm_version_label_uk
    ON algorithm_version(algorithm_id, version_label)
    WHERE version_label IS NOT NULL;

INSERT INTO algorithm(school_id, stable_key, name, purpose, status)
SELECT
    school_id,
    'bridge-video-master-analysis',
    'Bridge Video Master Analysis',
    'Verified lesson-video transcription and analysis pipeline',
    'active'
FROM school
WHERE stable_name='Школа спортивного бриджа'
ON CONFLICT (school_id, stable_key) DO NOTHING;

WITH expected(version_no, version_label, registry_status, configuration) AS (
    VALUES
        (1, '3.1-free-master-analysis-r5'::text, 'observed'::text, '{}'::jsonb),
        (2, '3.1-free-master-analysis-r7'::text, 'observed'::text, '{}'::jsonb),
        (3, '3.1-free-r25.1'::text, 'observed'::text, '{}'::jsonb),
        (4, '3.1-free-r25.3'::text, 'observed'::text, '{}'::jsonb),
        (5, '3.1-free-r25.4'::text, 'observed'::text, '{}'::jsonb),
        (6, '3.1-free-r25.4.1'::text, 'observed'::text, '{}'::jsonb),
        (7, '3.1-free-r25.6'::text, 'observed'::text, '{}'::jsonb),
        (8, '3.1-free-r25.10'::text, 'observed'::text, '{}'::jsonb),
        (9, '3.1-free-r25.11'::text, 'candidate'::text,
            '{"registration_basis":"repository_candidate","runtime_module":"bridge_runtime_hardening_r25_11.py"}'::jsonb)
), target_algorithm AS (
    SELECT a.algorithm_id
    FROM algorithm a
    JOIN school s ON s.school_id=a.school_id
    WHERE s.stable_name='Школа спортивного бриджа'
      AND a.stable_key='bridge-video-master-analysis'
)
INSERT INTO algorithm_version(
    algorithm_id, version_no, version_label, configuration, status
)
SELECT
    ta.algorithm_id, e.version_no, e.version_label, e.configuration, e.registry_status
FROM target_algorithm ta
CROSS JOIN expected e
ON CONFLICT (algorithm_id, version_no) DO NOTHING;

-- Preserve any additional revision already present in durable production history at the
-- moment of promotion. It receives an observed identity, not a quality promotion.
WITH target_algorithm AS (
    SELECT a.algorithm_id, a.school_id
    FROM algorithm a
    JOIN school s ON s.school_id=a.school_id
    WHERE s.stable_name='Школа спортивного бриджа'
      AND a.stable_key='bridge-video-master-analysis'
), extra AS (
    SELECT
        ta.algorithm_id,
        ar.algorithm_version AS version_label,
        min(ar.started_at) AS first_seen
    FROM target_algorithm ta
    JOIN analysis_run ar
      ON ar.school_id=ta.school_id
     AND ar.algorithm_key='bridge-video-master-analysis'
    WHERE NOT EXISTS (
        SELECT 1
        FROM algorithm_version av
        WHERE av.algorithm_id=ta.algorithm_id
          AND av.version_label=ar.algorithm_version
    )
    GROUP BY ta.algorithm_id, ar.algorithm_version
), numbered AS (
    SELECT
        e.algorithm_id,
        e.version_label,
        COALESCE((
            SELECT max(av.version_no)
            FROM algorithm_version av
            WHERE av.algorithm_id=e.algorithm_id
        ), 0)
        + row_number() OVER (
            PARTITION BY e.algorithm_id
            ORDER BY e.first_seen, e.version_label
        ) AS version_no
    FROM extra e
)
INSERT INTO algorithm_version(
    algorithm_id, version_no, version_label, configuration, status
)
SELECT
    algorithm_id,
    version_no,
    version_label,
    jsonb_build_object('registration_basis','preexisting_analysis_run'),
    'observed'
FROM numbered;

DO $$
DECLARE
    v_algorithm_id uuid;
BEGIN
    SELECT a.algorithm_id INTO v_algorithm_id
      FROM algorithm a
      JOIN school s ON s.school_id=a.school_id
     WHERE s.stable_name='Школа спортивного бриджа'
       AND a.stable_key='bridge-video-master-analysis';

    IF v_algorithm_id IS NULL THEN
        RAISE EXCEPTION 'canonical Bridge Video algorithm registry row missing';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (VALUES
            (1, '3.1-free-master-analysis-r5'::text),
            (2, '3.1-free-master-analysis-r7'::text),
            (3, '3.1-free-r25.1'::text),
            (4, '3.1-free-r25.3'::text),
            (5, '3.1-free-r25.4'::text),
            (6, '3.1-free-r25.4.1'::text),
            (7, '3.1-free-r25.6'::text),
            (8, '3.1-free-r25.10'::text),
            (9, '3.1-free-r25.11'::text)
        ) AS expected(version_no, version_label)
        WHERE NOT EXISTS (
            SELECT 1
            FROM algorithm_version av
            WHERE av.algorithm_id=v_algorithm_id
              AND av.version_no=expected.version_no
              AND av.version_label=expected.version_label
        )
    ) THEN
        RAISE EXCEPTION 'Bridge Video AlgorithmVersion registry conflicts with expected identities';
    END IF;
END $$;

UPDATE analysis_run ar
   SET algorithm_version_id=av.algorithm_version_id
  FROM algorithm a
  JOIN algorithm_version av ON av.algorithm_id=a.algorithm_id
 WHERE ar.school_id=a.school_id
   AND ar.algorithm_key=a.stable_key
   AND ar.algorithm_version=av.version_label
   AND ar.algorithm_version_id IS NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM analysis_run
        WHERE algorithm_key='bridge-video-master-analysis'
          AND algorithm_version_id IS NULL
    ) THEN
        RAISE EXCEPTION 'preexisting Bridge Video AnalysisRun remains unlinked to AlgorithmVersion';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION resolve_analysis_run_algorithm_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_registered_version_id uuid;
BEGIN
    SELECT av.algorithm_version_id INTO v_registered_version_id
      FROM algorithm a
      JOIN algorithm_version av ON av.algorithm_id=a.algorithm_id
     WHERE a.school_id=NEW.school_id
       AND a.stable_key=NEW.algorithm_key
       AND av.version_label=NEW.algorithm_version;

    IF NEW.algorithm_version_id IS NULL THEN
        IF v_registered_version_id IS NOT NULL THEN
            NEW.algorithm_version_id := v_registered_version_id;
        ELSIF NEW.algorithm_key='bridge-video-master-analysis' THEN
            RAISE EXCEPTION 'unregistered Bridge Video algorithm revision: %', NEW.algorithm_version;
        END IF;
    ELSIF v_registered_version_id IS NULL
       OR NEW.algorithm_version_id <> v_registered_version_id THEN
        RAISE EXCEPTION 'AnalysisRun algorithm identity does not match registered key/version';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS analysis_run_algorithm_identity_guard ON analysis_run;
CREATE TRIGGER analysis_run_algorithm_identity_guard
BEFORE INSERT OR UPDATE OF school_id, algorithm_key, algorithm_version, algorithm_version_id
ON analysis_run
FOR EACH ROW
EXECUTE FUNCTION resolve_analysis_run_algorithm_identity();

-- Storage evidence: convert already-recorded verification state into an explicit,
-- append-only verification history, and capture future verified location writes.
CREATE UNIQUE INDEX IF NOT EXISTS storage_verification_observation_uk
    ON storage_verification(
        asset_location_id,
        verified_at,
        method,
        COALESCE(checksum_observed, '')
    );
CREATE INDEX IF NOT EXISTS storage_verification_location_time_idx
    ON storage_verification(asset_location_id, verified_at DESC);

INSERT INTO storage_verification(
    asset_location_id,
    verified_at,
    checksum_algorithm,
    checksum_observed,
    availability_status,
    integrity_status,
    method,
    details
)
SELECT
    al.asset_location_id,
    al.last_verified_at,
    a.checksum_algorithm,
    a.checksum_value,
    al.availability_status,
    CASE
        WHEN al.availability_status='available' THEN 'checksum_bound_to_asset_registry'
        ELSE 'availability_not_confirmed'
    END,
    COALESCE(al.verification_method, 'asset_location_state'),
    jsonb_build_object(
        'historical_backfill', true,
        'evidence_source', 'preexisting_asset_location_state',
        'storage_provider', al.storage_provider,
        'locator_version', al.locator_version
    )
FROM asset_location al
JOIN asset a ON a.asset_id=al.asset_id
WHERE al.last_verified_at IS NOT NULL
ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION capture_asset_location_storage_verification()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_checksum_algorithm text;
    v_checksum_value text;
BEGIN
    IF NEW.last_verified_at IS NULL THEN
        RETURN NEW;
    END IF;

    IF TG_OP='UPDATE'
       AND NEW.last_verified_at IS NOT DISTINCT FROM OLD.last_verified_at
       AND NEW.availability_status IS NOT DISTINCT FROM OLD.availability_status
       AND NEW.verification_method IS NOT DISTINCT FROM OLD.verification_method THEN
        RETURN NEW;
    END IF;

    SELECT a.checksum_algorithm, a.checksum_value
      INTO v_checksum_algorithm, v_checksum_value
      FROM asset a
     WHERE a.asset_id=NEW.asset_id;

    IF v_checksum_value IS NULL THEN
        RAISE EXCEPTION 'asset registry checksum missing for verified asset location';
    END IF;

    INSERT INTO storage_verification(
        asset_location_id,
        verified_at,
        checksum_algorithm,
        checksum_observed,
        availability_status,
        integrity_status,
        method,
        details
    ) VALUES (
        NEW.asset_location_id,
        NEW.last_verified_at,
        v_checksum_algorithm,
        v_checksum_value,
        NEW.availability_status,
        CASE
            WHEN NEW.availability_status='available' THEN 'checksum_bound_to_asset_registry'
            ELSE 'availability_not_confirmed'
        END,
        COALESCE(NEW.verification_method, 'asset_location_state'),
        jsonb_build_object(
            'historical_backfill', false,
            'evidence_source', 'asset_location_write',
            'storage_provider', NEW.storage_provider,
            'locator_version', NEW.locator_version
        )
    )
    ON CONFLICT DO NOTHING;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS asset_location_storage_verification_capture ON asset_location;
CREATE TRIGGER asset_location_storage_verification_capture
AFTER INSERT OR UPDATE OF last_verified_at, availability_status, verification_method
ON asset_location
FOR EACH ROW
EXECUTE FUNCTION capture_asset_location_storage_verification();

-- Runtime may append verification evidence but may not rewrite history.
REVOKE UPDATE, DELETE ON storage_verification FROM bridge_school_worker;
GRANT INSERT ON storage_verification TO bridge_school_worker;

INSERT INTO schema_migration(migration_key)
VALUES ('0045_truth_storage_provenance')
ON CONFLICT DO NOTHING;

COMMIT;
