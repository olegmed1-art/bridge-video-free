\set ON_ERROR_STOP on
-- Rehearsal only: one transaction, always rolled back; never execute on Neon.
BEGIN;
DO $$ BEGIN
  IF current_database() <> 'autopilot_acl_cutf8_rehearsal' THEN
    RAISE EXCEPTION 'wrong rehearsal database';
  END IF;
END $$;
CREATE TEMP TABLE fence_baseline AS
SELECT 'public_rel_acl' AS kind, md5(coalesce(string_agg(c.oid::text || ':' || coalesce(c.relacl::text,''), '|' ORDER BY c.oid),'')) AS digest
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
UNION ALL
SELECT 'role_membership', md5(coalesce(string_agg(row_to_json(m)::text,'|' ORDER BY roleid,member),'')) FROM pg_auth_members m;

DO $$
DECLARE schema_name text; role_name text;
BEGIN
  FOREACH schema_name IN ARRAY ARRAY['autopilot','autopilot_reconcile'] LOOP
    FOREACH role_name IN ARRAY ARRAY['autopilot_callback','autopilot_runtime','autopilot_runtime_principal','bridge_school_health','bridge_school_worker'] LOOP
      EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA %I FROM %I',schema_name,role_name);
      EXECUTE format('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I FROM %I',schema_name,role_name);
      EXECUTE format('REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA %I FROM %I',schema_name,role_name);
      EXECUTE format('REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA %I FROM %I',schema_name,role_name);
    END LOOP;
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA %I FROM PUBLIC',schema_name);
    EXECUTE format('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I FROM PUBLIC',schema_name);
    EXECUTE format('REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA %I FROM PUBLIC',schema_name);
    EXECUTE format('REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA %I FROM PUBLIC',schema_name);
  END LOOP;
END $$;

DO $$
DECLARE principal text; violations bigint;
BEGIN
  FOREACH principal IN ARRAY ARRAY['autopilot_light_worker_login','autopilot_callback_login','bridge_school_worker_principal'] LOOP
    SELECT count(*) INTO violations FROM pg_namespace n
      WHERE n.nspname IN ('autopilot','autopilot_reconcile')
      AND has_schema_privilege(principal,n.oid,'USAGE,CREATE');
    IF violations <> 0 THEN RAISE EXCEPTION 'schema fence failed'; END IF;
    SELECT count(*) INTO violations FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname IN ('autopilot','autopilot_reconcile') AND has_function_privilege(principal,p.oid,'EXECUTE');
    IF violations <> 0 THEN RAISE EXCEPTION 'function fence failed'; END IF;
    SELECT count(*) INTO violations FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname IN ('autopilot','autopilot_reconcile') AND c.relkind IN ('r','p','v','m','f')
      AND has_table_privilege(principal,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER');
    IF violations <> 0 THEN RAISE EXCEPTION 'table fence failed'; END IF;
    WITH sequences AS MATERIALIZED (
      SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname IN ('autopilot','autopilot_reconcile') AND c.relkind='S'
    ) SELECT count(*) INTO violations FROM sequences
      WHERE has_sequence_privilege(principal,oid,'USAGE,SELECT,UPDATE');
    IF violations <> 0 THEN RAISE EXCEPTION 'sequence fence failed'; END IF;
  END LOOP;
  IF NOT has_table_privilege('bridge_school_worker_principal','public.schema_migration','SELECT') THEN
    RAISE EXCEPTION 'public ledger access changed';
  END IF;
  IF EXISTS (
    (SELECT * FROM fence_baseline) EXCEPT
    (SELECT 'public_rel_acl',md5(coalesce(string_agg(c.oid::text || ':' || coalesce(c.relacl::text,''),'|' ORDER BY c.oid),''))
       FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
     UNION ALL SELECT 'role_membership',md5(coalesce(string_agg(row_to_json(m)::text,'|' ORDER BY roleid,member),'')) FROM pg_auth_members m)
  ) THEN RAISE EXCEPTION 'public ACL or membership drift'; END IF;
END $$;
SELECT 'FENCE_REHEARSAL_PASS_NO_PUBLIC_ACL_OR_MEMBERSHIP_CHANGE' AS result;
ROLLBACK;
