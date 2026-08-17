\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person1 uuid;
    v_person2 uuid;
    v_identity1 uuid;
    v_identity2 uuid;
    v_service uuid;
    v_event uuid;
    v_booking1 uuid;
    v_booking2 uuid;
    v_comm uuid;
    v_msg1 uuid;
    v_count integer;
    v_balance numeric;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Member Isolation One') RETURNING person_id INTO v_person1;
    INSERT INTO person(preferred_name) VALUES ('Member Isolation Two') RETURNING person_id INTO v_person2;

    INSERT INTO auth_identity(person_id,provider_key,provider_subject)
    VALUES (v_person1,'isolation-provider','one') RETURNING auth_identity_id INTO v_identity1;
    INSERT INTO auth_identity(person_id,provider_key,provider_subject)
    VALUES (v_person2,'isolation-provider','two') RETURNING auth_identity_id INTO v_identity2;

    INSERT INTO person_role_assignment(school_id,person_id,role_key)
    VALUES (v_school,v_person1,'member');
    INSERT INTO person_role_assignment(school_id,person_id,role_key)
    VALUES (v_school,v_person2,'member');

    INSERT INTO club_membership(school_id,person_id,membership_type)
    VALUES (v_school,v_person1,'test');
    INSERT INTO club_membership(school_id,person_id,membership_type)
    VALUES (v_school,v_person2,'test');

    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'member-isolation-service','Isolation lesson','group_lesson')
    RETURNING service_id INTO v_service;

    INSERT INTO club_event(school_id,event_type,title,service_id,starts_at,status)
    VALUES (v_school,'lesson','Isolation event',v_service,now()+interval '1 day','open')
    RETURNING club_event_id INTO v_event;

    INSERT INTO club_booking(school_id,club_event_id,person_id)
    VALUES (v_school,v_event,v_person1) RETURNING booking_id INTO v_booking1;
    INSERT INTO club_booking_state_event(booking_id,state)
    VALUES (v_booking1,'confirmed');
    INSERT INTO club_booking(school_id,club_event_id,person_id)
    VALUES (v_school,v_event,v_person2) RETURNING booking_id INTO v_booking2;
    INSERT INTO club_booking_state_event(booking_id,state)
    VALUES (v_booking2,'confirmed');

    INSERT INTO person_entitlement(school_id,person_id,service_id,quantity_granted)
    VALUES (v_school,v_person1,v_service,4);
    INSERT INTO person_entitlement(school_id,person_id,service_id,quantity_granted)
    VALUES (v_school,v_person2,v_service,9);

    INSERT INTO club_charge(school_id,person_id,service_id,amount,currency_code)
    VALUES (v_school,v_person1,v_service,100,'ILS');
    INSERT INTO club_payment(school_id,person_id,amount,currency_code,paid_at,payment_method)
    VALUES (v_school,v_person1,30,'ILS',now(),'test');
    INSERT INTO club_charge(school_id,person_id,service_id,amount,currency_code)
    VALUES (v_school,v_person2,v_service,900,'ILS');
    INSERT INTO club_payment(school_id,person_id,amount,currency_code,paid_at,payment_method)
    VALUES (v_school,v_person2,900,'ILS',now(),'test');

    INSERT INTO club_communication(school_id,communication_type,subject)
    VALUES (v_school,'service','Isolation communication') RETURNING communication_id INTO v_comm;
    INSERT INTO club_message(
        school_id,communication_id,recipient_person_id,author_actor_type,body_text,visibility_class
    ) VALUES (
        v_school,v_comm,v_person1,'system','private-one','private_to_person'
    ) RETURNING message_id INTO v_msg1;
    INSERT INTO message_delivery(school_id,message_id,recipient_person_id,channel,status)
    VALUES (v_school,v_msg1,v_person1,'web','queued');
    INSERT INTO club_message(
        school_id,communication_id,recipient_person_id,author_actor_type,body_text,visibility_class
    ) VALUES (
        v_school,v_comm,v_person2,'system','private-two','private_to_person'
    );
    INSERT INTO club_message(
        school_id,communication_id,author_actor_type,body_text,visibility_class
    ) VALUES (
        v_school,v_comm,'system','public-message','public_club'
    );
    INSERT INTO club_message(
        school_id,communication_id,author_actor_type,body_text,visibility_class
    ) VALUES (
        v_school,v_comm,'system','member-message','member_visible'
    );
    INSERT INTO club_message(
        school_id,communication_id,recipient_person_id,author_actor_type,body_text,visibility_class
    ) VALUES (
        v_school,v_comm,v_person1,'administrator','admin-secret','admin_only'
    );

    -- Missing actor context is fail closed.
    SELECT count(*) INTO v_count FROM member_self_profile;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'member self view leaked rows without actor context';
    END IF;

    PERFORM bridge_establish_verified_actor_context(v_identity1,v_school,uuidv7());

    SELECT count(*) INTO v_count FROM member_self_profile WHERE person_id=v_person1;
    IF v_count <> 1 THEN RAISE EXCEPTION 'self profile missing'; END IF;
    SELECT count(*) INTO v_count FROM member_self_profile WHERE person_id=v_person2;
    IF v_count <> 0 THEN RAISE EXCEPTION 'other profile leaked'; END IF;

    SELECT count(*) INTO v_count FROM member_self_membership WHERE person_id=v_person2;
    IF v_count <> 0 THEN RAISE EXCEPTION 'other membership leaked'; END IF;
    SELECT count(*) INTO v_count FROM member_self_booking WHERE person_id=v_person2;
    IF v_count <> 0 THEN RAISE EXCEPTION 'other booking leaked'; END IF;
    SELECT count(*) INTO v_count FROM member_self_entitlement WHERE person_id=v_person2;
    IF v_count <> 0 THEN RAISE EXCEPTION 'other entitlement leaked'; END IF;
    SELECT count(*) INTO v_count FROM member_self_charge WHERE person_id=v_person2;
    IF v_count <> 0 THEN RAISE EXCEPTION 'other charge leaked'; END IF;
    SELECT count(*) INTO v_count FROM member_self_payment WHERE person_id=v_person2;
    IF v_count <> 0 THEN RAISE EXCEPTION 'other payment leaked'; END IF;

    SELECT balance_due INTO v_balance
      FROM member_self_financial_balance
     WHERE currency_code='ILS';
    IF v_balance <> 70 THEN
        RAISE EXCEPTION 'self financial balance expected 70, got %', v_balance;
    END IF;

    SELECT count(*) INTO v_count
      FROM member_self_message
     WHERE body_text='private-two' OR body_text='admin-secret';
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'private/admin message leaked to member';
    END IF;
    SELECT count(*) INTO v_count
      FROM member_self_message
     WHERE body_text IN ('private-one','public-message','member-message');
    IF v_count <> 3 THEN
        RAISE EXCEPTION 'expected member-visible messages missing, got %', v_count;
    END IF;

    SELECT count(*) INTO v_count
      FROM member_self_message_delivery
     WHERE recipient_person_id=v_person1;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'self message delivery missing';
    END IF;
END $$;

DO $$
BEGIN
    IF NOT has_table_privilege('bridge_school_member_principal','member_self_profile','SELECT')
       OR NOT has_table_privilege('bridge_school_member_principal','member_self_financial_balance','SELECT')
       OR NOT has_table_privilege('bridge_school_member_principal','member_self_message','SELECT') THEN
        RAISE EXCEPTION 'member principal is missing self-service projection access';
    END IF;

    IF has_table_privilege('bridge_school_member_principal','club_membership','SELECT')
       OR has_table_privilege('bridge_school_member_principal','club_charge','SELECT')
       OR has_table_privilege('bridge_school_member_principal','club_message','SELECT')
       OR has_table_privilege('bridge_school_member_principal','admin_task','SELECT') THEN
        RAISE EXCEPTION 'member principal has broad base-table read access';
    END IF;
END $$;

ROLLBACK;
