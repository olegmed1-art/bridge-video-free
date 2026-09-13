\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path=identity_staging,public;

DO $$
DECLARE
  v_school uuid; v_other_school uuid; v_source uuid; v_other_source uuid;
  v_batch uuid; v_item uuid; v_hash text; v_expected text; v_count bigint;
BEGIN
  SELECT school_id INTO v_school FROM public.school WHERE stable_name='Школа спортивного бриджа';
  INSERT INTO public.school(stable_name) VALUES ('Identity staging other school') RETURNING school_id INTO v_other_school;
  INSERT INTO public.source(school_id,source_type,title) VALUES (v_school,'manual_import','Identity staging CI') RETURNING source_id INTO v_source;
  INSERT INTO public.source(school_id,source_type,title) VALUES (v_other_school,'manual_import','Other identity staging CI') RETURNING source_id INTO v_other_source;

  BEGIN
    INSERT INTO identity_import_batch(school_id,source_id,external_batch_key)
    VALUES (v_school,v_other_source,'cross-school');
    RAISE EXCEPTION 'cross-school batch unexpectedly accepted';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM='cross-school batch unexpectedly accepted' THEN RAISE; END IF;
  END;

  INSERT INTO identity_import_batch(school_id,source_id,external_batch_key)
  VALUES (v_school,v_source,'batch-090') RETURNING identity_import_batch_id INTO v_batch;
  SELECT count(*) INTO v_count FROM identity_import_batch_state_event
   WHERE identity_import_batch_id=v_batch AND state='staged';
  IF v_count<>1 THEN RAISE EXCEPTION 'batch did not seed one staged event'; END IF;

  INSERT INTO identity_import_item(identity_import_batch_id,source_record_key,raw_payload,raw_payload_sha256)
  VALUES (v_batch,'item-1','{"name":"PII Test","email":"pii@example.invalid"}'::jsonb,repeat('0',64))
  RETURNING identity_import_item_id INTO v_item;
  SELECT raw_payload_sha256,encode(digest(raw_payload::text,'sha256'),'hex') INTO v_hash,v_expected
    FROM identity_import_item WHERE identity_import_item_id=v_item;
  IF v_hash<>v_expected OR v_hash=repeat('0',64) THEN
    RAISE EXCEPTION 'database evidence hash was not recomputed';
  END IF;

  BEGIN
    UPDATE identity_import_item SET normalized_candidate='{"tampered":true}'::jsonb
     WHERE identity_import_item_id=v_item;
    RAISE EXCEPTION 'append-only staging item unexpectedly mutable';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM='append-only staging item unexpectedly mutable' THEN RAISE; END IF;
  END;

  IF to_regclass('public.identity_import_item') IS NOT NULL THEN
    RAISE EXCEPTION 'raw identity staging table leaked into public schema';
  END IF;
END $$;

DO $$
DECLARE role_name text;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'bridge_school_reader','bridge_school_app','bridge_school_worker','bridge_school_health',
    'bridge_school_finance','bridge_school_member','bridge_school_member_principal','bridge_school_auth_gateway'
  ] LOOP
    IF has_schema_privilege(role_name,'identity_staging','USAGE')
       OR has_table_privilege(role_name,'identity_staging.identity_import_item','SELECT')
       OR has_table_privilege(role_name,'identity_staging.identity_import_item','INSERT') THEN
      RAISE EXCEPTION 'runtime role % can access raw identity staging',role_name;
    END IF;
  END LOOP;
END $$;
ROLLBACK;
