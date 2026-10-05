\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '10s';

-- All target-specific values must be supplied from a reviewed fresh snapshot.
SELECT set_config('bridge.reconcile.database', :'expected_database', true),
       set_config('bridge.reconcile.branch', :'expected_branch', true),
       set_config('bridge.reconcile.owner', :'expected_owner', true),
       set_config('bridge.reconcile.oid', :'expected_oid', true),
       set_config('bridge.reconcile.definition', :'expected_definition_sha256', true),
       set_config('bridge.reconcile.acl', :'expected_acl_sha256', true),
       set_config('bridge.reconcile.applied_at', :'expected_applied_at', true),
       set_config('bridge.reconcile.action', :'action', true);

DO $reconcile$
DECLARE
    proc regprocedure := 'autopilot.accept_role_dispatch_codex_ack(text,text,boolean,text,integer,text,bigint,text,text,bigint,text,bigint,jsonb)'::regprocedure;
    canonical constant text := 'bea4b407a63256f83f5eecbd8b220406070046edb34d87f849d645b39134dcc7';
    direction text := current_setting('bridge.reconcile.action');
    definition text;
    changed integer;
BEGIN
    IF direction NOT IN ('repair', 'rollback')
       OR current_setting('bridge.reconcile.database') = ''
       OR current_setting('bridge.reconcile.branch') = ''
       OR current_database() IS DISTINCT FROM current_setting('bridge.reconcile.database')
       OR current_setting('neon.branch_id', true) IS DISTINCT FROM current_setting('bridge.reconcile.branch')
       OR current_user IS DISTINCT FROM current_setting('bridge.reconcile.owner') THEN
        RAISE EXCEPTION 'ACK_CHECKSUM_TARGET_DRIFT';
    END IF;
    IF EXISTS (SELECT FROM pg_event_trigger WHERE evtenabled <> 'D')
       OR EXISTS (SELECT FROM pg_trigger WHERE tgrelid='public.schema_migration'::regclass AND NOT tgisinternal) THEN
        RAISE EXCEPTION 'ACK_CHECKSUM_UNREVIEWED_TRIGGER';
    END IF;

    definition := pg_get_functiondef(proc);
    IF NOT EXISTS (
        SELECT FROM pg_proc p WHERE p.oid=proc
          AND p.oid = current_setting('bridge.reconcile.oid')::oid
          AND pg_get_userbyid(p.proowner) = current_setting('bridge.reconcile.owner')
          AND p.prosecdef AND NOT p.proisstrict
          AND p.proconfig = ARRAY['search_path=pg_catalog, autopilot']::text[]
          AND encode(sha256(convert_to(definition,'UTF8')),'hex') = current_setting('bridge.reconcile.definition')
          AND encode(sha256(convert_to(coalesce(p.proacl::text,'<NULL>'),'UTF8')),'hex') = current_setting('bridge.reconcile.acl')
    ) OR strpos(definition,'TARGET_PR_CODEX_CONTEXT_V1') = 0
      OR strpos(definition,'OR p_payload_fingerprint IS NULL -- ACK_FINGERPRINT_NOT_NULL_V1') = 0
      OR (length(definition)-length(replace(definition,'ACK_FINGERPRINT_NOT_NULL_V1','')))
           / length('ACK_FINGERPRINT_NOT_NULL_V1') <> 1
      OR NOT EXISTS (SELECT FROM public.schema_migration WHERE migration_key='0362_autopilot_target_pr_codex_context') THEN
        RAISE EXCEPTION 'ACK_CHECKSUM_CATALOG_DRIFT';
    END IF;

    UPDATE public.schema_migration
    SET checksum = CASE WHEN direction='repair' THEN canonical ELSE NULL END
    WHERE migration_key='0401_autopilot_codex_ack_fingerprint_not_null'
      AND applied_at=current_setting('bridge.reconcile.applied_at')::timestamptz
      AND ((direction='repair' AND checksum IS NULL)
        OR (direction='rollback' AND checksum=canonical));
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed <> 1 THEN
        RAISE EXCEPTION 'ACK_CHECKSUM_REGISTRY_DRIFT';
    END IF;
END $reconcile$;
COMMIT;
