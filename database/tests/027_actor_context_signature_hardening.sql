\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person1 uuid;
    v_person2 uuid;
    v_identity1 uuid;
    v_request uuid := uuidv7();
    v_count integer;
    v_signature text;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Signed Context One') RETURNING person_id INTO v_person1;
    INSERT INTO person(preferred_name) VALUES ('Signed Context Two') RETURNING person_id INTO v_person2;

    INSERT INTO auth_identity(person_id,provider_key,provider_subject)
    VALUES (v_person1,'signed-provider','signed-one') RETURNING auth_identity_id INTO v_identity1;
    INSERT INTO person_role_assignment(school_id,person_id,role_key)
    VALUES (v_school,v_person1,'member');
    INSERT INTO person_role_assignment(school_id,person_id,role_key)
    VALUES (v_school,v_person2,'member');

    -- Raw custom settings alone must never create a valid actor context.
    PERFORM set_config('bridge.actor_person_id',v_person2::text,true);
    PERFORM set_config('bridge.actor_school_id',v_school::text,true);
    PERFORM set_config('bridge.actor_auth_identity_id',uuidv7()::text,true);
    PERFORM set_config('bridge.request_id',uuidv7()::text,true);
    PERFORM set_config('bridge.actor_xid',txid_current()::text,true);
    PERFORM set_config('bridge.actor_context_signature',repeat('0',64),true);

    IF bridge_current_actor_person_id() IS NOT NULL
       OR bridge_current_actor_school_id() IS NOT NULL
       OR bridge_current_actor_auth_identity_id() IS NOT NULL
       OR bridge_current_request_id() IS NOT NULL THEN
        RAISE EXCEPTION 'forged custom settings unexpectedly produced actor context';
    END IF;

    SELECT count(*) INTO v_count FROM member_self_profile;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'forged actor settings leaked member profile rows';
    END IF;

    -- The guarded establishment entry point creates a valid signed context.
    PERFORM bridge_establish_verified_actor_context(v_identity1,v_school,v_request);
    IF bridge_current_actor_person_id() <> v_person1
       OR bridge_current_actor_school_id() <> v_school
       OR bridge_current_actor_auth_identity_id() <> v_identity1
       OR bridge_current_request_id() <> v_request THEN
        RAISE EXCEPTION 'valid signed actor context did not resolve';
    END IF;

    v_signature := current_setting('bridge.actor_context_signature',true);
    IF v_signature IS NULL OR length(v_signature) <> 64 THEN
        RAISE EXCEPTION 'signed actor context signature missing';
    END IF;

    -- Mutating only the person while replaying the valid signature invalidates context.
    PERFORM set_config('bridge.actor_person_id',v_person2::text,true);
    IF bridge_current_actor_person_id() IS NOT NULL THEN
        RAISE EXCEPTION 'replayed signature accepted after actor substitution';
    END IF;
    SELECT count(*) INTO v_count FROM member_self_profile;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'replayed signature leaked substituted actor profile';
    END IF;

    -- Malformed attacker-controlled settings fail closed rather than breaking SELECTs.
    PERFORM set_config('bridge.actor_person_id','not-a-uuid',true);
    IF bridge_current_actor_person_id() IS NOT NULL THEN
        RAISE EXCEPTION 'malformed actor setting unexpectedly resolved';
    END IF;
END $$;

DO $$
BEGIN
    IF has_table_privilege('bridge_school_member_principal','actor_context_signing_secret','SELECT')
       OR has_table_privilege('bridge_school_reader','actor_context_signing_secret','SELECT') THEN
        RAISE EXCEPTION 'actor context signing secret is readable by runtime';
    END IF;

    IF has_function_privilege('bridge_school_member_principal','bridge_actor_context_is_valid()','EXECUTE')
       OR has_function_privilege('bridge_school_reader','bridge_actor_context_is_valid()','EXECUTE') THEN
        RAISE EXCEPTION 'internal actor signature validator leaked to runtime';
    END IF;
END $$;

ROLLBACK;
