\set ON_ERROR_STOP on
BEGIN;

-- Stable exercise identity. Content evolves through ExerciseVersion rather than
-- overwriting the conceptual exercise.
CREATE TABLE IF NOT EXISTS exercise (
    exercise_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    stable_key text NOT NULL,
    title text NOT NULL,
    exercise_type text NOT NULL,
    source_id uuid REFERENCES source(source_id),
    deal_id uuid REFERENCES deal(deal_id),
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (school_id, stable_key)
);
CREATE INDEX IF NOT EXISTS exercise_school_type_idx
    ON exercise(school_id, exercise_type, status, created_at DESC);

CREATE TABLE IF NOT EXISTS exercise_version (
    exercise_version_id uuid PRIMARY KEY DEFAULT uuidv7(),
    exercise_id uuid NOT NULL REFERENCES exercise(exercise_id),
    version_no integer NOT NULL CHECK (version_no > 0),
    prompt jsonb NOT NULL DEFAULT '{}'::jsonb,
    expected_solution jsonb NOT NULL DEFAULT '{}'::jsonb,
    rubric jsonb NOT NULL DEFAULT '{}'::jsonb,
    generated_by text,
    method_version text,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'candidate',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (exercise_id, version_no)
);
CREATE INDEX IF NOT EXISTS exercise_version_exercise_idx
    ON exercise_version(exercise_id, version_no DESC);

CREATE TABLE IF NOT EXISTS exercise_topic (
    exercise_id uuid NOT NULL REFERENCES exercise(exercise_id),
    topic_id uuid NOT NULL REFERENCES topic(topic_id),
    relation_type text NOT NULL DEFAULT 'trains',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (exercise_id, topic_id, relation_type)
);

CREATE TABLE IF NOT EXISTS exercise_skill (
    exercise_id uuid NOT NULL REFERENCES exercise(exercise_id),
    skill_id uuid NOT NULL REFERENCES skill(skill_id),
    relation_type text NOT NULL DEFAULT 'trains',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (exercise_id, skill_id, relation_type)
);

-- Assignment is an instructional event. Group/course/session describe where it came
-- from; actual recipients are frozen explicitly in HomeworkRecipient so later group
-- changes do not rewrite historical assignment membership.
CREATE TABLE IF NOT EXISTS homework_assignment (
    homework_assignment_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    source_session_id uuid REFERENCES session(session_id),
    source_group_id uuid REFERENCES learning_group(group_id),
    course_version_id uuid REFERENCES course_version(course_version_id),
    title text NOT NULL,
    instructions text,
    assigned_by_person_id uuid REFERENCES person(person_id),
    assigned_at timestamptz NOT NULL DEFAULT now(),
    due_at timestamptz,
    status text NOT NULL DEFAULT 'active',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (due_at IS NULL OR due_at >= assigned_at)
);
CREATE INDEX IF NOT EXISTS homework_assignment_school_due_idx
    ON homework_assignment(school_id, due_at, status);
