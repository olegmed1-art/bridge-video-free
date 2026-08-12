\set ON_ERROR_STOP on
BEGIN;

CREATE TABLE IF NOT EXISTS person (
    person_id uuid PRIMARY KEY DEFAULT uuidv7(),
    preferred_name text,
    locale text,
    timezone text,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS student (
    student_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    school_joined_at timestamptz,
    current_status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(school_id, person_id)
);

CREATE TABLE IF NOT EXISTS source_identity (
    source_identity_id uuid PRIMARY KEY DEFAULT uuidv7(),
    source_id uuid NOT NULL REFERENCES source(source_id),
    source_native_key text NOT NULL,
    display_name text,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(source_id, source_native_key)
);

CREATE TABLE IF NOT EXISTS entity_resolution_decision (
    resolution_id uuid PRIMARY KEY DEFAULT uuidv7(),
    source_identity_id uuid NOT NULL REFERENCES source_identity(source_identity_id),
    target_person_id uuid REFERENCES person(person_id),
    decision_type text NOT NULL,
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    method_version_id uuid,
    decided_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT 'active',
    CHECK (decision_type IN ('link','unlink','merge_candidate','reject','revoke'))
);
CREATE INDEX IF NOT EXISTS entity_resolution_active_idx ON entity_resolution_decision(source_identity_id, decided_at DESC) WHERE status='active';

CREATE TABLE IF NOT EXISTS learning_interaction (
    interaction_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    interaction_type text NOT NULL,
    started_at timestamptz,
    ended_at timestamptz,
    channel text,
    primary_student_id uuid REFERENCES student(student_id),
    group_id uuid,
    status text NOT NULL DEFAULT 'planned',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS topic (
    topic_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    name text NOT NULL,
    parent_topic_id uuid REFERENCES topic(topic_id),
    domain text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    UNIQUE(school_id, domain, name)
);

CREATE TABLE IF NOT EXISTS skill (
    skill_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    name text NOT NULL,
    description text,
    difficulty_band text,
    status text NOT NULL DEFAULT 'active',
    UNIQUE(school_id, name)
);

CREATE TABLE IF NOT EXISTS deal (
    deal_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    canonical_pbn text,
    hand_n text,
    hand_e text,
    hand_s text,
    hand_w text,
    dealer text,
    vulnerability text,
    reconstruction_status text NOT NULL DEFAULT 'UNKNOWN',
    deal_fingerprint text,
    source_id uuid REFERENCES source(source_id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS deal_fingerprint_idx ON deal(school_id, deal_fingerprint) WHERE deal_fingerprint IS NOT NULL;

CREATE TABLE IF NOT EXISTS decision (
    decision_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    actor_person_id uuid REFERENCES person(person_id),
    student_id uuid REFERENCES student(student_id),
    deal_id uuid REFERENCES deal(deal_id),
    interaction_id uuid REFERENCES learning_interaction(interaction_id),
    decision_type text NOT NULL,
    occurred_at timestamptz,
    sequence_no integer,
    action_taken jsonb NOT NULL,
    available_information jsonb NOT NULL DEFAULT '{}'::jsonb,
    stated_reasoning text,
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS decision_student_time_idx ON decision(student_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS decision_deal_idx ON decision(deal_id, sequence_no);

CREATE TABLE IF NOT EXISTS agreement_set (
    agreement_set_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    partnership_id uuid NOT NULL,
    bidding_system_key text,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agreement_version (
    agreement_version_id uuid PRIMARY KEY DEFAULT uuidv7(),
    agreement_set_id uuid NOT NULL REFERENCES agreement_set(agreement_set_id),
    version_label text NOT NULL,
    effective_from timestamptz,
    effective_to timestamptz,
    declared_by uuid REFERENCES person(person_id),
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'candidate',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agreement_activation (
    agreement_activation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    agreement_set_id uuid NOT NULL REFERENCES agreement_set(agreement_set_id),
    agreement_version_id uuid NOT NULL REFERENCES agreement_version(agreement_version_id),
    scope_key text NOT NULL DEFAULT 'default',
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    authority_state text NOT NULL DEFAULT 'confirmed',
    recorded_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT 'active',
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (authority_state IN ('confirmed','candidate','conflict'))
);
CREATE INDEX IF NOT EXISTS agreement_activation_lookup_idx ON agreement_activation(agreement_set_id, scope_key, valid_from, valid_to) WHERE status='active';

CREATE TABLE IF NOT EXISTS decision_assessment (
    decision_assessment_id uuid PRIMARY KEY DEFAULT uuidv7(),
    decision_id uuid NOT NULL REFERENCES decision(decision_id),
    evaluated_at timestamptz NOT NULL DEFAULT now(),
    assessor_actor_id uuid,
    authority_class text NOT NULL DEFAULT 'ai',
    assessment_purpose text NOT NULL DEFAULT 'learning',
    action_quality text,
    reasoning_quality text,
    agreement_compliance text,
    severity text,
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    agreement_context jsonb NOT NULL DEFAULT '{"resolution_type":"unknown"}'::jsonb,
    assessment_method_version_id uuid,
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS decision_assessment_decision_idx ON decision_assessment(decision_id, evaluated_at DESC);

CREATE TABLE IF NOT EXISTS metric_definition (
    metric_definition_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    stable_key text NOT NULL,
    name text NOT NULL,
    semantic_description text,
    value_type text NOT NULL,
    directionality text,
    status text NOT NULL DEFAULT 'active',
    UNIQUE(school_id, stable_key)
);

CREATE TABLE IF NOT EXISTS metric_version (
    metric_version_id uuid PRIMARY KEY DEFAULT uuidv7(),
    metric_definition_id uuid NOT NULL REFERENCES metric_definition(metric_definition_id),
    version_no integer NOT NULL CHECK (version_no > 0),
    formula_or_method_ref text NOT NULL,
    inputs_definition jsonb NOT NULL DEFAULT '{}'::jsonb,
    effective_from timestamptz,
    effective_to timestamptz,
    algorithm_version_id uuid,
    status text NOT NULL DEFAULT 'candidate',
    UNIQUE(metric_definition_id, version_no)
);

CREATE TABLE IF NOT EXISTS analysis_run (
    analysis_run_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    algorithm_key text NOT NULL,
    algorithm_version text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    run_status text NOT NULL DEFAULT 'running',
    parameters_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    qc_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (run_status IN ('running','success','partial_success','failed','cancelled'))
);

CREATE TABLE IF NOT EXISTS output_publication (
    publication_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    analysis_run_id uuid REFERENCES analysis_run(analysis_run_id),
    generation_id uuid NOT NULL DEFAULT uuidv7(),
    publication_type text NOT NULL,
    manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'staging',
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    UNIQUE(school_id, generation_id),
    CHECK (status IN ('staging','validated','published','invalidated','failed'))
);

CREATE TABLE IF NOT EXISTS projection_policy_version (
    projection_policy_version_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    stable_key text NOT NULL,
    version_no integer NOT NULL,
    policy jsonb NOT NULL,
    status text NOT NULL DEFAULT 'candidate',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(school_id, stable_key, version_no)
);

CREATE TABLE IF NOT EXISTS projection_run (
    projection_run_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    projection_key text NOT NULL,
    method_version text NOT NULL,
    projection_policy_version_id uuid REFERENCES projection_policy_version(projection_policy_version_id),
    scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    input_watermark bigint,
    generation_id uuid NOT NULL DEFAULT uuidv7(),
    checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'running',
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    validation_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(school_id, projection_key, generation_id)
);

CREATE TABLE IF NOT EXISTS student_profile_snapshot (
    snapshot_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    student_id uuid NOT NULL REFERENCES student(student_id),
    as_of_time timestamptz NOT NULL,
    projection_policy_version_id uuid REFERENCES projection_policy_version(projection_policy_version_id),
    projection_run_id uuid REFERENCES projection_run(projection_run_id),
    generation_id uuid NOT NULL,
    input_watermark bigint,
    computed_profile jsonb NOT NULL,
    status text NOT NULL DEFAULT 'active',
    stale_from timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS student_profile_current_idx ON student_profile_snapshot(student_id, as_of_time DESC) WHERE status='active';

CREATE TABLE IF NOT EXISTS dependency_edge (
    dependency_edge_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    parent_entity_id uuid NOT NULL,
    child_entity_id uuid NOT NULL,
    dependency_type text NOT NULL,
    method_version text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (parent_entity_id <> child_entity_id),
    UNIQUE(parent_entity_id, child_entity_id, dependency_type)
);
CREATE INDEX IF NOT EXISTS dependency_parent_idx ON dependency_edge(parent_entity_id);
CREATE INDEX IF NOT EXISTS dependency_child_idx ON dependency_edge(child_entity_id);

CREATE TABLE IF NOT EXISTS invalidation_record (
    invalidation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    target_entity_id uuid NOT NULL,
    cause_entity_id uuid,
    reason text NOT NULL,
    scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    invalidated_at timestamptz NOT NULL DEFAULT now(),
    recomputation_status text NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS version_relation (
    version_relation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    from_version_id uuid NOT NULL,
    to_version_id uuid NOT NULL,
    relation_type text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK(from_version_id <> to_version_id),
    UNIQUE(from_version_id, to_version_id, relation_type)
);

INSERT INTO schema_migration(migration_key) VALUES ('0002_learning_core') ON CONFLICT DO NOTHING;
COMMIT;
