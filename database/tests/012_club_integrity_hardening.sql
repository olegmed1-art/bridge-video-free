\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_service uuid;
    v_entitlement1 uuid;
    v_entitlement2 uuid;
    v_usage1 uuid;
    v_usage2 uuid;
    v_charge uuid;
    v_payment uuid;
    v_adjustment uuid;
    v_balance numeric;
    v_allocated_balance numeric;
    v_contact uuid;
    v_communication uuid;
    v_message uuid;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Club Integrity Test Person') RETURNING person_id INTO v_person;
    INSERT INTO club_service(school_id,stable_key,name,service_type)
    VALUES (v_school,'integrity-service','Integrity service','lesson')
    RETURNING service_id INTO v_service;

    -- Entitlement usage must be bounded and reversals must remain inside one entitlement.
    INSERT INTO person_entitlement(school_id,person_id,service_id,quantity_granted)
    VALUES (v_school,v_person,v_service,2) RETURNING entitlement_id INTO v_entitlement1;
    INSERT INTO person_entitlement(school_id,person_id,service_id,quantity_granted)
    VALUES (v_school,v_person,v_service,1) RETURNING entitlement_id INTO v_entitlement2;

    INSERT INTO entitlement_usage(entitlement_id,quantity_used,reference_type)
    VALUES (v_entitlement1,1,'test') RETURNING entitlement_usage_id INTO v_usage1;

    BEGIN
        INSERT INTO entitlement_usage(entitlement_id,quantity_used,reference_type,reversal_of_usage_id)
        VALUES (v_entitlement2,1,'bad-cross-entitlement-reversal',v_usage1);
        RAISE EXCEPTION 'cross-entitlement reversal unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='cross-entitlement reversal unexpectedly accepted' THEN RAISE; END IF;
    END;

    BEGIN
        INSERT INTO entitlement_usage(entitlement_id,quantity_used,reference_type)
        VALUES (v_entitlement2,2,'overuse');
        RAISE EXCEPTION 'entitlement overuse unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='entitlement overuse unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO entitlement_usage(entitlement_id,quantity_used,reference_type)
    VALUES (v_entitlement2,1,'consume') RETURNING entitlement_usage_id INTO v_usage2;

    BEGIN
        INSERT INTO entitlement_usage(entitlement_id,quantity_used,reference_type)
        VALUES (v_entitlement2,0.001,'overuse-after-full-consumption');
        RAISE EXCEPTION 'post-consumption overuse unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='post-consumption overuse unexpectedly accepted' THEN RAISE; END IF;
    END;

    BEGIN
        INSERT INTO entitlement_usage(entitlement_id,quantity_used,reference_type,reversal_of_usage_id)
        VALUES (v_entitlement2,0.5,'wrong-size-reversal',v_usage2);
        RAISE EXCEPTION 'partial entitlement reversal unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='partial entitlement reversal unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO entitlement_usage(entitlement_id,quantity_used,reference_type,reversal_of_usage_id)
    VALUES (v_entitlement2,1,'valid-reversal',v_usage2);

    SELECT quantity_remaining INTO v_balance
      FROM person_entitlement_balance WHERE entitlement_id=v_entitlement2;
    IF v_balance <> 1 THEN
        RAISE EXCEPTION 'entitlement balance expected 1 after exact reversal, got %', v_balance;
    END IF;

    -- Account balance must recognize received but not-yet-allocated cash.
    INSERT INTO club_charge(school_id,person_id,service_id,amount,currency_code)
    VALUES (v_school,v_person,v_service,100,'ILS') RETURNING charge_id INTO v_charge;
    INSERT INTO club_payment(school_id,person_id,amount,currency_code,paid_at,payment_method)
    VALUES (v_school,v_person,30,'ILS',now(),'test') RETURNING payment_id INTO v_payment;
    INSERT INTO financial_adjustment(school_id,person_id,currency_code,balance_delta,adjustment_type,related_charge_id,reason)
    VALUES (v_school,v_person,'ILS',-5,'discount',v_charge,'integrity test')
    RETURNING adjustment_id INTO v_adjustment;

    SELECT balance_due INTO v_balance FROM person_financial_balance
     WHERE school_id=v_school AND person_id=v_person AND currency_code='ILS';
    IF v_balance <> 65 THEN
        RAISE EXCEPTION 'account balance expected 65 with unallocated payment, got %', v_balance;
    END IF;

    SELECT balance_due INTO v_allocated_balance FROM person_allocated_receivable_balance
     WHERE school_id=v_school AND person_id=v_person AND currency_code='ILS';
    IF v_allocated_balance <> 95 THEN
        RAISE EXCEPTION 'allocated receivable balance expected 95, got %', v_allocated_balance;
    END IF;

    BEGIN
        INSERT INTO financial_adjustment(
            school_id,person_id,currency_code,balance_delta,adjustment_type,
            related_charge_id,reason,reversal_of_adjustment_id
        ) VALUES (
            v_school,v_person,'ILS',4,'reversal',v_charge,'bad reversal',v_adjustment
        );
        RAISE EXCEPTION 'non-exact financial reversal unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='non-exact financial reversal unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO financial_adjustment(
        school_id,person_id,currency_code,balance_delta,adjustment_type,
        related_charge_id,reason,reversal_of_adjustment_id
    ) VALUES (
        v_school,v_person,'ILS',5,'reversal',v_charge,'valid reversal',v_adjustment
    );

    SELECT balance_due INTO v_balance FROM person_financial_balance
     WHERE school_id=v_school AND person_id=v_person AND currency_code='ILS';
    IF v_balance <> 70 THEN
        RAISE EXCEPTION 'account balance expected 70 after exact adjustment reversal, got %', v_balance;
    END IF;

    -- Delivery must use a compatible contact method, coherent timestamps and explicit
    -- communication denials must be respected.
    INSERT INTO contact_method(school_id,person_id,channel,normalized_value,verification_status)
    VALUES (v_school,v_person,'email','integrity@example.invalid','verified')
    RETURNING contact_method_id INTO v_contact;

    INSERT INTO club_communication(school_id,communication_type,subject,primary_person_id)
    VALUES (v_school,'service','Integrity delivery test',v_person)
    RETURNING communication_id INTO v_communication;
    INSERT INTO club_message(school_id,communication_id,recipient_person_id,author_actor_type,body_text)
    VALUES (v_school,v_communication,v_person,'system','Integrity test message')
    RETURNING message_id INTO v_message;

    INSERT INTO message_delivery(school_id,message_id,recipient_person_id,contact_method_id,channel,status)
    VALUES (v_school,v_message,v_person,v_contact,'email','queued');

    BEGIN
        INSERT INTO message_delivery(school_id,message_id,recipient_person_id,contact_method_id,channel,status,attempt_no)
        VALUES (v_school,v_message,v_person,v_contact,'whatsapp','queued',2);
        RAISE EXCEPTION 'delivery channel mismatch unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='delivery channel mismatch unexpectedly accepted' THEN RAISE; END IF;
    END;

    BEGIN
        INSERT INTO message_delivery(
            school_id,message_id,recipient_person_id,contact_method_id,channel,status,
            delivered_at,attempt_no
        ) VALUES (
            v_school,v_message,v_person,v_contact,'email','delivered',now(),2
        );
        RAISE EXCEPTION 'delivery without sent timestamp unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='delivery without sent timestamp unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO contact_preference(school_id,person_id,channel,communication_type,permission_state)
    VALUES (v_school,v_person,'email','service','denied');

    BEGIN
        INSERT INTO message_delivery(school_id,message_id,recipient_person_id,contact_method_id,channel,status,attempt_no)
        VALUES (v_school,v_message,v_person,v_contact,'email','queued',2);
        RAISE EXCEPTION 'explicitly denied delivery unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='explicitly denied delivery unexpectedly accepted' THEN RAISE; END IF;
    END;
