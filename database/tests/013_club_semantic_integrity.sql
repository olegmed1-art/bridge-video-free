\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_service1 uuid;
    v_service2 uuid;
    v_price1 uuid;
    v_package uuid;
    v_package_version uuid;
    v_package_grant uuid;
    v_entitlement uuid;
    v_usage uuid;
    v_contact uuid;
    v_communication uuid;
    v_message uuid;
    v_constraint_valid boolean;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Club Semantic Test Person') RETURNING person_id INTO v_person;
    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'semantic-service-1','Semantic service 1','lesson') RETURNING service_id INTO v_service1;
    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'semantic-service-2','Semantic service 2','lesson') RETURNING service_id INTO v_service2;

    -- Closed active price periods still must not overlap.
    INSERT INTO service_price_version(service_id,version_no,amount,currency_code,effective_from,effective_to,status)
    VALUES (v_service1,1,100,'ILS',now(),now()+interval '10 days','active')
    RETURNING price_version_id INTO v_price1;
    BEGIN
        INSERT INTO service_price_version(service_id,version_no,amount,currency_code,effective_from,effective_to,status)
        VALUES (v_service1,2,120,'ILS',now()+interval '5 days',now()+interval '15 days','active');
        RAISE EXCEPTION 'overlapping active price versions unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='overlapping active price versions unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO club_package(school_id,stable_key,name)
    VALUES (v_school,'semantic-package','Semantic package') RETURNING package_id INTO v_package;
    INSERT INTO club_package_version(package_id,version_no,effective_from,effective_to,status)
    VALUES (v_package,1,now(),now()+interval '10 days','active')
    RETURNING package_version_id INTO v_package_version;
    BEGIN
        INSERT INTO club_package_version(package_id,version_no,effective_from,effective_to,status)
        VALUES (v_package,2,now()+interval '5 days',now()+interval '15 days','active');
        RAISE EXCEPTION 'overlapping active package versions unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='overlapping active package versions unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO package_service_rule(package_version_id,service_id,quantity)
    VALUES (v_package_version,v_service1,2);
    INSERT INTO person_package_grant(school_id,person_id,package_version_id)
    VALUES (v_school,v_person,v_package_version)
    RETURNING package_grant_id INTO v_package_grant;

    BEGIN
        INSERT INTO person_entitlement(school_id,person_id,service_id,package_version_id,package_grant_id,quantity_granted)
        VALUES (v_school,v_person,v_service2,v_package_version,v_package_grant,1);
        RAISE EXCEPTION 'package entitlement for ungranted service unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='package entitlement for ungranted service unexpectedly accepted' THEN RAISE; END IF;
    END;

    BEGIN
        INSERT INTO person_entitlement(school_id,person_id,service_id,package_version_id,package_grant_id,quantity_granted)
        VALUES (v_school,v_person,v_service1,v_package_version,v_package_grant,3);
        RAISE EXCEPTION 'package entitlement above rule quantity unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='package entitlement above rule quantity unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO person_entitlement(school_id,person_id,service_id,package_version_id,package_grant_id,quantity_granted)
    VALUES (v_school,v_person,v_service1,v_package_version,v_package_grant,2)
    RETURNING entitlement_id INTO v_entitlement;
    INSERT INTO entitlement_usage(entitlement_id,quantity_used,reference_type)
    VALUES (v_entitlement,1,'consume') RETURNING entitlement_usage_id INTO v_usage;
    UPDATE person_entitlement
       SET status='revoked', valid_to=now()+interval '1 second'
     WHERE entitlement_id=v_entitlement;

    BEGIN
        INSERT INTO entitlement_usage(entitlement_id,quantity_used,occurred_at,reference_type)
        VALUES (v_entitlement,0.5,now()+interval '2 seconds','consume-after-revoke');
        RAISE EXCEPTION 'new usage on revoked entitlement unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='new usage on revoked entitlement unexpectedly accepted' THEN RAISE; END IF;
    END;

    -- A correction/reversal is still allowed after revocation/validity closure.
    INSERT INTO entitlement_usage(entitlement_id,quantity_used,occurred_at,reference_type,reversal_of_usage_id)
    VALUES (v_entitlement,1,now()+interval '2 seconds','reversal-after-revoke',v_usage);

    BEGIN
        INSERT INTO club_charge(school_id,person_id,service_id,price_version_id,amount,currency_code)
        VALUES (v_school,v_person,v_service2,v_price1,100,'ILS');
        RAISE EXCEPTION 'charge with another service price version unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='charge with another service price version unexpectedly accepted' THEN RAISE; END IF;
    END;

    -- Preferred routing must be deterministic.
    INSERT INTO contact_method(school_id,person_id,channel,normalized_value,preferred_flag)
    VALUES (v_school,v_person,'email','preferred-1@example.invalid',true)
    RETURNING contact_method_id INTO v_contact;
    BEGIN
        INSERT INTO contact_method(school_id,person_id,channel,normalized_value,preferred_flag)
        VALUES (v_school,v_person,'email','preferred-2@example.invalid',true);
        RAISE EXCEPTION 'two preferred active emails unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='two preferred active emails unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO club_communication(school_id,communication_type,subject,primary_person_id)
    VALUES (v_school,'service','Semantic delivery test',v_person)
    RETURNING communication_id INTO v_communication;
    INSERT INTO club_message(school_id,communication_id,recipient_person_id,author_actor_type,body_text)
    VALUES (v_school,v_communication,v_person,'system','Semantic message')
    RETURNING message_id INTO v_message;

    UPDATE contact_method
       SET status='revoked', valid_to=now()+interval '1 second'
     WHERE contact_method_id=v_contact;
    BEGIN
        INSERT INTO message_delivery(
            school_id,message_id,recipient_person_id,contact_method_id,channel,status,queued_at
        ) VALUES (
            v_school,v_message,v_person,v_contact,'email','queued',now()+interval '2 seconds'
        );
        RAISE EXCEPTION 'delivery through revoked contact unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='delivery through revoked contact unexpectedly accepted' THEN RAISE; END IF;
    END;

    SELECT convalidated INTO v_constraint_valid
      FROM pg_constraint
     WHERE conname='club_event_one_specialized_reference_ck';
    IF v_constraint_valid IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'club event specialization constraint missing or unvalidated';
    END IF;
END $$;

DO $$
BEGIN
    IF has_function_privilege('bridge_school_app_principal','validate_entitlement_package_rule()','EXECUTE')
       OR has_function_privilege('bridge_school_app_principal','validate_entitlement_usage_active_status()','EXECUTE')
       OR has_function_privilege('bridge_school_finance_principal','validate_charge_price_service_integrity()','EXECUTE')
       OR has_function_privilege('bridge_school_worker_principal','validate_delivery_active_contact()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime principal can execute semantic integrity helper directly';
    END IF;
END $$;

ROLLBACK;
