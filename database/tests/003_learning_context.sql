\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_student uuid;
    v_group uuid;
    v_partnership uuid;
    v_interaction uuid;
    v_course uuid;
    v_course_version uuid;
    v_session uuid;
    failed boolean;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'school seed missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('CI learning-context probe') RETURNING person_id INTO v_person;
    INSERT INTO student(school_id, person_id) VALUES (v_school, v_person) RETURNING student_id INTO v_student;

    INSERT INTO learning_group(school_id, name)
    VALUES (v_school, 'CI temporary group') RETURNING group_id INTO v_group;
    INSERT INTO group_membership(group_id, student_id)
    VALUES (v_group, v_student);

    failed := false;
    BEGIN
        INSERT INTO group_membership(group_id, student_id)
        VALUES (v_group, v_student);
    EXCEPTION WHEN unique_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'open group membership uniqueness failed'; END IF;

    INSERT INTO partnership(school_id, display_name)
    VALUES (v_school, 'CI pair') RETURNING partnership_id INTO v_partnership;
    INSERT INTO partnership_member(partnership_id, person_id)
    VALUES (v_partnership, v_person);

    INSERT INTO agreement_set(school_id, partnership_id, bidding_system_key)
    VALUES (v_school, v_partnership, 'ci-system');

    INSERT INTO course(school_id, stable_key, name)
    VALUES (v_school, 'ci-course', 'CI course') RETURNING course_id INTO v_course;
    INSERT INTO course_version(course_id, version_no, status)
    VALUES (v_course, 1, 'active') RETURNING course_version_id INTO v_course_version;

    INSERT INTO learning_interaction(school_id, interaction_type, channel, group_id, status)
    VALUES (v_school, 'lesson', 'ci', v_group, 'planned') RETURNING interaction_id INTO v_interaction;

    INSERT INTO session(school_id, interaction_id, course_version_id, instructor_person_id, format)
    VALUES (v_school, v_interaction, v_course_version, v_person, 'online') RETURNING session_id INTO v_session;

    INSERT INTO session_participation(session_id, person_id, student_id, attendance_status)
    VALUES (v_session, v_person, v_student, 'present');

    failed := false;
    BEGIN
        INSERT INTO session_participation(session_id, person_id, student_id, attendance_status)
        VALUES (v_session, v_person, v_student, 'present');
    EXCEPTION WHEN unique_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'session student participation uniqueness failed'; END IF;

    INSERT INTO session_plan(session_id, version_no, status, plan)
    VALUES (v_session, 1, 'active', '{"goal":"ci"}'::jsonb);

    failed := false;
    BEGIN
        INSERT INTO session_plan(session_id, version_no, status, plan)
        VALUES (v_session, 2, 'active', '{"goal":"ci2"}'::jsonb);
    EXCEPTION WHEN unique_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'single active session plan invariant failed'; END IF;

    INSERT INTO session_plan(session_id, version_no, status, plan)
    VALUES (v_session, 2, 'draft', '{"goal":"ci2"}'::jsonb);

    INSERT INTO episode(interaction_id, session_id, sequence_no, episode_type, start_offset_seconds, end_offset_seconds)
    VALUES (v_interaction, v_session, 1, 'explanation', 10, 20);

    failed := false;
    BEGIN
        INSERT INTO episode(interaction_id, session_id, sequence_no, episode_type, start_offset_seconds, end_offset_seconds)
        VALUES (v_interaction, v_session, 2, 'bad-range', 20, 10);
    EXCEPTION WHEN check_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'episode offset range invariant failed'; END IF;

    IF NOT has_table_privilege('bridge_school_app','learning_group','INSERT')
       OR NOT has_table_privilege('bridge_school_app','session','UPDATE')
       OR NOT has_table_privilege('bridge_school_worker','session_plan','INSERT')
       OR NOT has_table_privilege('bridge_school_worker','episode','UPDATE') THEN
        RAISE EXCEPTION 'runtime learning-context grants missing';
    END IF;

    IF has_table_privilege('bridge_school_app','course','INSERT')
       OR has_table_privilege('bridge_school_worker','course_version','UPDATE') THEN
        RAISE EXCEPTION 'runtime role crossed curriculum admin boundary';
    END IF;
END $$;

ROLLBACK;