END $$;

DO $$
BEGIN
    IF has_table_privilege('bridge_school_app','person_entitlement','INSERT')
       OR has_table_privilege('bridge_school_worker','person_entitlement','INSERT')
       OR NOT has_table_privilege('bridge_school_finance','person_entitlement','INSERT')
       OR NOT has_column_privilege('bridge_school_finance','person_entitlement','valid_to','UPDATE')
       OR NOT has_table_privilege('bridge_school_app','entitlement_usage','INSERT') THEN
        RAISE EXCEPTION 'entitlement runtime permissions outside hardened contract';
    END IF;

    IF NOT has_table_privilege('bridge_school_finance','person_financial_balance','SELECT')
       OR NOT has_table_privilege('bridge_school_finance','person_allocated_receivable_balance','SELECT') THEN
        RAISE EXCEPTION 'finance balance-view permissions outside hardened contract';
    END IF;

    IF has_function_privilege('bridge_school_app_principal','validate_entitlement_usage_integrity()','EXECUTE')
       OR has_function_privilege('bridge_school_worker_principal','validate_message_delivery_integrity()','EXECUTE')
       OR has_function_privilege('bridge_school_finance_principal','validate_financial_adjustment_reversal_integrity()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime principal can execute Club Operations integrity trigger helper directly';
    END IF;
END $$;

ROLLBACK;
