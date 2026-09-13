\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_person2 uuid;
    v_student uuid;
    v_student2 uuid;
    v_exercise uuid;
    v_version uuid;
    v_assignment uuid;
    v_assignment2 uuid;
    v_item uuid;
    v_item2 uuid;
    v_submission uuid;
    v_attempt uuid;
    v_attempt2 uuid;
    v_assessment uuid;
    failed boolean;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'school seed missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('CI homework student') RETURNING person_id INTO v_person;
    INSERT INTO person(preferred_name) VALUES ('CI homework other student') RETURNING person_id INTO v_person2;
    INSERT INTO student(school_id, person_id) VALUES (v_school, v_person) RETURNING student_id INTO v_student;
    INSERT INTO student(school_id, person_id) VALUES (v_school, v_person2) RETURNING student_id INTO v_student2;

    INSERT INTO exercise(school_id, stable_key, title, exercise_type)
    VALUES (v_school, 'ci-homework-exercise', 'CI exercise', 'decision')
    RETURNING exercise_id INTO v_exercise;

    INSERT INTO exercise_version(exercise_id, version_no, prompt, expected_solution, status)
    VALUES (v_exercise, 1, '{"question":"Q"}'::jsonb, '{"answer":"A"}'::jsonb, 'active')
    RETURNING exercise_version_id INTO v_version;

    failed := false;
    BEGIN
        INSERT INTO exercise_version(exercise_id, version_no)
        VALUES (v_exercise, 1);
    EXCEPTION WHEN unique_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'exercise version uniqueness failed'; END IF;

    INSERT INTO homework_assignment(school_id, title, assigned_at, due_at)
    VALUES (v_school, 'CI homework', now(), now() + interval '1 day')
    RETURNING homework_assignment_id INTO v_assignment;

    failed := false;
    BEGIN
        INSERT INTO homework_item(homework_assignment_id, sequence_no)
        VALUES (v_assignment, 1);
    EXCEPTION WHEN check_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'homework item content guard failed'; END IF;

    INSERT INTO homework_item(homework_assignment_id, sequence_no, exercise_version_id)
    VALUES (v_assignment, 1, v_version)
    RETURNING homework_item_id INTO v_item;

    INSERT INTO homework_recipient(homework_assignment_id, student_id)
    VALUES (v_assignment, v_student);

    failed := false;
    BEGIN
        INSERT INTO homework_submission(homework_assignment_id, student_id)
        VALUES (v_assignment, v_student2);
    EXCEPTION WHEN foreign_key_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'submission recipient guard failed'; END IF;

    INSERT INTO homework_submission(homework_assignment_id, student_id, status)
    VALUES (v_assignment, v_student, 'draft')
    RETURNING homework_submission_id INTO v_submission;

    INSERT INTO exercise_attempt(school_id, student_id, exercise_version_id, homework_item_id, attempt_no, response, status)
    VALUES (v_school, v_student, v_version, v_item, 1, '{"answer":"first"}'::jsonb, 'submitted')
    RETURNING exercise_attempt_id INTO v_attempt;

    INSERT INTO homework_submission_attempt(homework_submission_id, homework_item_id, exercise_attempt_id, selected_flag)
    VALUES (v_submission, v_item, v_attempt, true);

    INSERT INTO exercise_attempt(school_id, student_id, exercise_version_id, homework_item_id, attempt_no, response, status)
    VALUES (v_school, v_student, v_version, v_item, 2, '{"answer":"second"}'::jsonb, 'submitted')
    RETURNING exercise_attempt_id INTO v_attempt2;

    failed := false;
    BEGIN
        INSERT INTO homework_submission_attempt(homework_submission_id, homework_item_id, exercise_attempt_id, selected_flag)
        VALUES (v_submission, v_item, v_attempt2, true);
    EXCEPTION WHEN unique_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'single selected attempt invariant failed'; END IF;

    INSERT INTO homework_submission_attempt(homework_submission_id, homework_item_id, exercise_attempt_id, selected_flag)
    VALUES (v_submission, v_item, v_attempt2, false);

    INSERT INTO exercise_attempt_assessment(exercise_attempt_id, score, max_score, result, method_version)
    VALUES (v_attempt, 1, 1, '{"quality":"correct"}'::jsonb, 'ci-v1')
    RETURNING exercise_attempt_assessment_id INTO v_assessment;

    INSERT INTO exercise_attempt_assessment(exercise_attempt_id, supersedes_assessment_id, score, max_score, result, method_version)
    VALUES (v_attempt, v_assessment, 0.5, 1, '{"quality":"revised"}'::jsonb, 'ci-v2');

    -- Trigger rejects a homework item from another assignment even for the same student.
    INSERT INTO homework_assignment(school_id, title)
    VALUES (v_school, 'CI second homework')
    RETURNING homework_assignment_id INTO v_assignment2;
    INSERT INTO homework_item(homework_assignment_id, sequence_no, exercise_version_id)
    VALUES (v_assignment2, 1, v_version)
    RETURNING homework_item_id INTO v_item2;

    failed := false;
    BEGIN
        UPDATE homework_submission_attempt
           SET homework_item_id = v_item2
         WHERE homework_submission_id = v_submission
           AND exercise_attempt_id = v_attempt2;
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'submission/item assignment trigger guard failed'; END IF;

    -- Runtime privilege model.
    IF NOT has_table_privilege('bridge_school_worker','exercise','INSERT')
       OR NOT has_table_privilege('bridge_school_worker','exercise_version','UPDATE')
       OR NOT has_table_privilege('bridge_school_worker','homework_assignment','INSERT')
       OR NOT has_table_privilege('bridge_school_worker','exercise_attempt_assessment','INSERT') THEN
        RAISE EXCEPTION 'worker homework/content privileges missing';
    END IF;

    IF has_table_privilege('bridge_school_app','homework_assignment','INSERT')
       OR has_table_privilege('bridge_school_app','exercise_attempt_assessment','INSERT') THEN
        RAISE EXCEPTION 'app crossed assignment/assessment boundary';
    END IF;

    IF NOT has_table_privilege('bridge_school_app','homework_submission','INSERT')
       OR NOT has_table_privilege('bridge_school_app','exercise_attempt','UPDATE')
       OR NOT has_table_privilege('bridge_school_app','homework_submission_attempt','INSERT') THEN
        RAISE EXCEPTION 'app student-work privileges missing';
    END IF;

    IF has_table_privilege('bridge_school_worker','exercise_attempt_assessment','UPDATE')
       OR has_table_privilege('bridge_school_worker','exercise_attempt_assessment','DELETE') THEN
        RAISE EXCEPTION 'attempt assessment is not append-only for worker';
    END IF;

    IF has_function_privilege('bridge_school_worker','validate_homework_submission_attempt()','EXECUTE')
       OR has_function_privilege('bridge_school_app','validate_homework_submission_attempt()','EXECUTE') THEN
        RAISE EXCEPTION 'internal homework validation function exposed to runtime';
    END IF;
END $$;

ROLLBACK;
