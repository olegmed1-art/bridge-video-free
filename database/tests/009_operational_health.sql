\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_asset uuid;
    v_projection_request uuid;
    v_server_severity text;
    v_migration_severity text;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'school seed missing'; END IF;

    IF NOT EXISTS (
        SELECT 1 FROM operational_health_policy WHERE school_id=v_school AND enabled
    ) THEN
        RAISE EXCEPTION 'operational health policy seed missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM database_runtime_fingerprint
         WHERE school_id=v_school
           AND server_version_num >= 180000
           AND migration_count >= 15
           AND latest_migration_key='0015_operational_health_checksum_fix'
           AND migration_checksum_missing_count=0
    ) THEN
        RAISE EXCEPTION 'database runtime fingerprint is incomplete or unhealthy';
    END IF;

    -- A clean signal registry must expose the expected technical dimensions.
    IF (SELECT count(*) FROM operational_health_signal WHERE school_id=v_school) <> 15 THEN
        RAISE EXCEPTION 'unexpected operational health signal count';
    END IF;

    SELECT severity INTO v_server_severity
      FROM operational_health_signal
     WHERE school_id=v_school AND signal_key='database_server_version';
    IF v_server_severity IS DISTINCT FROM 'ok' THEN
        RAISE EXCEPTION 'database_server_version baseline severity is %, expected ok', COALESCE(v_server_severity,'missing');
    END IF;

    SELECT severity INTO v_migration_severity
      FROM operational_health_signal
     WHERE school_id=v_school AND signal_key='migration_checksums';
    IF v_migration_severity IS DISTINCT FROM 'ok' THEN
        RAISE EXCEPTION 'migration_checksums baseline severity is %, expected ok', COALESCE(v_migration_severity,'missing');
    END IF;

    -- Stuck transaction/change command.
    INSERT INTO changeset(
        school_id, started_at, status, expected_aggregate_versions
    ) VALUES (
        v_school, now()-interval '2 hours', 'started', '{}'::jsonb
    );

    -- Long-running analysis.
    INSERT INTO analysis_run(
        school_id, algorithm_key, algorithm_version, started_at, run_status
    ) VALUES (
        v_school, 'ci-health-analysis', '1', now()-interval '13 hours', 'running'
    );

    -- Pending recompute that has exceeded the technical critical threshold.
    INSERT INTO projection_recompute_request(
        school_id, projection_key, scope_key, priority, status, requested_at
    ) VALUES (
        v_school, 'ci-health-projection', 'ci-health', 100, 'pending', now()-interval '3 hours'
    ) RETURNING projection_recompute_request_id INTO v_projection_request;

    -- Unresolved external reference.
    INSERT INTO pending_reference(
        school_id, target_namespace, target_key, expected_target_type,
        first_seen_at, last_seen_at, retry_count, status
    ) VALUES (
        v_school, 'ci-health', 'ci-health-pending', 'student',
        now()-interval '10 days', now()-interval '1 hour', 5, 'pending'
    );

    -- Explicitly unavailable storage location. Unknown/unverified locations are not
    -- classified as failure by this health layer; only provider-confirmed unavailable.
    INSERT INTO asset(
        school_id, asset_type, mime_type, checksum_algorithm, checksum_value
    ) VALUES (
        v_school, 'ci-health', 'application/octet-stream', 'sha256',
        'ci-health-asset-000000000000000000000000000000000000000000000000000000'
    ) RETURNING asset_id INTO v_asset;
    INSERT INTO asset_location(
        asset_id, storage_provider, locator, availability_status,
        unavailable_since, verification_method, status
    ) VALUES (
        v_asset, 'ci-provider', 'ci://health/unavailable', 'unavailable',
        now()-interval '1 hour', 'ci', 'active'
    );

    IF NOT EXISTS (
        SELECT 1 FROM operational_health_signal
         WHERE school_id=v_school AND signal_key='changeset_started_age' AND severity='critical'
           AND current_value >= 3600
    ) THEN
        RAISE EXCEPTION 'stuck changeset was not classified critical';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM operational_health_signal
         WHERE school_id=v_school AND signal_key='analysis_running_age' AND severity='critical'
    ) THEN
        RAISE EXCEPTION 'stuck analysis was not classified critical';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM operational_health_signal
         WHERE school_id=v_school AND signal_key='recompute_pending_age' AND severity='critical'
           AND (details->>'count')::integer >= 1
    ) THEN
        RAISE EXCEPTION 'stale recompute backlog was not classified critical';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM operational_health_signal
         WHERE school_id=v_school AND signal_key='pending_reference_age' AND severity='critical'
           AND (details->>'max_retry_count')::integer >= 5
    ) THEN
        RAISE EXCEPTION 'old pending reference was not classified critical';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM operational_health_signal
         WHERE school_id=v_school AND signal_key='asset_location_unavailable' AND severity='critical'
    ) THEN
        RAISE EXCEPTION 'explicit unavailable asset location was not classified critical';
    END IF;

    IF (SELECT count(*) FROM operational_health_issue WHERE school_id=v_school AND severity='critical') < 5 THEN
        RAISE EXCEPTION 'operational health issue view omitted critical signals';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM operational_health_summary
         WHERE school_id=v_school AND overall_severity='critical' AND critical_signal_count >= 5
    ) THEN
        RAISE EXCEPTION 'operational health summary did not roll up critical status';
    END IF;

    -- Runtime can diagnose health but cannot hide problems by changing policy.
    IF NOT has_table_privilege('bridge_school_reader','operational_health_policy','SELECT')
       OR NOT has_table_privilege('bridge_school_reader','operational_health_signal','SELECT')
       OR NOT has_table_privilege('bridge_school_reader','operational_health_summary','SELECT') THEN
        RAISE EXCEPTION 'reader lacks operational health visibility';
    END IF;

    IF has_table_privilege('bridge_school_reader','operational_health_policy','UPDATE')
       OR has_table_privilege('bridge_school_app','operational_health_policy','UPDATE')
       OR has_table_privilege('bridge_school_worker','operational_health_policy','UPDATE')
       OR has_table_privilege('bridge_school_worker','operational_health_policy','INSERT') THEN
        RAISE EXCEPTION 'runtime can mutate operational health thresholds';
    END IF;
END $$;

ROLLBACK;
