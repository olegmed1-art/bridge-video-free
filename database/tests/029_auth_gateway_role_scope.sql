\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_identity uuid;
    v_resolved uuid;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Gateway Scope Person') RETURNING person_id INTO v_person;
    INSERT INTO auth_identity(person_id,provider_key,provider_subject)
    VALUES (v_person,'gateway-provider','gateway-person') RETURNING auth_identity_id INTO v_identity;

    -- A scoped role is sufficient to establish that this Person belongs to the school,
    -- but it must not become a school-wide role through bridge_actor_has_role().
    INSERT INTO person_role_assignment(
        school_id,person_id,role_key,scope_type,scope_id
    ) VALUES (
        v_school,v_person,'student','group',uuidv7()
    );

    v_resolved := bridge_establish_verified_actor_context(v_identity,v_school,uuidv7());
    IF v_resolved <> v_person THEN
        RAISE EXCEPTION 'trusted gateway establishment did not resolve person';
    END IF;
    IF bridge_actor_has_role('student') THEN
        RAISE EXCEPTION 'scoped student role unexpectedly became school-wide';
    END IF;

    INSERT INTO person_role_assignment(school_id,person_id,role_key)
    VALUES (v_school,v_person,'student');
    IF NOT bridge_actor_has_role('student') THEN
        RAISE EXCEPTION 'school-wide student role was not recognized';
    END IF;
END $$;

DO $$
DECLARE r record;
BEGIN
    SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
      INTO r FROM pg_roles WHERE rolname='bridge_school_auth_gateway';
    IF NOT FOUND THEN RAISE EXCEPTION 'auth gateway role missing'; END IF;
    IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls THEN
        RAISE EXCEPTION 'auth gateway role has unsafe attributes';
    END IF;

    IF has_function_privilege(
        'bridge_school_member',
        'bridge_establish_verified_actor_context(uuid,uuid,uuid)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'ordinary member capability can establish arbitrary actor context';
    END IF;

    IF NOT has_function_privilege(
        'bridge_school_auth_gateway',
        'bridge_establish_verified_actor_context(uuid,uuid,uuid)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'trusted auth gateway lacks actor establishment capability';
    END IF;

    IF NOT has_function_privilege(
        'bridge_school_member_principal',
        'bridge_establish_verified_actor_context(uuid,uuid,uuid)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'dormant member server principal lacks inherited auth gateway capability';
    END IF;

    IF has_table_privilege('bridge_school_auth_gateway','auth_identity','SELECT')
       OR has_table_privilege('bridge_school_auth_gateway','person','SELECT')
       OR has_table_privilege('bridge_school_auth_gateway','club_payment','SELECT')
       OR has_table_privilege('bridge_school_auth_gateway','actor_context_signing_secret','SELECT')
       OR has_table_privilege('bridge_school_auth_gateway','audit_event','SELECT') THEN
        RAISE EXCEPTION 'auth gateway crossed direct data-read boundary';
    END IF;
END $$;

ROLLBACK;
