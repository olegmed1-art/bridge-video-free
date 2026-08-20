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
    v_run_published uuid;
    v_run_unpublished uuid;
    v_publication uuid;
    v_published_assessment uuid;
    v_unpublished_assessment uuid;
    v_unpublished_error uuid;
    v_unpublished_success uuid;
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
    -- Personal-cabinet authentication is a separate dimension from instructor status.
    INSERT INTO person_role_assignment(school_id,person_id,role_key,scope_type,scope_id)
    VALUES (v_school,v_instructor_person,'member','school',NULL);

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
    -- Portal permission allows actor context but does not broaden the scoped instructor role.
    INSERT INTO person_role_assignment(school_id,person_id,role_key,scope_type,scope_id)
    VALUES (v_school,v_scoped_person,'member','school',NULL);

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

    INSERT INTO skill(school_id,stable_key,name,description)
    VALUES (v_school,'ci-instructor-scope-skill','Instructor scope skill','education-only authorization test')
    RETURNING skill_id INTO v_skill;
    INSERT INTO topic(school_id,name,domain)
    VALUES (v_school,'Instructor scope topic','general')
    RETURNING topic_id INTO v_topic;

    -- Manual/direct educational fact is visible after authorization.
    INSERT INTO skill_assessment(
        school_id,student_id,skill_id,assessment_value,confidence_class,method_version
    ) VALUES (
        v_school,v_student1,v_skill,'{"level":"manual-visible"}'::jsonb,'HIGH','scope-test-v1'
    );
    -- Another Student never becomes visible without an explicit Person grant.
    INSERT INTO skill_assessment(
        school_id,student_id,skill_id,assessment_value,confidence_class,method_version
    ) VALUES (
        v_school,v_student2,v_skill,'{"level":"private-other"}'::jsonb,'HIGH','scope-test-v1'
    );

    INSERT INTO analysis_run(school_id,algorithm_key,algorithm_version,run_status)
    VALUES (v_school,'instructor-scope-published','1','success')
    RETURNING analysis_run_id INTO v_run_published;
    INSERT INTO analysis_run(school_id,algorithm_key,algorithm_version,run_status)
    VALUES (v_school,'instructor-scope-unpublished','1','success')
    RETURNING analysis_run_id INTO v_run_unpublished;

    INSERT INTO skill_assessment(
        school_id,student_id,skill_id,assessment_value,confidence_class,
        generated_by_analysis_run_id,method_version
    ) VALUES (
        v_school,v_student1,v_skill,'{"level":"published-analysis"}'::jsonb,'HIGH',
        v_run_published,'scope-analysis-v1'
    ) RETURNING skill_assessment_id INTO v_published_assessment;
    INSERT INTO skill_assessment(
        school_id,student_id,skill_id,assessment_value,confidence_class,
        generated_by_analysis_run_id,method_version
    ) VALUES (
        v_school,v_student1,v_skill,'{"level":"unpublished-analysis"}'::jsonb,'LOW',
        v_run_unpublished,'scope-analysis-v1'
    ) RETURNING skill_assessment_id INTO v_unpublished_assessment;

    INSERT INTO output_publication(
        school_id,analysis_run_id,publication_type,manifest,status,published_at
    ) VALUES (
        v_school,v_run_published,'instructor_education','{}'::jsonb,'published',now()
    ) RETURNING publication_id INTO v_publication;
    INSERT INTO analysis_run_output(
        analysis_run_id,output_entity_id,output_entity_type,publication_id,output_role,status
    ) VALUES (
        v_run_published,v_published_assessment,'skill_assessment',v_publication,'derived','published'
    );

    INSERT INTO error_observation(
        school_id,student_id,skill_id,topic_id,error_type,severity,recurrence_group_key,confidence_class
    ) VALUES (
        v_school,v_student1,v_skill,v_topic,'education-scope-error','medium','scope-group','MEDIUM'
    );
    INSERT INTO error_observation(
        school_id,student_id,skill_id,topic_id,error_type,severity,confidence_class,
        generated_by_analysis_run_id
    ) VALUES (
        v_school,v_student1,v_skill,v_topic,'unpublished-analysis-error','low','LOW',v_run_unpublished
    ) RETURNING error_observation_id INTO v_unpublished_error;

    INSERT INTO success_observation(
        school_id,student_id,skill_id,topic_id,success_type,independence_level,confidence_class
    ) VALUES (
        v_school,v_student1,v_skill,v_topic,'education-scope-success','guided','HIGH'
    );
    INSERT INTO success_observation(
        school_id,student_id,skill_id,topic_id,success_type,independence_level,confidence_class,
        generated_by_analysis_run_id
    ) VALUES (
        v_school,v_student1,v_skill,v_topic,'unpublished-analysis-success','unknown','LOW',v_run_unpublished
    ) RETURNING success_observation_id INTO v_unpublished_success;

    -- Target student has financial data, but the instructor surface must not expose it.
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

    -- Manual + exact published analysis output are visible; unpublished output is not.
    SELECT count(*) INTO v_count FROM instructor_student_skill_assessment;
    IF v_count <> 2 THEN RAISE EXCEPTION 'skill assessment publication scope expected two rows, got %',v_count; END IF;
    IF EXISTS (
        SELECT 1 FROM instructor_student_skill_assessment
         WHERE skill_assessment_id=v_unpublished_assessment
            OR assessment_value @> '{"level":"private-other"}'::jsonb
    ) THEN
        RAISE EXCEPTION 'unpublished or ungranted skill assessment leaked';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM instructor_student_skill_assessment
         WHERE skill_assessment_id=v_published_assessment
    ) THEN
        RAISE EXCEPTION 'published analysis assessment was not exposed';
    END IF;

    SELECT count(*) INTO v_count FROM instructor_student_error_observation;
    IF v_count <> 1 OR EXISTS (
        SELECT 1 FROM instructor_student_error_observation
         WHERE error_observation_id=v_unpublished_error
    ) THEN
        RAISE EXCEPTION 'error observation publication boundary failed';
    END IF;
    SELECT count(*) INTO v_count FROM instructor_student_success_observation;
    IF v_count <> 1 OR EXISTS (
        SELECT 1 FROM instructor_student_success_observation
         WHERE success_observation_id=v_unpublished_success
    ) THEN
        RAISE EXCEPTION 'success observation publication boundary failed';
    END IF;

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
       OR NOT has_table_privilege('bridge_school_member_principal','instructor_student_success_observation','SELECT') THEN
        RAISE EXCEPTION 'member server principal lacks instructor education views';
    END IF;

    IF to_regclass('public.instructor_student_recommendation') IS NOT NULL THEN
        RAISE EXCEPTION 'unrestricted recommendation payload view should not exist before visibility policy';
    END IF;

    IF has_table_privilege('bridge_school_member_principal','skill_assessment','SELECT')
       OR has_table_privilege('bridge_school_member_principal','error_observation','SELECT')
       OR has_table_privilege('bridge_school_member_principal','recommendation','SELECT')
       OR has_table_privilege('bridge_school_member_principal','student_profile_snapshot','SELECT')
       OR has_table_privilege('bridge_school_member_principal','club_charge','SELECT')
       OR has_table_privilege('bridge_school_member_principal','club_payment','SELECT') THEN
        RAISE EXCEPTION 'member server principal crossed base-table/instructor-finance/internal-profile boundary';
    END IF;
END $$;

ROLLBACK;
