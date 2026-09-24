\set ON_ERROR_STOP on
-- Exercised by database-ci against its disposable PostgreSQL instance.
-- All test receipts and the temporary verifier membership roll back.
BEGIN;
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM public.schema_migration
                 WHERE migration_key='0400_video_correction_receipt_kind') THEN
    RAISE EXCEPTION 'VIDEO_CORRECTION_KIND_MIGRATION_MISSING';
  END IF;
  EXECUTE format('GRANT bridge_school_canon_control_verifier TO %I',session_user);
END $$;
SET LOCAL ROLE bridge_school_canon_control_verifier;

DO $$
DECLARE
  payload jsonb;
  canonical text;
  receipt_sha text;
BEGIN
  payload := jsonb_build_object(
    'correction_id','ci-kind-valid','kind','ASR',
    'reviewer_ref','teacher:ci','source_sha256',repeat('a',64),
    'input_ref','segment-ci','corrected_value_sha256',repeat('b',64),
    'evidence_refs',jsonb_build_array('segment-ci'),'status','VERIFIED');
  canonical := payload::text;
  receipt_sha := encode(public.digest(convert_to(canonical,'UTF8'),'sha256'),'hex');
  INSERT INTO bidding.video_correction_review_receipt(
    receipt_sha256,receipt_canonical_json,receipt_payload
  ) VALUES (
    receipt_sha,canonical,payload||jsonb_build_object('receipt_sha256',receipt_sha)
  );

  payload := payload-'kind';
  canonical := payload::text;
  receipt_sha := encode(public.digest(convert_to(canonical,'UTF8'),'sha256'),'hex');
  BEGIN
    INSERT INTO bidding.video_correction_review_receipt(
      receipt_sha256,receipt_canonical_json,receipt_payload
    ) VALUES (
      receipt_sha,canonical,payload||jsonb_build_object('receipt_sha256',receipt_sha)
    );
    RAISE EXCEPTION 'VIDEO_CORRECTION_LEGACY_RECEIPT_ACCEPTED';
  EXCEPTION WHEN SQLSTATE '23514' THEN NULL;
  END;

  payload := payload||jsonb_build_object('kind','UNREVIEWED');
  canonical := payload::text;
  receipt_sha := encode(public.digest(convert_to(canonical,'UTF8'),'sha256'),'hex');
  BEGIN
    INSERT INTO bidding.video_correction_review_receipt(
      receipt_sha256,receipt_canonical_json,receipt_payload
    ) VALUES (
      receipt_sha,canonical,payload||jsonb_build_object('receipt_sha256',receipt_sha)
    );
    RAISE EXCEPTION 'VIDEO_CORRECTION_UNKNOWN_KIND_ACCEPTED';
  EXCEPTION WHEN SQLSTATE '23514' THEN NULL;
  END;
END $$;
ROLLBACK;
