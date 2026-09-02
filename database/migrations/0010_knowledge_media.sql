\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Knowledge graph: stable knowledge identity is separated from versioned content.
-- A runtime worker may propose/version knowledge, but only an administrative path may
-- activate a version as school canon.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_item (
    knowledge_item_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    stable_key text,
    knowledge_type text NOT NULL,
    title text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (school_id, stable_key)
);
CREATE INDEX IF NOT EXISTS knowledge_item_school_type_idx
    ON knowledge_item(school_id, knowledge_type, status, created_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_item_topic (
    knowledge_item_id uuid NOT NULL REFERENCES knowledge_item(knowledge_item_id),
    topic_id uuid NOT NULL REFERENCES topic(topic_id),
    relation_type text NOT NULL DEFAULT 'about',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (knowledge_item_id, topic_id, relation_type)
);

CREATE TABLE IF NOT EXISTS knowledge_version (
    knowledge_version_id uuid PRIMARY KEY DEFAULT uuidv7(),
    knowledge_item_id uuid NOT NULL REFERENCES knowledge_item(knowledge_item_id),
    version_no integer NOT NULL CHECK (version_no > 0),
    content jsonb NOT NULL,
    authority_class text NOT NULL DEFAULT 'research_candidate',
    review_status text NOT NULL DEFAULT 'unreviewed',
    bidding_system_key text,
    agreement_scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    level_scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    effective_from timestamptz,
    effective_to timestamptz,
    method_version text,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'candidate',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from),
    CHECK (authority_class IN ('school_canon','school_practice','external','research_candidate')),
    UNIQUE (knowledge_item_id, version_no)
);
CREATE INDEX IF NOT EXISTS knowledge_version_item_idx
    ON knowledge_version(knowledge_item_id, version_no DESC);
CREATE INDEX IF NOT EXISTS knowledge_version_authority_idx
    ON knowledge_version(authority_class, review_status, status, created_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_version_source (
    knowledge_version_id uuid NOT NULL REFERENCES knowledge_version(knowledge_version_id),
    source_id uuid NOT NULL REFERENCES source(source_id),
    relation_type text NOT NULL DEFAULT 'derived_from',
    source_locator jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (knowledge_version_id, source_id, relation_type)
);

CREATE TABLE IF NOT EXISTS canon_activation (
    canon_activation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    knowledge_version_id uuid NOT NULL REFERENCES knowledge_version(knowledge_version_id),
    scope_key text NOT NULL DEFAULT 'default',
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    approved_by_person_id uuid REFERENCES person(person_id),
    approval_provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'active',
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (status IN ('active','superseded','revoked','candidate'))
);
CREATE INDEX IF NOT EXISTS canon_activation_lookup_idx
    ON canon_activation(knowledge_version_id, scope_key, valid_from, valid_to)
    WHERE status='active';

CREATE OR REPLACE FUNCTION prevent_canon_activation_overlap()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_item uuid;
BEGIN
    IF NEW.status <> 'active' THEN
        RETURN NEW;
    END IF;

    SELECT knowledge_item_id
      INTO v_item
      FROM knowledge_version
     WHERE knowledge_version_id = NEW.knowledge_version_id;

    IF v_item IS NULL THEN
        RAISE EXCEPTION 'knowledge version missing for canon activation';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM canon_activation ca
          JOIN knowledge_version kv ON kv.knowledge_version_id = ca.knowledge_version_id
         WHERE kv.knowledge_item_id = v_item
           AND ca.scope_key = NEW.scope_key
           AND ca.status = 'active'
           AND ca.canon_activation_id <> NEW.canon_activation_id
           AND tstzrange(ca.valid_from, ca.valid_to, '[)') && tstzrange(NEW.valid_from, NEW.valid_to, '[)')
    ) THEN
        RAISE EXCEPTION 'overlapping active canon activation for knowledge item/scope';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS canon_activation_overlap_guard ON canon_activation;
CREATE TRIGGER canon_activation_overlap_guard
BEFORE INSERT OR UPDATE OF knowledge_version_id, scope_key, valid_from, valid_to, status
ON canon_activation
FOR EACH ROW
EXECUTE FUNCTION prevent_canon_activation_overlap();

