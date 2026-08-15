\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    -- Service identity is stable; descriptive/lifecycle fields remain maintainable.
    IF has_column_privilege('bridge_school_finance','club_service','stable_key','UPDATE')
       OR has_column_privilege('bridge_school_finance','club_service','school_id','UPDATE')
       OR has_column_privilege('bridge_school_finance','club_service','service_type','UPDATE')
       OR NOT has_column_privilege('bridge_school_finance','club_service','name','UPDATE')
       OR NOT has_column_privilege('bridge_school_finance','club_service','status','UPDATE') THEN
        RAISE EXCEPTION 'club service runtime mutability outside contract';
    END IF;

    -- Price facts cannot be rewritten. A prior version may only be closed/superseded.
    IF has_table_privilege('bridge_school_finance','service_price_version','UPDATE')
       OR has_column_privilege('bridge_school_finance','service_price_version','amount','UPDATE')
       OR has_column_privilege('bridge_school_finance','service_price_version','currency_code','UPDATE')
       OR has_column_privilege('bridge_school_finance','service_price_version','effective_from','UPDATE')
       OR has_column_privilege('bridge_school_finance','service_price_version','conditions','UPDATE')
       OR NOT has_column_privilege('bridge_school_finance','service_price_version','effective_to','UPDATE')
       OR NOT has_column_privilege('bridge_school_finance','service_price_version','status','UPDATE') THEN
        RAISE EXCEPTION 'service price version runtime mutability outside contract';
    END IF;

    -- Package definition stable identity and immutable version terms/rules.
    IF has_column_privilege('bridge_school_finance','club_package','stable_key','UPDATE')
       OR has_column_privilege('bridge_school_finance','club_package','school_id','UPDATE')
       OR has_column_privilege('bridge_school_finance','club_package','package_type','UPDATE')
       OR NOT has_column_privilege('bridge_school_finance','club_package','name','UPDATE')
       OR has_table_privilege('bridge_school_finance','club_package_version','UPDATE')
       OR has_column_privilege('bridge_school_finance','club_package_version','terms','UPDATE')
       OR has_column_privilege('bridge_school_finance','club_package_version','effective_from','UPDATE')
       OR NOT has_column_privilege('bridge_school_finance','club_package_version','effective_to','UPDATE')
       OR NOT has_column_privilege('bridge_school_finance','club_package_version','status','UPDATE')
       OR has_table_privilege('bridge_school_finance','package_service_rule','UPDATE')
       OR has_table_privilege('bridge_school_finance','package_service_rule','DELETE') THEN
        RAISE EXCEPTION 'package definition/version runtime mutability outside contract';
    END IF;

    IF has_table_privilege('bridge_school_finance','package_price_version','UPDATE')
       OR has_column_privilege('bridge_school_finance','package_price_version','amount','UPDATE')
       OR has_column_privilege('bridge_school_finance','package_price_version','currency_code','UPDATE')
       OR has_column_privilege('bridge_school_finance','package_price_version','effective_from','UPDATE')
       OR has_column_privilege('bridge_school_finance','package_price_version','conditions','UPDATE')
       OR NOT has_column_privilege('bridge_school_finance','package_price_version','effective_to','UPDATE')
       OR NOT has_column_privilege('bridge_school_finance','package_price_version','status','UPDATE') THEN
        RAISE EXCEPTION 'package price version runtime mutability outside contract';
    END IF;
END $$;

ROLLBACK;
