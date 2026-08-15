\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_membership uuid;
    v_service uuid;
    v_price uuid;
    v_package uuid;
    v_package_version uuid;
    v_package_price uuid;
    v_grant uuid;
    v_entitlement uuid;
    v_usage uuid;
    v_contact uuid;
    v_communication uuid;
    v_message uuid;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Historical Boundary Test Person')
    RETURNING person_id INTO v_person;

    INSERT INTO club_membership(school_id,person_id,membership_type,status)
    VALUES (v_school,v_person,'history-boundary','active')
    RETURNING club_membership_id INTO v_membership;
    BEGIN
        UPDATE club_membership SET status='ended', valid_to=NULL
         WHERE club_membership_id=v_membership;
        RAISE EXCEPTION 'ended membership without valid_to unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='ended membership without valid_to unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'history-boundary-service','History boundary service','lesson')
    RETURNING service_id INTO v_service;

    INSERT INTO service_price_version(
        service_id,version_no,amount,currency_code,effective_from,effective_to,status
    ) VALUES (
        v_service,1,100,'ILS',now()-interval '2 days',now()+interval '10 days','active'
    ) RETURNING price_version_id INTO v_price;

    INSERT INTO club_charge(
        school_id,person_id,service_id,price_version_id,amount,currency_code,charged_at
    ) VALUES (v_school,v_person,v_service,v_price,100,'ILS',now());

    BEGIN
        UPDATE service_price_version
           SET effective_to=now()-interval '1 minute'
         WHERE price_version_id=v_price;
        RAISE EXCEPTION 'service price history was retroactively invalidated';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='service price history was retroactively invalidated' THEN RAISE; END IF;
    END;

    INSERT INTO club_package(school_id,stable_key,name)
    VALUES (v_school,'history-boundary-package','History boundary package')
    RETURNING package_id INTO v_package;

    INSERT INTO club_package_version(
        package_id,version_no,effective_from,effective_to,status
    ) VALUES (
        v_package,1,now()-interval '2 days',now()+interval '10 days','active'
    ) RETURNING package_version_id INTO v_package_version;
    INSERT INTO package_service_rule(package_version_id,service_id,quantity)
    VALUES (v_package_version,v_service,4);

    INSERT INTO package_price_version(
        package_id,version_no,amount,currency_code,effective_from,effective_to,status
    ) VALUES (
        v_package,1,350,'ILS',now()-interval '2 days',now()+interval '10 days','active'
    ) RETURNING package_price_version_id INTO v_package_price;

    INSERT INTO person_package_grant(
        school_id,person_id,package_version_id,package_price_version_id,
        granted_at,valid_from,valid_to,status
    ) VALUES (
        v_school,v_person,v_package_version,v_package_price,
        now(),now(),now()+interval '5 days','active'
    ) RETURNING package_grant_id INTO v_grant;

    BEGIN
        UPDATE club_package_version
           SET effective_to=now()-interval '1 minute'
         WHERE package_version_id=v_package_version;
        RAISE EXCEPTION 'package version history was retroactively invalidated';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='package version history was retroactively invalidated' THEN RAISE; END IF;
    END;

    BEGIN
        UPDATE package_price_version
           SET effective_to=now()-interval '1 minute'
         WHERE package_price_version_id=v_package_price;
        RAISE EXCEPTION 'package price history was retroactively invalidated';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='package price history was retroactively invalidated' THEN RAISE; END IF;
    END;

    INSERT INTO person_entitlement(
        school_id,person_id,service_id,package_version_id,package_grant_id,
        quantity_granted,valid_from,valid_to
    ) VALUES (
        v_school,v_person,v_service,v_package_version,v_grant,
        4,now(),now()+interval '4 days'
    ) RETURNING entitlement_id INTO v_entitlement;

    INSERT INTO entitlement_usage(entitlement_id,quantity_used,occurred_at,reference_type)
    VALUES (v_entitlement,1,now(),'history-boundary-use')
    RETURNING entitlement_usage_id INTO v_usage;

    BEGIN
        UPDATE person_entitlement SET status='expired', valid_to=NULL
         WHERE entitlement_id=v_entitlement;
        RAISE EXCEPTION 'expired entitlement without valid_to unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='expired entitlement without valid_to unexpectedly accepted' THEN RAISE; END IF;
    END;

    BEGIN
        UPDATE person_entitlement
           SET valid_to=now()-interval '1 minute'
         WHERE entitlement_id=v_entitlement;
        RAISE EXCEPTION 'entitlement validity was moved before recorded usage';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='entitlement validity was moved before recorded usage' THEN RAISE; END IF;
    END;

    BEGIN
        UPDATE person_package_grant
           SET valid_to=now()-interval '1 minute'
         WHERE package_grant_id=v_grant;
        RAISE EXCEPTION 'package grant validity was moved before recorded usage';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='package grant validity was moved before recorded usage' THEN RAISE; END IF;
    END;

    -- Closing the grant after the recorded use is valid. Fresh later use is blocked by
    -- the parent grant boundary, while a correction/reversal remains possible later.
    UPDATE person_package_grant
       SET valid_to=now()+interval '1 hour'
     WHERE package_grant_id=v_grant;

    BEGIN
        INSERT INTO entitlement_usage(entitlement_id,quantity_used,occurred_at,reference_type)
        VALUES (v_entitlement,1,now()+interval '2 hours','use-after-package-close');
        RAISE EXCEPTION 'fresh entitlement use after package grant close unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='fresh entitlement use after package grant close unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO entitlement_usage(
        entitlement_id,quantity_used,occurred_at,reference_type,reversal_of_usage_id
    ) VALUES (
        v_entitlement,1,now()+interval '2 hours','reversal-after-package-close',v_usage
    );

    -- A closed package lifecycle must carry the boundary that makes the closure auditable.
    BEGIN
        UPDATE person_package_grant
           SET valid_to=NULL, status='revoked'
         WHERE package_grant_id=v_grant;
        RAISE EXCEPTION 'revoked package grant without valid_to unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='revoked package grant without valid_to unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO contact_method(
        school_id,person_id,channel,normalized_value,verification_status,preferred_flag
    ) VALUES (
        v_school,v_person,'email','history-boundary@example.invalid','verified',true
    ) RETURNING contact_method_id INTO v_contact;

    BEGIN
        UPDATE contact_method SET status='revoked', valid_to=NULL
         WHERE contact_method_id=v_contact;
        RAISE EXCEPTION 'revoked contact without valid_to unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='revoked contact without valid_to unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO club_communication(school_id,communication_type,subject,primary_person_id)
    VALUES (v_school,'service','Historical boundary delivery',v_person)
    RETURNING communication_id INTO v_communication;
    INSERT INTO club_message(
        school_id,communication_id,recipient_person_id,author_actor_type,body_text
    ) VALUES (
        v_school,v_communication,v_person,'system','Historical boundary message'
    ) RETURNING message_id INTO v_message;
    INSERT INTO message_delivery(
        school_id,message_id,recipient_person_id,contact_method_id,channel,status
    ) VALUES (
        v_school,v_message,v_person,v_contact,'email','queued'
    );

    BEGIN
        UPDATE contact_method
           SET valid_to=now()-interval '1 minute'
         WHERE contact_method_id=v_contact;
        RAISE EXCEPTION 'contact validity was moved before recorded delivery';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='contact validity was moved before recorded delivery' THEN RAISE; END IF;
    END;
END $$;

DO $$
BEGIN
    IF has_function_privilege('bridge_school_app_principal','validate_entitlement_valid_to_history()','EXECUTE')
       OR has_function_privilege('bridge_school_finance_principal','validate_package_grant_valid_to_history()','EXECUTE')
       OR has_function_privilege('bridge_school_app_principal','validate_contact_valid_to_history()','EXECUTE')
       OR has_function_privilege('bridge_school_finance_principal','validate_commercial_effective_to_history()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime principal can execute historical-boundary helper directly';
    END IF;
END $$;

ROLLBACK;
