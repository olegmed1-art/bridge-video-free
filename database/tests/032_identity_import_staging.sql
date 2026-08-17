\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_other_school uuid;
    v_source uuid;
    v_other_source uuid;
    v_identity uuid;
    v_other_identity uuid;
    v_batch uuid;
    v_item_link uuid;
    v_item_new uuid;
    v_item_other uuid;
    v_target uuid;
    v_other_target uuid;
    v_resolution uuid;
    v_action uuid;
    v_other_action uuid;
    v_count_before bigint;
    v_count_after bigint;
    v_count bigint;
    v_text text;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO school(stable_name) VALUES ('Import staging other school')
    RETURNING school_id INTO v_other_school;
    INSERT INTO source(school_id,source_type,title)
    VALUES (v_school,'manual_import','Import staging source') RETURNING source_id INTO v_source;
    INSERT INTO source(school_id,source_type,title)
    VALUES (v_other_school,'manual_import','Other import staging source') RETURNING source_id INTO v_other_source;

    BEGIN
        INSERT INTO identity_import_batch(school_id,source_id,external_batch_key)
        VALUES (v_school,v_other_source,'bad-cross-school');
        RAISE EXCEPTION 'cross-school import batch unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='cross-school import batch unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO identity_import_batch(
        school_id,source_id,external_batch_key,import_label,provenance
    ) VALUES (
        v_school,v_source,'batch-001','CI protected import','{"kind":"test"}'::jsonb
    ) RETURNING identity_import_batch_id INTO v_batch;

    SELECT count(*) INTO v_count
      FROM identity_import_batch_state_event
     WHERE identity_import_batch_id=v_batch AND state='staged';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'batch insert did not seed exactly one staged event';
    END IF;

    INSERT INTO source_identity(source_id,source_native_key,display_name)
    VALUES (v_source,'source-person-1','Import Existing Person')
    RETURNING source_identity_id INTO v_identity;
    INSERT INTO source_identity(source_id,source_native_key,display_name)
    VALUES (v_other_source,'other-source-person','Other source identity')
    RETURNING source_identity_id INTO v_other_identity;

    INSERT INTO identity_import_item(
        identity_import_batch_id,source_record_key,raw_payload,raw_payload_hash,
        normalized_candidate,source_identity_id
    ) VALUES (
        v_batch,'record-link','{"name":"Import Existing Person","email":"pii@example.invalid"}'::jsonb,
        'hash-link','{"preferred_name":"Import Existing Person"}'::jsonb,v_identity
    ) RETURNING identity_import_item_id INTO v_item_link;

    INSERT INTO identity_import_item(
        identity_import_batch_id,source_record_key,raw_payload,raw_payload_hash,normalized_candidate
    ) VALUES (
        v_batch,'record-new','{"name":"Potential New Person"}'::jsonb,
        'hash-new','{"preferred_name":"Potential New Person"}'::jsonb
    ) RETURNING identity_import_item_id INTO v_item_new;

    INSERT INTO identity_import_item(
        identity_import_batch_id,source_record_key,raw_payload,raw_payload_hash,normalized_candidate
    ) VALUES (
        v_batch,'record-other','{"name":"Other Record"}'::jsonb,
        'hash-other','{}'::jsonb
    ) RETURNING identity_import_item_id INTO v_item_other;

    SELECT count(*) INTO v_count
      FROM identity_import_item_state_event
     WHERE identity_import_item_id IN (v_item_link,v_item_new,v_item_other)
       AND state='staged';
    IF v_count <> 3 THEN
        RAISE EXCEPTION 'item inserts did not seed staged state events';
    END IF;

    BEGIN
        INSERT INTO identity_import_item(
            identity_import_batch_id,source_record_key,raw_payload,raw_payload_hash
        ) VALUES (v_batch,'record-new','{}'::jsonb,'duplicate-key');
        RAISE EXCEPTION 'duplicate source record key unexpectedly accepted';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;

    BEGIN
        INSERT INTO identity_import_item(
            identity_import_batch_id,source_record_key,raw_payload,raw_payload_hash,source_identity_id
        ) VALUES (v_batch,'wrong-source-identity','{}'::jsonb,'wrong-source-hash',v_other_identity);
        RAISE EXCEPTION 'cross-source source identity unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='cross-source source identity unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO person(preferred_name) VALUES ('Existing Import Target') RETURNING person_id INTO v_target;
    INSERT INTO person(preferred_name) VALUES ('Wrong Import Target') RETURNING person_id INTO v_other_target;
    INSERT INTO entity_resolution_decision(
        source_identity_id,target_person_id,decision_type,confidence_class,status
    ) VALUES (
        v_identity,v_target,'link','HIGH','active'
    ) RETURNING resolution_id INTO v_resolution;

    BEGIN
        INSERT INTO identity_import_action(
            identity_import_item_id,action_type,target_person_id,entity_resolution_decision_id,reason
        ) VALUES (
            v_item_link,'link_existing_person',v_other_target,v_resolution,'mismatched target'
        );
        RAISE EXCEPTION 'mismatched link-existing action unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='mismatched link-existing action unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO identity_import_action(
        identity_import_item_id,action_type,target_person_id,entity_resolution_decision_id,reason
    ) VALUES (
        v_item_link,'link_existing_person',v_target,v_resolution,'reviewed canonical link'
    ) RETURNING identity_import_action_id INTO v_action;

    SELECT count(*) INTO v_count_before FROM person;
    INSERT INTO identity_import_action(identity_import_item_id,action_type,reason)
    VALUES (v_item_new,'create_new_person','reviewed intent only');
    SELECT count(*) INTO v_count_after FROM person;
    IF v_count_after <> v_count_before THEN
        RAISE EXCEPTION 'create-new staging action created a Person before controlled apply';
    END IF;

    INSERT INTO identity_import_action(identity_import_item_id,action_type,reason)
    VALUES (v_item_other,'defer','needs human reconciliation')
    RETURNING identity_import_action_id INTO v_other_action;

    BEGIN
        INSERT INTO identity_import_action(
            identity_import_item_id,action_type,supersedes_action_id,reason
        ) VALUES (
            v_item_new,'defer',v_other_action,'bad cross-item supersede'
        );
        RAISE EXCEPTION 'cross-item action supersede unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='cross-item action supersede unexpectedly accepted' THEN RAISE; END IF;
    END;

    INSERT INTO identity_import_action(
        identity_import_item_id,action_type,supersedes_action_id,reason
    ) VALUES (
        v_item_link,'defer',v_action,'later review requests more evidence'
    );

    SELECT action_type INTO v_text
      FROM identity_import_current_action
     WHERE identity_import_item_id=v_item_link;
    IF v_text <> 'defer' THEN
        RAISE EXCEPTION 'current import action did not select latest append-only action';
    END IF;

    INSERT INTO identity_import_item_state_event(identity_import_item_id,state,reason)
    VALUES (v_item_link,'needs_review','more evidence requested');
    SELECT state INTO v_text
      FROM identity_import_item_current_state
     WHERE identity_import_item_id=v_item_link;
    IF v_text <> 'needs_review' THEN
        RAISE EXCEPTION 'current import item state did not select latest event';
    END IF;

    BEGIN
        UPDATE identity_import_item
           SET normalized_candidate='{"tampered":true}'::jsonb
         WHERE identity_import_item_id=v_item_link;
        RAISE EXCEPTION 'identity import raw item unexpectedly mutable';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='identity import raw item unexpectedly mutable' THEN RAISE; END IF;
    END;

    BEGIN
        DELETE FROM identity_import_action WHERE identity_import_action_id=v_action;
        RAISE EXCEPTION 'identity import action unexpectedly deletable';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='identity import action unexpectedly deletable' THEN RAISE; END IF;
    END;

    SELECT item_count INTO v_count
      FROM identity_import_batch_summary
     WHERE identity_import_batch_id=v_batch;
    IF v_count <> 3 THEN
        RAISE EXCEPTION 'import batch summary expected three items, got %',v_count;
    END IF;
END $$;

DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'bridge_school_reader','bridge_school_app','bridge_school_worker',
        'bridge_school_health','bridge_school_finance','bridge_school_member',
        'bridge_school_member_principal','bridge_school_auth_gateway'
    ] LOOP
        IF has_table_privilege(role_name,'identity_import_item','SELECT')
           OR has_table_privilege(role_name,'identity_import_item','INSERT')
           OR has_table_privilege(role_name,'identity_import_action','SELECT')
           OR has_table_privilege(role_name,'identity_import_action','INSERT')
           OR has_table_privilege(role_name,'identity_import_batch_summary','SELECT') THEN
            RAISE EXCEPTION 'runtime role % can access protected identity import staging',role_name;
        END IF;
    END LOOP;

    IF has_function_privilege('bridge_school_app_principal','validate_identity_import_action()','EXECUTE')
       OR has_function_privilege('bridge_school_member_principal','reject_identity_import_mutation()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime can directly execute identity import trigger helper';
    END IF;
END $$;

ROLLBACK;
