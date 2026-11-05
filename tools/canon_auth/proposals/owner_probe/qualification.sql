-- Single bounded metadata SELECT; no application rows or school functions run.
-- pg_stat_ssl of an SQL-editor gateway is NOT client/runtime TLS verification.
WITH required_relations(name, needs_insert) AS (VALUES
 ('public.school', false), ('public.source', true), ('public.knowledge_item', true),
 ('public.knowledge_version', true), ('public.knowledge_version_source', true),
 ('bidding.rule', true), ('bidding.rule_test', true), ('bidding.rule_test_run', true),
 ('ai.decision_position', true), ('public.canon_activation', true),
 ('bidding.runtime_activation', true), ('bidding.ingestion_run', true),
 ('bidding.ingestion_event', true)),
required_updates(name, column_name) AS (VALUES
 ('public.knowledge_version','authority_class'), ('public.knowledge_version','review_status'),
 ('bidding.rule','lifecycle_status'), ('public.canon_activation','status'),
 ('public.canon_activation','valid_to'), ('bidding.runtime_activation','status'),
 ('bidding.runtime_activation','valid_to'), ('bidding.ingestion_run','status'),
 ('bidding.ingestion_run','finished_at')),
relations AS (
 SELECT r.*, c.oid, c.relkind FROM required_relations r
 LEFT JOIN pg_catalog.pg_namespace n ON n.nspname=split_part(r.name,'.',1)
 LEFT JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid AND c.relname=split_part(r.name,'.',2)),
columns AS (
 SELECT u.*, c.oid, a.attnum FROM required_updates u
 LEFT JOIN relations c ON c.name=u.name
 LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attname=u.column_name
                                      AND a.attnum>0 AND NOT a.attisdropped),
gate AS (
 SELECT p.oid, p.prokind, p.prorettype='pg_catalog.bool'::regtype AS returns_boolean
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='bidding' AND p.proname='rule_passes_activation_gates'
       AND pg_catalog.oidvectortypes(p.proargtypes)='uuid')
SELECT jsonb_build_object(
 'database',current_database(), 'session_user',session_user, 'current_user',current_user,
 'transaction_read_only',current_setting('transaction_read_only'),
 'statement_timeout',current_setting('statement_timeout'),
 'schemas',(SELECT jsonb_agg(jsonb_build_object('schema',s.name,'exists',n.oid IS NOT NULL,
      'usage',CASE WHEN n.oid IS NOT NULL THEN has_schema_privilege(current_user,n.oid,'USAGE') ELSE false END)
      ORDER BY s.name) FROM (VALUES ('public'),('bidding'),('ai')) s(name)
      LEFT JOIN pg_catalog.pg_namespace n ON n.nspname=s.name),
 'relations',(SELECT jsonb_agg(jsonb_build_object('relation',name,'exists',oid IS NOT NULL,
      'kind',relkind,'select',coalesce(has_table_privilege(current_user,oid,'SELECT'),false),
      'insert_required',needs_insert,'insert',CASE WHEN needs_insert
         THEN coalesce(has_table_privilege(current_user,oid,'INSERT'),false) ELSE NULL END) ORDER BY name) FROM relations),
 'updates',(SELECT jsonb_agg(jsonb_build_object('relation',name,'column',column_name,
      'exists',attnum IS NOT NULL,'update',CASE WHEN attnum IS NOT NULL
         THEN has_column_privilege(current_user,oid,attnum,'UPDATE') ELSE false END)
      ORDER BY name,column_name) FROM columns),
 'activation_gate',(SELECT jsonb_build_object('matches',count(*),'is_function',coalesce(bool_and(prokind='f'),false),
      'returns_boolean',coalesce(bool_and(returns_boolean),false),
      'execute',coalesce(bool_and(has_function_privilege(current_user,oid,'EXECUTE')),false)) FROM gate),
 'production_mutations',false, 'write_admission',false) AS owner_qualification_metadata;
