\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Student profile projection inputs.
-- These tables keep observations/assessments separate from the materialized profile.
-- No bridge-specific scoring formula is embedded here: methods remain versioned data.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skill_assessment (
    skill_assessment_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    student_id uuid NOT NULL REFERENCES student(student_id),
    skill_id uuid NOT NULL REFERENCES skill(skill_id),
    assessed_at timestamptz NOT NULL DEFAULT now(),
    assessment_value jsonb NOT NULL,
    scale_key text,
    authority_class text NOT NULL DEFAULT 'ai',
    assessment_purpose text NOT NULL DEFAULT 'learning',
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    confidence_value numeric(8,6) CHECK (confidence_value IS NULL OR confidence_value BETWEEN 0 AND 1),
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    generated_by_analysis_run_id uuid REFERENCES analysis_run(analysis_run_id),
    method_version text,
    supersedes_assessment_id uuid REFERENCES skill_assessment(skill_assessment_id),
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (supersedes_assessment_id IS NULL OR supersedes_assessment_id <> skill_assessment_id)
);
CREATE INDEX IF NOT EXISTS skill_assessment_student_skill_idx
    ON skill_assessment(student_id, skill_id, assessed_at DESC);
CREATE INDEX IF NOT EXISTS skill_assessment_run_idx
    ON skill_assessment(generated_by_analysis_run_id)
    WHERE generated_by_analysis_run_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS metric_observation (
    metric_observation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    subject_type text NOT NULL,
    student_id uuid REFERENCES student(student_id),
    group_id uuid REFERENCES learning_group(group_id),
    other_subject_id uuid,
    other_subject_type text,
    metric_version_id uuid NOT NULL REFERENCES metric_version(metric_version_id),
    value jsonb NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    confidence_value numeric(8,6) CHECK (confidence_value IS NULL OR confidence_value BETWEEN 0 AND 1),
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    generated_by_analysis_run_id uuid REFERENCES analysis_run(analysis_run_id),
    supersedes_observation_id uuid REFERENCES metric_observation(metric_observation_id),
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (subject_type IN ('student','group','other')),
    CHECK (num_nonnulls(student_id, group_id, other_subject_id) = 1),
    CHECK (
        (subject_type='student' AND student_id IS NOT NULL AND group_id IS NULL AND other_subject_id IS NULL)
        OR (subject_type='group' AND group_id IS NOT NULL AND student_id IS NULL AND other_subject_id IS NULL)
        OR (subject_type='other' AND other_subject_id IS NOT NULL AND student_id IS NULL AND group_id IS NULL AND other_subject_type IS NOT NULL)
    ),
    CHECK (supersedes_observation_id IS NULL OR supersedes_observation_id <> metric_observation_id)
);
CREATE INDEX IF NOT EXISTS metric_observation_student_idx
    ON metric_observation(student_id, metric_version_id, observed_at DESC)
    WHERE student_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS metric_observation_group_idx
    ON metric_observation(group_id, metric_version_id, observed_at DESC)
    WHERE group_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS metric_observation_run_idx
    ON metric_observation(generated_by_analysis_run_id)
    WHERE generated_by_analysis_run_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS error_observation (
    error_observation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    student_id uuid NOT NULL REFERENCES student(student_id),
    decision_id uuid REFERENCES decision(decision_id),
    exercise_attempt_id uuid REFERENCES exercise_attempt(exercise_attempt_id),
    table_result_id uuid REFERENCES table_result(result_id),
    skill_id uuid REFERENCES skill(skill_id),
    topic_id uuid REFERENCES topic(topic_id),
    error_type text NOT NULL,
    causal_hypothesis jsonb NOT NULL DEFAULT '{}'::jsonb,
    severity text,
    recurrence_group_key text,
    observed_at timestamptz NOT NULL DEFAULT now(),
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    confidence_value numeric(8,6) CHECK (confidence_value IS NULL OR confidence_value BETWEEN 0 AND 1),
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    generated_by_analysis_run_id uuid REFERENCES analysis_run(analysis_run_id),
    method_version text,
    supersedes_error_observation_id uuid REFERENCES error_observation(error_observation_id),
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (supersedes_error_observation_id IS NULL OR supersedes_error_observation_id <> error_observation_id)
);
CREATE INDEX IF NOT EXISTS error_observation_student_time_idx
    ON error_observation(student_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS error_observation_skill_idx
    ON error_observation(student_id, skill_id, recurrence_group_key, observed_at DESC)
    WHERE skill_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS error_observation_topic_idx
    ON error_observation(student_id, topic_id, observed_at DESC)
    WHERE topic_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS success_observation (
    success_observation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    student_id uuid NOT NULL REFERENCES student(student_id),
    decision_id uuid REFERENCES decision(decision_id),
    exercise_attempt_id uuid REFERENCES exercise_attempt(exercise_attempt_id),
    table_result_id uuid REFERENCES table_result(result_id),
    skill_id uuid REFERENCES skill(skill_id),
    topic_id uuid REFERENCES topic(topic_id),
    success_type text NOT NULL,
    independence_level text,
    observed_at timestamptz NOT NULL DEFAULT now(),
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    confidence_value numeric(8,6) CHECK (confidence_value IS NULL OR confidence_value BETWEEN 0 AND 1),
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    generated_by_analysis_run_id uuid REFERENCES analysis_run(analysis_run_id),
    method_version text,
    supersedes_success_observation_id uuid REFERENCES success_observation(success_observation_id),
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (supersedes_success_observation_id IS NULL OR supersedes_success_observation_id <> success_observation_id)
);
CREATE INDEX IF NOT EXISTS success_observation_student_time_idx
    ON success_observation(student_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS success_observation_skill_idx
    ON success_observation(student_id, skill_id, observed_at DESC)
    WHERE skill_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS success_observation_topic_idx
    ON success_observation(student_id, topic_id, observed_at DESC)
    WHERE topic_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- Scope guards for assessments/observations.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION validate_skill_assessment_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_student_school uuid;
    v_skill_school uuid;
    v_run_school uuid;
BEGIN
    SELECT school_id INTO v_student_school FROM student WHERE student_id=NEW.student_id;
    SELECT school_id INTO v_skill_school FROM skill WHERE skill_id=NEW.skill_id;
    IF v_student_school IS NULL OR v_skill_school IS NULL
       OR v_student_school <> NEW.school_id OR v_skill_school <> NEW.school_id THEN
        RAISE EXCEPTION 'skill assessment student/skill school mismatch';
    END IF;
    IF NEW.generated_by_analysis_run_id IS NOT NULL THEN
        SELECT school_id INTO v_run_school FROM analysis_run WHERE analysis_run_id=NEW.generated_by_analysis_run_id;
        IF v_run_school IS NULL OR v_run_school <> NEW.school_id THEN
            RAISE EXCEPTION 'skill assessment analysis run belongs to another school or is missing';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS skill_assessment_scope_guard ON skill_assessment;
CREATE TRIGGER skill_assessment_scope_guard
BEFORE INSERT OR UPDATE OF school_id, student_id, skill_id, generated_by_analysis_run_id
ON skill_assessment
FOR EACH ROW EXECUTE FUNCTION validate_skill_assessment_scope();

CREATE OR REPLACE FUNCTION validate_metric_observation_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_metric_school uuid;
    v_subject_school uuid;
    v_run_school uuid;
BEGIN
    SELECT md.school_id
      INTO v_metric_school
      FROM metric_version mv
      JOIN metric_definition md ON md.metric_definition_id=mv.metric_definition_id
     WHERE mv.metric_version_id=NEW.metric_version_id;
    IF v_metric_school IS NULL OR v_metric_school <> NEW.school_id THEN
        RAISE EXCEPTION 'metric observation definition belongs to another school or is missing';
    END IF;

    IF NEW.subject_type='student' THEN
        SELECT school_id INTO v_subject_school FROM student WHERE student_id=NEW.student_id;
    ELSIF NEW.subject_type='group' THEN
        SELECT school_id INTO v_subject_school FROM learning_group WHERE group_id=NEW.group_id;
    ELSE
        v_subject_school := NEW.school_id;
    END IF;
    IF v_subject_school IS NULL OR v_subject_school <> NEW.school_id THEN
        RAISE EXCEPTION 'metric observation subject belongs to another school or is missing';
    END IF;

    IF NEW.generated_by_analysis_run_id IS NOT NULL THEN
        SELECT school_id INTO v_run_school FROM analysis_run WHERE analysis_run_id=NEW.generated_by_analysis_run_id;
        IF v_run_school IS NULL OR v_run_school <> NEW.school_id THEN
            RAISE EXCEPTION 'metric observation analysis run belongs to another school or is missing';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS metric_observation_scope_guard ON metric_observation;
CREATE TRIGGER metric_observation_scope_guard
BEFORE INSERT OR UPDATE OF school_id, subject_type, student_id, group_id, metric_version_id, generated_by_analysis_run_id
ON metric_observation
FOR EACH ROW EXECUTE FUNCTION validate_metric_observation_scope();

CREATE OR REPLACE FUNCTION validate_learning_observation_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_student_school uuid;
    v_ref_student uuid;
    v_ref_school uuid;
    v_skill_school uuid;
    v_topic_school uuid;
    v_run_school uuid;
BEGIN
    SELECT school_id INTO v_student_school FROM student WHERE student_id=NEW.student_id;
    IF v_student_school IS NULL OR v_student_school <> NEW.school_id THEN
        RAISE EXCEPTION 'learning observation student belongs to another school or is missing';
    END IF;

    IF NEW.decision_id IS NOT NULL THEN
        SELECT student_id, school_id INTO v_ref_student, v_ref_school FROM decision WHERE decision_id=NEW.decision_id;
        IF v_ref_student IS NULL OR v_ref_student <> NEW.student_id OR v_ref_school <> NEW.school_id THEN
            RAISE EXCEPTION 'learning observation decision does not belong to student/school';
        END IF;
    END IF;

    IF NEW.exercise_attempt_id IS NOT NULL THEN
        SELECT student_id, school_id INTO v_ref_student, v_ref_school FROM exercise_attempt WHERE exercise_attempt_id=NEW.exercise_attempt_id;
        IF v_ref_student IS NULL OR v_ref_student <> NEW.student_id OR v_ref_school <> NEW.school_id THEN
            RAISE EXCEPTION 'learning observation exercise attempt does not belong to student/school';
        END IF;
    END IF;

    IF NEW.table_result_id IS NOT NULL THEN
        SELECT school_id INTO v_ref_school FROM table_result WHERE result_id=NEW.table_result_id;
        IF v_ref_school IS NULL OR v_ref_school <> NEW.school_id THEN
            RAISE EXCEPTION 'learning observation table result belongs to another school or is missing';
        END IF;
    END IF;

    IF NEW.skill_id IS NOT NULL THEN
        SELECT school_id INTO v_skill_school FROM skill WHERE skill_id=NEW.skill_id;
        IF v_skill_school IS NULL OR v_skill_school <> NEW.school_id THEN
            RAISE EXCEPTION 'learning observation skill belongs to another school or is missing';
        END IF;
    END IF;
    IF NEW.topic_id IS NOT NULL THEN
        SELECT school_id INTO v_topic_school FROM topic WHERE topic_id=NEW.topic_id;
        IF v_topic_school IS NULL OR v_topic_school <> NEW.school_id THEN
            RAISE EXCEPTION 'learning observation topic belongs to another school or is missing';
        END IF;
    END IF;

    IF NEW.generated_by_analysis_run_id IS NOT NULL THEN
        SELECT school_id INTO v_run_school FROM analysis_run WHERE analysis_run_id=NEW.generated_by_analysis_run_id;
        IF v_run_school IS NULL OR v_run_school <> NEW.school_id THEN
            RAISE EXCEPTION 'learning observation analysis run belongs to another school or is missing';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS error_observation_scope_guard ON error_observation;
CREATE TRIGGER error_observation_scope_guard
BEFORE INSERT OR UPDATE OF school_id, student_id, decision_id, exercise_attempt_id, table_result_id, skill_id, topic_id, generated_by_analysis_run_id
ON error_observation
FOR EACH ROW EXECUTE FUNCTION validate_learning_observation_scope();
DROP TRIGGER IF EXISTS success_observation_scope_guard ON success_observation;
CREATE TRIGGER success_observation_scope_guard
BEFORE INSERT OR UPDATE OF school_id, student_id, decision_id, exercise_attempt_id, table_result_id, skill_id, topic_id, generated_by_analysis_run_id
ON success_observation
FOR EACH ROW EXECUTE FUNCTION validate_learning_observation_scope();

-- -----------------------------------------------------------------------------
-- Student profile snapshots are immutable projection generations. Lifecycle changes
-- are append-only state events; current generation is selected atomically below.
-- -----------------------------------------------------------------------------
ALTER TABLE student_profile_snapshot
    ADD COLUMN IF NOT EXISTS supersedes_snapshot_id uuid,
    ADD COLUMN IF NOT EXISTS profile_schema_version text;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='student_profile_snapshot_supersedes_fk') THEN
        ALTER TABLE student_profile_snapshot
        ADD CONSTRAINT student_profile_snapshot_supersedes_fk
        FOREIGN KEY (supersedes_snapshot_id) REFERENCES student_profile_snapshot(snapshot_id) NOT VALID;
        ALTER TABLE student_profile_snapshot VALIDATE CONSTRAINT student_profile_snapshot_supersedes_fk;
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS student_profile_snapshot_generation_uk
    ON student_profile_snapshot(student_id, generation_id);

CREATE TABLE IF NOT EXISTS student_profile_snapshot_state_event (
    snapshot_state_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    snapshot_id uuid NOT NULL REFERENCES student_profile_snapshot(snapshot_id),
    state_type text NOT NULL,
    effective_at timestamptz NOT NULL DEFAULT now(),
    stale_from timestamptz,
    cause_entity_id uuid,
    reason text,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (state_type IN ('created','validated','stale','invalidated','superseded')),
    CHECK (state_type <> 'stale' OR stale_from IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS student_profile_snapshot_state_idx
    ON student_profile_snapshot_state_event(snapshot_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS student_profile_snapshot_metric (
    snapshot_id uuid NOT NULL REFERENCES student_profile_snapshot(snapshot_id),
    metric_version_id uuid NOT NULL REFERENCES metric_version(metric_version_id),
    value jsonb NOT NULL,
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    confidence_value numeric(8,6) CHECK (confidence_value IS NULL OR confidence_value BETWEEN 0 AND 1),
    observation_count integer NOT NULL DEFAULT 0 CHECK (observation_count >= 0),
    last_observed_at timestamptz,
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_id, metric_version_id)
);

CREATE TABLE IF NOT EXISTS student_profile_snapshot_skill (
    snapshot_id uuid NOT NULL REFERENCES student_profile_snapshot(snapshot_id),
    skill_id uuid NOT NULL REFERENCES skill(skill_id),
    state_value jsonb NOT NULL DEFAULT '{}'::jsonb,
    mastery_state text,
    training_state text,
    training_priority numeric(12,6),
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    confidence_value numeric(8,6) CHECK (confidence_value IS NULL OR confidence_value BETWEEN 0 AND 1),
    last_assessed_at timestamptz,
    recurrence_signal_count integer NOT NULL DEFAULT 0 CHECK (recurrence_signal_count >= 0),
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_id, skill_id)
);
CREATE INDEX IF NOT EXISTS student_profile_snapshot_skill_lookup_idx
    ON student_profile_snapshot_skill(skill_id, training_priority DESC NULLS LAST, last_assessed_at DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS student_profile_snapshot_topic (
    snapshot_id uuid NOT NULL REFERENCES student_profile_snapshot(snapshot_id),
    topic_id uuid NOT NULL REFERENCES topic(topic_id),
    state_value jsonb NOT NULL DEFAULT '{}'::jsonb,
    mastery_state text,
    training_state text,
    training_priority numeric(12,6),
    reactivation_reason text,
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    confidence_value numeric(8,6) CHECK (confidence_value IS NULL OR confidence_value BETWEEN 0 AND 1),
    last_observed_at timestamptz,
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_id, topic_id)
);
CREATE INDEX IF NOT EXISTS student_profile_snapshot_topic_lookup_idx
    ON student_profile_snapshot_topic(topic_id, training_priority DESC NULLS LAST, last_observed_at DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS student_profile_inference (
    student_profile_inference_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    student_id uuid NOT NULL REFERENCES student(student_id),
    snapshot_id uuid NOT NULL REFERENCES student_profile_snapshot(snapshot_id),
    inference_type text NOT NULL,
    topic_id uuid REFERENCES topic(topic_id),
    skill_id uuid REFERENCES skill(skill_id),
    inference_value jsonb NOT NULL,
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    confidence_value numeric(8,6) CHECK (confidence_value IS NULL OR confidence_value BETWEEN 0 AND 1),
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    generated_by_analysis_run_id uuid REFERENCES analysis_run(analysis_run_id),
    method_version text,
    supersedes_inference_id uuid REFERENCES student_profile_inference(student_profile_inference_id),
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (supersedes_inference_id IS NULL OR supersedes_inference_id <> student_profile_inference_id)
);
CREATE INDEX IF NOT EXISTS student_profile_inference_student_idx
    ON student_profile_inference(student_id, inference_type, created_at DESC);
CREATE INDEX IF NOT EXISTS student_profile_inference_snapshot_idx
    ON student_profile_inference(snapshot_id, created_at);

-- Exact selected inputs make the profile reproducible and enable targeted invalidation.
-- Tournament facts must carry the identity-attribution and resolution decision that
-- justified attaching the external result to this Student.
CREATE TABLE IF NOT EXISTS student_profile_input (
    student_profile_input_id uuid PRIMARY KEY DEFAULT uuidv7(),
    snapshot_id uuid NOT NULL REFERENCES student_profile_snapshot(snapshot_id),
    skill_assessment_id uuid REFERENCES skill_assessment(skill_assessment_id),
    metric_observation_id uuid REFERENCES metric_observation(metric_observation_id),
    error_observation_id uuid REFERENCES error_observation(error_observation_id),
    success_observation_id uuid REFERENCES success_observation(success_observation_id),
    decision_assessment_id uuid REFERENCES decision_assessment(decision_assessment_id),
    exercise_attempt_assessment_id uuid REFERENCES exercise_attempt_assessment(exercise_attempt_assessment_id),
    table_result_id uuid REFERENCES table_result(result_id),
    source_observation_id uuid REFERENCES source_observation(source_observation_id),
    tournament_identity_attribution_id uuid REFERENCES tournament_identity_attribution(tournament_identity_attribution_id),
    entity_resolution_decision_id uuid REFERENCES entity_resolution_decision(resolution_id),
    input_role text NOT NULL DEFAULT 'selected',
    effective_weight numeric(12,6),
    selection_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(
        skill_assessment_id,
        metric_observation_id,
        error_observation_id,
        success_observation_id,
        decision_assessment_id,
        exercise_attempt_assessment_id,
        table_result_id,
        source_observation_id
    ) = 1),
    CHECK (
        (table_result_id IS NULL AND tournament_identity_attribution_id IS NULL AND entity_resolution_decision_id IS NULL)
        OR (table_result_id IS NOT NULL AND tournament_identity_attribution_id IS NOT NULL AND entity_resolution_decision_id IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS student_profile_input_snapshot_idx
    ON student_profile_input(snapshot_id, created_at);
CREATE INDEX IF NOT EXISTS student_profile_input_resolution_idx
    ON student_profile_input(entity_resolution_decision_id, snapshot_id)
    WHERE entity_resolution_decision_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS student_profile_input_tournament_attribution_idx
    ON student_profile_input(tournament_identity_attribution_id, snapshot_id)
    WHERE tournament_identity_attribution_id IS NOT NULL;

CREATE OR REPLACE FUNCTION validate_student_profile_snapshot_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_student_school uuid;
    v_policy_school uuid;
    v_run_school uuid;
    v_run_generation uuid;
    v_sup_student uuid;
BEGIN
    SELECT school_id INTO v_student_school FROM student WHERE student_id=NEW.student_id;
    IF v_student_school IS NULL OR v_student_school <> NEW.school_id THEN
        RAISE EXCEPTION 'student profile snapshot student belongs to another school or is missing';
    END IF;
    IF NEW.projection_policy_version_id IS NOT NULL THEN
        SELECT school_id INTO v_policy_school FROM projection_policy_version WHERE projection_policy_version_id=NEW.projection_policy_version_id;
        IF v_policy_school IS NULL OR v_policy_school <> NEW.school_id THEN
            RAISE EXCEPTION 'student profile projection policy belongs to another school or is missing';
        END IF;
    END IF;
    IF NEW.projection_run_id IS NOT NULL THEN
        SELECT school_id, generation_id INTO v_run_school, v_run_generation
          FROM projection_run WHERE projection_run_id=NEW.projection_run_id;
        IF v_run_school IS NULL OR v_run_school <> NEW.school_id OR v_run_generation <> NEW.generation_id THEN
            RAISE EXCEPTION 'student profile projection run/generation mismatch';
        END IF;
    END IF;
    IF NEW.supersedes_snapshot_id IS NOT NULL THEN
        SELECT student_id INTO v_sup_student FROM student_profile_snapshot WHERE snapshot_id=NEW.supersedes_snapshot_id;
        IF v_sup_student IS NULL OR v_sup_student <> NEW.student_id THEN
            RAISE EXCEPTION 'student profile superseded snapshot belongs to another student or is missing';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS student_profile_snapshot_scope_guard ON student_profile_snapshot;
CREATE TRIGGER student_profile_snapshot_scope_guard
BEFORE INSERT OR UPDATE OF school_id, student_id, projection_policy_version_id, projection_run_id, generation_id, supersedes_snapshot_id
ON student_profile_snapshot
FOR EACH ROW EXECUTE FUNCTION validate_student_profile_snapshot_scope();

CREATE OR REPLACE FUNCTION validate_student_profile_metric_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_snapshot_school uuid;
    v_metric_school uuid;
BEGIN
    SELECT school_id INTO v_snapshot_school FROM student_profile_snapshot WHERE snapshot_id=NEW.snapshot_id;
    SELECT md.school_id
      INTO v_metric_school
      FROM metric_version mv
      JOIN metric_definition md ON md.metric_definition_id=mv.metric_definition_id
     WHERE mv.metric_version_id=NEW.metric_version_id;
    IF v_snapshot_school IS NULL OR v_metric_school IS NULL OR v_snapshot_school <> v_metric_school THEN
        RAISE EXCEPTION 'profile snapshot metric belongs to another school or is missing';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS student_profile_metric_scope_guard ON student_profile_snapshot_metric;
CREATE TRIGGER student_profile_metric_scope_guard
BEFORE INSERT OR UPDATE OF snapshot_id, metric_version_id
ON student_profile_snapshot_metric
FOR EACH ROW EXECUTE FUNCTION validate_student_profile_metric_scope();

CREATE OR REPLACE FUNCTION validate_student_profile_skill_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_snapshot_school uuid;
    v_skill_school uuid;
BEGIN
    SELECT school_id INTO v_snapshot_school FROM student_profile_snapshot WHERE snapshot_id=NEW.snapshot_id;
    SELECT school_id INTO v_skill_school FROM skill WHERE skill_id=NEW.skill_id;
    IF v_snapshot_school IS NULL OR v_skill_school IS NULL OR v_snapshot_school <> v_skill_school THEN
        RAISE EXCEPTION 'profile snapshot skill belongs to another school or is missing';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS student_profile_skill_scope_guard ON student_profile_snapshot_skill;
CREATE TRIGGER student_profile_skill_scope_guard
BEFORE INSERT OR UPDATE OF snapshot_id, skill_id
ON student_profile_snapshot_skill
FOR EACH ROW EXECUTE FUNCTION validate_student_profile_skill_scope();

CREATE OR REPLACE FUNCTION validate_student_profile_topic_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_snapshot_school uuid;
    v_topic_school uuid;
BEGIN
    SELECT school_id INTO v_snapshot_school FROM student_profile_snapshot WHERE snapshot_id=NEW.snapshot_id;
    SELECT school_id INTO v_topic_school FROM topic WHERE topic_id=NEW.topic_id;
    IF v_snapshot_school IS NULL OR v_topic_school IS NULL OR v_snapshot_school <> v_topic_school THEN
        RAISE EXCEPTION 'profile snapshot topic belongs to another school or is missing';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS student_profile_topic_scope_guard ON student_profile_snapshot_topic;
CREATE TRIGGER student_profile_topic_scope_guard
BEFORE INSERT OR UPDATE OF snapshot_id, topic_id
ON student_profile_snapshot_topic
FOR EACH ROW EXECUTE FUNCTION validate_student_profile_topic_scope();

CREATE OR REPLACE FUNCTION validate_student_profile_inference_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_snapshot_school uuid;
    v_snapshot_student uuid;
    v_ref_school uuid;
    v_sup_student uuid;
BEGIN
    SELECT school_id, student_id INTO v_snapshot_school, v_snapshot_student
      FROM student_profile_snapshot WHERE snapshot_id=NEW.snapshot_id;
    IF v_snapshot_school IS NULL OR v_snapshot_school <> NEW.school_id OR v_snapshot_student <> NEW.student_id THEN
        RAISE EXCEPTION 'profile inference snapshot/student/school mismatch';
    END IF;
    IF NEW.topic_id IS NOT NULL THEN
        SELECT school_id INTO v_ref_school FROM topic WHERE topic_id=NEW.topic_id;
        IF v_ref_school IS NULL OR v_ref_school <> NEW.school_id THEN RAISE EXCEPTION 'profile inference topic school mismatch'; END IF;
    END IF;
    IF NEW.skill_id IS NOT NULL THEN
        SELECT school_id INTO v_ref_school FROM skill WHERE skill_id=NEW.skill_id;
        IF v_ref_school IS NULL OR v_ref_school <> NEW.school_id THEN RAISE EXCEPTION 'profile inference skill school mismatch'; END IF;
    END IF;
    IF NEW.generated_by_analysis_run_id IS NOT NULL THEN
        SELECT school_id INTO v_ref_school FROM analysis_run WHERE analysis_run_id=NEW.generated_by_analysis_run_id;
        IF v_ref_school IS NULL OR v_ref_school <> NEW.school_id THEN RAISE EXCEPTION 'profile inference analysis run school mismatch'; END IF;
    END IF;
    IF NEW.supersedes_inference_id IS NOT NULL THEN
        SELECT student_id INTO v_sup_student FROM student_profile_inference WHERE student_profile_inference_id=NEW.supersedes_inference_id;
        IF v_sup_student IS NULL OR v_sup_student <> NEW.student_id THEN RAISE EXCEPTION 'profile inference supersedes another student'; END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS student_profile_inference_scope_guard ON student_profile_inference;
CREATE TRIGGER student_profile_inference_scope_guard
BEFORE INSERT OR UPDATE OF school_id, student_id, snapshot_id, topic_id, skill_id, generated_by_analysis_run_id, supersedes_inference_id
ON student_profile_inference
FOR EACH ROW EXECUTE FUNCTION validate_student_profile_inference_scope();

CREATE OR REPLACE FUNCTION validate_student_profile_input()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school uuid;
    v_student uuid;
    v_ref_school uuid;
    v_ref_student uuid;
    v_run uuid;
    v_output_type text;
    v_attribution_student uuid;
    v_attribution_resolution uuid;
    v_attribution_participation uuid;
    v_ns uuid;
    v_ew uuid;
BEGIN
    SELECT school_id, student_id INTO v_school, v_student
      FROM student_profile_snapshot WHERE snapshot_id=NEW.snapshot_id;
    IF v_school IS NULL THEN RAISE EXCEPTION 'profile snapshot missing'; END IF;

    IF NEW.skill_assessment_id IS NOT NULL THEN
        SELECT school_id, student_id, generated_by_analysis_run_id
          INTO v_ref_school, v_ref_student, v_run
          FROM skill_assessment WHERE skill_assessment_id=NEW.skill_assessment_id;
        v_output_type := 'skill_assessment';
    ELSIF NEW.metric_observation_id IS NOT NULL THEN
        SELECT school_id, student_id, generated_by_analysis_run_id
          INTO v_ref_school, v_ref_student, v_run
          FROM metric_observation WHERE metric_observation_id=NEW.metric_observation_id;
        v_output_type := 'metric_observation';
        IF v_ref_student IS NULL THEN RAISE EXCEPTION 'student profile cannot select non-student metric observation'; END IF;
    ELSIF NEW.error_observation_id IS NOT NULL THEN
        SELECT school_id, student_id, generated_by_analysis_run_id
          INTO v_ref_school, v_ref_student, v_run
          FROM error_observation WHERE error_observation_id=NEW.error_observation_id;
        v_output_type := 'error_observation';
    ELSIF NEW.success_observation_id IS NOT NULL THEN
        SELECT school_id, student_id, generated_by_analysis_run_id
          INTO v_ref_school, v_ref_student, v_run
          FROM success_observation WHERE success_observation_id=NEW.success_observation_id;
        v_output_type := 'success_observation';
    ELSIF NEW.decision_assessment_id IS NOT NULL THEN
        SELECT d.school_id, d.student_id
          INTO v_ref_school, v_ref_student
          FROM decision_assessment da
          JOIN decision d ON d.decision_id=da.decision_id
         WHERE da.decision_assessment_id=NEW.decision_assessment_id;
    ELSIF NEW.exercise_attempt_assessment_id IS NOT NULL THEN
        SELECT ea.school_id, ea.student_id
          INTO v_ref_school, v_ref_student
          FROM exercise_attempt_assessment aa
          JOIN exercise_attempt ea ON ea.exercise_attempt_id=aa.exercise_attempt_id
         WHERE aa.exercise_attempt_assessment_id=NEW.exercise_attempt_assessment_id;
    ELSIF NEW.source_observation_id IS NOT NULL THEN
        SELECT s.school_id
          INTO v_ref_school
          FROM source_observation so
          JOIN source s ON s.source_id=so.source_id
         WHERE so.source_observation_id=NEW.source_observation_id;
        v_ref_student := v_student;
    ELSIF NEW.table_result_id IS NOT NULL THEN
        SELECT school_id, ns_participation_id, ew_participation_id
          INTO v_ref_school, v_ns, v_ew
          FROM table_result WHERE result_id=NEW.table_result_id;

        SELECT tia.student_id, tia.entity_resolution_decision_id, tpm.tournament_participation_id
          INTO v_attribution_student, v_attribution_resolution, v_attribution_participation
          FROM tournament_identity_attribution tia
          JOIN tournament_participant_member tpm
            ON tpm.tournament_participant_member_id=tia.tournament_participant_member_id
         WHERE tia.tournament_identity_attribution_id=NEW.tournament_identity_attribution_id;

        IF v_attribution_student IS NULL OR v_attribution_student <> v_student THEN
            RAISE EXCEPTION 'tournament profile input attribution does not belong to snapshot student';
        END IF;
        IF v_attribution_resolution IS NULL OR v_attribution_resolution <> NEW.entity_resolution_decision_id THEN
            RAISE EXCEPTION 'tournament profile input resolution does not match attribution';
        END IF;
        IF v_attribution_participation IS NULL OR (v_attribution_participation <> v_ns AND v_attribution_participation <> v_ew) THEN
            RAISE EXCEPTION 'tournament attribution is not a participant in selected table result';
        END IF;
        v_ref_student := v_student;
    END IF;

    IF v_ref_school IS NULL OR v_ref_school <> v_school OR v_ref_student IS NULL OR v_ref_student <> v_student THEN
        RAISE EXCEPTION 'profile input does not belong to snapshot student/school';
    END IF;

    -- A derived observation from AnalysisRun may enter the profile only if that exact
    -- output was explicitly published. Staging/partial outputs cannot leak into profile.
    IF v_run IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
              FROM analysis_run_output aro
              JOIN output_publication op ON op.publication_id=aro.publication_id
             WHERE aro.analysis_run_id=v_run
               AND aro.output_entity_id=COALESCE(
                    NEW.skill_assessment_id,
                    NEW.metric_observation_id,
                    NEW.error_observation_id,
                    NEW.success_observation_id
               )
               AND aro.output_entity_type=v_output_type
               AND aro.status='published'
               AND op.status='published'
        ) THEN
            RAISE EXCEPTION 'derived profile input is not explicitly published';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS student_profile_input_guard ON student_profile_input;
CREATE TRIGGER student_profile_input_guard
BEFORE INSERT OR UPDATE
ON student_profile_input
FOR EACH ROW EXECUTE FUNCTION validate_student_profile_input();

-- -----------------------------------------------------------------------------
-- Atomic projection generation activation. The mutable current pointer is only a cache;
-- every switch is preserved in append-only activation history.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projection_generation_activation (
    projection_generation_activation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    projection_key text NOT NULL,
    scope_key text NOT NULL DEFAULT 'default',
    generation_id uuid NOT NULL,
    projection_run_id uuid NOT NULL REFERENCES projection_run(projection_run_id),
    previous_generation_id uuid,
    action_type text NOT NULL DEFAULT 'activate',
    reason text,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (action_type IN ('activate','rollback','invalidate'))
);
CREATE INDEX IF NOT EXISTS projection_generation_activation_history_idx
    ON projection_generation_activation(school_id, projection_key, scope_key, recorded_at DESC);

CREATE TABLE IF NOT EXISTS projection_generation_current (
    school_id uuid NOT NULL REFERENCES school(school_id),
    projection_key text NOT NULL,
    scope_key text NOT NULL DEFAULT 'default',
    generation_id uuid NOT NULL,
    projection_run_id uuid NOT NULL REFERENCES projection_run(projection_run_id),
    activation_id uuid NOT NULL REFERENCES projection_generation_activation(projection_generation_activation_id),
    activated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (school_id, projection_key, scope_key)
);
CREATE INDEX IF NOT EXISTS projection_generation_current_generation_idx
    ON projection_generation_current(generation_id, projection_key);

CREATE OR REPLACE FUNCTION activate_projection_generation(
    p_projection_run_id uuid,
    p_scope_key text DEFAULT 'default'
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_school uuid;
    v_projection_key text;
    v_generation uuid;
    v_status text;
    v_completed timestamptz;
    v_previous uuid;
    v_activation uuid;
    v_scope text := COALESCE(NULLIF(p_scope_key,''),'default');
BEGIN
    SELECT school_id, projection_key, generation_id, status, completed_at
      INTO v_school, v_projection_key, v_generation, v_status, v_completed
      FROM projection_run
     WHERE projection_run_id=p_projection_run_id;

    IF v_school IS NULL THEN
        RAISE EXCEPTION 'projection run missing';
    END IF;
    IF v_status <> 'success' OR v_completed IS NULL THEN
        RAISE EXCEPTION 'only completed successful projection run may be activated';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(v_school::text || ':' || v_projection_key || ':' || v_scope, 0));

    SELECT generation_id INTO v_previous
      FROM projection_generation_current
     WHERE school_id=v_school AND projection_key=v_projection_key AND scope_key=v_scope
     FOR UPDATE;

    INSERT INTO projection_generation_activation(
        school_id, projection_key, scope_key, generation_id, projection_run_id,
        previous_generation_id, action_type
    ) VALUES (
        v_school, v_projection_key, v_scope, v_generation, p_projection_run_id,
        v_previous, 'activate'
    ) RETURNING projection_generation_activation_id INTO v_activation;

    INSERT INTO projection_generation_current(
        school_id, projection_key, scope_key, generation_id, projection_run_id,
        activation_id, activated_at
    ) VALUES (
        v_school, v_projection_key, v_scope, v_generation, p_projection_run_id,
        v_activation, now()
    )
    ON CONFLICT (school_id, projection_key, scope_key)
    DO UPDATE SET
        generation_id=EXCLUDED.generation_id,
        projection_run_id=EXCLUDED.projection_run_id,
        activation_id=EXCLUDED.activation_id,
        activated_at=EXCLUDED.activated_at;

    RETURN v_activation;
END;
$$;

-- -----------------------------------------------------------------------------
-- Recommendations are derived objects, not facts. Content is immutable; lifecycle is
-- append-only so accepted/applied/superseded/expired/invalidated history is preserved.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recommendation (
    recommendation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    student_id uuid NOT NULL REFERENCES student(student_id),
    source_snapshot_id uuid NOT NULL REFERENCES student_profile_snapshot(snapshot_id),
    projection_run_id uuid REFERENCES projection_run(projection_run_id),
    recommendation_type text NOT NULL,
    priority_class text,
    priority_value numeric(12,6),
    rationale text,
    recommendation_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    target_topic_id uuid REFERENCES topic(topic_id),
    target_skill_id uuid REFERENCES skill(skill_id),
    target_exercise_version_id uuid REFERENCES exercise_version(exercise_version_id),
    target_knowledge_version_id uuid REFERENCES knowledge_version(knowledge_version_id),
    method_version text,
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    supersedes_recommendation_id uuid REFERENCES recommendation(recommendation_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (supersedes_recommendation_id IS NULL OR supersedes_recommendation_id <> recommendation_id)
);
CREATE INDEX IF NOT EXISTS recommendation_student_idx
    ON recommendation(student_id, created_at DESC);
CREATE INDEX IF NOT EXISTS recommendation_snapshot_idx
    ON recommendation(source_snapshot_id, priority_value DESC NULLS LAST, created_at);

CREATE TABLE IF NOT EXISTS recommendation_state_event (
    recommendation_state_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    recommendation_id uuid NOT NULL REFERENCES recommendation(recommendation_id),
    state_type text NOT NULL,
    actor_id uuid,
    reason text,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (state_type IN ('created','accepted','applied','superseded','expired','rejected','invalidated'))
);
CREATE INDEX IF NOT EXISTS recommendation_state_history_idx
    ON recommendation_state_event(recommendation_id, occurred_at DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS session_plan_recommendation (
    session_plan_id uuid NOT NULL REFERENCES session_plan(session_plan_id),
    recommendation_id uuid NOT NULL REFERENCES recommendation(recommendation_id),
    relation_type text NOT NULL DEFAULT 'uses',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_plan_id, recommendation_id, relation_type)
);

CREATE OR REPLACE FUNCTION validate_recommendation_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_student_school uuid;
    v_snapshot_school uuid;
    v_snapshot_student uuid;
    v_ref_school uuid;
    v_sup_student uuid;
BEGIN
    SELECT school_id INTO v_student_school FROM student WHERE student_id=NEW.student_id;
    SELECT school_id, student_id INTO v_snapshot_school, v_snapshot_student
      FROM student_profile_snapshot WHERE snapshot_id=NEW.source_snapshot_id;
    IF v_student_school IS NULL OR v_student_school <> NEW.school_id
       OR v_snapshot_school <> NEW.school_id OR v_snapshot_student <> NEW.student_id THEN
        RAISE EXCEPTION 'recommendation student/snapshot/school mismatch';
    END IF;

    IF NEW.projection_run_id IS NOT NULL THEN
        SELECT school_id INTO v_ref_school FROM projection_run WHERE projection_run_id=NEW.projection_run_id;
        IF v_ref_school IS NULL OR v_ref_school <> NEW.school_id THEN RAISE EXCEPTION 'recommendation projection run school mismatch'; END IF;
    END IF;
    IF NEW.target_topic_id IS NOT NULL THEN
        SELECT school_id INTO v_ref_school FROM topic WHERE topic_id=NEW.target_topic_id;
        IF v_ref_school IS NULL OR v_ref_school <> NEW.school_id THEN RAISE EXCEPTION 'recommendation topic school mismatch'; END IF;
    END IF;
    IF NEW.target_skill_id IS NOT NULL THEN
        SELECT school_id INTO v_ref_school FROM skill WHERE skill_id=NEW.target_skill_id;
        IF v_ref_school IS NULL OR v_ref_school <> NEW.school_id THEN RAISE EXCEPTION 'recommendation skill school mismatch'; END IF;
    END IF;
    IF NEW.target_exercise_version_id IS NOT NULL THEN
        SELECT e.school_id INTO v_ref_school
          FROM exercise_version ev JOIN exercise e ON e.exercise_id=ev.exercise_id
         WHERE ev.exercise_version_id=NEW.target_exercise_version_id;
        IF v_ref_school IS NULL OR v_ref_school <> NEW.school_id THEN RAISE EXCEPTION 'recommendation exercise school mismatch'; END IF;
    END IF;
    IF NEW.target_knowledge_version_id IS NOT NULL THEN
        SELECT ki.school_id INTO v_ref_school
          FROM knowledge_version kv JOIN knowledge_item ki ON ki.knowledge_item_id=kv.knowledge_item_id
         WHERE kv.knowledge_version_id=NEW.target_knowledge_version_id;
        IF v_ref_school IS NULL OR v_ref_school <> NEW.school_id THEN RAISE EXCEPTION 'recommendation knowledge school mismatch'; END IF;
    END IF;
    IF NEW.supersedes_recommendation_id IS NOT NULL THEN
        SELECT student_id INTO v_sup_student FROM recommendation WHERE recommendation_id=NEW.supersedes_recommendation_id;
        IF v_sup_student IS NULL OR v_sup_student <> NEW.student_id THEN RAISE EXCEPTION 'recommendation supersedes another student'; END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS recommendation_scope_guard ON recommendation;
CREATE TRIGGER recommendation_scope_guard
BEFORE INSERT OR UPDATE OF school_id, student_id, source_snapshot_id, projection_run_id, target_topic_id, target_skill_id, target_exercise_version_id, target_knowledge_version_id, supersedes_recommendation_id
ON recommendation
FOR EACH ROW EXECUTE FUNCTION validate_recommendation_scope();

CREATE OR REPLACE FUNCTION validate_session_plan_recommendation_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_plan_school uuid;
    v_rec_school uuid;
BEGIN
    SELECT s.school_id INTO v_plan_school
      FROM session_plan sp JOIN session s ON s.session_id=sp.session_id
     WHERE sp.session_plan_id=NEW.session_plan_id;
    SELECT school_id INTO v_rec_school FROM recommendation WHERE recommendation_id=NEW.recommendation_id;
    IF v_plan_school IS NULL OR v_rec_school IS NULL OR v_plan_school <> v_rec_school THEN
        RAISE EXCEPTION 'session plan/recommendation school mismatch';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS session_plan_recommendation_scope_guard ON session_plan_recommendation;
CREATE TRIGGER session_plan_recommendation_scope_guard
BEFORE INSERT OR UPDATE OF session_plan_id, recommendation_id
ON session_plan_recommendation
FOR EACH ROW EXECUTE FUNCTION validate_session_plan_recommendation_scope();

-- -----------------------------------------------------------------------------
-- Runtime boundaries.
-- Observations, snapshot components, inferences and recommendations are append-only.
-- Projection current pointer can only be changed through guarded atomic activation.
-- -----------------------------------------------------------------------------
GRANT INSERT ON TABLE
    skill_assessment,
    metric_observation,
    error_observation,
    success_observation,
    student_profile_snapshot,
    student_profile_snapshot_state_event,
    student_profile_snapshot_metric,
    student_profile_snapshot_skill,
    student_profile_snapshot_topic,
    student_profile_inference,
    student_profile_input,
    recommendation,
    recommendation_state_event,
    session_plan_recommendation
TO bridge_school_worker;

REVOKE UPDATE, DELETE ON TABLE
    skill_assessment,
    metric_observation,
    error_observation,
    success_observation,
    student_profile_snapshot,
    student_profile_snapshot_state_event,
    student_profile_snapshot_metric,
    student_profile_snapshot_skill,
    student_profile_snapshot_topic,
    student_profile_inference,
    student_profile_input,
    recommendation,
    recommendation_state_event,
    session_plan_recommendation
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE INSERT, UPDATE, DELETE ON TABLE
    projection_generation_activation,
    projection_generation_current
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE INSERT, UPDATE, DELETE ON TABLE
    skill_assessment,
    metric_observation,
    error_observation,
    success_observation,
    student_profile_snapshot_state_event,
    student_profile_snapshot_metric,
    student_profile_snapshot_skill,
    student_profile_snapshot_topic,
    student_profile_inference,
    student_profile_input,
    recommendation,
    recommendation_state_event,
    session_plan_recommendation
FROM bridge_school_app;

REVOKE ALL ON FUNCTION activate_projection_generation(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION activate_projection_generation(uuid, text) TO bridge_school_worker;

REVOKE ALL ON FUNCTION validate_skill_assessment_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_metric_observation_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_learning_observation_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_student_profile_snapshot_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_student_profile_metric_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_student_profile_skill_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_student_profile_topic_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_student_profile_inference_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_student_profile_input() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_recommendation_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_session_plan_recommendation_scope() FROM PUBLIC;

REVOKE EXECUTE ON FUNCTION validate_skill_assessment_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_metric_observation_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_learning_observation_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_student_profile_snapshot_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_student_profile_metric_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_student_profile_skill_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_student_profile_topic_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_student_profile_inference_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_student_profile_input() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_recommendation_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_session_plan_recommendation_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

INSERT INTO schema_migration(migration_key)
VALUES ('0011_student_profile_projections')
ON CONFLICT DO NOTHING;

COMMIT;
