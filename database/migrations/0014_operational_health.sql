\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Operational health policy is technical configuration, not teaching methodology.
-- Runtime services may read it but cannot change thresholds. Values are deliberately
-- conservative defaults for an early low-volume deployment and remain owner-versioned
-- configuration rather than hidden constants in application code.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS operational_health_policy (
    school_id uuid PRIMARY KEY REFERENCES school(school_id),
    enabled boolean NOT NULL DEFAULT true,
    changeset_warn_after interval NOT NULL DEFAULT interval '15 minutes',
    changeset_critical_after interval NOT NULL DEFAULT interval '1 hour',
    outbox_warn_after interval NOT NULL DEFAULT interval '10 minutes',
    outbox_critical_after interval NOT NULL DEFAULT interval '30 minutes',
    ingestion_warn_after interval NOT NULL DEFAULT interval '2 hours',
    ingestion_critical_after interval NOT NULL DEFAULT interval '6 hours',
    analysis_warn_after interval NOT NULL DEFAULT interval '4 hours',
    analysis_critical_after interval NOT NULL DEFAULT interval '12 hours',
    projection_warn_after interval NOT NULL DEFAULT interval '1 hour',
    projection_critical_after interval NOT NULL DEFAULT interval '4 hours',
    publication_warn_after interval NOT NULL DEFAULT interval '1 hour',
    publication_critical_after interval NOT NULL DEFAULT interval '6 hours',
    recompute_pending_warn_after interval NOT NULL DEFAULT interval '30 minutes',
    recompute_pending_critical_after interval NOT NULL DEFAULT interval '2 hours',
    recompute_running_warn_after interval NOT NULL DEFAULT interval '2 hours',
    recompute_running_critical_after interval NOT NULL DEFAULT interval '6 hours',
    stale_profile_warn_after interval NOT NULL DEFAULT interval '30 minutes',
    stale_profile_critical_after interval NOT NULL DEFAULT interval '6 hours',
    pending_reference_warn_after interval NOT NULL DEFAULT interval '1 day',
    pending_reference_critical_after interval NOT NULL DEFAULT interval '7 days',
    updated_at timestamptz NOT NULL DEFAULT now(),
    notes text,
    CHECK (changeset_critical_after >= changeset_warn_after),
    CHECK (outbox_critical_after >= outbox_warn_after),
    CHECK (ingestion_critical_after >= ingestion_warn_after),
    CHECK (analysis_critical_after >= analysis_warn_after),
    CHECK (projection_critical_after >= projection_warn_after),
    CHECK (publication_critical_after >= publication_warn_after),
    CHECK (recompute_pending_critical_after >= recompute_pending_warn_after),
    CHECK (recompute_running_critical_after >= recompute_running_warn_after),
    CHECK (stale_profile_critical_after >= stale_profile_warn_after),
    CHECK (pending_reference_critical_after >= pending_reference_warn_after)
);

INSERT INTO operational_health_policy(school_id, notes)
SELECT school_id, 'Initial technical thresholds; review after real production load is observed.'
  FROM school
 WHERE stable_name='Школа спортивного бриджа'
ON CONFLICT (school_id) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Runtime fingerprint. Repository-to-database checksum drift is intentionally enforced
-- by migrate.sh because PostgreSQL cannot see repository file bytes. This view exposes
-- the database half of that contract plus the actual server/database identity.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW database_runtime_fingerprint AS
SELECT
    s.school_id,
    s.stable_name AS school_name,
    current_database() AS database_name,
    current_setting('server_version') AS server_version,
    current_setting('server_version_num')::bigint AS server_version_num,
    (SELECT count(*) FROM schema_migration) AS migration_count,
    (SELECT max(migration_key) FROM schema_migration) AS latest_migration_key,
    (SELECT max(applied_at) FROM schema_migration) AS latest_migration_applied_at,
    (SELECT count(*) FROM schema_migration WHERE checksum IS NULL) AS migration_checksum_missing_count,
    clock_timestamp() AS observed_at
FROM school s;

