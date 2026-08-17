\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person1 uuid;
    v_person2 uuid;
    v_identity1 uuid;
    v_request uuid := uuidv7();
    v_contact uuid;
    v_membership uuid;
    v_actor uuid;
    v_count integer;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Verified Audit One') RETURNING person_id INTO v_person1;
    INSERT INTO person(preferred_name) VALUES ('Verified Audit Two') RETURNING person_id INTO v_person2;
    INSERT INTO auth_identity(person_id,provider_key,provider_subject)
    VALUES (v_person1,'verified-audit-provider','one') RETURNING auth_identity_id INTO v_identity1;
    INSERT INTO person_role_assignment(school_id,person_id,role_key)
    VALUES (v_school,v_person1,'member');
    INSERT INTO person_role_assignment(school_id,person_id,role_key)
    VALUES (v_school,v_person2,'member');

    PERFORM bridge_establish_verified_actor_context(v_identity1,v_school,v_request);

    -- Self identity is not a wildcard permission. Person grants are explicit.
    IF bridge_actor_has_person_permission(v_person1,'owner.write') THEN
        RAISE EXCEPTION 'self target unexpectedly implied owner.write permission';
    END IF;
    IF bridge_actor_has_person_permission(v_person2,'education.read') THEN
        RAISE EXCEPTION 'missing explicit education grant unexpectedly allowed';
    END IF;
    INSERT INTO person_access_grant(
        school_id,grantee_person_id,target_person_id,permission_key
    ) VALUES (
        v_school,v_person1,v_person2,'education.read'
    );
    IF NOT bridge_actor_has_person_permission(v_person2,'education.read') THEN
        RAISE EXCEPTION 'explicit education grant was not recognized';
    END IF;
    IF bridge_actor_has_person_permission(v_person2,'finance.read') THEN
        RAISE EXCEPTION 'education grant expanded into finance permission';
    END IF;

    -- Forge raw context toward person2 while retaining person1's signature. Resolved
    -- actor must become NULL, and the audit trail must not falsely attribute person2.
    PERFORM set_config('bridge.actor_person_id',v_person2::text,true);
    IF bridge_current_actor_person_id() IS NOT NULL THEN
        RAISE EXCEPTION 'forged actor substitution unexpectedly resolved';
    END IF;

    INSERT INTO contact_method(
        school_id,person_id,channel,normalized_value,verification_status
    ) VALUES (
        v_school,v_person1,'email','forged-attribution@example.invalid','verified'
    ) RETURNING contact_method_id INTO v_contact;

    SELECT actor_person_id INTO v_actor
      FROM audit_event
     WHERE object_type='contact_method'
       AND object_id=v_contact
     ORDER BY occurred_at DESC, audit_event_id DESC
     LIMIT 1;
    IF v_actor IS NOT NULL THEN
        RAISE EXCEPTION 'forged settings produced false audit actor %', v_actor;
    END IF;

    -- Membership history must also refuse forged attribution.
    INSERT INTO club_membership(school_id,person_id,membership_type,status)
    VALUES (v_school,v_person1,'verified-audit-forged','active')
    RETURNING club_membership_id INTO v_membership;
    SELECT actor_person_id INTO v_actor
      FROM club_membership_state_event
     WHERE club_membership_id=v_membership
     ORDER BY occurred_at DESC, state_sequence DESC
     LIMIT 1;
    IF v_actor IS NOT NULL THEN
        RAISE EXCEPTION 'forged settings produced false membership actor %', v_actor;
    END IF;

    -- Re-establish a legitimate signed context and verify attribution resumes.
    PERFORM bridge_establish_verified_actor_context(v_identity1,v_school,uuidv7());
    INSERT INTO contact_method(
        school_id,person_id,channel,normalized_value,verification_status
    ) VALUES (
        v_school,v_person1,'email','verified-attribution@example.invalid','verified'
    ) RETURNING contact_method_id INTO v_contact;
    SELECT actor_person_id INTO v_actor
      FROM audit_event
     WHERE object_type='contact_method'
       AND object_id=v_contact
     ORDER BY occurred_at DESC, audit_event_id DESC
     LIMIT 1;
    IF v_actor IS DISTINCT FROM v_person1 THEN
        RAISE EXCEPTION 'valid signed context failed audit attribution';
    END IF;

    SELECT count(*) INTO v_count
      FROM audit_event
     WHERE actor_person_id=v_person2;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'person2 was falsely attributed in audit history';
    END IF;
END $$;

ROLLBACK;
