\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_instructor_person uuid;
    v_instructor_identity uuid;
    v_member_person uuid;
    v_member_identity uuid;
    v_scoped_person uuid;
    v_scoped_identity uuid;
    v_student_person1 uuid;
    v_student_person2 uuid;
    v_student1 uuid;
    v_student2 uuid;
    v_skill uuid;
    v_topic uuid;
    v_snapshot uuid;
    v_count integer;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Instructor Education Scope') RETURNING person_id INTO v_instructor_person;
    INSERT INTO auth_identity(person_id,provider_key,provider_subject)
    VALUES (v_instructor_person,'instructor-scope-provider','instructor')
    RETURNING auth_identity_id INTO v_instructor_identity;
    INSERT INTO person_role_assignment(school_id,person_id,role_key)
    VALUES (v_school,v_instructor_person,'instructor');

    INSERT INTO person(preferred_name) VALUES ('Non Instructor With Grant') RETURNING person_id INTO v_member_person;
    INSERT INTO auth_identity(person_id,provider_key,provider_subject)
    VALUES (v_member_person,'instructor-scope-provider','member')
    RETURNING auth_identity_id INTO v_member_identity;
    INSERT INTO person_role_assignment(school_id,person_id,role_key)
    VALUES (v_school,v_member_person,'member');

    INSERT INTO person(preferred_name) VALUES ('Scoped Instructor Only') RETURNING person_id INTO v_scoped_person;
    INSERT INTO auth_identity(person_id,provider_key,provider_subject)
    VALUES (v_scoped_person,'instructor-scope-provider','scoped')
    RETURNING auth_identity_id INTO v_scoped_identity;
    INSERT INTO person_role_assignment(school_id,person_id,role_key,scope_type,scope_id)
    VALUES (v_school,v_scoped_person,'instructor','group',uuidv7());

    INSERT INTO person(preferred_name) VALUES ('Authorized Student') RETURNING person_id INTO v_student_person1;
    INSERT INTO student(school_id,person_id) VALUES (v_school,v_student_person1) RETURNING student_id INTO v_student1;
    INSERT INTO person(preferred_name) VALUES ('Ungrant Student') RETURNING person_id INTO v_student_person2;
    INSERT INTO student(school_id,person_id) VALUES (v_school,v_student_person2) RETURNING student_id INTO v_student2;

    INSERT INTO person_access_grant(school_id,grantee_person_id,target_person_id,permission_key)
    VALUES (v_school,v_instructor_person,v_student_person1,'education.read');
    INSERT INTO person_access_grant(school_id,grantee_person_id,target_person_id,permission_key)
    VALUES (v_school,v_member_person,v_student_person1,'education.read');
    INSERT INTO person_access_grant(school_id,grantee_person_id,target_person_id,permission_key)
    VALUES (v_school,v_scoped_person,v_student_person1,'education.read');

    INSERT INTO skill(school_id,name,description)
    VALUES (v_school,'Instructor scope skill','education-only authorization test')
    RETURNING skill_id INTO v_skill;
    INSERT INTO topic(school_id,name,domain)
    VALUES (v_school,'Instructor scope topic','general')
    RETURNING topic_id INTO v_topic;

    INSERT INTO skill_assessment(
        school_id,student_id,skill_id,assessment_value,confidence_class,method_version
    ) VALUES (
        v_school,v_student1,v_skill,'{"level":"learning"}'::jsonb,'HIGH','scope-test-v1'
    );
    INSERT INTO skill_assessment(
        school_id,student_id,skill_id,assessment_value,confidence_class,method_version
    ) VALUES (
        v_school,v_student2,v_skill,'{"level":"private-other"}'::jsonb,'HIGH','scope-test-v1'
    );

    INSERT INTO error_observation(
        school_id,student_id,skill_id,topic_id,error_type,severity,recurrence_group_key,confidence_class
    ) VALUES (
        v_school,v_student1,v_skill,v_topic,'education-scope-error','medium','scope-group','MEDIUM'
    );
    INSERT INTO success_observation(
        school_id,student_id,skill_id,topic_id,success_type,independence_level,confidence_class
    ) VALUES (
        v_school,v_student1,v_skill,v_topic,'education-scope-success','guided','HIGH'
    );

    INSERT INTO student_profile_snapshot(
        school_id,student_id,as_of_time,generation_id,computed_profile,status
    ) VALUES (
        v_school,v_student1,now(),uuidv7(),'{"internal":"not exposed by instructor views"}'::jsonb,'active'
    ) RETURNING snapshot_id INTO v_snapshot;
    INSERT INTO recommendation(
        school_id,student_id,source_snapshot_id,recommendation_type,priority_class,
        priority_value,rationale,recommendation_payload,target_topic_id,target_skill_id,method_version
    ) VALUES (
        v_school,v_student1,v_snapshot,'next_focus','high',0.9,'Repeat approved topic',
        '{"action":"practice"}'::jsonb,v_topic,v_skill,'scope-rec-v1'
    );

    -- Target student has financial data, but the instructor surface must not expose it.
    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'instructor-scope-finance-service','Finance hidden service','lesson');
    INSERT INTO club_charge(school_id,person_id,amount,currency_code,charge_type)
    VALUES (v_school,v_student_person1,777,'ILS','manual');
    INSERT INTO club_payment(school_id,person_id,amount,currency_code,paid_at,payment_method)
    VALUES (v_school,v_student_person1,111,'ILS',now(),'test');

    -- No actor context: fail closed.
    SELECT count(*) INTO v_count FROM instructor_authorized_student;
    IF v_count <> 0 THEN RAISE EXCEPTION 'instructor view leaked without actor context'; END IF;

    -- A member with an education grant but no instructor role sees nothing.
    PERFORM bridge_establish_verified_actor_context(v_member_identity,v_school,uuidv7());
    SELECT count(*) INTO v_count FROM instructor_authorized_student;
    IF v_count <> 0 THEN RAISE EXCEPTION 'non-instructor education grant leaked student data'; END IF;

    -- A scoped instructor role is not silently treated as school-wide.
    PERFORM bridge_establish_verified_actor_context(v_scoped_identity,v_school,uuidv7());
    SELECT count(*) INTO v_count FROM instructor_authorized_student;
    IF v_count <> 0 THEN RAISE EXCEPTION 'scoped instructor role unexpectedly became school-wide'; END IF;

    -- School-wide instructor + explicit grant gets exactly the authorized student.
    PERFORM bridge_establish_verified_actor_context(v_instructor_identity,v_school,uuidv7());
    SELECT count(*) INTO v_count FROM instructor_authorized_student;
    IF v_count <> 1 THEN RAISE EXCEPTION 'expected one authorized student, got %',v_count; END IF;
    IF NOT EXISTS (SELECT 1 FROM instructor_authorized_student WHERE student_id=v_student1)
       OR EXISTS (SELECT 1 FROM instructor_authorized_student WHERE student_id=v_student2) THEN
        RAISE EXCEPTION 'instructor student authorization set is wrong';
    END IF;

    SELECT count(*) INTO v_count FROM instructor_student_skill_assessment;
    IF v_count <> 1 THEN RAISE EXCEPTION 'skill assessment scope expected one row, got %',v_count; END IF;
    IF EXISTS (
        SELECT 1 FROM instructor_student_skill_assessment
         WHERE assessment_value @> '{"level":"private-other"}'::jsonb
    ) THEN
        RAISE EXCEPTION 'ungranted student skill assessment leaked';
    END IF;

    SELECT count(*) INTO v_count FROM instructor_student_error_observation;
    IF v_count <> 1 THEN RAISE EXCEPTION 'authorized error observation missing'; END IF;
    SELECT count(*) INTO v_count FROM instructor_student_success_observation;
    IF v_count <> 1 THEN RAISE EXCEPTION 'authorized success observation missing'; END IF;
    SELECT count(*) INTO v_count FROM instructor_student_recommendation;
    IF v_count <> 1 THEN RAISE EXCEPTION 'authorized recommendation missing'; END IF;

    -- Self-service finance remains bound to the actor, not to an instructor's target.
    SELECT count(*) INTO v_count
      FROM member_self_charge
     WHERE person_id=v_student_person1;
    IF v_count <> 0 THEN RAISE EXCEPTION 'target student charge leaked through member self view'; END IF;
    SELECT count(*) INTO v_count
      FROM member_self_payment
     WHERE person_id=v_student_person1;
    IF v_count <> 0 THEN RAISE EXCEPTION 'target student payment leaked through member self view'; END IF;
END $$;

DO $$
BEGIN
    IF NOT has_table_privilege('bridge_school_member_principal','instructor_authorized_student','SELECT')
       OR NOT has_table_privilege('bridge_school_member_principal','instructor_student_skill_assessment','SELECT')
       OR NOT has_table_privilege('bridge_school_member_principal','instructor_student_error_observation','SELECT')
       OR NOT has_table_privilege('bridge_school_member_principal','instructor_student_success_observation','SELECT')
       OR NOT has_table_privilege('bridge_school_member_principal','instructor_student_recommendation','SELECT') THEN
        RAISE EXCEPTION 'member server principal lacks instructor education views';
    END IF;

    IF has_table_privilege('bridge_school_member_principal','skill_assessment','SELECT')
       OR has_table_privilege('bridge_school_member_principal','error_observation','SELECT')
       OR has_table_privilege('bridge_school_member_principal','recommendation','SELECT')
       OR has_table_privilege('bridge_school_member_principal','club_charge','SELECT')
       OR has_table_privilege('bridge_school_member_principal','club_payment','SELECT') THEN
        RAISE EXCEPTION 'member server principal crossed base-table/instructor-finance boundary';
    END IF;
END $$;

ROLLBACK;
