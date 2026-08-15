\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_other_school uuid;
    v_person uuid;
    v_other_person uuid;
    v_service uuid;
    v_other_service uuid;
    v_price uuid;
    v_package uuid;
    v_package_version uuid;
    v_entitlement uuid;
    v_usage uuid;
    v_event uuid;
    v_booking uuid;
    v_charge uuid;
    v_payment uuid;
    v_communication uuid;
    v_message uuid;
    v_contact uuid;
    v_task uuid;
    v_balance numeric;
    v_remaining numeric;
    v_state text;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO school(stable_name) VALUES ('Club Operations Test School') RETURNING school_id INTO v_other_school;
    INSERT INTO person(preferred_name) VALUES ('Club Test Person') RETURNING person_id INTO v_person;
    INSERT INTO person(preferred_name) VALUES ('Club Other Person') RETURNING person_id INTO v_other_person;

    INSERT INTO club_membership(school_id,person_id,membership_type)
    VALUES (v_school,v_person,'standard');

    INSERT INTO contact_method(school_id,person_id,channel,normalized_value,verification_status,preferred_flag)
    VALUES (v_school,v_person,'email','club-test@example.invalid','verified',true)
    RETURNING contact_method_id INTO v_contact;

    INSERT INTO contact_preference(school_id,person_id,channel,communication_type,permission_state)
    VALUES (v_school,v_person,'email','service','allowed');

    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'test-group-lesson','Test group lesson','group_lesson')
    RETURNING service_id INTO v_service;
    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_other_school,'other-service','Other service','other')
    RETURNING service_id INTO v_other_service;

    INSERT INTO service_price_version(service_id,version_no,amount,currency_code,effective_from,status)
    VALUES (v_service,1,100,'ILS',now(),'active') RETURNING price_version_id INTO v_price;

    INSERT INTO club_package(school_id,stable_key,name)
    VALUES (v_school,'test-8','Test 8 lessons') RETURNING package_id INTO v_package;
    INSERT INTO club_package_version(package_id,version_no,effective_from,status)
    VALUES (v_package,1,now(),'active') RETURNING package_version_id INTO v_package_version;
    INSERT INTO package_service_rule(package_version_id,service_id,quantity)
    VALUES (v_package_version,v_service,8);

    INSERT INTO person_entitlement(school_id,person_id,service_id,package_version_id,quantity_granted)
    VALUES (v_school,v_person,v_service,v_package_version,8)
    RETURNING entitlement_id INTO v_entitlement;

    INSERT INTO entitlement_usage(entitlement_id,quantity_used,reference_type)
    VALUES (v_entitlement,1,'test') RETURNING entitlement_usage_id INTO v_usage;
    INSERT INTO entitlement_usage(entitlement_id,quantity_used,reference_type,reversal_of_usage_id)
    VALUES (v_entitlement,1,'test_reversal',v_usage);

    SELECT quantity_remaining INTO v_remaining FROM person_entitlement_balance WHERE entitlement_id=v_entitlement;
    IF v_remaining <> 8 THEN RAISE EXCEPTION 'entitlement balance expected 8 after reversal, got %', v_remaining; END IF;

    BEGIN
        INSERT INTO person_entitlement(school_id,person_id,service_id,quantity_granted)
        VALUES (v_school,v_person,v_other_service,1);
        RAISE EXCEPTION 'cross-school entitlement unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='cross-school entitlement unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO club_event(school_id,event_type,title,service_id,starts_at,status)
    VALUES (v_school,'lesson','Club Test Event',v_service,now()+interval '1 day','open')
    RETURNING club_event_id INTO v_event;
    INSERT INTO club_booking(school_id,club_event_id,person_id)
    VALUES (v_school,v_event,v_person) RETURNING booking_id INTO v_booking;
    INSERT INTO club_booking_state_event(booking_id,state) VALUES (v_booking,'requested');
    INSERT INTO club_booking_state_event(booking_id,state) VALUES (v_booking,'confirmed');
    SELECT state INTO v_state FROM club_booking_current_state WHERE booking_id=v_booking;
    IF v_state <> 'confirmed' THEN RAISE EXCEPTION 'booking current state expected confirmed, got %', v_state; END IF;

    INSERT INTO club_charge(school_id,person_id,service_id,booking_id,price_version_id,amount,currency_code)
    VALUES (v_school,v_person,v_service,v_booking,v_price,100,'ILS') RETURNING charge_id INTO v_charge;
    INSERT INTO club_payment(school_id,person_id,amount,currency_code,paid_at,payment_method)
    VALUES (v_school,v_person,70,'ILS',now(),'test') RETURNING payment_id INTO v_payment;
    INSERT INTO payment_allocation(school_id,payment_id,charge_id,amount)
    VALUES (v_school,v_payment,v_charge,70);

    BEGIN
        INSERT INTO payment_allocation(school_id,payment_id,charge_id,amount)
        VALUES (v_school,v_payment,v_charge,1);
        RAISE EXCEPTION 'payment over-allocation unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='payment over-allocation unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO financial_adjustment(school_id,person_id,currency_code,balance_delta,adjustment_type,related_charge_id,reason)
    VALUES (v_school,v_person,'ILS',-10,'discount',v_charge,'test discount');
    SELECT balance_due INTO v_balance FROM person_financial_balance
     WHERE school_id=v_school AND person_id=v_person AND currency_code='ILS';
    IF v_balance <> 20 THEN RAISE EXCEPTION 'financial balance expected 20, got %', v_balance; END IF;

    INSERT INTO club_communication(school_id,communication_type,subject,primary_person_id)
    VALUES (v_school,'service','Test communication',v_person)
    RETURNING communication_id INTO v_communication;
    INSERT INTO club_message(school_id,communication_id,recipient_person_id,author_actor_type,body_text)
    VALUES (v_school,v_communication,v_person,'system','Test message')
    RETURNING message_id INTO v_message;
    INSERT INTO message_delivery(school_id,message_id,recipient_person_id,contact_method_id,channel,status)
    VALUES (v_school,v_message,v_person,v_contact,'email','queued');

    BEGIN
        INSERT INTO message_delivery(school_id,message_id,recipient_person_id,contact_method_id,channel,status,attempt_no)
        VALUES (v_school,v_message,v_other_person,v_contact,'email','queued',2);
        RAISE EXCEPTION 'delivery recipient mismatch unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='delivery recipient mismatch unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO admin_task(school_id,title,subject_person_id,priority)
    VALUES (v_school,'Test admin task',v_person,'normal') RETURNING admin_task_id INTO v_task;
    INSERT INTO admin_task_state_event(admin_task_id,state) VALUES (v_task,'open');
    INSERT INTO admin_task_state_event(admin_task_id,state) VALUES (v_task,'completed');
    SELECT state INTO v_state FROM admin_task_current_state WHERE admin_task_id=v_task;
    IF v_state <> 'completed' THEN RAISE EXCEPTION 'admin task current state expected completed, got %', v_state; END IF;
