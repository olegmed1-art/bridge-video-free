\set ON_ERROR_STOP on
BEGIN;

-- r25.11 exists in the current repository as a tested candidate revision. Registering
-- its identity allows provenance linkage; this is not a Stable/Operational promotion.
WITH target_algorithm AS (
    SELECT a.algorithm_id
      FROM algorithm a
      JOIN school s ON s.school_id=a.school_id
     WHERE s.stable_name='Школа спортивного бриджа'
       AND a.stable_key='bridge-video-master-analysis'
)
INSERT INTO algorithm_version(
    algorithm_id,
    version_no,
    version_label,
    configuration,
    status
)
SELECT
    algorithm_id,
    9,
    '3.1-free-r25.11',
    jsonb_build_object(
        'registration_basis','repository_candidate',
        'runtime_module','bridge_runtime_hardening_r25_11.py'
    ),
    'candidate'
FROM target_algorithm
ON CONFLICT (algorithm_id, version_no) DO NOTHING;

DO $$
DECLARE
    v_count integer;
BEGIN
    SELECT count(*) INTO v_count
      FROM algorithm a
      JOIN algorithm_version av ON av.algorithm_id=a.algorithm_id
      JOIN school s ON s.school_id=a.school_id
     WHERE s.stable_name='Школа спортивного бриджа'
       AND a.stable_key='bridge-video-master-analysis'
       AND av.version_no=9
       AND av.version_label='3.1-free-r25.11';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'r25.11 AlgorithmVersion registration missing or conflicting';
    END IF;
END $$;

INSERT INTO schema_migration(migration_key)
VALUES ('0044_register_video_r25_11')
ON CONFLICT DO NOTHING;

COMMIT;
