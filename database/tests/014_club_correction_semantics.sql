\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_service uuid;
    v_entitlement uuid;
    v_usage uuid;
    v_charge uuid;
    v_payment uuid;
    v_allocation uuid;
    v_balance numeric;
    v_unallocated numeric;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name)
    VALUES ('Club Correction Test Person') RETURNING person_id INTO v_person;
    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'correction-service','Correction service','lesson')
    RETURNING service_id INTO v_service;

    -- Historical consumption may be corrected after the entitlement validity window ended.
    INSERT INTO person_entitlement(
        school_id,person_id,service_id,quantity_granted,valid_from,valid_to,status
    ) VALUES (
        v_school,v_person,v_service,2,now()-interval '3 days',now()-interval '1 day','active'
    ) RETURNING entitlement_id INTO v_entitlement;

    INSERT INTO entitlement_usage(entitlement_id,quantity_used,occurred_at,reference_type)
    VALUES (v_entitlement,1,now()-interval '2 days','historical-consumption')
    RETURNING entitlement_usage_id INTO v_usage;

    -- This is intentionally after valid_to. It must remain possible as a correction.
    INSERT INTO entitlement_usage(
        entitlement_id,quantity_used,occurred_at,reference_type,reversal_of_usage_id
    ) VALUES (
        v_entitlement,1,now(),'late-correction',v_usage
    );

    SELECT quantity_remaining INTO v_balance
      FROM person_entitlement_balance WHERE entitlement_id=v_entitlement;
    IF v_balance <> 2 THEN
        RAISE EXCEPTION 'late entitlement reversal did not restore quantity, got %', v_balance;
    END IF;

    -- A fresh use after expiry must still be rejected.
    BEGIN
        INSERT INTO entitlement_usage(entitlement_id,quantity_used,occurred_at,reference_type)
        VALUES (v_entitlement,0.5,now(),'late-fresh-consumption');
        RAISE EXCEPTION 'fresh entitlement use after expiry unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='fresh entitlement use after expiry unexpectedly accepted' THEN RAISE; END IF;
    END;

    -- Allocation correction: reverse the whole immutable allocation and append a corrected one.
    INSERT INTO club_charge(school_id,person_id,service_id,amount,currency_code)
    VALUES (v_school,v_person,v_service,100,'ILS') RETURNING charge_id INTO v_charge;
    INSERT INTO club_payment(school_id,person_id,amount,currency_code,paid_at,payment_method)
    VALUES (v_school,v_person,100,'ILS',now(),'test') RETURNING payment_id INTO v_payment;
    INSERT INTO payment_allocation(school_id,payment_id,charge_id,amount)
    VALUES (v_school,v_payment,v_charge,70) RETURNING payment_allocation_id INTO v_allocation;

    SELECT balance_due INTO v_balance
      FROM person_allocated_receivable_balance
     WHERE school_id=v_school AND person_id=v_person AND currency_code='ILS';
    IF v_balance <> 30 THEN
        RAISE EXCEPTION 'allocated receivable expected 30 before reversal, got %', v_balance;
    END IF;
    SELECT unallocated_amount INTO v_unallocated
      FROM person_unallocated_payment WHERE payment_id=v_payment;
    IF v_unallocated <> 30 THEN
        RAISE EXCEPTION 'unallocated payment expected 30 before reversal, got %', v_unallocated;
    END IF;

    INSERT INTO payment_allocation_reversal(school_id,payment_allocation_id,reason)
    VALUES (v_school,v_allocation,'wrong original allocation');

    SELECT balance_due INTO v_balance
      FROM person_allocated_receivable_balance
     WHERE school_id=v_school AND person_id=v_person AND currency_code='ILS';
    IF v_balance <> 100 THEN
        RAISE EXCEPTION 'allocated receivable expected 100 after reversal, got %', v_balance;
    END IF;
    SELECT unallocated_amount INTO v_unallocated
      FROM person_unallocated_payment WHERE payment_id=v_payment;
    IF v_unallocated <> 100 THEN
        RAISE EXCEPTION 'unallocated payment expected 100 after reversal, got %', v_unallocated;
    END IF;

    -- Correct allocation can now be appended without rewriting history.
    INSERT INTO payment_allocation(school_id,payment_id,charge_id,amount)
    VALUES (v_school,v_payment,v_charge,50);

    SELECT balance_due INTO v_balance
      FROM person_allocated_receivable_balance
     WHERE school_id=v_school AND person_id=v_person AND currency_code='ILS';
    IF v_balance <> 50 THEN
        RAISE EXCEPTION 'allocated receivable expected 50 after corrected allocation, got %', v_balance;
    END IF;
    SELECT unallocated_amount INTO v_unallocated
      FROM person_unallocated_payment WHERE payment_id=v_payment;
    IF v_unallocated <> 50 THEN
        RAISE EXCEPTION 'unallocated payment expected 50 after corrected allocation, got %', v_unallocated;
    END IF;

    -- Account balance uses cash received, not allocation bookkeeping, and remains zero.
    SELECT balance_due INTO v_balance
      FROM person_financial_balance
     WHERE school_id=v_school AND person_id=v_person AND currency_code='ILS';
    IF v_balance <> 0 THEN
        RAISE EXCEPTION 'person financial balance expected 0, got %', v_balance;
    END IF;

    BEGIN
        INSERT INTO payment_allocation_reversal(school_id,payment_allocation_id,reason)
        VALUES (v_school,v_allocation,'duplicate reversal');
        RAISE EXCEPTION 'duplicate allocation reversal unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='duplicate allocation reversal unexpectedly accepted' THEN RAISE; END IF;
    END;
END $$;

DO $$
BEGIN
    IF NOT has_table_privilege('bridge_school_finance','payment_allocation_reversal','INSERT')
       OR has_table_privilege('bridge_school_finance','payment_allocation_reversal','UPDATE')
       OR has_table_privilege('bridge_school_finance','payment_allocation_reversal','DELETE')
       OR has_table_privilege('bridge_school_app','payment_allocation_reversal','INSERT') THEN
        RAISE EXCEPTION 'allocation reversal permissions outside contract';
    END IF;

    IF has_function_privilege('bridge_school_finance_principal','validate_payment_allocation_reversal()','EXECUTE')
       OR has_function_privilege('bridge_school_app_principal','validate_entitlement_usage_integrity()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime principal can execute correction helper directly';
    END IF;
END $$;

ROLLBACK;
