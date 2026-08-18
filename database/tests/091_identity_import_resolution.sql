\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path=identity_staging,public;

DO $$
DECLARE
  v_school uuid; v_source uuid; v_identity uuid; v_batch uuid; v_item uuid;
  v_target uuid; v_wrong uuid; v_resolution uuid; v_action text; v_created timestamptz;
  v_before bigint; v_after bigint;
BEGIN
  SELECT school_id INTO v_school FROM public.school WHERE stable_name='Школа спортивного бриджа';
  INSERT INTO public.source(school_id,source_type,title) VALUES (v_school,'manual_import','Identity resolution CI') RETURNING source_id INTO v_source;
  INSERT INTO identity_import_batch(school_id,source_id,external_batch_key) VALUES (v_school,v_source,'batch-091') RETURNING identity_import_batch_id INTO v_batch;
  INSERT INTO public.source_identity(source_id,source_native_key,display_name)
  VALUES (v_source,'source-person-091','Existing Identity') RETURNING source_identity_id INTO v_identity;
  INSERT INTO identity_import_item(identity_import_batch_id,source_record_key,raw_payload,source_identity_id)
  VALUES (v_batch,'link-item','{"name":"Existing Identity"}'::jsonb,v_identity)
  RETURNING identity_import_item_id,created_at INTO v_item,v_created;

  BEGIN
    INSERT INTO identity_import_item_state_event(identity_import_item_id,state,occurred_at)
    VALUES (v_item,'validated',v_created-interval '1 second');
    RAISE EXCEPTION 'backdated item state unexpectedly accepted';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM='backdated item state unexpectedly accepted' THEN RAISE; END IF;
  END;

  INSERT INTO public.person(preferred_name) VALUES ('Identity Target') RETURNING person_id INTO v_target;
  INSERT INTO public.person(preferred_name) VALUES ('Wrong Target') RETURNING person_id INTO v_wrong;
  INSERT INTO public.entity_resolution_decision(source_identity_id,target_person_id,decision_type,confidence_class,status)
  VALUES (v_identity,v_target,'link','HIGH','active') RETURNING resolution_id INTO v_resolution;

  BEGIN
    INSERT INTO identity_import_action(identity_import_item_id,action_type,target_person_id,entity_resolution_decision_id)
    VALUES (v_item,'link_existing_person',v_wrong,v_resolution);
    RAISE EXCEPTION 'mismatched canonical link unexpectedly accepted';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM='mismatched canonical link unexpectedly accepted' THEN RAISE; END IF;
  END;

  INSERT INTO identity_import_action(identity_import_item_id,action_type,target_person_id,entity_resolution_decision_id,reason)
  VALUES (v_item,'link_existing_person',v_target,v_resolution,'reviewed canonical link');
  INSERT INTO identity_import_action(identity_import_item_id,action_type,reason)
  VALUES (v_item,'defer','later review');
  SELECT action_type INTO v_action FROM identity_import_current_action WHERE identity_import_item_id=v_item;
  IF v_action<>'defer' THEN RAISE EXCEPTION 'current action did not follow append sequence'; END IF;

  SELECT count(*) INTO v_before FROM public.person;
  INSERT INTO identity_import_action(identity_import_item_id,action_type,reason)
  VALUES (v_item,'create_new_person','intent only');
  SELECT count(*) INTO v_after FROM public.person;
  IF v_after<>v_before THEN RAISE EXCEPTION 'staging create-new intent created a real Person'; END IF;

  BEGIN
    INSERT INTO identity_import_item_state_event(identity_import_item_id,state,reason)
    VALUES (v_item,'applied','no apply operation exists');
    RAISE EXCEPTION 'staging unexpectedly accepted applied state';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;
ROLLBACK;
