\set ON_ERROR_STOP on

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS school (
    school_id uuid PRIMARY KEY DEFAULT uuidv7(),
    stable_name text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    policy_scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT school_stable_name_uk UNIQUE (stable_name)
);

CREATE TABLE IF NOT EXISTS object_registry (
    object_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid REFERENCES school(school_id),
    entity_type text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT 'active',
    privacy_class text NOT NULL DEFAULT 'school_internal',
    retention_class text,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS object_registry_school_type_idx ON object_registry(school_id, entity_type, created_at DESC);

CREATE TABLE IF NOT EXISTS source (
    source_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    source_type text NOT NULL,
    title text,
    author_owner text,
    source_date timestamptz,
    canonical_locator text,
    trust_class text,
    rights_notes text,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS source_school_type_idx ON source(school_id, source_type, created_at DESC);

CREATE TABLE IF NOT EXISTS asset (
    asset_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    asset_type text NOT NULL,
    mime_type text,
    byte_size bigint CHECK (byte_size IS NULL OR byte_size >= 0),
    checksum_algorithm text NOT NULL DEFAULT 'sha256',
    checksum_value text NOT NULL,
    immutable_flag boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT asset_content_uk UNIQUE (checksum_algorithm, checksum_value)
);

CREATE TABLE IF NOT EXISTS source_asset (
    source_id uuid NOT NULL REFERENCES source(source_id),
    asset_id uuid NOT NULL REFERENCES asset(asset_id),
    relation_type text NOT NULL DEFAULT 'embodies',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, asset_id, relation_type)
);

CREATE TABLE IF NOT EXISTS asset_location (
    asset_location_id uuid PRIMARY KEY DEFAULT uuidv7(),
    asset_id uuid NOT NULL REFERENCES asset(asset_id),
    storage_provider text NOT NULL,
    locator text NOT NULL,
    locator_version bigint NOT NULL DEFAULT 1,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_verified_at timestamptz,
    availability_status text NOT NULL DEFAULT 'unknown',
    unavailable_since timestamptz,
    verification_method text,
    status text NOT NULL DEFAULT 'active',
    CONSTRAINT asset_location_provider_locator_uk UNIQUE (storage_provider, locator, locator_version)
);
CREATE INDEX IF NOT EXISTS asset_location_asset_idx ON asset_location(asset_id, status, last_verified_at DESC);

CREATE TABLE IF NOT EXISTS storage_verification (
    storage_verification_id uuid PRIMARY KEY DEFAULT uuidv7(),
    asset_location_id uuid NOT NULL REFERENCES asset_location(asset_location_id),
    verified_at timestamptz NOT NULL DEFAULT now(),
    checksum_algorithm text,
    checksum_observed text,
    availability_status text NOT NULL,
    integrity_status text NOT NULL,
    method text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS changeset (
    changeset_id uuid PRIMARY KEY DEFAULT uuidv7(),
    command_id uuid NOT NULL DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    actor_id uuid,
    started_at timestamptz NOT NULL DEFAULT now(),
    committed_at timestamptz,
    status text NOT NULL DEFAULT 'started',
    expected_aggregate_versions jsonb NOT NULL DEFAULT '{}'::jsonb,
    correlation_id uuid NOT NULL DEFAULT uuidv7(),
    CONSTRAINT changeset_command_uk UNIQUE (school_id, command_id),
    CONSTRAINT changeset_status_ck CHECK (status IN ('started','committed','failed','cancelled')),
    CONSTRAINT changeset_commit_ck CHECK ((status = 'committed' AND committed_at IS NOT NULL) OR status <> 'committed')
);

CREATE TABLE IF NOT EXISTS event_type (
    event_type_id uuid PRIMARY KEY DEFAULT uuidv7(),
    stable_key text NOT NULL UNIQUE,
    description text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS event_schema_version (
    event_schema_version_id uuid PRIMARY KEY DEFAULT uuidv7(),
    event_type_id uuid NOT NULL REFERENCES event_type(event_type_id),
    version_no integer NOT NULL CHECK (version_no > 0),
    compatibility_mode text NOT NULL DEFAULT 'backward',
    schema_definition jsonb NOT NULL DEFAULT '{}'::jsonb,
    upcaster_method_ref text,
    created_at timestamptz NOT NULL DEFAULT now(),
    effective_from timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT 'active',
    UNIQUE(event_type_id, version_no)
);

CREATE SEQUENCE IF NOT EXISTS domain_event_position_seq AS bigint;

CREATE TABLE IF NOT EXISTS domain_event (
    event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    partition_key text NOT NULL,
    event_type text NOT NULL,
    event_schema_version_id uuid REFERENCES event_schema_version(event_schema_version_id),
    aggregate_id uuid NOT NULL,
    aggregate_type text NOT NULL,
    aggregate_version bigint NOT NULL CHECK (aggregate_version > 0),
    occurred_at timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    event_position bigint NOT NULL DEFAULT nextval('domain_event_position_seq'),
    actor_id uuid,
    changeset_id uuid NOT NULL REFERENCES changeset(changeset_id),
    correlation_id uuid NOT NULL,
    causation_event_id uuid REFERENCES domain_event(event_id),
    idempotency_namespace text NOT NULL,
    idempotency_key text NOT NULL,
    payload_hash text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    temporal_precision text NOT NULL DEFAULT 'exact',
    source_local_time text,
    source_timezone text,
    UNIQUE (school_id, aggregate_id, aggregate_version),
    UNIQUE (school_id, idempotency_namespace, idempotency_key, payload_hash),
    UNIQUE (event_position)
);
CREATE INDEX IF NOT EXISTS domain_event_aggregate_idx ON domain_event(school_id, aggregate_id, aggregate_version);
CREATE INDEX IF NOT EXISTS domain_event_recorded_idx ON domain_event(school_id, event_position);
CREATE INDEX IF NOT EXISTS domain_event_correlation_idx ON domain_event(school_id, correlation_id);

CREATE TABLE IF NOT EXISTS outbox_message (
    outbox_id uuid PRIMARY KEY DEFAULT uuidv7(),
    changeset_id uuid NOT NULL REFERENCES changeset(changeset_id),
    event_id uuid NOT NULL UNIQUE REFERENCES domain_event(event_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error text,
    status text NOT NULL DEFAULT 'pending',
    CONSTRAINT outbox_status_ck CHECK (status IN ('pending','publishing','published','failed'))
);
CREATE INDEX IF NOT EXISTS outbox_pending_idx ON outbox_message(status, created_at) WHERE status IN ('pending','failed');

CREATE TABLE IF NOT EXISTS ingestion_run (
    ingestion_run_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    source_system text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
    schema_version text,
    status text NOT NULL DEFAULT 'running',
    counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_manifest jsonb NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT ingestion_run_status_ck CHECK (status IN ('running','completed','partial','failed','cancelled'))
);

CREATE TABLE IF NOT EXISTS ingestion_item (
    ingestion_item_id uuid PRIMARY KEY DEFAULT uuidv7(),
    ingestion_run_id uuid NOT NULL REFERENCES ingestion_run(ingestion_run_id),
    native_namespace text NOT NULL,
    native_key text NOT NULL,
    payload_hash text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL,
    result_ref uuid,
    error_details jsonb,
    CONSTRAINT ingestion_item_status_ck CHECK (status IN ('new','duplicate','updated','quarantined','failed','processed')),
    UNIQUE (ingestion_run_id, native_namespace, native_key, payload_hash)
);

CREATE TABLE IF NOT EXISTS source_observation (
    source_observation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    source_id uuid NOT NULL REFERENCES source(source_id),
    provider_native_key text NOT NULL,
    provider_revision text,
    observed_at timestamptz NOT NULL DEFAULT now(),
    payload_hash text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    correction_of_id uuid REFERENCES source_observation(source_observation_id),
    status text NOT NULL DEFAULT 'observed',
    UNIQUE(source_id, provider_native_key, payload_hash)
);

CREATE TABLE IF NOT EXISTS pending_reference (
    pending_reference_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    source_item_id uuid,
    target_namespace text NOT NULL,
    target_key text NOT NULL,
    expected_target_type text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    status text NOT NULL DEFAULT 'pending',
    resolved_target_id uuid,
    UNIQUE(school_id, target_namespace, target_key, expected_target_type)
);

INSERT INTO school(stable_name, policy_scope)
VALUES ('Школа спортивного бриджа', '{"source_originals":"immutable"}'::jsonb)
ON CONFLICT (stable_name) DO NOTHING;

CREATE TABLE IF NOT EXISTS schema_migration (
    migration_key text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now(),
    checksum text
);
INSERT INTO schema_migration(migration_key) VALUES ('0001_global_registry') ON CONFLICT DO NOTHING;

COMMIT;
