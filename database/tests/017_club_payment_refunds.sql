\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_service uuid;
    v_charge uuid;
    v_payment uuid;
    v_allocation uuid;
    v_refund uuid;
    v_balance numeric;
    v_unallocated numeric;
    v_refunded numeric;
    v_net numeric;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Payment Refund Test Person') RETURNING person_id INTO v_person;
    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'refund-test-service','Refund test service','lesson')
    RETURNING service_id INTO v_service;

    INSERT INTO club_charge(school_id,person_id,service_id,amount,currency_code)
    VALUES (v_school,v_person,v_service,100,'ILS') RETURNING charge_id INTO v_charge;
    INSERT INTO club_payment(school_id,person_id,amount,currency_code,paid_at,payment_method)
    VALUES (v_school,v_person,100,'ILS',now(),'test') RETURNING payment_id INTO v_payment;
    INSERT INTO payment_allocation(school_id,payment_id,charge_id,amount)
    VALUES (v_school,v_payment,v_charge,70) RETURNING payment_allocation_id INTO v_allocation;

    -- Only the 30 unallocated cash may be refunded while 70 remains effectively allocated.
    BEGIN
        INSERT INTO club_payment_refund(school_id,payment_id,amount,reason)
        VALUES (v_school,v_payment,40,'too much');
        RAISE EXCEPTION 'refund beyond unallocated cash unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='refund beyond unallocated cash unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO club_payment_refund(school_id,payment_id,amount,reason)
    VALUES (v_school,v_payment,30,'valid refund') RETURNING payment_refund_id INTO v_refund;

    SELECT refunded_amount, net_amount
      INTO v_refunded, v_net
      FROM club_payment_net WHERE payment_id=v_payment;
    IF v_refunded <> 30 OR v_net <> 70 THEN
        RAISE EXCEPTION 'net payment after refund expected refunded=30 net=70, got %, %', v_refunded, v_net;
    END IF;

    SELECT unallocated_amount INTO v_unallocated
      FROM person_unallocated_payment WHERE payment_id=v_payment;
    IF v_unallocated <> 0 THEN
        RAISE EXCEPTION 'unallocated payment expected 0 after refund, got %', v_unallocated;
    END IF;

    SELECT balance_due INTO v_balance
      FROM person_financial_balance
     WHERE school_id=v_school AND person_id=v_person AND currency_code='ILS';
    IF v_balance <> 30 THEN
        RAISE EXCEPTION 'member account balance expected 30 after cash refund, got %', v_balance;
    END IF;

    -- No additional allocation fits until refund is reversed or an allocation is reversed.
    BEGIN
        INSERT INTO payment_allocation(school_id,payment_id,charge_id,amount)
        VALUES (v_school,v_payment,v_charge,1);
        RAISE EXCEPTION 'allocation above net payment after refund unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='allocation above net payment after refund unexpectedly accepted' THEN RAISE; END IF;
    END;

    -- Reversal restores cash capacity but does not rewrite the original refund row.
    INSERT INTO club_payment_refund(
        school_id,payment_id,amount,reason,reversal_of_refund_id
    ) VALUES (
        v_school,v_payment,30,'refund returned/reversed',v_refund
    );

    SELECT refunded_amount, net_amount
      INTO v_refunded, v_net
      FROM club_payment_net WHERE payment_id=v_payment;
    IF v_refunded <> 0 OR v_net <> 100 THEN
        RAISE EXCEPTION 'net payment after refund reversal expected refunded=0 net=100, got %, %', v_refunded, v_net;
    END IF;

    SELECT unallocated_amount INTO v_unallocated
      FROM person_unallocated_payment WHERE payment_id=v_payment;
    IF v_unallocated <> 30 THEN
        RAISE EXCEPTION 'unallocated payment expected 30 after refund reversal, got %', v_unallocated;
    END IF;

    SELECT balance_due INTO v_balance
      FROM person_financial_balance
     WHERE school_id=v_school AND person_id=v_person AND currency_code='ILS';
    IF v_balance <> 0 THEN
        RAISE EXCEPTION 'member account balance expected 0 after refund reversal, got %', v_balance;
    END IF;

    BEGIN
        INSERT INTO club_payment_refund(
            school_id,payment_id,amount,reason,reversal_of_refund_id
        ) VALUES (
            v_school,v_payment,30,'duplicate reversal',v_refund
        );
        RAISE EXCEPTION 'duplicate refund reversal unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='duplicate refund reversal unexpectedly accepted' THEN RAISE; END IF;
    END;

    BEGIN
        INSERT INTO club_payment_refund(
            school_id,payment_id,amount,reason,reversal_of_refund_id
        ) VALUES (
            v_school,v_payment,20,'wrong reversal amount',v_refund
        );
        RAISE EXCEPTION 'wrong refund reversal amount unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='wrong refund reversal amount unexpectedly accepted' THEN RAISE; END IF;
    END;

    -- Reverse the allocation, refund 60, then corrected allocation 40 is allowed.
    INSERT INTO payment_allocation_reversal(school_id,payment_allocation_id,reason)
    VALUES (v_school,v_allocation,'prepare partial cash refund');
    INSERT INTO club_payment_refund(school_id,payment_id,amount,reason)
    VALUES (v_school,v_payment,60,'partial cash refund');
    INSERT INTO payment_allocation(school_id,payment_id,charge_id,amount)
    VALUES (v_school,v_payment,v_charge,40);

    SELECT unallocated_amount INTO v_unallocated
      FROM person_unallocated_payment WHERE payment_id=v_payment;
    IF v_unallocated <> 0 THEN
        RAISE EXCEPTION 'unallocated payment expected 0 after corrected allocation/refund, got %', v_unallocated;
    END IF;

    SELECT balance_due INTO v_balance
      FROM person_financial_balance
     WHERE school_id=v_school AND person_id=v_person AND currency_code='ILS';
    IF v_balance <> 60 THEN
        RAISE EXCEPTION 'member account balance expected 60 after net cash refund, got %', v_balance;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT has_table_privilege('bridge_school_finance','club_payment_refund','INSERT')
       OR has_table_privilege('bridge_school_finance','club_payment_refund','UPDATE')
       OR has_table_privilege('bridge_school_finance','club_payment_refund','DELETE')
       OR has_table_privilege('bridge_school_app','club_payment_refund','INSERT') THEN
        RAISE EXCEPTION 'payment refund permissions outside contract';
    END IF;

    IF has_function_privilege('bridge_school_finance_principal','validate_payment_refund_integrity()','EXECUTE')
       OR has_function_privilege('bridge_school_finance_principal','validate_payment_allocation_net_capacity()','EXECUTE')
       OR has_function_privilege('bridge_school_finance_principal','validate_accounting_document_refund_scope()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime principal can execute payment refund helper directly';
    END IF;
END $$;

ROLLBACK;
