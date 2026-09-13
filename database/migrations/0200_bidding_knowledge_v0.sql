\set ON_ERROR_STOP on
-- Composite migration. The immutable checksum includes this wrapper and every
-- SQL file in the sibling 0200_bidding_knowledge_v0 directory.
BEGIN;
\ir 0200_bidding_knowledge_v0/01_core_rule.sql
\ir 0200_bidding_knowledge_v0/02_tests_conflicts_activation.sql
\ir 0200_bidding_knowledge_v0/03_activation_immutability_trace.sql
\ir 0200_bidding_knowledge_v0/04_ingestion_audit.sql
\ir 0200_bidding_knowledge_v0/05_runtime_views.sql
\ir 0200_bidding_knowledge_v0/06_privileges.sql
\ir 0200_bidding_knowledge_v0/07_function_acl.sql
\ir 0200_bidding_knowledge_v0/08_internal_acl_and_registry.sql
COMMIT;
