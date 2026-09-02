\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_service1 uuid;
    v_service2 uuid;
    v_price1 uuid;
    v_price2 uuid;
    v_event uuid;
    v_booking uuid;
    v_charge_old uuid;
    v_charge_now uuid;
    v_payment_now uuid;
    v_payment_old uuid;
    v_package uuid;
    v_package_version uuid;
    v_package_price uuid;
    v_package_grant uuid;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Charge Provenance Test Person')
    RETURNING person_id INTO v_person;

    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'charge-provenance-service-1','Charge provenance service 1','lesson')
    RETURNING service_id INTO v_service1;
    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'charge-provenance-service-2','Charge provenance service 2','lesson')
    RETURNING service_id INTO v_service2;

    INSERT INTO service_price_version(
        service_id,version_no,amount,currency_code,effective_from,effective_to,status
    ) VALUES (
        v_service1,1,100,'ILS',now()-interval '3 days',now()+interval '3 days','active'
    ) RETURNING price_version_id INTO v_price1;
    INSERT INTO service_price_version(
        service_id,version_no,amount,currency_code,effective_from,effective_to,status
    ) VALUES (
        v_service2,1,120,'ILS',now()-interval '3 days',now()+interval '3 days','active'
    ) RETURNING price_version_id INTO v_price2;

    INSERT INTO club_event(school_id,event_type,title,service_id,starts_at,status)
    VALUES (v_school,'lesson','Charge provenance event',v_service1,now()+interval '1 day','open')
    RETURNING club_event_id INTO v_event;
    INSERT INTO club_booking(school_id,club_event_id,person_id)
    VALUES (v_school,v_event,v_person)
    RETURNING booking_id INTO v_booking;

    BEGIN
        INSERT INTO club_charge(
            school_id,person_id,service_id,booking_id,price_version_id,
            amount,currency_code,charged_at
        ) VALUES (
            v_school,v_person,v_service2,v_booking,v_price2,
            120,'ILS',now()
        );
        RAISE EXCEPTION 'charge with service different from booked event unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='charge with service different from booked event unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO club_charge(
        school_id,person_id,service_id,booking_id,price_version_id,
        amount,currency_code,charged_at
    ) VALUES (
        v_school,v_person,v_service1,v_booking,v_price1,
        100,'ILS',now()-interval '2 hours'
    ) RETURNING charge_id INTO v_charge_old;

    INSERT INTO club_package(school_id,stable_key,name)
    VALUES (v_school,'charge-provenance-package','Charge provenance package')
    RETURNING package_id INTO v_package;
    INSERT INTO club_package_version(
        package_id,version_no,effective_from,effective_to,status
    ) VALUES (
        v_package,1,now()-interval '1 day',now()+interval '1 day','active'
    ) RETURNING package_version_id INTO v_package_version;
    INSERT INTO package_service_rule(package_version_id,service_id,quantity)
    VALUES (v_package_version,v_service1,1);
    INSERT INTO package_price_version(
        package_id,version_no,amount,currency_code,effective_from,effective_to,status
    ) VALUES (
        v_package,1,90,'ILS',now()-interval '1 day',now()+interval '1 day','active'
    ) RETURNING package_price_version_id INTO v_package_price;
    INSERT INTO person_package_grant(
        school_id,person_id,package_version_id,package_price_version_id,
        granted_at,valid_from,valid_to,status
    ) VALUES (
        v_school,v_person,v_package_version,v_package_price,
        now(),now(),now()+interval '1 day','active'
    ) RETURNING package_grant_id INTO v_package_grant;

    BEGIN
        INSERT INTO club_charge(
            school_id,person_id,service_id,package_grant_id,
            amount,currency_code,charged_at
        ) VALUES (
            v_school,v_person,v_service1,v_package_grant,
            100,'ILS',now()
        );
        RAISE EXCEPTION 'charge with service and package origins unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='charge with service and package origins unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO club_payment(
        school_id,person_id,amount,currency_code,paid_at,payment_method
    ) VALUES (
        v_school,v_person,100,'ILS',now(),'test'
    ) RETURNING payment_id INTO v_payment_now;

    BEGIN
        INSERT INTO payment_allocation(
            school_id,payment_id,charge_id,amount,allocated_at
        ) VALUES (
            v_school,v_payment_now,v_charge_old,50,now()-interval '1 hour'
        );
        RAISE EXCEPTION 'allocation before payment unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='allocation before payment unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO club_charge(
        school_id,person_id,service_id,price_version_id,
        amount,currency_code,charged_at
    ) VALUES (
        v_school,v_person,v_service1,v_price1,
        100,'ILS',now()
    ) RETURNING charge_id INTO v_charge_now;

    INSERT INTO club_payment(
        school_id,person_id,amount,currency_code,paid_at,payment_method
    ) VALUES (
        v_school,v_person,100,'ILS',now()-interval '2 hours','test'
    ) RETURNING payment_id INTO v_payment_old;

    BEGIN
        INSERT INTO payment_allocation(
            school_id,payment_id,charge_id,amount,allocated_at
        ) VALUES (
            v_school,v_payment_old,v_charge_now,50,now()-interval '1 hour'
        );
        RAISE EXCEPTION 'allocation before charge unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='allocation before charge unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO payment_allocation(
        school_id,payment_id,charge_id,amount,allocated_at
    ) VALUES (
        v_school,v_payment_old,v_charge_now,50,now()+interval '1 minute'
    );
END $$;

DO $$
BEGIN
    IF has_function_privilege('bridge_school_finance_principal','validate_charge_booking_service_provenance()','EXECUTE')
       OR has_function_privilege('bridge_school_finance_principal','validate_payment_allocation_business_chronology()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime principal can execute charge/ledger provenance helper directly';
    END IF;
END $$;

ROLLBACK;
