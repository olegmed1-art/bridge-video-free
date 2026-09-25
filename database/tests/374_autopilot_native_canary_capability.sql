\set ON_ERROR_STOP on
BEGIN;

DO $test$ DECLARE signature text;
BEGIN
 IF NOT EXISTS(SELECT FROM public.schema_migration
               WHERE migration_key='0374_autopilot_native_canary_capability')
 OR (SELECT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole
            OR rolreplication OR rolbypassrls
       FROM pg_roles WHERE rolname='autopilot_native_canary') IS DISTINCT FROM false
 OR EXISTS(SELECT FROM pg_auth_members m
           JOIN pg_roles r ON r.oid=m.roleid
           JOIN pg_roles member ON member.oid=m.member
           WHERE r.rolname='autopilot_native_canary'
             AND (member.rolname<>SESSION_USER OR NOT m.admin_option
                  OR m.inherit_option OR m.set_option)) THEN
  RAISE EXCEPTION 'NATIVE_CAPABILITY_NOT_DORMANT';
 END IF;
 FOREACH signature IN ARRAY ARRAY[
  'native_cli_reserve_canary(uuid,jsonb,text)',
  'native_cli_snapshot(uuid)',
  'native_cli_begin_canary(jsonb)',
  'native_cli_canary_current(jsonb)',
  'native_cli_ack(jsonb,text,text)',
  'native_cli_finish(jsonb,text,jsonb)'
 ] LOOP
  IF NOT has_function_privilege('autopilot_native_canary',
                                'autopilot.'||signature,'EXECUTE') THEN
   RAISE EXCEPTION 'NATIVE_CAPABILITY_REQUIRED_RPC_MISSING: %',signature;
  END IF;
 END LOOP;
 IF has_function_privilege('autopilot_native_canary',
                          'autopilot.native_cli_reserve(uuid,jsonb,text)','EXECUTE')
 OR has_function_privilege('autopilot_native_canary',
                           'autopilot.native_cli_begin(jsonb)','EXECUTE')
 OR has_table_privilege('autopilot_native_canary',
                         'autopilot.native_cli_receipt','INSERT')
 OR has_table_privilege('autopilot_native_canary',
                         'autopilot.native_cli_single_canary_permit','INSERT')
 OR (SELECT enabled FROM autopilot.native_cli_config) THEN
  RAISE EXCEPTION 'NATIVE_CAPABILITY_PRIVILEGE_OR_ACTIVATION_DRIFT';
 END IF;
END $test$;
ROLLBACK;
