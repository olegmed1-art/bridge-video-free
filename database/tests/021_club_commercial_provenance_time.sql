\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_service uuid;
    v_service_price_active uuid;
    v_service_price_candidate uuid;
    v_package uuid;
    v_package_version_active uuid;
    v_package_version_candidate uuid;
    v_package_price_active uuid;
    v_package_price_candidate uuid;
    v_grant uuid;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Commercial Provenance Test Person')
    RETURNING person_id INTO v_person;
    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'provenance-time-service','Provenance time service','lesson')
    RETURNING service_id INTO v_service;

    INSERT INTO service_price_version(
        service_id,version_no,amount,currency_code,effective_from,effective_to,status
    ) VALUES (
        v_service,1,100,'ILS',now()-interval '2 days',now()+interval '2 days','active'
    ) RETURNING price_version_id INTO v_service_price_active;

    -- Candidate may overlap the active period because only active versions are excluded;
    -- this isolates candidate-status rejection from timestamp rejection.
    INSERT INTO service_price_version(
        service_id,version_no,amount,currency_code,effective_from,effective_to,status
    ) VALUES (
        v_service,2,120,'ILS',now()-interval '1 day',now()+interval '1 day','candidate'
    ) RETURNING price_version_id INTO v_service_price_candidate;

    BEGIN
        INSERT INTO club_charge(
            school_id,person_id,service_id,price_version_id,amount,currency_code,charged_at
        ) VALUES (
            v_school,v_person,v_service,v_service_price_candidate,120,'ILS',now()
        );
        RAISE EXCEPTION 'charge using candidate service price unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='charge using candidate service price unexpectedly accepted' THEN RAISE; END IF;
    END;

    BEGIN
        INSERT INTO club_charge(
            school_id,person_id,service_id,price_version_id,amount,currency_code,charged_at
        ) VALUES (
            v_school,v_person,v_service,v_service_price_active,100,'ILS',now()+interval '5 days'
        );
        RAISE EXCEPTION 'charge outside service price validity unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='charge outside service price validity unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO club_charge(
        school_id,person_id,service_id,price_version_id,amount,currency_code,charged_at
    ) VALUES (
        v_school,v_person,v_service,v_service_price_active,90,'ILS',now()
    );
    -- Amount equality is intentionally not enforced: discount/override policy is separate.

    INSERT INTO club_package(school_id,stable_key,name)
    VALUES (v_school,'provenance-time-package','Provenance time package')
    RETURNING package_id INTO v_package;

    INSERT INTO club_package_version(
        package_id,version_no,effective_from,effective_to,status
    ) VALUES (
        v_package,1,now()-interval '2 days',now()+interval '2 days','active'
    ) RETURNING package_version_id INTO v_package_version_active;
    INSERT INTO package_service_rule(package_version_id,service_id,quantity)
    VALUES (v_package_version_active,v_service,2);

    INSERT INTO club_package_version(
        package_id,version_no,effective_from,effective_to,status
    ) VALUES (
        v_package,2,now()-interval '1 day',now()+interval '1 day','candidate'
    ) RETURNING package_version_id INTO v_package_version_candidate;

    INSERT INTO package_price_version(
        package_id,version_no,amount,currency_code,effective_from,effective_to,status
    ) VALUES (
        v_package,1,180,'ILS',now()-interval '2 days',now()+interval '2 days','active'
    ) RETURNING package_price_version_id INTO v_package_price_active;
    INSERT INTO package_price_version(
        package_id,version_no,amount,currency_code,effective_from,effective_to,status
    ) VALUES (
        v_package,2,200,'ILS',now()-interval '1 day',now()+interval '1 day','candidate'
    ) RETURNING package_price_version_id INTO v_package_price_candidate;

    BEGIN
        INSERT INTO person_package_grant(
            school_id,person_id,package_version_id,granted_at
        ) VALUES (
            v_school,v_person,v_package_version_candidate,now()
        );
        RAISE EXCEPTION 'grant using candidate package version unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='grant using candidate package version unexpectedly accepted' THEN RAISE; END IF;
    END;

    BEGIN
        INSERT INTO person_package_grant(
            school_id,person_id,package_version_id,package_price_version_id,granted_at
        ) VALUES (
            v_school,v_person,v_package_version_active,v_package_price_candidate,now()
        );
        RAISE EXCEPTION 'grant using candidate package price unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='grant using candidate package price unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO person_package_grant(
        school_id,person_id,package_version_id,package_price_version_id,granted_at
    ) VALUES (
        v_school,v_person,v_package_version_active,v_package_price_active,now()
    ) RETURNING package_grant_id INTO v_grant;

    BEGIN
        INSERT INTO club_charge(
            school_id,person_id,package_grant_id,package_price_version_id,
            amount,currency_code,charge_type,charged_at
        ) VALUES (
            v_school,v_person,v_grant,v_package_price_active,
            180,'ILS','package',now()+interval '5 days'
        );
        RAISE EXCEPTION 'package charge outside agreed price validity unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='package charge outside agreed price validity unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO club_charge(
        school_id,person_id,package_grant_id,package_price_version_id,
        amount,currency_code,charge_type,charged_at
    ) VALUES (
        v_school,v_person,v_grant,v_package_price_active,
        170,'ILS','package',now()
    );
END $$;

DO $$
BEGIN
    IF has_function_privilege('bridge_school_finance_principal','validate_charge_price_service_integrity()','EXECUTE')
       OR has_function_privilege('bridge_school_finance_principal','validate_person_package_grant_scope()','EXECUTE')
       OR has_function_privilege('bridge_school_finance_principal','validate_charge_package_grant()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime principal can execute commercial provenance helper directly';
    END IF;
END $$;

ROLLBACK;
