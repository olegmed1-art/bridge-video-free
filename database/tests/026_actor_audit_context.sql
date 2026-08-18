\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_identity uuid;
    v_request uuid := uuidv7();
    v_membership uuid;
    v_service uuid;
    v_count integer;
    v_actor uuid;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Actor Audit Person') RETURNING person_id INTO v_person;
    INSERT INTO auth_identity(person_id,provider_key,provider_subject)
    VALUES (v_person,'audit-provider','audit-subject') RETURNING auth_identity_id INTO v_identity;
    INSERT INTO person_role_assignment(school_id,person_id,role_key)
    VALUES (v_school,v_person,'member');

    PERFORM bridge_establish_verified_actor_context(v_identity,v_school,v_request);

    INSERT INTO club_membership(school_id,person_id,membership_type,status)
    VALUES (v_school,v_person,'audit-test','active')
    RETURNING club_membership_id INTO v_membership;

    UPDATE club_membership
       SET status='paused'
     WHERE club_membership_id=v_membership;

    INSERT INTO contact_method(
        school_id,person_id,channel,normalized_value,verification_status
    ) VALUES (
        v_school,v_person,'email','actor-audit@example.invalid','verified'
    );

    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'actor-audit-service','Actor audit service','lesson')
    RETURNING service_id INTO v_service;

    INSERT INTO club_charge(school_id,person_id,service_id,amount,currency_code)
    VALUES (v_school,v_person,v_service,50,'ILS');
    INSERT INTO club_payment(school_id,person_id,amount,currency_code,paid_at,payment_method)
    VALUES (v_school,v_person,20,'ILS',now(),'test');

    SELECT count(*) INTO v_count
      FROM audit_event
     WHERE request_id=v_request
       AND actor_person_id=v_person
       AND auth_identity_id=v_identity
       AND school_id=v_school
       AND object_type IN ('club_membership','contact_method','club_charge','club_payment');
    IF v_count < 5 THEN
        RAISE EXCEPTION 'expected at least five attributed sensitive audit events, got %', v_count;
    END IF;

    SELECT actor_person_id INTO v_actor
      FROM club_membership_state_event
     WHERE club_membership_id=v_membership
       AND state='paused'
     ORDER BY occurred_at DESC, state_sequence DESC
     LIMIT 1;
    IF v_actor IS DISTINCT FROM v_person THEN
        RAISE EXCEPTION 'membership state history did not capture authenticated actor';
    END IF;

    SELECT count(*) INTO v_count
      FROM club_membership_state_event
     WHERE club_membership_id=v_membership
       AND actor_person_id=v_person
       AND metadata->>'auth_identity_id'=v_identity::text
       AND metadata->>'request_id'=v_request::text;
    IF v_count <> 2 THEN
        RAISE EXCEPTION 'initial/status membership events expected two attributed rows, got %', v_count;
    END IF;
END $$;

DO $$
BEGIN
    IF has_table_privilege('bridge_school_member_principal','audit_event','SELECT')
       OR has_table_privilege('bridge_school_member_principal','audit_event','INSERT')
       OR has_table_privilege('bridge_school_app_principal','audit_event','INSERT')
       OR has_table_privilege('bridge_school_finance_principal','audit_event','INSERT') THEN
        RAISE EXCEPTION 'runtime can read/forge protected audit history';
    END IF;

    IF has_function_privilege('bridge_school_member_principal','capture_sensitive_audit_event()','EXECUTE')
       OR has_function_privilege('bridge_school_app_principal','capture_sensitive_audit_event()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime can directly execute audit trigger helper';
    END IF;
END $$;

ROLLBACK;
