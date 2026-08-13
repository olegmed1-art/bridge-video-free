\set ON_ERROR_STOP on
BEGIN;

-- 0014 correctly exposed the runtime fingerprint, but its migration_checksums signal
-- accidentally counted every migration row as a missing checksum. Keep that original
-- migration immutable in this branch history and correct the read model forward.
ALTER VIEW operational_health_signal RENAME TO operational_health_signal_v14_raw;

CREATE OR REPLACE VIEW operational_health_signal AS
SELECT
    r.school_id,
    r.signal_key,
    r.severity,
    r.current_value,
    r.warning_threshold,
    r.critical_threshold,
    r.oldest_at,
    r.details,
    r.observed_at
FROM operational_health_signal_v14_raw r
WHERE r.signal_key <> 'migration_checksums'

UNION ALL

SELECT
    f.school_id,
    'migration_checksums'::text AS signal_key,
    CASE WHEN f.migration_checksum_missing_count=0 THEN 'ok' ELSE 'critical' END::text AS severity,
    f.migration_checksum_missing_count::numeric AS current_value,
    0::numeric AS warning_threshold,
    0::numeric AS critical_threshold,
    NULL::timestamptz AS oldest_at,
    jsonb_build_object(
        'migration_count',f.migration_count,
        'latest_migration_key',f.latest_migration_key,
        'latest_migration_applied_at',f.latest_migration_applied_at
    ) AS details,
    clock_timestamp() AS observed_at
FROM database_runtime_fingerprint f;

-- Views created in 0014 retain their dependency on the renamed v14 object by OID;
-- explicitly rebind them to the corrected public read model.
CREATE OR REPLACE VIEW operational_health_issue AS
SELECT *
  FROM operational_health_signal
 WHERE severity <> 'ok';

CREATE OR REPLACE VIEW operational_health_summary AS
SELECT
    s.school_id,
    CASE
        WHEN count(*) FILTER (WHERE s.severity='critical') > 0 THEN 'critical'
        WHEN count(*) FILTER (WHERE s.severity='warning') > 0 THEN 'warning'
        ELSE 'ok'
    END::text AS overall_severity,
    count(*) FILTER (WHERE s.severity='critical')::bigint AS critical_signal_count,
    count(*) FILTER (WHERE s.severity='warning')::bigint AS warning_signal_count,
    count(*) FILTER (WHERE s.severity='ok')::bigint AS ok_signal_count,
    max(s.observed_at) AS observed_at
FROM operational_health_signal s
GROUP BY s.school_id;

GRANT SELECT ON TABLE
    operational_health_signal,
    operational_health_issue,
    operational_health_summary
TO bridge_school_reader;

-- The obsolete raw compatibility view remains only because the original issue/summary
-- views depended on it during rename. It is not part of the runtime API.
REVOKE SELECT ON TABLE operational_health_signal_v14_raw
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

INSERT INTO schema_migration(migration_key)
VALUES ('0015_operational_health_checksum_fix')
ON CONFLICT DO NOTHING;

COMMIT;
