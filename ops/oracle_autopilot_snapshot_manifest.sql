\set ON_ERROR_STOP on
CREATE TEMP TABLE snapshot_manifest (
  key text PRIMARY KEY,
  item_count bigint NOT NULL,
  digest text NOT NULL
);

DO $$
DECLARE item record;
BEGIN
  FOR item IN
    SELECT n.nspname, c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r','p','m')
      AND (
        n.nspname IN ('autopilot','autopilot_reconcile')
        OR (n.nspname = 'public' AND c.relname = 'schema_migration')
      )
    ORDER BY 1,2
  LOOP
    EXECUTE format(
      'INSERT INTO snapshot_manifest SELECT %L,count(*),'
      || 'md5(coalesce(string_agg(md5(to_jsonb(t)::text),'''' ORDER BY md5(to_jsonb(t)::text)),'''')) '
      || 'FROM %I.%I t',
      'data:' || item.nspname || '.' || item.relname,
      item.nspname,
      item.relname
    );
  END LOOP;
END $$;

DO $$
DECLARE item record;
DECLARE value_text text;
BEGIN
  FOR item IN
    SELECT n.nspname, c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'S' AND n.nspname IN ('autopilot','autopilot_reconcile')
    ORDER BY 1,2
  LOOP
    EXECUTE format('SELECT last_value::text || '':'' || is_called::text FROM %I.%I',
                   item.nspname,item.relname) INTO value_text;
    INSERT INTO snapshot_manifest
      VALUES ('sequence:' || item.nspname || '.' || item.relname,1,md5(value_text));
  END LOOP;
END $$;

INSERT INTO snapshot_manifest
SELECT 'functions',count(*),
       md5(coalesce(string_agg(
         n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' ||
         '|owner=' || r.rolname || '|secdef=' || p.prosecdef::text ||
         '|acl=' || coalesce(p.proacl::text,'NULL') || E'\n' || pg_get_functiondef(p.oid),
         E'\n' ORDER BY n.nspname,p.proname,pg_get_function_identity_arguments(p.oid)
       ),''))
FROM pg_proc p
JOIN pg_namespace n ON n.oid=p.pronamespace
JOIN pg_roles r ON r.oid=p.proowner
WHERE n.nspname IN ('autopilot','autopilot_reconcile');

INSERT INTO snapshot_manifest
SELECT 'objects',count(*),
       md5(coalesce(string_agg(
         n.nspname || '.' || c.relname || '|kind=' || c.relkind::text ||
         '|owner=' || r.rolname || '|acl=' || coalesce(c.relacl::text,'NULL'),
         E'\n' ORDER BY n.nspname,c.relname,c.relkind
       ),''))
FROM pg_class c
JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_roles r ON r.oid=c.relowner
WHERE n.nspname IN ('autopilot','autopilot_reconcile')
   OR (n.nspname='public' AND NOT EXISTS (
       SELECT 1 FROM pg_depend d
       JOIN pg_extension e ON e.oid=d.refobjid
       WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e'
   ));

INSERT INTO snapshot_manifest
SELECT 'schemas',count(*),
       md5(coalesce(string_agg(
         n.nspname || '|owner=' || r.rolname || '|acl=' || coalesce(n.nspacl::text,'NULL'),
         E'\n' ORDER BY n.nspname
       ),''))
FROM pg_namespace n
JOIN pg_roles r ON r.oid=n.nspowner
WHERE n.nspname IN ('autopilot','autopilot_reconcile');

INSERT INTO snapshot_manifest
SELECT 'extensions',count(*),
       md5(coalesce(string_agg(extname || '=' || extversion,E'\n' ORDER BY extname),''))
FROM pg_extension
WHERE extname IN ('plpgsql','pgcrypto','btree_gist');

INSERT INTO snapshot_manifest
SELECT 'unexpected_schemas',count(*),md5(coalesce(string_agg(nspname,E'\n' ORDER BY nspname),''))
FROM pg_namespace
WHERE nspname NOT IN ('pg_catalog','information_schema','public','autopilot','autopilot_reconcile')
  AND nspname NOT LIKE 'pg_toast%'
  AND nspname NOT LIKE 'pg_temp_%';

INSERT INTO snapshot_manifest
SELECT 'health_view',count(*),
       md5(coalesce(string_agg(md5(to_jsonb(v)::text),'' ORDER BY md5(to_jsonb(v)::text)),''))
FROM public.autopilot_operational_health_signal v;

INSERT INTO snapshot_manifest
SELECT 'security_definer_superowner',count(*),md5(count(*)::text)
FROM pg_proc p
JOIN pg_namespace n ON n.oid=p.pronamespace
JOIN pg_roles r ON r.oid=p.proowner
WHERE n.nspname IN ('autopilot','autopilot_reconcile')
  AND p.prosecdef AND r.rolsuper;

SELECT jsonb_build_object(
  'format',2,
  'scope','AUTOPILOT_ONLY',
  'entries',jsonb_object_agg(key,jsonb_build_array(item_count,digest) ORDER BY key)
)
FROM snapshot_manifest;
