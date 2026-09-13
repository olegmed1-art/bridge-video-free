\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path=identity_staging,public;

DO $$
DECLARE
    v_school uuid;
    v_source uuid;
    v_batch uuid;
    v_link_item uuid;
    v_new_item uuid;
    v_source_identity uuid;
    v_existing_target uuid;
    v_resolution uuid;
    v_apply_run uuid;
    v_apply_run_repeat uuid;
    v_new_person uuid;
    v_student uuid;
    v_membership uuid;
    v_member_role uuid;
    v_auth uuid;
    v_person_before bigint;
    v_student_before bigint;
    v_membership_before bigint;
    v_auth_before bigint;
    v_person_after bigint;
    v_count bigint;
    v_state text;
    v_status text;
    v_resolved uuid;
    v_role text;
BEGIN
    SELECT school_id INTO v_school
      FROM public.school
     WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO public.source(school_id,source_type,title)
    VALUES (v_school,'manual_import','Controlled onboarding CI')
    RETURNING source_id INTO v_source;

    -- Existing target is deliberately low-level: linking to an already-existing Person
    -- must not create another Person or silently alter that person's ecosystem roles.
    INSERT INTO public.person(preferred_name)
    VALUES ('Controlled Existing Target')
    RETURNING person_id INTO v_existing_target;

    INSERT INTO public.source_identity(source_id,source_native_key,display_name)
    VALUES (v_source,'controlled-existing-001','Controlled Existing Target')
    RETURNING source_identity_id INTO v_source_identity;
    INSERT INTO public.entity_resolution_decision(
        source_identity_id,target_person_id,decision_type,confidence_class,status
    ) VALUES (
        v_source_identity,v_existing_target,'link','HIGH','active'
    ) RETURNING resolution_id INTO v_resolution;

    INSERT INTO identity_import_batch(
        school_id,source_id,external_batch_key,import_label,provenance
    ) VALUES (
        v_school,v_source,'controlled-onboarding-batch','Controlled onboarding batch',
        jsonb_build_object('test','094')
    ) RETURNING identity_import_batch_id INTO v_batch;

    INSERT INTO identity_import_item(
        identity_import_batch_id,source_record_key,raw_payload,source_identity_id,normalized_candidate
    ) VALUES (
        v_batch,'existing-record',jsonb_build_object('name','Controlled Existing Target'),
        v_source_identity,jsonb_build_object('preferred_name','Controlled Existing Target')
    ) RETURNING identity_import_item_id INTO v_link_item;
    INSERT INTO identity_import_action(
        identity_import_item_id,action_type,target_person_id,entity_resolution_decision_id,reason
    ) VALUES (
        v_link_item,'link_existing_person',v_existing_target,v_resolution,'reviewed canonical link'
    );
    INSERT INTO identity_import_item_state_event(identity_import_item_id,state,reason)
    VALUES (v_link_item,'ready','link ready');

    INSERT INTO identity_import_item(
        identity_import_batch_id,source_record_key,raw_payload,normalized_candidate
    ) VALUES (
        v_batch,'new-record',jsonb_build_object('name','Controlled New Person'),
        jsonb_build_object('preferred_name','Controlled New Person')
    ) RETURNING identity_import_item_id INTO v_new_item;
    INSERT INTO identity_import_action(identity_import_item_id,action_type,reason)
    VALUES (v_new_item,'create_new_person','reviewed create intent');
    INSERT INTO identity_import_item_state_event(identity_import_item_id,state,reason)
    VALUES (v_new_item,'ready','create ready');

    INSERT INTO identity_import_batch_state_event(identity_import_batch_id,state,reason)
    VALUES (v_batch,'ready','all controlled onboarding items reviewed');

    SELECT count(*) INTO v_person_before FROM public.person;
    SELECT count(*) INTO v_student_before FROM public.student;
    SELECT count(*) INTO v_membership_before FROM public.club_membership;
    SELECT count(*) INTO v_auth_before FROM public.auth_identity;

    v_apply_run := identity_staging.apply_identity_import_batch(v_batch);
    IF v_apply_run IS NULL THEN RAISE EXCEPTION 'controlled apply did not return a run id'; END IF;

    SELECT rr.person_id,rr.student_id,rr.club_membership_id,rr.member_role_assignment_id
      INTO v_new_person,v_student,v_membership,v_member_role
      FROM identity_import_apply_receipt rr
     WHERE rr.identity_import_apply_run_id=v_apply_run
       AND rr.identity_import_item_id=v_new_item
       AND rr.action_type='create_new_person'
       AND rr.new_person_created;
    IF v_new_person IS NULL OR v_student IS NULL OR v_membership IS NULL OR v_member_role IS NULL THEN
        RAISE EXCEPTION 'new-person controlled apply receipt is incomplete';
    END IF;

    SELECT count(*) INTO v_count
      FROM identity_import_apply_receipt rr
     WHERE rr.identity_import_apply_run_id=v_apply_run
       AND rr.identity_import_item_id=v_link_item
       AND rr.person_id=v_existing_target
       AND NOT rr.new_person_created;
    IF v_count<>1 THEN RAISE EXCEPTION 'existing-person link receipt missing or incorrect'; END IF;

    IF (SELECT count(*) FROM public.person)<>v_person_before+1 THEN
        RAISE EXCEPTION 'controlled apply did not create exactly one new Person';
    END IF;
    IF (SELECT count(*) FROM public.student)<>v_student_before+1 THEN
        RAISE EXCEPTION 'new Person was not automatically created as Student';
    END IF;
    IF (SELECT count(*) FROM public.club_membership)<>v_membership_before+1 THEN
        RAISE EXCEPTION 'new Person was not automatically given ClubMembership';
    END IF;
    IF (SELECT count(*) FROM public.auth_identity)<>v_auth_before THEN
        RAISE EXCEPTION 'controlled onboarding fabricated an AuthIdentity without verified provider binding';
    END IF;

    SELECT current_status INTO v_status FROM public.student WHERE student_id=v_student;
    IF v_status<>'active' THEN RAISE EXCEPTION 'automatic Student is not active'; END IF;
    SELECT status INTO v_status FROM public.club_membership WHERE club_membership_id=v_membership;
    IF v_status<>'active' THEN RAISE EXCEPTION 'automatic ClubMembership is not active'; END IF;
    SELECT status INTO v_status FROM public.person_role_assignment WHERE person_role_assignment_id=v_member_role;
    IF v_status<>'active' THEN RAISE EXCEPTION 'automatic portal/member role is not active'; END IF;

    SELECT state INTO v_state FROM identity_import_item_current_state WHERE identity_import_item_id=v_new_item;
    IF v_state<>'applied' THEN RAISE EXCEPTION 'new item did not reach applied state'; END IF;
    SELECT state INTO v_state FROM identity_import_batch_current_state WHERE identity_import_batch_id=v_batch;
    IF v_state<>'applied' THEN RAISE EXCEPTION 'batch did not reach applied state'; END IF;

    -- Repeating apply is idempotent and returns the original receipt set.
    SELECT count(*) INTO v_person_after FROM public.person;
    v_apply_run_repeat := identity_staging.apply_identity_import_batch(v_batch);
    IF v_apply_run_repeat IS DISTINCT FROM v_apply_run THEN
        RAISE EXCEPTION 'controlled apply idempotence returned a different run';
    END IF;
    IF (SELECT count(*) FROM public.person)<>v_person_after THEN
        RAISE EXCEPTION 'idempotent re-apply created another Person';
    END IF;

    -- Access dimensions are independent. First disable only Student status.
    PERFORM public.bridge_set_person_ecosystem_access(
        v_school,v_new_person,false,true,true,'temporarily not a student'
    );
    SELECT current_status INTO v_status FROM public.student WHERE student_id=v_student;
    IF v_status<>'inactive' THEN RAISE EXCEPTION 'student-only restriction did not deactivate Student'; END IF;
    SELECT status INTO v_status FROM public.club_membership WHERE club_membership_id=v_membership;
    IF v_status<>'active' THEN RAISE EXCEPTION 'student-only restriction changed membership'; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.person_role_assignment
         WHERE school_id=v_school AND person_id=v_new_person AND role_key='member'
           AND scope_type='school' AND scope_id IS NULL AND status='active' AND valid_to IS NULL
    ) THEN RAISE EXCEPTION 'student-only restriction changed portal access'; END IF;

    -- Restore Student, pause only membership.
    PERFORM public.bridge_set_person_ecosystem_access(
        v_school,v_new_person,true,false,true,'temporarily not a club member'
    );
    SELECT current_status INTO v_status FROM public.student WHERE student_id=v_student;
    IF v_status<>'active' THEN RAISE EXCEPTION 'student restore failed'; END IF;
    SELECT status INTO v_status FROM public.club_membership WHERE club_membership_id=v_membership;
    IF v_status<>'paused' THEN RAISE EXCEPTION 'membership-only restriction did not pause membership'; END IF;

    -- Portal disable must block personal-cabinet context even if another school role exists.
    INSERT INTO public.person_role_assignment(school_id,person_id,role_key,scope_type,status,provenance)
    VALUES (v_school,v_new_person,'instructor','school','active',jsonb_build_object('test','094'));
    INSERT INTO public.auth_identity(person_id,provider_key,provider_subject)
    VALUES (v_new_person,'controlled-test-provider','controlled-subject')
    RETURNING auth_identity_id INTO v_auth;

    PERFORM public.bridge_set_person_ecosystem_access(
        v_school,v_new_person,true,true,false,'portal access disabled'
    );
    BEGIN
        PERFORM public.bridge_establish_verified_actor_context(v_auth,v_school,uuidv7());
        RAISE EXCEPTION 'portal-disabled person unexpectedly established member context';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='portal-disabled person unexpectedly established member context' THEN RAISE; END IF;
    END;

    -- Re-enable only portal while Student and membership remain enabled; context works again.
    PERFORM public.bridge_set_person_ecosystem_access(
        v_school,v_new_person,true,true,true,'portal access restored'
    );
    v_resolved := public.bridge_establish_verified_actor_context(v_auth,v_school,uuidv7());
    IF v_resolved IS DISTINCT FROM v_new_person THEN
        RAISE EXCEPTION 'restored portal access resolved wrong person';
    END IF;

    SELECT count(*) INTO v_count FROM public.person_ecosystem_access_event
     WHERE school_id=v_school AND person_id=v_new_person;
    IF v_count<5 THEN RAISE EXCEPTION 'ecosystem access audit history is incomplete'; END IF;

    -- Runtime/member capabilities cannot invoke privileged onboarding/apply controls.
    FOREACH v_role IN ARRAY ARRAY[
        'bridge_school_reader','bridge_school_app','bridge_school_worker','bridge_school_health',
        'bridge_school_finance','bridge_school_member','bridge_school_member_principal','bridge_school_auth_gateway'
    ] LOOP
        IF has_function_privilege(v_role,'bridge_onboard_school_person(uuid,text,jsonb)','EXECUTE')
           OR has_function_privilege(v_role,'bridge_set_person_ecosystem_access(uuid,uuid,boolean,boolean,boolean,text,uuid,jsonb)','EXECUTE')
           OR has_function_privilege(v_role,'identity_staging.apply_identity_import_batch(uuid)','EXECUTE') THEN
            RAISE EXCEPTION 'runtime role % can execute privileged identity onboarding/apply',v_role;
        END IF;
    END LOOP;

    IF NOT has_function_privilege('bridge_school_identity_admin','bridge_onboard_school_person(uuid,text,jsonb)','EXECUTE')
       OR NOT has_function_privilege('bridge_school_identity_admin','bridge_set_person_ecosystem_access(uuid,uuid,boolean,boolean,boolean,text,uuid,jsonb)','EXECUTE')
       OR NOT has_function_privilege('bridge_school_identity_admin','identity_staging.apply_identity_import_batch(uuid)','EXECUTE') THEN
        RAISE EXCEPTION 'identity admin is missing a controlled capability';
    END IF;

    -- The identity-admin capability itself must remain dormant and non-administrative.
    SELECT rolname INTO v_role FROM pg_roles
     WHERE rolname='bridge_school_identity_admin'
       AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
       AND NOT rolreplication AND NOT rolbypassrls;
    IF v_role IS NULL THEN RAISE EXCEPTION 'identity admin role attributes are unsafe'; END IF;
END $$;

ROLLBACK;
