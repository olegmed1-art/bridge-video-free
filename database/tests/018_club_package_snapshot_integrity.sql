\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_service1 uuid;
    v_service2 uuid;
    v_package uuid;
    v_package_version uuid;
    v_price1 uuid;
    v_price2 uuid;
    v_grant uuid;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Package Snapshot Test Person') RETURNING person_id INTO v_person;
    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'snapshot-service-1','Snapshot service 1','lesson') RETURNING service_id INTO v_service1;
    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'snapshot-service-2','Snapshot service 2','lesson') RETURNING service_id INTO v_service2;

    INSERT INTO club_package(school_id,stable_key,name)
    VALUES (v_school,'snapshot-package','Snapshot package') RETURNING package_id INTO v_package;
    INSERT INTO club_package_version(package_id,version_no,effective_from,status)
    VALUES (v_package,1,now()-interval '1 day','active')
    RETURNING package_version_id INTO v_package_version;
    INSERT INTO package_service_rule(package_version_id,service_id,quantity)
    VALUES (v_package_version,v_service1,4);

    INSERT INTO package_price_version(
        package_id,version_no,amount,currency_code,effective_from,effective_to,status
    ) VALUES (
        v_package,1,400,'ILS',now()-interval '1 day',now()+interval '1 day','active'
    ) RETURNING package_price_version_id INTO v_price1;

    INSERT INTO package_price_version(
        package_id,version_no,amount,currency_code,effective_from,effective_to,status
    ) VALUES (
        v_package,2,450,'ILS',now()+interval '2 days',now()+interval '3 days','candidate'
    ) RETURNING package_price_version_id INTO v_price2;

    -- A price version that is not effective at acquisition time cannot be recorded as agreed price.
    BEGIN
        INSERT INTO person_package_grant(
            school_id,person_id,package_version_id,package_price_version_id,granted_at
        ) VALUES (
            v_school,v_person,v_package_version,v_price2,now()
        );
        RAISE EXCEPTION 'future package price unexpectedly accepted for current acquisition';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='future package price unexpectedly accepted for current acquisition' THEN RAISE; END IF;
    END;

    INSERT INTO person_package_grant(
        school_id,person_id,package_version_id,package_price_version_id,granted_at
    ) VALUES (
        v_school,v_person,v_package_version,v_price1,now()
    ) RETURNING package_grant_id INTO v_grant;

    -- Once acquired, the service-rule set of that package version is frozen.
    BEGIN
        INSERT INTO package_service_rule(package_version_id,service_id,quantity)
        VALUES (v_package_version,v_service2,1);
        RAISE EXCEPTION 'package rule added after acquisition unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='package rule added after acquisition unexpectedly accepted' THEN RAISE; END IF;
    END;

    -- A charge for the acquisition cannot silently switch to another price version.
    BEGIN
        INSERT INTO club_charge(
            school_id,person_id,package_grant_id,package_price_version_id,amount,currency_code,charge_type
        ) VALUES (
            v_school,v_person,v_grant,v_price2,450,'ILS','package'
        );
        RAISE EXCEPTION 'charge with price different from acquisition unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='charge with price different from acquisition unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO club_charge(
        school_id,person_id,package_grant_id,package_price_version_id,amount,currency_code,charge_type
    ) VALUES (
        v_school,v_person,v_grant,v_price1,400,'ILS','package'
    );
END $$;

DO $$
BEGIN
    IF has_function_privilege('bridge_school_finance_principal','validate_package_rule_frozen_after_grant()','EXECUTE')
       OR has_function_privilege('bridge_school_finance_principal','validate_person_package_grant_scope()','EXECUTE')
       OR has_function_privilege('bridge_school_finance_principal','validate_charge_package_grant()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime principal can execute package snapshot helper directly';
    END IF;
END $$;

ROLLBACK;
