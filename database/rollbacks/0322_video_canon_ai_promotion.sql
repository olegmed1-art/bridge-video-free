\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM bidding.video_canon_ai_promotion_receipt)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_ai_verification)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_source_policy) THEN
    RAISE EXCEPTION '0322 rollback refused: Video-to-Canon state exists';
  END IF;
END $$;
DROP FUNCTION bidding.activate_ai_verified_video_canon(uuid,uuid,text,text,text,timestamptz,timestamptz);
DROP TABLE bidding.video_canon_ai_promotion_receipt;
DROP TABLE bidding.video_canon_ai_verification;
DROP TABLE bidding.video_canon_source_policy;
DELETE FROM public.schema_migration WHERE migration_key='0322_video_canon_ai_promotion';
COMMIT;
