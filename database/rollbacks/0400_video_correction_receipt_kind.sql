\set ON_ERROR_STOP on
-- Restoring the old guard would reject new-format receipts. Refuse if any
-- have already been recorded; append-only evidence must never be removed.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('video-correction-review-receipt-kind-v3',0));
-- Conflicts with INSERT's ROW EXCLUSIVE lock until COMMIT, so a new receipt
-- cannot arrive after the guard scan but before the prior trigger is restored.
LOCK TABLE bidding.video_correction_review_receipt IN SHARE MODE;
DO $rollback$
DECLARE previous_definition text;
BEGIN
  IF EXISTS (SELECT FROM bidding.video_correction_review_receipt
             WHERE receipt_payload ? 'kind') THEN
    RAISE EXCEPTION 'VIDEO_CORRECTION_KIND_ROLLBACK_REFUSED_NEW_RECEIPTS';
  END IF;
  IF (SELECT count(*) FROM bidding.migration_0400_correction_receipt_guard_backup)<>1 THEN
    RAISE EXCEPTION 'VIDEO_CORRECTION_KIND_ROLLBACK_BACKUP_MISSING';
  END IF;
  IF strpos(pg_get_functiondef(
       'bidding.validate_video_correction_review_receipt()'::regprocedure),
       '(SELECT count(*) FROM jsonb_object_keys(NEW.receipt_payload))<>9')=0 THEN
    RAISE EXCEPTION 'VIDEO_CORRECTION_KIND_ROLLBACK_GUARD_DRIFT';
  END IF;
  SELECT definition INTO previous_definition
    FROM bidding.migration_0400_correction_receipt_guard_backup;
  EXECUTE previous_definition;
END $rollback$;
DROP TABLE bidding.migration_0400_correction_receipt_guard_backup;
DELETE FROM public.schema_migration
 WHERE migration_key='0400_video_correction_receipt_kind';
COMMIT;
