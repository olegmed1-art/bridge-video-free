\set ON_ERROR_STOP on

-- Roll back only the forward terminal-v2 layer. Restore the immutable
-- historical 0057 contract exactly, keep its ledger entry, and remove only
-- the 0058 ledger entry. No queue rows are released or deleted here.
\ir ../migrations/0057_universal_video_canary_review_gate.sql

BEGIN;
DELETE FROM schema_migration
 WHERE migration_key = '0058_universal_video_terminal_v2_gate';
COMMIT;
