\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path=identity_staging,public;

DO $$
DECLARE
    v_school uuid;
    v_source uuid;
    v_batch uuid;
    v_item uuid;
    v_identity uuid;
    v_item2 uuid;
    v_state text;
    v_ready boolean;
BEGIN
    SELECT school_id INTO v_school FROM public.school WHERE stable_name='Школа спортивного бриджа';
    INSERT INTO public.source(school_id,source_type,title)
    VALUES (v_school,'manual_import','Identity readiness hardening source') RETURNING source_id INTO v_source;
    INSERT INTO identity_import_batch(school_id,source_id,external_batch_key)
    VALUES (v_school,v_source,'ready-hardening-batch') RETURNING identity_import_batch_id INTO v_batch;
    INSERT INTO public.source_identity(source_id,source_native_key,display_name)
    VALUES (v_source,'ready-source-id','Ready Candidate') RETURNING source_identity_id INTO v_identity;

    INSERT INTO identity_import_item(identity_import_batch_id,source_record_key,raw_payload,source_identity_id)
    VALUES (v_batch,'ready-item','{"name":"Ready Candidate"}'::jsonb,v_identity)
    RETURNING identity_import_item_id INTO v_item;

    BEGIN
        INSERT INTO identity_import_item(identity_import_batch_id,source_record_key,raw_payload,source_identity_id)
        VALUES (v_batch,'duplicate-identity','{}'::jsonb,v_identity)
        RETURNING identity_import_item_id INTO v_item2;
        RAISE EXCEPTION 'duplicate source identity unexpectedly accepted in one batch';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;

    BEGIN
        INSERT INTO identity_import_item_state_event(identity_import_item_id,state,reason)
        VALUES (v_item,'ready','must fail without reconciliation action');
        RAISE EXCEPTION 'ready state unexpectedly accepted without a current action';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='ready state unexpectedly accepted without a current action' THEN RAISE; END IF;
    END;

    INSERT INTO identity_import_action(identity_import_item_id,action_type,reason)
    VALUES (v_item,'defer','still unresolved');
    BEGIN
        INSERT INTO identity_import_item_state_event(identity_import_item_id,state,reason)
        VALUES (v_item,'ready','must fail while deferred');
        RAISE EXCEPTION 'ready state unexpectedly accepted for deferred item';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='ready state unexpectedly accepted for deferred item' THEN RAISE; END IF;
    END;

    INSERT INTO identity_import_action(identity_import_item_id,action_type,reason)
    VALUES (v_item,'create_new_person','explicit reviewed intent only');
    INSERT INTO identity_import_item_state_event(identity_import_item_id,state,reason)
    VALUES (v_item,'ready','eligible for a future separately controlled apply');

    SELECT state INTO v_state FROM identity_import_item_current_state
     WHERE identity_import_item_id=v_item;
    IF v_state<>'ready' THEN
        RAISE EXCEPTION 'item did not reach ready state after explicit reviewed intent';
    END IF;

    INSERT INTO identity_import_batch_state_event(identity_import_batch_id,state,reason)
    VALUES (v_batch,'ready','all items currently safe for future apply');
    SELECT eligible_for_future_apply INTO v_ready
      FROM identity_import_batch_future_apply_readiness
     WHERE identity_import_batch_id=v_batch;
    IF v_ready IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'batch readiness projection did not report eligible future apply';
    END IF;

    -- A later append-only review can invalidate readiness without rewriting history.
    INSERT INTO identity_import_action(identity_import_item_id,action_type,reason)
    VALUES (v_item,'defer','new evidence requires review');
    SELECT eligible_for_future_apply INTO v_ready
      FROM identity_import_batch_future_apply_readiness
     WHERE identity_import_batch_id=v_batch;
    IF v_ready IS DISTINCT FROM false THEN
        RAISE EXCEPTION 'dynamic readiness failed closed after later defer action';
    END IF;
END $$;

DO $$
DECLARE role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'bridge_school_reader','bridge_school_app','bridge_school_worker',
        'bridge_school_health','bridge_school_finance','bridge_school_member',
        'bridge_school_member_principal','bridge_school_auth_gateway'
    ] LOOP
        IF has_schema_privilege(role_name,'identity_staging','USAGE')
           OR has_table_privilege(role_name,'identity_staging.identity_import_item','SELECT')
           OR has_table_privilege(role_name,'identity_staging.identity_import_item_future_apply_readiness','SELECT') THEN
            RAISE EXCEPTION 'runtime role % can access identity_staging',role_name;
        END IF;
    END LOOP;
END $$;

ROLLBACK;
