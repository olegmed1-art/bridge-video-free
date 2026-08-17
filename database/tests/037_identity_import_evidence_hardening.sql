\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_source uuid;
    v_batch uuid;
    v_item uuid;
    v_created_at timestamptz;
    v_hash text;
    v_expected text;
    v_state text;
    v_action text;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO source(school_id,source_type,title)
    VALUES (v_school,'manual_import','Import evidence hardening source')
    RETURNING source_id INTO v_source;

    INSERT INTO identity_import_batch(school_id,source_id,external_batch_key)
    VALUES (v_school,v_source,'evidence-hardening-batch')
    RETURNING identity_import_batch_id INTO v_batch;

    -- A caller may supply a source hash, but may not control the database evidence hash.
    INSERT INTO identity_import_item(
        identity_import_batch_id,source_record_key,raw_payload,source_payload_hash,
        raw_payload_sha256,normalized_candidate
    ) VALUES (
        v_batch,'evidence-item','{"name":"Evidence Person","email":"evidence@example.invalid"}'::jsonb,
        'source-claims-this-hash',repeat('0',64),'{}'::jsonb
    ) RETURNING identity_import_item_id,created_at INTO v_item,v_created_at;

    SELECT raw_payload_sha256,
           encode(digest(raw_payload::text,'sha256'),'hex')
      INTO v_hash,v_expected
      FROM identity_import_item
     WHERE identity_import_item_id=v_item;
    IF v_hash <> v_expected OR v_hash=repeat('0',64) OR length(v_hash)<>64 THEN
        RAISE EXCEPTION 'database evidence hash was not recomputed from raw payload';
    END IF;

    -- Source/importer hash is optional and separate from the trusted database hash.
    INSERT INTO identity_import_item(
        identity_import_batch_id,source_record_key,raw_payload,normalized_candidate
    ) VALUES (
        v_batch,'no-source-hash','{"name":"No Source Hash"}'::jsonb,'{}'::jsonb
    );

    -- Current state is insertion-sequence based, not caller timestamp based.
    INSERT INTO identity_import_item_state_event(
        identity_import_item_id,state,occurred_at,reason
    ) VALUES (
        v_item,'validated',v_created_at+interval '20 seconds','inserted first with later timestamp'
    );
    INSERT INTO identity_import_item_state_event(
        identity_import_item_id,state,occurred_at,reason
    ) VALUES (
        v_item,'needs_review',v_created_at+interval '10 seconds','inserted second with earlier timestamp'
    );
    SELECT state INTO v_state
      FROM identity_import_item_current_state
     WHERE identity_import_item_id=v_item;
    IF v_state <> 'needs_review' THEN
        RAISE EXCEPTION 'current import state followed timestamp instead of immutable sequence';
    END IF;

    -- Same rule for reconciliation intent: later append supersedes timestamp ordering.
    INSERT INTO identity_import_action(identity_import_item_id,action_type,decided_at,reason)
    VALUES (v_item,'defer',v_created_at+interval '20 seconds','inserted first');
    INSERT INTO identity_import_action(identity_import_item_id,action_type,decided_at,reason)
    VALUES (v_item,'reject',v_created_at+interval '10 seconds','inserted second');
    SELECT action_type INTO v_action
      FROM identity_import_current_action
     WHERE identity_import_item_id=v_item;
    IF v_action <> 'reject' THEN
        RAISE EXCEPTION 'current import action followed timestamp instead of immutable sequence';
    END IF;

    -- Staging cannot claim application because there is no apply operation in this layer.
    BEGIN
        INSERT INTO identity_import_item_state_event(identity_import_item_id,state,reason)
        VALUES (v_item,'applied','must be rejected');
        RAISE EXCEPTION 'identity import staging unexpectedly accepted applied item state';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    BEGIN
        INSERT INTO identity_import_batch_state_event(identity_import_batch_id,state,reason)
        VALUES (v_batch,'applied','must be rejected');
        RAISE EXCEPTION 'identity import staging unexpectedly accepted applied batch state';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
END $$;

DO $$
BEGIN
    IF has_sequence_privilege('bridge_school_reader','identity_import_action_sequence_seq','USAGE')
       OR has_sequence_privilege('bridge_school_member_principal','identity_import_action_sequence_seq','USAGE') THEN
        RAISE EXCEPTION 'identity import action sequence leaked to runtime';
    END IF;

    IF has_function_privilege('bridge_school_app_principal','compute_identity_import_item_hash()','EXECUTE')
       OR has_function_privilege('bridge_school_member_principal','compute_identity_import_item_hash()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime can directly execute identity import evidence hash helper';
    END IF;
END $$;

ROLLBACK;
