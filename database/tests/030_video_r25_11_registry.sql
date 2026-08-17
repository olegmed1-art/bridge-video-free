\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_version uuid;
    v_linked uuid;
BEGIN
    SELECT school_id INTO v_school
      FROM school
     WHERE stable_name='Школа спортивного бриджа';

    SELECT av.algorithm_version_id INTO v_version
      FROM algorithm a
      JOIN algorithm_version av ON av.algorithm_id=a.algorithm_id
     WHERE a.school_id=v_school
       AND a.stable_key='bridge-video-master-analysis'
       AND av.version_label='3.1-free-r25.11'
       AND av.status='candidate';

    IF v_version IS NULL THEN
        RAISE EXCEPTION 'r25.11 candidate identity is not registered';
    END IF;

    INSERT INTO analysis_run(
        school_id,
        algorithm_key,
        algorithm_version,
        completed_at,
        run_status
    ) VALUES (
        v_school,
        'bridge-video-master-analysis',
        '3.1-free-r25.11',
        now(),
        'success'
    ) RETURNING algorithm_version_id INTO v_linked;

    IF v_linked IS DISTINCT FROM v_version THEN
        RAISE EXCEPTION 'r25.11 AnalysisRun did not resolve registered candidate identity';
    END IF;
END $$;

ROLLBACK;