CREATE TABLE IF NOT EXISTS knowledge_relation (
    knowledge_relation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    from_version_id uuid NOT NULL REFERENCES knowledge_version(knowledge_version_id),
    to_version_id uuid NOT NULL REFERENCES knowledge_version(knowledge_version_id),
    relation_type text NOT NULL,
    scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    preconditions jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    method_version text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (from_version_id <> to_version_id),
    CHECK (relation_type IN ('supports','refines','compatible_with','alternative_to','contradicts','supersedes','equivalent_to')),
    UNIQUE (from_version_id, to_version_id, relation_type)
);
CREATE INDEX IF NOT EXISTS knowledge_relation_from_idx ON knowledge_relation(from_version_id, relation_type);
CREATE INDEX IF NOT EXISTS knowledge_relation_to_idx ON knowledge_relation(to_version_id, relation_type);

CREATE TABLE IF NOT EXISTS knowledge_gap (
    knowledge_gap_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    question text NOT NULL,
    context_scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    discovered_at timestamptz NOT NULL DEFAULT now(),
    discovered_from_ids uuid[] NOT NULL DEFAULT '{}',
    priority text,
    status text NOT NULL DEFAULT 'open',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('open','researching','resolved','deferred','invalidated'))
);
CREATE INDEX IF NOT EXISTS knowledge_gap_status_idx
    ON knowledge_gap(school_id, status, discovered_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_gap_candidate_solution (
    solution_id uuid PRIMARY KEY DEFAULT uuidv7(),
    knowledge_gap_id uuid NOT NULL REFERENCES knowledge_gap(knowledge_gap_id),
    knowledge_version_id uuid REFERENCES knowledge_version(knowledge_version_id),
    proposed_content jsonb,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    assessment jsonb NOT NULL DEFAULT '{}'::jsonb,
    recommendation_status text NOT NULL DEFAULT 'candidate',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (knowledge_version_id IS NOT NULL OR proposed_content IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS knowledge_gap_solution_idx
    ON knowledge_gap_candidate_solution(knowledge_gap_id, recommendation_status, created_at DESC);

-- -----------------------------------------------------------------------------
-- Artifacts: generated materials have stable product identity and immutable versions.
-- The Asset contains bytes; ArtifactVersion describes the product/version that uses them.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artifact (
    artifact_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    artifact_type text NOT NULL,
    title text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS artifact_school_type_idx
    ON artifact(school_id, artifact_type, status, created_at DESC);

CREATE TABLE IF NOT EXISTS artifact_version (
    artifact_version_id uuid PRIMARY KEY DEFAULT uuidv7(),
    artifact_id uuid NOT NULL REFERENCES artifact(artifact_id),
    version_no integer NOT NULL CHECK (version_no > 0),
    version_label text,
    asset_id uuid REFERENCES asset(asset_id),
    generated_by_analysis_run_id uuid REFERENCES analysis_run(analysis_run_id),
    generation_method text,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'candidate',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (artifact_id, version_no)
);
CREATE INDEX IF NOT EXISTS artifact_version_artifact_idx
    ON artifact_version(artifact_id, version_no DESC);
CREATE INDEX IF NOT EXISTS artifact_version_analysis_idx
    ON artifact_version(generated_by_analysis_run_id)
    WHERE generated_by_analysis_run_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS artifact_version_source (
    artifact_version_id uuid NOT NULL REFERENCES artifact_version(artifact_version_id),
    source_id uuid NOT NULL REFERENCES source(source_id),
    relation_type text NOT NULL DEFAULT 'derived_from',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (artifact_version_id, source_id, relation_type)
);

CREATE TABLE IF NOT EXISTS artifact_version_knowledge (
    artifact_version_id uuid NOT NULL REFERENCES artifact_version(artifact_version_id),
    knowledge_version_id uuid NOT NULL REFERENCES knowledge_version(knowledge_version_id),
    relation_type text NOT NULL DEFAULT 'uses',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (artifact_version_id, knowledge_version_id, relation_type)
);

-- -----------------------------------------------------------------------------
-- Media/transcripts. MediaAsset is a typed logical extension of immutable Asset.
-- Transcript corrections create another Transcript rather than overwriting raw ASR/VTT.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS media_asset (
    media_asset_id uuid PRIMARY KEY REFERENCES asset(asset_id),
    school_id uuid NOT NULL REFERENCES school(school_id),
    duration_seconds numeric(14,3) CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
    media_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS media_asset_school_idx
    ON media_asset(school_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS transcript (
    transcript_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    media_asset_id uuid NOT NULL REFERENCES media_asset(media_asset_id),
    transcript_type text NOT NULL,
    language text,
    asr_model_version text,
    source_id uuid REFERENCES source(source_id),
    supersedes_transcript_id uuid REFERENCES transcript(transcript_id),
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'staging',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (transcript_type IN ('raw_asr','zoom_vtt','corrected','merged','manual','other')),
    CHECK (supersedes_transcript_id IS NULL OR supersedes_transcript_id <> transcript_id)
);
CREATE INDEX IF NOT EXISTS transcript_media_idx
    ON transcript(media_asset_id, created_at DESC);

CREATE TABLE IF NOT EXISTS transcript_segment (
    transcript_segment_id uuid PRIMARY KEY DEFAULT uuidv7(),
    transcript_id uuid NOT NULL REFERENCES transcript(transcript_id),
    sequence_no integer NOT NULL CHECK (sequence_no > 0),
    start_seconds numeric(14,3),
    end_seconds numeric(14,3),
    speaker_label text,
    speaker_source_identity_id uuid REFERENCES source_identity(source_identity_id),
    text text NOT NULL,
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    confidence_value numeric(8,6) CHECK (confidence_value IS NULL OR confidence_value BETWEEN 0 AND 1),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (start_seconds IS NULL OR start_seconds >= 0),
    CHECK (end_seconds IS NULL OR start_seconds IS NULL OR end_seconds >= start_seconds),
    UNIQUE (transcript_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS transcript_segment_time_idx
    ON transcript_segment(transcript_id, start_seconds, sequence_no);
CREATE INDEX IF NOT EXISTS transcript_segment_speaker_idx
    ON transcript_segment(speaker_source_identity_id, transcript_id)
    WHERE speaker_source_identity_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- Evidence/provenance and local quality. Evidence locates a fact in a source/asset;
-- analytical meaning is attached by EvidenceLink rather than stored inside the raw locator.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    evidence_type text NOT NULL,
    source_id uuid REFERENCES source(source_id),
    asset_id uuid REFERENCES asset(asset_id),
    locator jsonb NOT NULL DEFAULT '{}'::jsonb,
    start_seconds numeric(14,3),
    end_seconds numeric(14,3),
    page_no integer CHECK (page_no IS NULL OR page_no > 0),
    cell_ref text,
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    quality_status text NOT NULL DEFAULT 'unknown',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (source_id IS NOT NULL OR asset_id IS NOT NULL),
    CHECK (start_seconds IS NULL OR start_seconds >= 0),
    CHECK (end_seconds IS NULL OR start_seconds IS NULL OR end_seconds >= start_seconds)
);
CREATE INDEX IF NOT EXISTS evidence_source_idx ON evidence(source_id, created_at DESC) WHERE source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS evidence_asset_idx ON evidence(asset_id, start_seconds, created_at DESC) WHERE asset_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS evidence_link (
    evidence_link_id uuid PRIMARY KEY DEFAULT uuidv7(),
    evidence_id uuid NOT NULL REFERENCES evidence(evidence_id),
    target_entity_id uuid NOT NULL,
    target_entity_type text NOT NULL,
    relation_type text NOT NULL,
    weight numeric(10,6),
    analysis_run_id uuid REFERENCES analysis_run(analysis_run_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (relation_type IN ('supports','contradicts','context_for','derived_from','locates')),
    UNIQUE (evidence_id, target_entity_id, target_entity_type, relation_type)
);
CREATE INDEX IF NOT EXISTS evidence_link_target_idx
    ON evidence_link(target_entity_type, target_entity_id, relation_type);

CREATE TABLE IF NOT EXISTS quality_assessment (
    quality_assessment_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    target_entity_id uuid NOT NULL,
    target_entity_type text NOT NULL,
    dimension text NOT NULL,
    score numeric(12,6),
    quality_class text,
    assessor_actor_id uuid,
    method_version text,
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    assessed_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS quality_assessment_target_idx
    ON quality_assessment(target_entity_type, target_entity_id, dimension, assessed_at DESC);

CREATE TABLE IF NOT EXISTS quality_issue (
    quality_issue_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    target_entity_id uuid NOT NULL,
    target_entity_type text NOT NULL,
    issue_type text NOT NULL,
    severity text NOT NULL DEFAULT 'medium',
    locator jsonb NOT NULL DEFAULT '{}'::jsonb,
    description text,
    status text NOT NULL DEFAULT 'open',
    resolution_notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    CHECK (status IN ('open','confirmed','resolved','invalidated','ignored')),
    CHECK ((status <> 'resolved') OR resolved_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS quality_issue_target_idx
    ON quality_issue(target_entity_type, target_entity_id, status, created_at DESC);

-- -----------------------------------------------------------------------------
-- Algorithm registry and explicit AnalysisRun inputs/outputs. Existing AnalysisRun string
-- fields remain for backward compatibility; future runs can point to AlgorithmVersion.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS algorithm (
    algorithm_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    stable_key text NOT NULL,
    name text NOT NULL,
    purpose text,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (school_id, stable_key)
);

CREATE TABLE IF NOT EXISTS algorithm_version (
    algorithm_version_id uuid PRIMARY KEY DEFAULT uuidv7(),
    algorithm_id uuid NOT NULL REFERENCES algorithm(algorithm_id),
    version_no integer NOT NULL CHECK (version_no > 0),
    version_label text,
    code_commit_ref text,
    config_schema_version text,
    configuration jsonb NOT NULL DEFAULT '{}'::jsonb,
    effective_from timestamptz,
    effective_to timestamptz,
    status text NOT NULL DEFAULT 'candidate',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from),
    UNIQUE (algorithm_id, version_no)
);

ALTER TABLE analysis_run
    ADD COLUMN IF NOT EXISTS algorithm_version_id uuid;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='analysis_run_algorithm_version_fk') THEN
        ALTER TABLE analysis_run
        ADD CONSTRAINT analysis_run_algorithm_version_fk
        FOREIGN KEY (algorithm_version_id) REFERENCES algorithm_version(algorithm_version_id) NOT VALID;
        ALTER TABLE analysis_run VALIDATE CONSTRAINT analysis_run_algorithm_version_fk;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS analysis_run_input (
    analysis_run_input_id uuid PRIMARY KEY DEFAULT uuidv7(),
    analysis_run_id uuid NOT NULL REFERENCES analysis_run(analysis_run_id),
    source_id uuid REFERENCES source(source_id),
    asset_id uuid REFERENCES asset(asset_id),
    artifact_version_id uuid REFERENCES artifact_version(artifact_version_id),
    input_role text NOT NULL DEFAULT 'primary',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(source_id, asset_id, artifact_version_id) = 1),
    UNIQUE NULLS NOT DISTINCT (analysis_run_id, source_id, asset_id, artifact_version_id, input_role)
);
CREATE INDEX IF NOT EXISTS analysis_run_input_run_idx ON analysis_run_input(analysis_run_id);

CREATE TABLE IF NOT EXISTS analysis_run_output (
    analysis_run_output_id uuid PRIMARY KEY DEFAULT uuidv7(),
    analysis_run_id uuid NOT NULL REFERENCES analysis_run(analysis_run_id),
    output_entity_id uuid NOT NULL,
    output_entity_type text NOT NULL,
    artifact_version_id uuid REFERENCES artifact_version(artifact_version_id),
    publication_id uuid REFERENCES output_publication(publication_id),
    output_role text NOT NULL DEFAULT 'derived',
    status text NOT NULL DEFAULT 'staging',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (analysis_run_id, output_entity_id, output_entity_type, output_role)
);
CREATE INDEX IF NOT EXISTS analysis_run_output_run_idx
    ON analysis_run_output(analysis_run_id, status, created_at);

-- -----------------------------------------------------------------------------
-- Cross-school/source integrity guards.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION validate_knowledge_source_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_item_school uuid;
    v_source_school uuid;
BEGIN
    SELECT ki.school_id
      INTO v_item_school
      FROM knowledge_version kv
      JOIN knowledge_item ki ON ki.knowledge_item_id = kv.knowledge_item_id
     WHERE kv.knowledge_version_id = NEW.knowledge_version_id;
    SELECT school_id INTO v_source_school FROM source WHERE source_id = NEW.source_id;
    IF v_item_school IS NULL OR v_source_school IS NULL OR v_item_school <> v_source_school THEN
        RAISE EXCEPTION 'knowledge version source belongs to another school or is missing';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS knowledge_version_source_scope_guard ON knowledge_version_source;
CREATE TRIGGER knowledge_version_source_scope_guard
BEFORE INSERT OR UPDATE OF knowledge_version_id, source_id
ON knowledge_version_source
FOR EACH ROW EXECUTE FUNCTION validate_knowledge_source_scope();

CREATE OR REPLACE FUNCTION validate_media_asset_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_asset_school uuid;
BEGIN
    SELECT school_id INTO v_asset_school FROM asset WHERE asset_id = NEW.media_asset_id;
    IF v_asset_school IS NULL OR v_asset_school <> NEW.school_id THEN
        RAISE EXCEPTION 'media asset school does not match underlying asset';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS media_asset_scope_guard ON media_asset;
CREATE TRIGGER media_asset_scope_guard
BEFORE INSERT OR UPDATE OF media_asset_id, school_id
ON media_asset
FOR EACH ROW EXECUTE FUNCTION validate_media_asset_scope();

CREATE OR REPLACE FUNCTION validate_transcript_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_media_school uuid;
    v_source_school uuid;
BEGIN
    SELECT school_id INTO v_media_school FROM media_asset WHERE media_asset_id = NEW.media_asset_id;
    IF v_media_school IS NULL OR v_media_school <> NEW.school_id THEN
        RAISE EXCEPTION 'transcript school does not match media asset';
    END IF;
    IF NEW.source_id IS NOT NULL THEN
        SELECT school_id INTO v_source_school FROM source WHERE source_id = NEW.source_id;
        IF v_source_school IS NULL OR v_source_school <> NEW.school_id THEN
            RAISE EXCEPTION 'transcript source belongs to another school or is missing';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS transcript_scope_guard ON transcript;
CREATE TRIGGER transcript_scope_guard
BEFORE INSERT OR UPDATE OF school_id, media_asset_id, source_id
ON transcript
FOR EACH ROW EXECUTE FUNCTION validate_transcript_scope();

CREATE OR REPLACE FUNCTION validate_evidence_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_source_school uuid;
    v_asset_school uuid;
BEGIN
    IF NEW.source_id IS NOT NULL THEN
        SELECT school_id INTO v_source_school FROM source WHERE source_id = NEW.source_id;
        IF v_source_school IS NULL OR v_source_school <> NEW.school_id THEN
            RAISE EXCEPTION 'evidence source belongs to another school or is missing';
        END IF;
    END IF;
    IF NEW.asset_id IS NOT NULL THEN
        SELECT school_id INTO v_asset_school FROM asset WHERE asset_id = NEW.asset_id;
        IF v_asset_school IS NULL OR v_asset_school <> NEW.school_id THEN
            RAISE EXCEPTION 'evidence asset belongs to another school or is missing';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS evidence_scope_guard ON evidence;
CREATE TRIGGER evidence_scope_guard
BEFORE INSERT OR UPDATE OF school_id, source_id, asset_id
ON evidence
FOR EACH ROW EXECUTE FUNCTION validate_evidence_scope();

CREATE OR REPLACE FUNCTION validate_artifact_version_asset_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_artifact_school uuid;
    v_asset_school uuid;
BEGIN
    IF NEW.asset_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT a.school_id
      INTO v_artifact_school
      FROM artifact a
     WHERE a.artifact_id = NEW.artifact_id;
    SELECT school_id INTO v_asset_school FROM asset WHERE asset_id = NEW.asset_id;
    IF v_artifact_school IS NULL OR v_asset_school IS NULL OR v_artifact_school <> v_asset_school THEN
        RAISE EXCEPTION 'artifact version asset belongs to another school or is missing';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS artifact_version_asset_scope_guard ON artifact_version;
CREATE TRIGGER artifact_version_asset_scope_guard
BEFORE INSERT OR UPDATE OF artifact_id, asset_id
ON artifact_version
FOR EACH ROW EXECUTE FUNCTION validate_artifact_version_asset_scope();

-- -----------------------------------------------------------------------------
-- Runtime boundaries.
-- Worker can propose knowledge and build derived/media structures. Strategic canon
-- activation and algorithm definitions remain administrative/owner-write.
-- Versioned/raw content is append-only where an update would erase provenance.
-- -----------------------------------------------------------------------------
GRANT INSERT, UPDATE ON TABLE
    knowledge_item,
    knowledge_item_topic,
    knowledge_relation,
    knowledge_gap,
    knowledge_gap_candidate_solution,
    artifact,
    transcript,
    quality_issue,
    analysis_run
TO bridge_school_worker;

GRANT INSERT ON TABLE
    knowledge_version,
    knowledge_version_source,
    artifact_version,
    artifact_version_source,
    artifact_version_knowledge,
    media_asset,
    transcript_segment,
    evidence,
    evidence_link,
    quality_assessment,
    analysis_run_input,
    analysis_run_output
TO bridge_school_worker;

-- Limited lifecycle updates without permission to rewrite immutable content columns.
GRANT UPDATE (status, review_status) ON knowledge_version TO bridge_school_worker;
GRANT UPDATE (status) ON artifact_version TO bridge_school_worker;
GRANT UPDATE (status, duration_seconds, media_metadata) ON media_asset TO bridge_school_worker;

REVOKE INSERT, UPDATE, DELETE ON TABLE
    canon_activation,
    algorithm,
    algorithm_version
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE INSERT, UPDATE, DELETE ON TABLE
    knowledge_item,
    knowledge_item_topic,
    knowledge_version,
    knowledge_version_source,
    knowledge_relation,
    knowledge_gap,
    knowledge_gap_candidate_solution,
    artifact,
    artifact_version,
    artifact_version_source,
    artifact_version_knowledge,
    media_asset,
    transcript,
    transcript_segment,
    evidence,
    evidence_link,
    quality_assessment,
    quality_issue,
    algorithm,
    algorithm_version,
    analysis_run_input,
    analysis_run_output
FROM bridge_school_app;

REVOKE DELETE ON TABLE
    knowledge_item,
    knowledge_item_topic,
    knowledge_version,
    knowledge_version_source,
    canon_activation,
    knowledge_relation,
    knowledge_gap,
    knowledge_gap_candidate_solution,
    artifact,
    artifact_version,
    artifact_version_source,
    artifact_version_knowledge,
    media_asset,
    transcript,
    transcript_segment,
    evidence,
    evidence_link,
    quality_assessment,
    quality_issue,
    algorithm,
    algorithm_version,
    analysis_run_input,
    analysis_run_output
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

-- Explicitly preserve append-only semantics for version/source/evidence facts.
REVOKE UPDATE ON TABLE
    knowledge_version_source,
    artifact_version_source,
    artifact_version_knowledge,
    transcript_segment,
    evidence,
    evidence_link,
    quality_assessment,
    analysis_run_input,
    analysis_run_output
FROM bridge_school_worker;

-- Internal guard functions must not be callable as runtime APIs.
REVOKE ALL ON FUNCTION prevent_canon_activation_overlap() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_knowledge_source_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_media_asset_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_transcript_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_evidence_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_artifact_version_asset_scope() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION prevent_canon_activation_overlap() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_knowledge_source_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_media_asset_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_transcript_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_evidence_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_artifact_version_asset_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

INSERT INTO schema_migration(migration_key)
VALUES ('0010_knowledge_media')
ON CONFLICT DO NOTHING;

COMMIT;