END $$;

DO $$
DECLARE r record;
BEGIN
    SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
      INTO r FROM pg_roles WHERE rolname='bridge_school_finance';
    IF NOT FOUND OR r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls THEN
        RAISE EXCEPTION 'finance capability unsafe or missing';
    END IF;
    SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
      INTO r FROM pg_roles WHERE rolname='bridge_school_finance_principal';
    IF NOT FOUND OR r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls THEN
        RAISE EXCEPTION 'finance principal unsafe or missing';
    END IF;

    IF NOT has_table_privilege('bridge_school_app','club_membership','INSERT')
       OR has_table_privilege('bridge_school_app','club_service','INSERT')
       OR has_table_privilege('bridge_school_app','club_charge','INSERT')
       OR has_table_privilege('bridge_school_app','club_membership','DELETE')
       OR has_table_privilege('bridge_school_app','club_booking','UPDATE')
       OR NOT has_column_privilege('bridge_school_app','club_membership','status','UPDATE')
       OR has_column_privilege('bridge_school_app','club_membership','person_id','UPDATE') THEN
        RAISE EXCEPTION 'application Club Operations permissions outside contract';
    END IF;

    IF NOT has_table_privilege('bridge_school_finance','club_charge','INSERT')
       OR has_table_privilege('bridge_school_finance','club_charge','UPDATE')
       OR has_table_privilege('bridge_school_finance','club_charge','DELETE')
       OR NOT has_table_privilege('bridge_school_finance','service_price_version','INSERT')
       OR NOT has_table_privilege('bridge_school_finance','person','SELECT')
       OR has_table_privilege('bridge_school_finance','student_profile_snapshot','SELECT') THEN
        RAISE EXCEPTION 'finance permissions outside contract';
    END IF;

    IF NOT has_table_privilege('bridge_school_worker','message_delivery','INSERT')
       OR has_table_privilege('bridge_school_worker','message_delivery','UPDATE')
       OR NOT has_column_privilege('bridge_school_worker','message_delivery','status','UPDATE')
       OR has_column_privilege('bridge_school_worker','message_delivery','recipient_person_id','UPDATE')
       OR has_table_privilege('bridge_school_worker','message_delivery','DELETE') THEN
        RAISE EXCEPTION 'communication worker permissions outside contract';
    END IF;

    IF has_function_privilege('bridge_school_app_principal','validate_club_booking_scope()','EXECUTE')
       OR has_function_privilege('bridge_school_worker_principal','validate_communication_scope()','EXECUTE')
       OR has_function_privilege('bridge_school_finance_principal','validate_financial_ledger_scope()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime principal can execute trigger helper directly';
    END IF;
END $$;

ROLLBACK;
