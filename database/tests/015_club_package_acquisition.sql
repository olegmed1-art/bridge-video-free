\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_other_person uuid;
    v_service uuid;
    v_package uuid;
    v_package_version uuid;
    v_package_price uuid;
    v_grant1 uuid;
    v_grant2 uuid;
    v_entitlement1 uuid;
    v_entitlement2 uuid;
    v_charge uuid;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Package Acquisition Person') RETURNING person_id INTO v_person;
    INSERT INTO person(preferred_name) VALUES ('Package Acquisition Other') RETURNING person_id INTO v_other_person;

    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'package-acquisition-lesson','Package acquisition lesson','group_lesson')
    RETURNING service_id INTO v_service;

    INSERT INTO club_package(school_id,stable_key,name)
    VALUES (v_school,'package-acquisition-8','Package acquisition 8 lessons')
    RETURNING package_id INTO v_package;

    INSERT INTO club_package_version(package_id,version_no,effective_from,status)
    VALUES (v_package,1,now()-interval '1 minute','active')
    RETURNING package_version_id INTO v_package_version;

    INSERT INTO package_service_rule(package_version_id,service_id,quantity)
    VALUES (v_package_version,v_service,8);

    INSERT INTO package_price_version(package_id,version_no,amount,currency_code,effective_from,status)
    VALUES (v_package,1,640,'ILS',now()-interval '1 minute','active')
    RETURNING package_price_version_id INTO v_package_price;

    BEGIN
        INSERT INTO package_price_version(package_id,version_no,amount,currency_code,effective_from,effective_to,status)
        VALUES (v_package,2,680,'ILS',now(),now()+interval '10 days','active');
        RAISE EXCEPTION 'overlapping active package price unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='overlapping active package price unexpectedly accepted' THEN RAISE; END IF;
    END;

    -- Two acquisitions of the same catalog package are separate durable instances.
    INSERT INTO person_package_grant(
        school_id,person_id,package_version_id,package_price_version_id,grant_reason
    ) VALUES (
        v_school,v_person,v_package_version,v_package_price,'first purchase'
    ) RETURNING package_grant_id INTO v_grant1;

    INSERT INTO person_package_grant(
        school_id,person_id,package_version_id,package_price_version_id,grant_reason
    ) VALUES (
        v_school,v_person,v_package_version,v_package_price,'second purchase'
    ) RETURNING package_grant_id INTO v_grant2;

    IF v_grant1=v_grant2 THEN
        RAISE EXCEPTION 'two package acquisitions collapsed into one grant';
    END IF;

    INSERT INTO person_entitlement(
        school_id,person_id,service_id,package_version_id,package_grant_id,quantity_granted
    ) VALUES (
        v_school,v_person,v_service,v_package_version,v_grant1,8
    ) RETURNING entitlement_id INTO v_entitlement1;

    INSERT INTO person_entitlement(
        school_id,person_id,service_id,package_version_id,package_grant_id,quantity_granted
    ) VALUES (
        v_school,v_person,v_service,v_package_version,v_grant2,8
    ) RETURNING entitlement_id INTO v_entitlement2;

    IF v_entitlement1=v_entitlement2 THEN
        RAISE EXCEPTION 'two package acquisitions collapsed into one entitlement';
    END IF;

    -- The same acquired package cannot mint the same service twice.
    BEGIN
        INSERT INTO person_entitlement(
            school_id,person_id,service_id,package_version_id,package_grant_id,quantity_granted
        ) VALUES (
            v_school,v_person,v_service,v_package_version,v_grant1,1
        );
        RAISE EXCEPTION 'duplicate service entitlement for one package grant unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='duplicate service entitlement for one package grant unexpectedly accepted' THEN RAISE; END IF;
    END;

    -- Catalog package reference without a person-specific acquisition is no longer valid.
    BEGIN
        INSERT INTO person_entitlement(
            school_id,person_id,service_id,package_version_id,quantity_granted
        ) VALUES (
            v_school,v_person,v_service,v_package_version,1
        );
        RAISE EXCEPTION 'package entitlement without acquisition unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='package entitlement without acquisition unexpectedly accepted' THEN RAISE; END IF;
    END;

    -- A grant cannot be reused for another person.
    BEGIN
        INSERT INTO person_entitlement(
            school_id,person_id,service_id,package_version_id,package_grant_id,quantity_granted
        ) VALUES (
            v_school,v_other_person,v_service,v_package_version,v_grant1,1
        );
        RAISE EXCEPTION 'package grant reused for another person unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='package grant reused for another person unexpectedly accepted' THEN RAISE; END IF;
    END;

    -- Financial charge can be attached to the exact acquisition and package price.
    INSERT INTO club_charge(
        school_id,person_id,package_grant_id,package_price_version_id,amount,currency_code,charge_type
    ) VALUES (
        v_school,v_person,v_grant1,v_package_price,640,'ILS','package'
    ) RETURNING charge_id INTO v_charge;

    IF v_charge IS NULL THEN RAISE EXCEPTION 'package charge was not created'; END IF;

    BEGIN
        INSERT INTO club_charge(
            school_id,person_id,package_grant_id,package_price_version_id,amount,currency_code,charge_type
        ) VALUES (
            v_school,v_other_person,v_grant1,v_package_price,640,'ILS','package'
        );
        RAISE EXCEPTION 'package charge for another person unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='package charge for another person unexpectedly accepted' THEN RAISE; END IF;
    END;
END $$;

DO $$
BEGIN
    IF NOT has_table_privilege('bridge_school_finance','person_package_grant','INSERT')
       OR has_table_privilege('bridge_school_finance','person_package_grant','DELETE')
       OR has_table_privilege('bridge_school_app','person_package_grant','INSERT')
       OR NOT has_table_privilege('bridge_school_finance','package_price_version','INSERT')
       OR has_table_privilege('bridge_school_app','package_price_version','INSERT') THEN
        RAISE EXCEPTION 'package acquisition permissions outside contract';
    END IF;

    IF has_function_privilege('bridge_school_finance_principal','validate_person_package_grant_scope()','EXECUTE')
       OR has_function_privilege('bridge_school_app_principal','validate_entitlement_package_grant()','EXECUTE')
       OR has_function_privilege('bridge_school_finance_principal','validate_charge_package_grant()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime principal can execute package acquisition helper directly';
    END IF;
END $$;

ROLLBACK;
