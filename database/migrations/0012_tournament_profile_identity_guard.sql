\set ON_ERROR_STOP on
BEGIN;

-- A student-facing Error/SuccessObservation derived from an external tournament
-- result must preserve the exact identity attribution and EntityResolutionDecision
-- that justified associating that source fact with the Student.
ALTER TABLE error_observation
    ADD COLUMN IF NOT EXISTS tournament_identity_attribution_id uuid REFERENCES tournament_identity_attribution(tournament_identity_attribution_id),
    ADD COLUMN IF NOT EXISTS entity_resolution_decision_id uuid REFERENCES entity_resolution_decision(resolution_id);

ALTER TABLE success_observation
    ADD COLUMN IF NOT EXISTS tournament_identity_attribution_id uuid REFERENCES tournament_identity_attribution(tournament_identity_attribution_id),
    ADD COLUMN IF NOT EXISTS entity_resolution_decision_id uuid REFERENCES entity_resolution_decision(resolution_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='error_observation_tournament_identity_ck') THEN
        ALTER TABLE error_observation
        ADD CONSTRAINT error_observation_tournament_identity_ck CHECK (
            (table_result_id IS NULL AND tournament_identity_attribution_id IS NULL AND entity_resolution_decision_id IS NULL)
            OR (table_result_id IS NOT NULL AND tournament_identity_attribution_id IS NOT NULL AND entity_resolution_decision_id IS NOT NULL)
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='success_observation_tournament_identity_ck') THEN
        ALTER TABLE success_observation
        ADD CONSTRAINT success_observation_tournament_identity_ck CHECK (
            (table_result_id IS NULL AND tournament_identity_attribution_id IS NULL AND entity_resolution_decision_id IS NULL)
            OR (table_result_id IS NOT NULL AND tournament_identity_attribution_id IS NOT NULL AND entity_resolution_decision_id IS NOT NULL)
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS error_observation_resolution_idx
    ON error_observation(entity_resolution_decision_id, student_id, observed_at DESC)
    WHERE entity_resolution_decision_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS success_observation_resolution_idx
    ON success_observation(entity_resolution_decision_id, student_id, observed_at DESC)
    WHERE entity_resolution_decision_id IS NOT NULL;

-- This guard is deliberately named with an "a_" prefix so it runs before the wider
-- student_profile_input_guard. Missing identity provenance is a constraint violation;
-- deeper attribution/scope validation is left to the existing profile-input guard.
CREATE OR REPLACE FUNCTION validate_student_profile_tournament_identity_presence()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.table_result_id IS NOT NULL
       AND (NEW.tournament_identity_attribution_id IS NULL OR NEW.entity_resolution_decision_id IS NULL) THEN
        RAISE EXCEPTION 'tournament profile input requires identity attribution and resolution decision'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS a_student_profile_tournament_identity_presence_guard ON student_profile_input;
CREATE TRIGGER a_student_profile_tournament_identity_presence_guard
BEFORE INSERT OR UPDATE OF table_result_id, tournament_identity_attribution_id, entity_resolution_decision_id
ON student_profile_input
FOR EACH ROW EXECUTE FUNCTION validate_student_profile_tournament_identity_presence();

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
    v_attribution_student uuid;
    v_attribution_resolution uuid;
    v_attribution_participation uuid;
    v_ns uuid;
    v_ew uuid;
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
        IF NEW.tournament_identity_attribution_id IS NULL OR NEW.entity_resolution_decision_id IS NULL THEN
            RAISE EXCEPTION 'tournament learning observation requires identity attribution and resolution decision'
                USING ERRCODE='23514';
        END IF;

        SELECT school_id, ns_participation_id, ew_participation_id
          INTO v_ref_school, v_ns, v_ew
          FROM table_result WHERE result_id=NEW.table_result_id;
        IF v_ref_school IS NULL OR v_ref_school <> NEW.school_id THEN
            RAISE EXCEPTION 'learning observation table result belongs to another school or is missing';
        END IF;

        SELECT tia.student_id, tia.entity_resolution_decision_id, tpm.tournament_participation_id
          INTO v_attribution_student, v_attribution_resolution, v_attribution_participation
          FROM tournament_identity_attribution tia
          JOIN tournament_participant_member tpm
            ON tpm.tournament_participant_member_id=tia.tournament_participant_member_id
         WHERE tia.tournament_identity_attribution_id=NEW.tournament_identity_attribution_id;

        IF v_attribution_student IS NULL OR v_attribution_student <> NEW.student_id THEN
            RAISE EXCEPTION 'tournament learning observation attribution does not belong to student';
        END IF;
        IF v_attribution_resolution IS NULL OR v_attribution_resolution <> NEW.entity_resolution_decision_id THEN
            RAISE EXCEPTION 'tournament learning observation resolution does not match attribution';
        END IF;
        IF v_attribution_participation IS NULL OR (v_attribution_participation <> v_ns AND v_attribution_participation <> v_ew) THEN
            RAISE EXCEPTION 'tournament learning observation attribution is not a participant in table result';
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

-- Triggers created by 0011 already call this function; include the new columns in the
-- UPDATE trigger column lists so future owner-side corrections cannot bypass the guard.
DROP TRIGGER IF EXISTS error_observation_scope_guard ON error_observation;
CREATE TRIGGER error_observation_scope_guard
BEFORE INSERT OR UPDATE OF school_id, student_id, decision_id, exercise_attempt_id, table_result_id, tournament_identity_attribution_id, entity_resolution_decision_id, skill_id, topic_id, generated_by_analysis_run_id
ON error_observation
FOR EACH ROW EXECUTE FUNCTION validate_learning_observation_scope();

DROP TRIGGER IF EXISTS success_observation_scope_guard ON success_observation;
CREATE TRIGGER success_observation_scope_guard
BEFORE INSERT OR UPDATE OF school_id, student_id, decision_id, exercise_attempt_id, table_result_id, tournament_identity_attribution_id, entity_resolution_decision_id, skill_id, topic_id, generated_by_analysis_run_id
ON success_observation
FOR EACH ROW EXECUTE FUNCTION validate_learning_observation_scope();

-- The new provenance columns are part of append-only observation facts.
REVOKE UPDATE ON TABLE error_observation, success_observation FROM bridge_school_worker;

REVOKE ALL ON FUNCTION validate_student_profile_tournament_identity_presence() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION validate_student_profile_tournament_identity_presence() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

INSERT INTO schema_migration(migration_key)
VALUES ('0012_tournament_profile_identity_guard')
ON CONFLICT DO NOTHING;

COMMIT;
