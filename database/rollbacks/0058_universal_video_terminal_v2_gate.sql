\set ON_ERROR_STOP on

-- Roll back only the forward terminal-v2 layer. Remove the v2-only revision
-- constraint first, then restore the immutable historical 0057 contract,
-- keeping its ledger entry and deleting only the 0058 ledger entry. No queue
-- rows are released or deleted here.
BEGIN;
ALTER TABLE video_queue.job
    DROP CONSTRAINT IF EXISTS video_job_terminal_revision_check;
COMMIT;

\ir ../migrations/0057_universal_video_canary_review_gate.sql

BEGIN;
DELETE FROM schema_migration
 WHERE migration_key = '0058_universal_video_terminal_v2_gate';
COMMIT;
