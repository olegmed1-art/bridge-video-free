\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_source uuid;
    v_batch uuid;
    v_bad_batch uuid;
    v_link_item uuid;
    v_new_item uuid;
    v_bad_item uuid;
    v_source_identity uuid;
    v_target uuid;
    v_resolution uuid;
    v_person_before bigint;
    v_student_before bigint;
    v_membership_before bigint;
    v_auth_before bigint;
    v_ready boolean;
    v_count bigint;
    v_hash text;
    v_expected text;
    v_role text;
BEGIN
    SELECT school_id INTO v_school
      FROM public.school
     WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO public.source(school_id,source_type,title)
    VALUES (v_school,'manual_import','Synthetic identity Neon pilot 20260818')
    RETURNING source_id INTO v_source;

    -- Seed exactly one explicit existing target only to exercise canonical linking.
    INSERT INTO public.person(preferred_name)
    VALUES ('Synthetic Existing Identity Pilot 20260818')
    RETURNING person_id INTO v_target;

    SELECT count(*) INTO v_person_before FROM public.person;
    SELECT count(*) INTO v_student_before FROM public.student;
    SELECT count(*) INTO v_membership_before FROM public.club_membership;
    SELECT count(*) INTO v_auth_before FROM public.auth_identity;

    INSERT INTO public.source_identity(source_id,source_native_key,display_name)
    VALUES (v_source,'pilot-existing-001','Synthetic Existing Identity Pilot 20260818')
    RETURNING source_identity_id INTO v_source_identity;

    INSERT INTO public.entity_resolution_decision(
        source_identity_id,target_person_id,decision_type,confidence_class,status
    ) VALUES (
        v_source_identity,v_target,'link','HIGH','active'
    ) RETURNING resolution_id INTO v_resolution;

    INSERT INTO identity_staging.identity_import_batch(
        school_id,source_id,external_batch_key,import_label,provenance
    ) VALUES (
        v_school,v_source,'pilot-ready-20260818','Synthetic ready pilot',
        jsonb_build_object('test','neon-branch-e2e')
    ) RETURNING identity_import_batch_id INTO v_batch;

    INSERT INTO identity_staging.identity_import_item(
        identity_import_batch_id,source_record_key,raw_payload,source_identity_id,normalized_candidate
    ) VALUES (
        v_batch,'existing-record',
        jsonb_build_object('name','Synthetic Existing Identity Pilot 20260818','email','synthetic-existing@example.invalid'),
        v_source_identity,jsonb_build_object('preferred_name','Synthetic Existing Identity Pilot 20260818')
    ) RETURNING identity_import_item_id INTO v_link_item;

    INSERT INTO identity_staging.identity_import_item(
        identity_import_batch_id,source_record_key,raw_payload,normalized_candidate
    ) VALUES (
        v_batch,'new-record',
        jsonb_build_object('name','Synthetic New Identity Pilot 20260818','email','synthetic-new@example.invalid'),
        jsonb_build_object('preferred_name','Synthetic New Identity Pilot 20260818')
    ) RETURNING identity_import_item_id INTO v_new_item;

    SELECT raw_payload_sha256,encode(digest(raw_payload::text,'sha256'),'hex')
      INTO v_hash,v_expected
      FROM identity_staging.identity_import_item
     WHERE identity_import_item_id=v_link_item;
    IF v_hash IS DISTINCT FROM v_expected THEN
        RAISE EXCEPTION 'database evidence hash mismatch';
    END IF;

    INSERT INTO identity_staging.identity_import_action(
        identity_import_item_id,action_type,target_person_id,entity_resolution_decision_id,reason
    ) VALUES (
        v_link_item,'link_existing_person',v_target,v_resolution,'synthetic canonical link review'
    );
    INSERT INTO identity_staging.identity_import_item_state_event(
        identity_import_item_id,state,reason
    ) VALUES (v_link_item,'ready','canonical link reviewed');

    INSERT INTO identity_staging.identity_import_action(
        identity_import_item_id,action_type,reason
    ) VALUES (v_new_item,'create_new_person','synthetic explicit create intent only');
    INSERT INTO identity_staging.identity_import_item_state_event(
        identity_import_item_id,state,reason
    ) VALUES (v_new_item,'ready','explicit create intent reviewed');

    -- Staging intent must never materialize operational identity records.
    IF (SELECT count(*) FROM public.person) <> v_person_before THEN
        RAISE EXCEPTION 'staging create intent materialized a Person';
    END IF;
    IF (SELECT count(*) FROM public.student) <> v_student_before THEN
        RAISE EXCEPTION 'staging materialized a Student';
    END IF;
    IF (SELECT count(*) FROM public.club_membership) <> v_membership_before THEN
        RAISE EXCEPTION 'staging materialized a ClubMembership';
    END IF;
    IF (SELECT count(*) FROM public.auth_identity) <> v_auth_before THEN
        RAISE EXCEPTION 'staging materialized an AuthIdentity';
    END IF;

    INSERT INTO identity_staging.identity_import_batch_state_event(
        identity_import_batch_id,state,reason
    ) VALUES (v_batch,'ready','all synthetic items reviewed');

    SELECT eligible_for_future_apply INTO v_ready
      FROM identity_staging.identity_import_batch_future_apply_readiness
     WHERE identity_import_batch_id=v_batch;
    IF v_ready IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'ready batch projection is not eligible';
    END IF;

    -- Later evidence must invalidate eligibility append-only.
    INSERT INTO identity_staging.identity_import_action(
        identity_import_item_id,action_type,reason
    ) VALUES (v_new_item,'defer','later synthetic evidence requires review');
    SELECT eligible_for_future_apply INTO v_ready
      FROM identity_staging.identity_import_batch_future_apply_readiness
     WHERE identity_import_batch_id=v_batch;
    IF v_ready IS DISTINCT FROM false THEN
        RAISE EXCEPTION 'later defer did not fail closed';
    END IF;

    SELECT count(*) INTO v_count
      FROM identity_staging.identity_import_action
     WHERE identity_import_item_id=v_new_item;
    IF v_count <> 2 THEN RAISE EXCEPTION 'append-only action audit history incomplete'; END IF;
    SELECT count(*) INTO v_count
      FROM identity_staging.identity_import_item_state_event
     WHERE identity_import_item_id=v_new_item;
    IF v_count <> 2 THEN RAISE EXCEPTION 'append-only state audit history incomplete'; END IF;

    BEGIN
        UPDATE identity_staging.identity_import_item
           SET normalized_candidate='{}'::jsonb
         WHERE identity_import_item_id=v_new_item;
        RAISE EXCEPTION 'append-only staging item unexpectedly mutable';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='append-only staging item unexpectedly mutable' THEN RAISE; END IF;
    END;

    -- Independent fail-closed case: unresolved item cannot become ready.
    INSERT INTO identity_staging.identity_import_batch(
        school_id,source_id,external_batch_key,import_label
    ) VALUES (v_school,v_source,'pilot-unresolved-20260818','Synthetic unresolved pilot')
    RETURNING identity_import_batch_id INTO v_bad_batch;
    INSERT INTO identity_staging.identity_import_item(
        identity_import_batch_id,source_record_key,raw_payload
    ) VALUES (v_bad_batch,'unresolved-record',jsonb_build_object('name','Synthetic Unresolved'))
    RETURNING identity_import_item_id INTO v_bad_item;
    BEGIN
        INSERT INTO identity_staging.identity_import_item_state_event(
            identity_import_item_id,state,reason
        ) VALUES (v_bad_item,'ready','must fail');
        RAISE EXCEPTION 'unresolved item unexpectedly became ready';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='unresolved item unexpectedly became ready' THEN RAISE; END IF;
    END;

    FOREACH v_role IN ARRAY ARRAY[
        'bridge_school_reader','bridge_school_app','bridge_school_worker',
        'bridge_school_health','bridge_school_finance','bridge_school_member',
        'bridge_school_member_principal','bridge_school_auth_gateway'
    ] LOOP
        IF has_schema_privilege(v_role,'identity_staging','USAGE')
           OR has_table_privilege(v_role,'identity_staging.identity_import_item','SELECT')
           OR has_table_privilege(v_role,'identity_staging.identity_import_item','INSERT') THEN
            RAISE EXCEPTION 'runtime role % can access protected identity staging',v_role;
        END IF;
    END LOOP;

    SELECT count(*) INTO v_count
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid=p.pronamespace
     WHERE n.nspname IN ('public','identity_staging')
       AND p.proname LIKE '%identity_import%apply%';
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'unexpected identity import apply function exists';
    END IF;
END $$;

ROLLBACK;
