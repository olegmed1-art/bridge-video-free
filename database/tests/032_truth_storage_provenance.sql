\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_algorithm uuid;
    v_version uuid;
    v_run uuid;
    v_asset uuid;
    v_location uuid;
    v_count integer;
    v_linked uuid;
BEGIN
    SELECT school_id INTO v_school
      FROM school
     WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN
        RAISE EXCEPTION 'canonical school missing';
    END IF;

    SELECT algorithm_id INTO v_algorithm
      FROM algorithm
     WHERE school_id=v_school
       AND stable_key='bridge-video-master-analysis';
    IF v_algorithm IS NULL THEN
        RAISE EXCEPTION 'Bridge Video algorithm registry row missing';
    END IF;

    SELECT count(*) INTO v_count
      FROM algorithm_version
     WHERE algorithm_id=v_algorithm
       AND version_label IN (
           '3.1-free-master-analysis-r5',
           '3.1-free-master-analysis-r7',
           '3.1-free-r25.1',
           '3.1-free-r25.3',
           '3.1-free-r25.4',
           '3.1-free-r25.4.1',
           '3.1-free-r25.6',
           '3.1-free-r25.10',
           '3.1-free-r25.11'
       );
    IF v_count <> 9 THEN
        RAISE EXCEPTION 'expected nine registered Bridge Video version identities, got %', v_count;
    END IF;

    SELECT algorithm_version_id INTO v_version
      FROM algorithm_version
     WHERE algorithm_id=v_algorithm
       AND version_label='3.1-free-r25.11';

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
    ) RETURNING analysis_run_id, algorithm_version_id INTO v_run, v_linked;

    IF v_linked IS DISTINCT FROM v_version THEN
        RAISE EXCEPTION 'AnalysisRun did not resolve canonical AlgorithmVersion';
    END IF;

    BEGIN
        INSERT INTO analysis_run(
            school_id,
            algorithm_key,
            algorithm_version,
            completed_at,
            run_status
        ) VALUES (
            v_school,
            'bridge-video-master-analysis',
            'unregistered-test-revision',
            now(),
            'success'
        );
        RAISE EXCEPTION 'unregistered Bridge Video revision unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='unregistered Bridge Video revision unexpectedly accepted' THEN
            RAISE;
        END IF;
    END;

    BEGIN
        INSERT INTO analysis_run(
            school_id,
            algorithm_key,
            algorithm_version,
            algorithm_version_id,
            completed_at,
            run_status
        ) VALUES (
            v_school,
            'bridge-video-master-analysis',
            '3.1-free-r25.10',
            v_version,
            now(),
            'success'
        );
        RAISE EXCEPTION 'mismatched AlgorithmVersion identity unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='mismatched AlgorithmVersion identity unexpectedly accepted' THEN
            RAISE;
        END IF;
    END;

    INSERT INTO asset(
        school_id,
        asset_type,
        mime_type,
        byte_size,
        checksum_algorithm,
        checksum_value,
        immutable_flag
    ) VALUES (
        v_school,
        'truth_storage_test',
        'application/octet-stream',
        1,
        'sha256',
        repeat('a',64),
        true
    ) RETURNING asset_id INTO v_asset;

    INSERT INTO asset_location(
        asset_id,
        storage_provider,
        locator,
        last_verified_at,
        availability_status,
        verification_method
    ) VALUES (
        v_asset,
        'test_provider',
        'test:truth-storage-provenance',
        clock_timestamp(),
        'available',
        'test_verifier'
    ) RETURNING asset_location_id INTO v_location;

    SELECT count(*) INTO v_count
      FROM storage_verification
     WHERE asset_location_id=v_location
       AND checksum_algorithm='sha256'
       AND checksum_observed=repeat('a',64)
       AND availability_status='available'
       AND integrity_status='checksum_bound_to_asset_registry'
       AND method='test_verifier';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'initial storage verification evidence expected one row, got %', v_count;
    END IF;

    UPDATE asset_location
       SET last_verified_at=last_verified_at + interval '1 second'
     WHERE asset_location_id=v_location;

    SELECT count(*) INTO v_count
      FROM storage_verification
     WHERE asset_location_id=v_location;
    IF v_count <> 2 THEN
        RAISE EXCEPTION 'verification update should append evidence, got % rows', v_count;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT has_table_privilege('bridge_school_worker','storage_verification','INSERT') THEN
        RAISE EXCEPTION 'worker lacks append permission for storage verification evidence';
    END IF;
    IF has_table_privilege('bridge_school_worker','storage_verification','UPDATE')
       OR has_table_privilege('bridge_school_worker','storage_verification','DELETE') THEN
        RAISE EXCEPTION 'worker can mutate append-only storage verification evidence';
    END IF;
END $$;

ROLLBACK;
