\set ON_ERROR_STOP on
BEGIN;

-- Stable groups/classes. "group" is avoided as a SQL keyword-like name.
CREATE TABLE IF NOT EXISTS learning_group (
    group_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    name text NOT NULL,
    group_type text NOT NULL DEFAULT 'class',
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (school_id, name)
);
CREATE INDEX IF NOT EXISTS learning_group_school_status_idx
    ON learning_group(school_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS group_membership (
    group_membership_id uuid PRIMARY KEY DEFAULT uuidv7(),
    group_id uuid NOT NULL REFERENCES learning_group(group_id),
    student_id uuid NOT NULL REFERENCES student(student_id),
    membership_role text NOT NULL DEFAULT 'student',
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS group_membership_one_open_uk
    ON group_membership(group_id, student_id)
    WHERE status='active' AND valid_to IS NULL;
CREATE INDEX IF NOT EXISTS group_membership_student_idx
    ON group_membership(student_id, valid_from DESC);

-- Partnership is a first-class time-varying bridge context. Agreements attach to it.
CREATE TABLE IF NOT EXISTS partnership (
    partnership_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    display_name text,
    partnership_type text NOT NULL DEFAULT 'pair',
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS partnership_school_status_idx
    ON partnership(school_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS partnership_member (
    partnership_member_id uuid PRIMARY KEY DEFAULT uuidv7(),
    partnership_id uuid NOT NULL REFERENCES partnership(partnership_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    seat_preference text,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS partnership_member_one_open_uk
    ON partnership_member(partnership_id, person_id)
    WHERE status='active' AND valid_to IS NULL;
CREATE INDEX IF NOT EXISTS partnership_member_person_idx
    ON partnership_member(person_id, valid_from DESC);

CREATE TABLE IF NOT EXISTS partnership_context (
    partnership_context_id uuid PRIMARY KEY DEFAULT uuidv7(),
    partnership_id uuid NOT NULL REFERENCES partnership(partnership_id),
    context_key text NOT NULL DEFAULT 'default',
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    context jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE INDEX IF NOT EXISTS partnership_context_lookup_idx
    ON partnership_context(partnership_id, context_key, valid_from DESC);

-- Repair two intentionally deferred references from the initial learning-core migration.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='learning_interaction_group_fk') THEN
        ALTER TABLE learning_interaction
        ADD CONSTRAINT learning_interaction_group_fk
        FOREIGN KEY (group_id) REFERENCES learning_group(group_id) NOT VALID;
        ALTER TABLE learning_interaction VALIDATE CONSTRAINT learning_interaction_group_fk;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='agreement_set_partnership_fk') THEN
        ALTER TABLE agreement_set
        ADD CONSTRAINT agreement_set_partnership_fk
        FOREIGN KEY (partnership_id) REFERENCES partnership(partnership_id) NOT VALID;
        ALTER TABLE agreement_set VALIDATE CONSTRAINT agreement_set_partnership_fk;
    END IF;
END $$;

-- Courses and their versioned curriculum definitions are configuration, not student state.
CREATE TABLE IF NOT EXISTS course (
    course_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    stable_key text NOT NULL,
    name text NOT NULL,
    description text,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (school_id, stable_key)
);

CREATE TABLE IF NOT EXISTS course_version (
    course_version_id uuid PRIMARY KEY DEFAULT uuidv7(),
    course_id uuid NOT NULL REFERENCES course(course_id),
    version_no integer NOT NULL CHECK (version_no > 0),
    version_label text,
    curriculum jsonb NOT NULL DEFAULT '{}'::jsonb,
    effective_from timestamptz,
    effective_to timestamptz,
    status text NOT NULL DEFAULT 'candidate',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from),
    UNIQUE (course_id, version_no)
);

CREATE TABLE IF NOT EXISTS course_topic (
    course_version_id uuid NOT NULL REFERENCES course_version(course_version_id),
    topic_id uuid NOT NULL REFERENCES topic(topic_id),
    sequence_no integer,
    required_flag boolean NOT NULL DEFAULT true,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (course_version_id, topic_id)
);

CREATE TABLE IF NOT EXISTS course_skill (
    course_version_id uuid NOT NULL REFERENCES course_version(course_version_id),
    skill_id uuid NOT NULL REFERENCES skill(skill_id),
    target_mastery text,
    required_flag boolean NOT NULL DEFAULT true,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (course_version_id, skill_id)
);

-- Formal lesson/session is a specialization of the generic LearningInteraction.
CREATE TABLE IF NOT EXISTS session (
    session_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    interaction_id uuid NOT NULL UNIQUE REFERENCES learning_interaction(interaction_id),
    course_version_id uuid REFERENCES course_version(course_version_id),
    instructor_person_id uuid REFERENCES person(person_id),
    planned_start_at timestamptz,
    planned_end_at timestamptz,
    actual_start_at timestamptz,
    actual_end_at timestamptz,
    format text,
    status text NOT NULL DEFAULT 'planned',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (planned_end_at IS NULL OR planned_start_at IS NULL OR planned_end_at > planned_start_at),
    CHECK (actual_end_at IS NULL OR actual_start_at IS NULL OR actual_end_at > actual_start_at)
);
CREATE INDEX IF NOT EXISTS session_school_time_idx
    ON session(school_id, COALESCE(actual_start_at, planned_start_at) DESC);

CREATE TABLE IF NOT EXISTS session_participation (
    session_participation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    session_id uuid NOT NULL REFERENCES session(session_id),
    person_id uuid REFERENCES person(person_id),
    student_id uuid REFERENCES student(student_id),
    participant_role text NOT NULL DEFAULT 'student',
    attendance_status text NOT NULL DEFAULT 'unknown',
    joined_at timestamptz,
    left_at timestamptz,
    participation_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (person_id IS NOT NULL OR student_id IS NOT NULL),
    CHECK (left_at IS NULL OR joined_at IS NULL OR left_at >= joined_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS session_participation_student_uk
    ON session_participation(session_id, student_id)
    WHERE student_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS session_participation_person_uk
    ON session_participation(session_id, person_id)
    WHERE person_id IS NOT NULL AND student_id IS NULL;
CREATE INDEX IF NOT EXISTS session_participation_student_time_idx
    ON session_participation(student_id, session_id)
    WHERE student_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS session_plan (
    session_plan_id uuid PRIMARY KEY DEFAULT uuidv7(),
    session_id uuid NOT NULL REFERENCES session(session_id),
    version_no integer NOT NULL CHECK (version_no > 0),
    plan jsonb NOT NULL DEFAULT '{}'::jsonb,
    generated_by text,
    method_version text,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'draft',
    created_at timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz,
    UNIQUE (session_id, version_no)
);
CREATE UNIQUE INDEX IF NOT EXISTS session_plan_one_active_uk
    ON session_plan(session_id)
    WHERE status='active';

-- Episodes support semantic segmentation of lessons/videos without forcing every interaction
-- to be a formal Session.
CREATE TABLE IF NOT EXISTS episode (
    episode_id uuid PRIMARY KEY DEFAULT uuidv7(),
    interaction_id uuid NOT NULL REFERENCES learning_interaction(interaction_id),
    session_id uuid REFERENCES session(session_id),
    sequence_no integer NOT NULL CHECK (sequence_no > 0),
    episode_type text NOT NULL,
    topic_id uuid REFERENCES topic(topic_id),
    skill_id uuid REFERENCES skill(skill_id),
    start_offset_seconds numeric(12,3),
    end_offset_seconds numeric(12,3),
    summary text,
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (start_offset_seconds IS NULL OR start_offset_seconds >= 0),
    CHECK (end_offset_seconds IS NULL OR start_offset_seconds IS NULL OR end_offset_seconds >= start_offset_seconds),
    UNIQUE (interaction_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS episode_topic_idx ON episode(topic_id, interaction_id);
CREATE INDEX IF NOT EXISTS episode_skill_idx ON episode(skill_id, interaction_id);

-- Runtime grants: curriculum definitions remain owner/admin-write only.
GRANT INSERT, UPDATE ON TABLE
    learning_group,
    group_membership,
    partnership,
    partnership_member,
    partnership_context,
    session,
    session_participation
TO bridge_school_app;

GRANT INSERT, UPDATE ON TABLE
    session_plan,
    episode
TO bridge_school_worker;

REVOKE DELETE ON TABLE
    learning_group,
    group_membership,
    partnership,
    partnership_member,
    partnership_context,
    session,
    session_participation,
    session_plan,
    episode,
    course,
    course_version,
    course_topic,
    course_skill
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE INSERT, UPDATE, DELETE ON TABLE
    course,
    course_version,
    course_topic,
    course_skill
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

INSERT INTO schema_migration(migration_key)
VALUES ('0007_learning_context')
ON CONFLICT DO NOTHING;

COMMIT;
