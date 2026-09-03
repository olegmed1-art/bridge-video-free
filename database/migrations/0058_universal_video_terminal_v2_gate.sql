\set ON_ERROR_STOP on

-- Forward-only terminal-v2 gate. The SQL payload is kept as a composite part so
-- databases that already recorded historical 0057 still execute the v2 gate.
\ir 0058_universal_video_terminal_v2_gate/001_apply_terminal_v2.sql

BEGIN;
INSERT INTO schema_migration(migration_key)
VALUES ('0058_universal_video_terminal_v2_gate')
ON CONFLICT DO NOTHING;
COMMIT;
