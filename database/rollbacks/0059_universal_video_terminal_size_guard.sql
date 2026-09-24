\set ON_ERROR_STOP on
BEGIN;
-- Roll back only 0059; preserve 0058, all queue rows and event history.
ALTER TABLE video_queue.job DROP CONSTRAINT IF EXISTS video_job_terminal_size_check;
DELETE FROM schema_migration WHERE migration_key='0059_universal_video_terminal_size_guard';
COMMIT;