-- -----------------------------------------------------------------------------
-- One row per technical health signal. `current_value` is a count for count signals
-- and seconds of age for age signals. `details` contains enough context for diagnosis
-- without exposing secrets or source payloads.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW operational_health_signal AS
WITH policy AS (
    SELECT
        s.school_id,
        COALESCE(p.enabled, true) AS enabled,
        COALESCE(p.changeset_warn_after, interval '15 minutes') AS changeset_warn_after,
        COALESCE(p.changeset_critical_after, interval '1 hour') AS changeset_critical_after,
        COALESCE(p.outbox_warn_after, interval '10 minutes') AS outbox_warn_after,
        COALESCE(p.outbox_critical_after, interval '30 minutes') AS outbox_critical_after,
        COALESCE(p.ingestion_warn_after, interval '2 hours') AS ingestion_warn_after,
        COALESCE(p.ingestion_critical_after, interval '6 hours') AS ingestion_critical_after,
        COALESCE(p.analysis_warn_after, interval '4 hours') AS analysis_warn_after,
        COALESCE(p.analysis_critical_after, interval '12 hours') AS analysis_critical_after,
        COALESCE(p.projection_warn_after, interval '1 hour') AS projection_warn_after,
        COALESCE(p.projection_critical_after, interval '4 hours') AS projection_critical_after,
        COALESCE(p.publication_warn_after, interval '1 hour') AS publication_warn_after,
        COALESCE(p.publication_critical_after, interval '6 hours') AS publication_critical_after,
        COALESCE(p.recompute_pending_warn_after, interval '30 minutes') AS recompute_pending_warn_after,
        COALESCE(p.recompute_pending_critical_after, interval '2 hours') AS recompute_pending_critical_after,
        COALESCE(p.recompute_running_warn_after, interval '2 hours') AS recompute_running_warn_after,
        COALESCE(p.recompute_running_critical_after, interval '6 hours') AS recompute_running_critical_after,
        COALESCE(p.stale_profile_warn_after, interval '30 minutes') AS stale_profile_warn_after,
        COALESCE(p.stale_profile_critical_after, interval '6 hours') AS stale_profile_critical_after,
        COALESCE(p.pending_reference_warn_after, interval '1 day') AS pending_reference_warn_after,
        COALESCE(p.pending_reference_critical_after, interval '7 days') AS pending_reference_critical_after
      FROM school s
      LEFT JOIN operational_health_policy p ON p.school_id=s.school_id
),
changeset_state AS (
    SELECT school_id, count(*) AS item_count, min(started_at) AS oldest_at
      FROM changeset
     WHERE status='started'
     GROUP BY school_id
),
outbox_pending_state AS (
    SELECT c.school_id, count(*) AS item_count, min(o.created_at) AS oldest_at
      FROM outbox_message o
      JOIN changeset c ON c.changeset_id=o.changeset_id
     WHERE o.status IN ('pending','publishing')
     GROUP BY c.school_id
),
outbox_failed_state AS (
    SELECT c.school_id, count(*) AS item_count, min(o.created_at) AS oldest_at,
           max(o.attempt_count) AS max_attempt_count
      FROM outbox_message o
      JOIN changeset c ON c.changeset_id=o.changeset_id
     WHERE o.status='failed'
     GROUP BY c.school_id
),
ingestion_state AS (
    SELECT school_id, count(*) AS item_count, min(started_at) AS oldest_at
      FROM ingestion_run
     WHERE status='running'
     GROUP BY school_id
),
analysis_state AS (
    SELECT school_id, count(*) AS item_count, min(started_at) AS oldest_at
      FROM analysis_run
     WHERE run_status='running'
     GROUP BY school_id
),
projection_state AS (
    SELECT school_id, count(*) AS item_count, min(started_at) AS oldest_at
      FROM projection_run
     WHERE status='running'
     GROUP BY school_id
),
publication_state AS (
    SELECT school_id, count(*) AS item_count, min(created_at) AS oldest_at
      FROM output_publication
     WHERE status IN ('staging','validated')
     GROUP BY school_id
),
recompute_pending_state AS (
    SELECT school_id, count(*) AS item_count, min(requested_at) AS oldest_at
      FROM projection_recompute_request
     WHERE status='pending'
     GROUP BY school_id
),
recompute_running_state AS (
    SELECT school_id, count(*) AS item_count, min(claimed_at) AS oldest_at
      FROM projection_recompute_request
     WHERE status='running'
     GROUP BY school_id
),
recompute_failed_state AS (
    SELECT school_id, count(*) AS item_count, max(completed_at) AS latest_at
      FROM projection_recompute_request
     WHERE status='failed'
       AND EXISTS (
            SELECT 1
              FROM projection_recompute_cause c
              JOIN invalidation_record ir ON ir.invalidation_id=c.invalidation_id
             WHERE c.projection_recompute_request_id=projection_recompute_request.projection_recompute_request_id
               AND ir.recomputation_status='failed'
       )
     GROUP BY school_id
),
stale_profile_state AS (
    SELECT school_id, count(*) AS item_count,
           min(COALESCE(stale_from, latest_state_recorded_at)) AS oldest_at
      FROM current_student_profile_status
     WHERE latest_state='stale'
     GROUP BY school_id
),
pending_reference_state AS (
    SELECT school_id, count(*) AS item_count, min(first_seen_at) AS oldest_at,
           max(retry_count) AS max_retry_count
      FROM pending_reference
     WHERE status='pending'
     GROUP BY school_id
),
unavailable_asset_state AS (
    SELECT a.school_id, count(*) AS item_count, min(al.unavailable_since) AS oldest_at
      FROM asset_location al
      JOIN asset a ON a.asset_id=al.asset_id
     WHERE al.status='active'
       AND al.availability_status='unavailable'
     GROUP BY a.school_id
),
migration_state AS (
    SELECT count(*) AS missing_checksum_count,
           count(*) AS migration_count,
           max(migration_key) AS latest_migration_key
      FROM schema_migration
)
SELECT * FROM (
    SELECT
        p.school_id,
        'database_server_version'::text AS signal_key,
        CASE WHEN current_setting('server_version_num')::bigint >= 180000 THEN 'ok' ELSE 'critical' END::text AS severity,
        current_setting('server_version_num')::numeric AS current_value,
        180000::numeric AS warning_threshold,
        180000::numeric AS critical_threshold,
        NULL::timestamptz AS oldest_at,
        jsonb_build_object('server_version',current_setting('server_version'),'database',current_database()) AS details,
        clock_timestamp() AS observed_at
      FROM policy p WHERE p.enabled

    UNION ALL
    SELECT
        p.school_id,
        'migration_checksums'::text,
        CASE WHEN m.missing_checksum_count=0 THEN 'ok' ELSE 'critical' END,
        m.missing_checksum_count::numeric,
        0::numeric,
        0::numeric,
        NULL::timestamptz,
        jsonb_build_object('migration_count',m.migration_count,'latest_migration_key',m.latest_migration_key),
        clock_timestamp()
      FROM policy p CROSS JOIN migration_state m WHERE p.enabled

    UNION ALL
    SELECT
        p.school_id,
        'changeset_started_age'::text,
        CASE
            WHEN c.oldest_at IS NULL THEN 'ok'
            WHEN clock_timestamp()-c.oldest_at >= p.changeset_critical_after THEN 'critical'
            WHEN clock_timestamp()-c.oldest_at >= p.changeset_warn_after THEN 'warning'
            ELSE 'ok'
        END,
        COALESCE(EXTRACT(EPOCH FROM (clock_timestamp()-c.oldest_at)),0)::numeric,
        EXTRACT(EPOCH FROM p.changeset_warn_after)::numeric,
        EXTRACT(EPOCH FROM p.changeset_critical_after)::numeric,
        c.oldest_at,
        jsonb_build_object('count',COALESCE(c.item_count,0)),
        clock_timestamp()
      FROM policy p LEFT JOIN changeset_state c USING (school_id) WHERE p.enabled

    UNION ALL
    SELECT
        p.school_id,
        'outbox_pending_age'::text,
        CASE
            WHEN o.oldest_at IS NULL THEN 'ok'
            WHEN clock_timestamp()-o.oldest_at >= p.outbox_critical_after THEN 'critical'
            WHEN clock_timestamp()-o.oldest_at >= p.outbox_warn_after THEN 'warning'
            ELSE 'ok'
        END,
        COALESCE(EXTRACT(EPOCH FROM (clock_timestamp()-o.oldest_at)),0)::numeric,
        EXTRACT(EPOCH FROM p.outbox_warn_after)::numeric,
        EXTRACT(EPOCH FROM p.outbox_critical_after)::numeric,
        o.oldest_at,
        jsonb_build_object('count',COALESCE(o.item_count,0)),
        clock_timestamp()
      FROM policy p LEFT JOIN outbox_pending_state o USING (school_id) WHERE p.enabled

    UNION ALL
    SELECT
        p.school_id,
        'outbox_failed'::text,
        CASE WHEN COALESCE(o.item_count,0)=0 THEN 'ok' ELSE 'critical' END,
        COALESCE(o.item_count,0)::numeric,
        0::numeric,
        0::numeric,
        o.oldest_at,
        jsonb_build_object('count',COALESCE(o.item_count,0),'max_attempt_count',COALESCE(o.max_attempt_count,0)),
        clock_timestamp()
      FROM policy p LEFT JOIN outbox_failed_state o USING (school_id) WHERE p.enabled

    UNION ALL
    SELECT
        p.school_id,
        'ingestion_running_age'::text,
        CASE
            WHEN s.oldest_at IS NULL THEN 'ok'
            WHEN clock_timestamp()-s.oldest_at >= p.ingestion_critical_after THEN 'critical'
            WHEN clock_timestamp()-s.oldest_at >= p.ingestion_warn_after THEN 'warning'
            ELSE 'ok'
        END,
        COALESCE(EXTRACT(EPOCH FROM (clock_timestamp()-s.oldest_at)),0)::numeric,
        EXTRACT(EPOCH FROM p.ingestion_warn_after)::numeric,
        EXTRACT(EPOCH FROM p.ingestion_critical_after)::numeric,
        s.oldest_at,
        jsonb_build_object('count',COALESCE(s.item_count,0)),
        clock_timestamp()
      FROM policy p LEFT JOIN ingestion_state s USING (school_id) WHERE p.enabled

    UNION ALL
    SELECT
        p.school_id,
        'analysis_running_age'::text,
        CASE
            WHEN s.oldest_at IS NULL THEN 'ok'
            WHEN clock_timestamp()-s.oldest_at >= p.analysis_critical_after THEN 'critical'
            WHEN clock_timestamp()-s.oldest_at >= p.analysis_warn_after THEN 'warning'
            ELSE 'ok'
        END,
        COALESCE(EXTRACT(EPOCH FROM (clock_timestamp()-s.oldest_at)),0)::numeric,
        EXTRACT(EPOCH FROM p.analysis_warn_after)::numeric,
        EXTRACT(EPOCH FROM p.analysis_critical_after)::numeric,
        s.oldest_at,
        jsonb_build_object('count',COALESCE(s.item_count,0)),
        clock_timestamp()
      FROM policy p LEFT JOIN analysis_state s USING (school_id) WHERE p.enabled

    UNION ALL
    SELECT
        p.school_id,
        'projection_running_age'::text,
        CASE
            WHEN s.oldest_at IS NULL THEN 'ok'
            WHEN clock_timestamp()-s.oldest_at >= p.projection_critical_after THEN 'critical'
            WHEN clock_timestamp()-s.oldest_at >= p.projection_warn_after THEN 'warning'
            ELSE 'ok'
        END,
        COALESCE(EXTRACT(EPOCH FROM (clock_timestamp()-s.oldest_at)),0)::numeric,
        EXTRACT(EPOCH FROM p.projection_warn_after)::numeric,
        EXTRACT(EPOCH FROM p.projection_critical_after)::numeric,
        s.oldest_at,
        jsonb_build_object('count',COALESCE(s.item_count,0)),
        clock_timestamp()
      FROM policy p LEFT JOIN projection_state s USING (school_id) WHERE p.enabled

    UNION ALL
    SELECT
        p.school_id,
        'publication_staging_age'::text,
        CASE
            WHEN s.oldest_at IS NULL THEN 'ok'
            WHEN clock_timestamp()-s.oldest_at >= p.publication_critical_after THEN 'critical'
            WHEN clock_timestamp()-s.oldest_at >= p.publication_warn_after THEN 'warning'
            ELSE 'ok'
        END,
        COALESCE(EXTRACT(EPOCH FROM (clock_timestamp()-s.oldest_at)),0)::numeric,
        EXTRACT(EPOCH FROM p.publication_warn_after)::numeric,
        EXTRACT(EPOCH FROM p.publication_critical_after)::numeric,
        s.oldest_at,
        jsonb_build_object('count',COALESCE(s.item_count,0)),
        clock_timestamp()
      FROM policy p LEFT JOIN publication_state s USING (school_id) WHERE p.enabled

    UNION ALL
    SELECT
        p.school_id,
        'recompute_pending_age'::text,
        CASE
            WHEN s.oldest_at IS NULL THEN 'ok'
            WHEN clock_timestamp()-s.oldest_at >= p.recompute_pending_critical_after THEN 'critical'
            WHEN clock_timestamp()-s.oldest_at >= p.recompute_pending_warn_after THEN 'warning'
            ELSE 'ok'
        END,
        COALESCE(EXTRACT(EPOCH FROM (clock_timestamp()-s.oldest_at)),0)::numeric,
        EXTRACT(EPOCH FROM p.recompute_pending_warn_after)::numeric,
        EXTRACT(EPOCH FROM p.recompute_pending_critical_after)::numeric,
        s.oldest_at,
        jsonb_build_object('count',COALESCE(s.item_count,0)),
        clock_timestamp()
      FROM policy p LEFT JOIN recompute_pending_state s USING (school_id) WHERE p.enabled

    UNION ALL
    SELECT
        p.school_id,
        'recompute_running_age'::text,
        CASE
            WHEN s.oldest_at IS NULL THEN 'ok'
            WHEN clock_timestamp()-s.oldest_at >= p.recompute_running_critical_after THEN 'critical'
            WHEN clock_timestamp()-s.oldest_at >= p.recompute_running_warn_after THEN 'warning'
            ELSE 'ok'
        END,
        COALESCE(EXTRACT(EPOCH FROM (clock_timestamp()-s.oldest_at)),0)::numeric,
        EXTRACT(EPOCH FROM p.recompute_running_warn_after)::numeric,
        EXTRACT(EPOCH FROM p.recompute_running_critical_after)::numeric,
        s.oldest_at,
        jsonb_build_object('count',COALESCE(s.item_count,0)),
        clock_timestamp()
      FROM policy p LEFT JOIN recompute_running_state s USING (school_id) WHERE p.enabled

    UNION ALL
    SELECT
        p.school_id,
        'recompute_failed_unresolved'::text,
        CASE WHEN COALESCE(s.item_count,0)=0 THEN 'ok' ELSE 'critical' END,
        COALESCE(s.item_count,0)::numeric,
        0::numeric,
        0::numeric,
        s.latest_at,
        jsonb_build_object('count',COALESCE(s.item_count,0)),
        clock_timestamp()
      FROM policy p LEFT JOIN recompute_failed_state s USING (school_id) WHERE p.enabled

    UNION ALL
    SELECT
        p.school_id,
        'current_profile_stale_age'::text,
        CASE
            WHEN s.oldest_at IS NULL THEN 'ok'
            WHEN clock_timestamp()-s.oldest_at >= p.stale_profile_critical_after THEN 'critical'
            WHEN clock_timestamp()-s.oldest_at >= p.stale_profile_warn_after THEN 'warning'
            ELSE 'ok'
        END,
        COALESCE(EXTRACT(EPOCH FROM (clock_timestamp()-s.oldest_at)),0)::numeric,
        EXTRACT(EPOCH FROM p.stale_profile_warn_after)::numeric,
        EXTRACT(EPOCH FROM p.stale_profile_critical_after)::numeric,
        s.oldest_at,
        jsonb_build_object('count',COALESCE(s.item_count,0)),
        clock_timestamp()
      FROM policy p LEFT JOIN stale_profile_state s USING (school_id) WHERE p.enabled

    UNION ALL
    SELECT
        p.school_id,
        'pending_reference_age'::text,
        CASE
            WHEN s.oldest_at IS NULL THEN 'ok'
            WHEN clock_timestamp()-s.oldest_at >= p.pending_reference_critical_after THEN 'critical'
            WHEN clock_timestamp()-s.oldest_at >= p.pending_reference_warn_after THEN 'warning'
            ELSE 'ok'
        END,
        COALESCE(EXTRACT(EPOCH FROM (clock_timestamp()-s.oldest_at)),0)::numeric,
        EXTRACT(EPOCH FROM p.pending_reference_warn_after)::numeric,
        EXTRACT(EPOCH FROM p.pending_reference_critical_after)::numeric,
        s.oldest_at,
        jsonb_build_object('count',COALESCE(s.item_count,0),'max_retry_count',COALESCE(s.max_retry_count,0)),
        clock_timestamp()
      FROM policy p LEFT JOIN pending_reference_state s USING (school_id) WHERE p.enabled

    UNION ALL
    SELECT
        p.school_id,
        'asset_location_unavailable'::text,
        CASE WHEN COALESCE(s.item_count,0)=0 THEN 'ok' ELSE 'critical' END,
        COALESCE(s.item_count,0)::numeric,
        0::numeric,
        0::numeric,
        s.oldest_at,
        jsonb_build_object('count',COALESCE(s.item_count,0)),
        clock_timestamp()
      FROM policy p LEFT JOIN unavailable_asset_state s USING (school_id) WHERE p.enabled
) signals;

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

-- Operational diagnosis should be readable by all runtime capabilities but writable only
-- by the migration/admin owner. This also prevents a compromised worker from hiding a
-- queue problem by relaxing thresholds.
GRANT SELECT ON TABLE
    operational_health_policy,
    database_runtime_fingerprint,
    operational_health_signal,
    operational_health_issue,
    operational_health_summary
TO bridge_school_reader;

REVOKE INSERT, UPDATE, DELETE ON TABLE operational_health_policy
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

INSERT INTO schema_migration(migration_key)
VALUES ('0014_operational_health')
ON CONFLICT DO NOTHING;

COMMIT;
