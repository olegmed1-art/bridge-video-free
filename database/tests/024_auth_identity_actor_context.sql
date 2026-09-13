\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_other uuid;
    v_identity uuid;
    v_other_identity uuid;
    v_request uuid := uuidv7();
    v_resolved uuid;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Auth Context Person') RETURNING person_id INTO v_person;
    INSERT INTO person(preferred_name) VALUES ('Auth Context Other') RETURNING person_id INTO v_other;

    INSERT INTO auth_identity(person_id,provider_key,provider_subject)
    VALUES (v_person,'test-provider','subject-1') RETURNING auth_identity_id INTO v_identity;
    INSERT INTO auth_identity(person_id,provider_key,provider_subject)
    VALUES (v_other,'test-provider','subject-2') RETURNING auth_identity_id INTO v_other_identity;

    INSERT INTO person_role_assignment(school_id,person_id,role_key)
    VALUES (v_school,v_person,'member');

    v_resolved := bridge_establish_verified_actor_context(v_identity,v_school,v_request);
    IF v_resolved <> v_person THEN
        RAISE EXCEPTION 'actor context resolved wrong person';
    END IF;
    IF bridge_current_actor_person_id() <> v_person
       OR bridge_current_actor_school_id() <> v_school
       OR bridge_current_actor_auth_identity_id() <> v_identity
       OR bridge_current_request_id() <> v_request THEN
        RAISE EXCEPTION 'actor context GUCs are inconsistent';
    END IF;
    IF NOT bridge_actor_has_role('member') OR bridge_actor_has_role('owner') THEN
        RAISE EXCEPTION 'actor role resolution is inconsistent';
    END IF;

    BEGIN
        PERFORM bridge_establish_verified_actor_context(v_other_identity,v_school,uuidv7());
        RAISE EXCEPTION 'identity without school role unexpectedly established context';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='identity without school role unexpectedly established context' THEN RAISE; END IF;
    END;

    INSERT INTO person_access_grant(
        school_id,grantee_person_id,target_person_id,permission_key
    ) VALUES (
        v_school,v_person,v_other,'education.read'
    );
    IF bridge_actor_has_person_permission(v_person,'education.read') THEN
        RAISE EXCEPTION 'self target unexpectedly implied arbitrary person permission';
    END IF;
    IF NOT bridge_actor_has_person_permission(v_other,'education.read') THEN
        RAISE EXCEPTION 'explicit person permission was not recognized';
    END IF;
    IF bridge_actor_has_person_permission(v_other,'finance.read') THEN
        RAISE EXCEPTION 'ungranted permission unexpectedly recognized';
    END IF;

    UPDATE auth_identity
       SET status='revoked', valid_to=now()+interval '1 second'
     WHERE auth_identity_id=v_identity;
    BEGIN
        PERFORM bridge_establish_verified_actor_context(v_identity,v_school,uuidv7());
        RAISE EXCEPTION 'revoked identity unexpectedly established context';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='revoked identity unexpectedly established context' THEN RAISE; END IF;
    END;
END $$;

DO $$
BEGIN
    IF has_table_privilege('bridge_school_member','auth_identity','SELECT')
       OR has_table_privilege('bridge_school_member','person_role_assignment','SELECT')
       OR has_table_privilege('bridge_school_member','person_access_grant','SELECT')
       OR has_table_privilege('bridge_school_member_principal','person','SELECT')
       OR has_table_privilege('bridge_school_member_principal','club_payment','SELECT') THEN
        RAISE EXCEPTION 'member runtime can directly read protected base tables';
    END IF;

    IF NOT has_function_privilege(
            'bridge_school_member_principal',
            'bridge_establish_verified_actor_context(uuid,uuid,uuid)',
            'EXECUTE'
       )
       OR NOT has_function_privilege(
            'bridge_school_member_principal',
            'bridge_actor_has_person_permission(uuid,text)',
            'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'member principal is missing guarded actor-context capability';
    END IF;

    IF has_function_privilege(
            'bridge_school_reader',
            'bridge_establish_verified_actor_context(uuid,uuid,uuid)',
            'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'broad reader can establish member actor context';
    END IF;
END $$;

ROLLBACK;