CREATE INDEX IF NOT EXISTS homework_assignment_session_idx
    ON homework_assignment(source_session_id, assigned_at DESC)
    WHERE source_session_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS homework_item (
    homework_item_id uuid PRIMARY KEY DEFAULT uuidv7(),
    homework_assignment_id uuid NOT NULL REFERENCES homework_assignment(homework_assignment_id),
    sequence_no integer NOT NULL CHECK (sequence_no > 0),
    exercise_version_id uuid REFERENCES exercise_version(exercise_version_id),
    prompt_override jsonb NOT NULL DEFAULT '{}'::jsonb,
    required_flag boolean NOT NULL DEFAULT true,
    weight numeric(10,4) CHECK (weight IS NULL OR weight >= 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (exercise_version_id IS NOT NULL OR prompt_override <> '{}'::jsonb),
    UNIQUE (homework_assignment_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS homework_item_exercise_idx
    ON homework_item(exercise_version_id)
    WHERE exercise_version_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS homework_recipient (
    homework_recipient_id uuid PRIMARY KEY DEFAULT uuidv7(),
    homework_assignment_id uuid NOT NULL REFERENCES homework_assignment(homework_assignment_id),
    student_id uuid NOT NULL REFERENCES student(student_id),
    assigned_at timestamptz NOT NULL DEFAULT now(),
    due_at timestamptz,
    status text NOT NULL DEFAULT 'assigned',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (due_at IS NULL OR due_at >= assigned_at),
    UNIQUE (homework_assignment_id, student_id)
);
CREATE INDEX IF NOT EXISTS homework_recipient_student_due_idx
    ON homework_recipient(student_id, due_at, status);

CREATE TABLE IF NOT EXISTS homework_submission (
    homework_submission_id uuid PRIMARY KEY DEFAULT uuidv7(),
    homework_assignment_id uuid NOT NULL,
    student_id uuid NOT NULL,
    submission_no integer NOT NULL DEFAULT 1 CHECK (submission_no > 0),
    started_at timestamptz NOT NULL DEFAULT now(),
    submitted_at timestamptz,
    status text NOT NULL DEFAULT 'draft',
    student_note text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (submitted_at IS NULL OR submitted_at >= started_at),
    CHECK (status IN ('draft','submitted','superseded','withdrawn')),
    UNIQUE (homework_assignment_id, student_id, submission_no),
    FOREIGN KEY (homework_assignment_id, student_id)
        REFERENCES homework_recipient(homework_assignment_id, student_id)
);
CREATE INDEX IF NOT EXISTS homework_submission_student_idx
    ON homework_submission(student_id, started_at DESC);

-- Attempt is a factual student action. Evaluation is deliberately separated below.
CREATE TABLE IF NOT EXISTS exercise_attempt (
    exercise_attempt_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    student_id uuid NOT NULL REFERENCES student(student_id),
    exercise_version_id uuid NOT NULL REFERENCES exercise_version(exercise_version_id),
    homework_item_id uuid REFERENCES homework_item(homework_item_id),
    session_id uuid REFERENCES session(session_id),
    interaction_id uuid REFERENCES learning_interaction(interaction_id),
    attempt_no integer NOT NULL DEFAULT 1 CHECK (attempt_no > 0),
    started_at timestamptz NOT NULL DEFAULT now(),
    submitted_at timestamptz,
    response jsonb NOT NULL DEFAULT '{}'::jsonb,
    stated_reasoning text,
    status text NOT NULL DEFAULT 'in_progress',
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (submitted_at IS NULL OR submitted_at >= started_at),
    CHECK (status IN ('in_progress','submitted','abandoned','superseded'))
);
CREATE UNIQUE INDEX IF NOT EXISTS exercise_attempt_number_uk
    ON exercise_attempt(
        student_id,
        exercise_version_id,
        COALESCE(homework_item_id, '00000000-0000-0000-0000-000000000000'::uuid),
        attempt_no
    );
CREATE INDEX IF NOT EXISTS exercise_attempt_student_time_idx
    ON exercise_attempt(student_id, started_at DESC);
CREATE INDEX IF NOT EXISTS exercise_attempt_item_idx
    ON exercise_attempt(homework_item_id, student_id)
    WHERE homework_item_id IS NOT NULL;

-- A submission can preserve several attempts per item, while exactly one may be the
-- selected answer for that submission.
CREATE TABLE IF NOT EXISTS homework_submission_attempt (
    homework_submission_attempt_id uuid PRIMARY KEY DEFAULT uuidv7(),
    homework_submission_id uuid NOT NULL REFERENCES homework_submission(homework_submission_id),
    homework_item_id uuid NOT NULL REFERENCES homework_item(homework_item_id),
    exercise_attempt_id uuid NOT NULL REFERENCES exercise_attempt(exercise_attempt_id),
    selected_flag boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (homework_submission_id, homework_item_id, exercise_attempt_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS homework_submission_one_selected_attempt_uk
    ON homework_submission_attempt(homework_submission_id, homework_item_id)
    WHERE selected_flag;
CREATE UNIQUE INDEX IF NOT EXISTS homework_attempt_single_submission_uk
    ON homework_submission_attempt(exercise_attempt_id);

-- Preserve fact/evaluation separation: assessments are append-only observations of an
-- attempt and can supersede another assessment without rewriting it.
CREATE TABLE IF NOT EXISTS exercise_attempt_assessment (
    exercise_attempt_assessment_id uuid PRIMARY KEY DEFAULT uuidv7(),
    exercise_attempt_id uuid NOT NULL REFERENCES exercise_attempt(exercise_attempt_id),
    supersedes_assessment_id uuid REFERENCES exercise_attempt_assessment(exercise_attempt_assessment_id),
    assessed_at timestamptz NOT NULL DEFAULT now(),
    assessor_actor_id uuid,
    authority_class text NOT NULL DEFAULT 'ai',
    assessment_purpose text NOT NULL DEFAULT 'learning',
    score numeric(12,4),
    max_score numeric(12,4),
    result jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    method_version text,
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (max_score IS NULL OR max_score >= 0),
    CHECK (score IS NULL OR max_score IS NULL OR score <= max_score),
    CHECK (supersedes_assessment_id IS NULL OR supersedes_assessment_id <> exercise_attempt_assessment_id)
);
CREATE INDEX IF NOT EXISTS exercise_attempt_assessment_attempt_idx
    ON exercise_attempt_assessment(exercise_attempt_id, assessed_at DESC);

-- Cross-object integrity for a submission item/attempt link.
CREATE OR REPLACE FUNCTION validate_homework_submission_attempt()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_submission_assignment uuid;
    v_submission_student uuid;
    v_item_assignment uuid;
    v_attempt_student uuid;
    v_attempt_item uuid;
BEGIN
    SELECT homework_assignment_id, student_id
      INTO v_submission_assignment, v_submission_student
      FROM homework_submission
     WHERE homework_submission_id = NEW.homework_submission_id;

    SELECT homework_assignment_id
      INTO v_item_assignment
      FROM homework_item
     WHERE homework_item_id = NEW.homework_item_id;

    SELECT student_id, homework_item_id
      INTO v_attempt_student, v_attempt_item
      FROM exercise_attempt
     WHERE exercise_attempt_id = NEW.exercise_attempt_id;

    IF v_submission_assignment IS NULL OR v_item_assignment IS NULL OR v_attempt_student IS NULL THEN
        RAISE EXCEPTION 'submission, homework item, or attempt not found';
    END IF;
    IF v_submission_assignment <> v_item_assignment THEN
        RAISE EXCEPTION 'homework item does not belong to submission assignment';
    END IF;
    IF v_submission_student <> v_attempt_student THEN
        RAISE EXCEPTION 'attempt student does not match submission student';
    END IF;
    IF v_attempt_item IS NULL OR v_attempt_item <> NEW.homework_item_id THEN
        RAISE EXCEPTION 'attempt is not bound to the linked homework item';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS homework_submission_attempt_guard ON homework_submission_attempt;
CREATE TRIGGER homework_submission_attempt_guard
BEFORE INSERT OR UPDATE OF homework_submission_id, homework_item_id, exercise_attempt_id
ON homework_submission_attempt
FOR EACH ROW
EXECUTE FUNCTION validate_homework_submission_attempt();

-- Runtime capability boundaries.
-- AI/background worker may build and version exercise/homework content, but cannot delete it.
GRANT INSERT, UPDATE ON TABLE
    exercise,
    exercise_version,
    exercise_topic,
    exercise_skill,
    homework_assignment,
    homework_item,
    homework_recipient
TO bridge_school_worker;

-- Interactive runtime records student work; it cannot create assignments or assessments.
GRANT INSERT, UPDATE ON TABLE
    homework_submission,
    exercise_attempt,
    homework_submission_attempt
TO bridge_school_app;

-- Assessment history is append-only for the worker.
GRANT INSERT ON TABLE exercise_attempt_assessment TO bridge_school_worker;
REVOKE UPDATE, DELETE ON TABLE exercise_attempt_assessment FROM bridge_school_worker;

REVOKE DELETE ON TABLE
    exercise,
    exercise_version,
    exercise_topic,
    exercise_skill,
    homework_assignment,
    homework_item,
    homework_recipient,
    homework_submission,
    exercise_attempt,
    homework_submission_attempt,
    exercise_attempt_assessment
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

-- Validation trigger is internal; runtime only receives table operations.
REVOKE ALL ON FUNCTION validate_homework_submission_attempt() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION validate_homework_submission_attempt() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

INSERT INTO schema_migration(migration_key)
VALUES ('0008_exercises_homework')
ON CONFLICT DO NOTHING;

COMMIT;
