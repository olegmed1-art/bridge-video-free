\set ON_ERROR_STOP on
-- Composite migration. The immutable checksum includes this wrapper and every
-- SQL file in the sibling 0300_autopilot_core directory.
BEGIN;
\ir 0300_autopilot_core/01_schema.sql
\ir 0300_autopilot_core/02_functions.sql
\ir 0300_autopilot_core/03_privileges.sql
COMMIT;
