\set ON_ERROR_STOP on
-- Update the append-only receipt store to match video learning feedback v3.
-- This is schema preparation only; no receipts are inserted or rewritten.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('video-correction-review-receipt-kind-v3',0));
DO $preflight$
DECLARE definition text;
BEGIN
  IF NOT EXISTS (SELECT FROM public.schema_migration
                 WHERE migration_key='0329_workflow_video_canon_ai_promotion') THEN
    RAISE EXCEPTION 'VIDEO_CORRECTION_KIND_REQUIRES_0329';
  END IF;
  definition := pg_get_functiondef(
    'bidding.validate_video_correction_review_receipt()'::regprocedure);
  IF strpos(definition,'jsonb_object_length(NEW.receipt_payload)<>8')=0 THEN
    RAISE EXCEPTION 'VIDEO_CORRECTION_KIND_GUARD_DRIFT';
  END IF;
END $preflight$;

CREATE TABLE bidding.migration_0400_correction_receipt_guard_backup (
  definition text NOT NULL
);
REVOKE ALL ON bidding.migration_0400_correction_receipt_guard_backup FROM PUBLIC;
INSERT INTO bidding.migration_0400_correction_receipt_guard_backup(definition)
SELECT pg_get_functiondef(
  'bidding.validate_video_correction_review_receipt()'::regprocedure);

CREATE OR REPLACE FUNCTION bidding.validate_video_correction_review_receipt()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_principal bidding.video_canon_verifier_registry%ROWTYPE;
    v_decoded jsonb;
    v_computed text;
BEGIN
    SELECT * INTO v_principal FROM bidding.video_canon_verifier_registry
     WHERE database_role=current_user AND status='active';
    IF NOT FOUND OR NOT ('CORRECTION_REVIEW'=ANY(v_principal.allowed_check_ids))
       OR v_principal.max_assurance_level NOT IN ('I1','I2','I3')
       OR NEW.recorded_by_role<>current_user
       OR NEW.recorded_by_principal<>session_user
       OR NOT EXISTS (
         SELECT 1 FROM pg_catalog.pg_roles login_role
          WHERE login_role.rolname=session_user
            AND login_role.rolcanlogin
            AND pg_has_role(login_role.oid,v_principal.database_role,'MEMBER')
       ) THEN
        RAISE EXCEPTION 'VIDEO_CORRECTION_REVIEW_PRINCIPAL_MISMATCH' USING ERRCODE='42501';
    END IF;
    BEGIN
        v_decoded := NEW.receipt_canonical_json::jsonb;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'VIDEO_CORRECTION_REVIEW_CANONICAL_JSON_INVALID' USING ERRCODE='23514';
    END;
    v_computed := encode(public.digest(convert_to(NEW.receipt_canonical_json,'UTF8'),'sha256'),'hex');
    IF v_decoded<>(NEW.receipt_payload-'receipt_sha256')
       OR v_computed<>NEW.receipt_sha256
       OR NEW.receipt_payload->>'receipt_sha256'<>NEW.receipt_sha256
       OR jsonb_object_length(NEW.receipt_payload)<>9
       OR NOT (NEW.receipt_payload ?& ARRAY[
         'correction_id','kind','reviewer_ref','source_sha256','input_ref',
         'corrected_value_sha256','evidence_refs','status','receipt_sha256'
       ])
       OR COALESCE(NEW.receipt_payload->>'kind','') NOT IN
         ('ASR','SPEAKER','CARD','AUCTION','EXTRACTION','PEDAGOGY')
       OR NEW.receipt_payload->>'status' IS DISTINCT FROM 'VERIFIED'
       OR NOT ((NEW.receipt_payload->>'source_sha256') ~ '^[0-9a-f]{64}$')
       OR NOT ((NEW.receipt_payload->>'corrected_value_sha256') ~ '^[0-9a-f]{64}$')
       OR jsonb_typeof(NEW.receipt_payload->'evidence_refs')<>'array'
       OR jsonb_array_length(NEW.receipt_payload->'evidence_refs')=0 THEN
        RAISE EXCEPTION 'VIDEO_CORRECTION_REVIEW_RECEIPT_INVALID' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0400_video_correction_receipt_kind');
COMMIT;
