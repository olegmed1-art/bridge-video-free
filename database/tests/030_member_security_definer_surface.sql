\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    r record;
BEGIN
    -- The ordinary member capability may execute only the explicitly reviewed
    -- SECURITY DEFINER authorization helpers below. Any newly leaked privileged helper
    -- fails this regression test instead of silently expanding the member boundary.
    FOR r IN
        SELECT p.oid,
               p.proname,
               pg_get_function_identity_arguments(p.oid) AS args
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
         WHERE n.nspname='public'
           AND p.prosecdef
           AND has_function_privilege('bridge_school_member',p.oid,'EXECUTE')
           AND (p.proname,pg_get_function_identity_arguments(p.oid)) NOT IN (
               ('bridge_current_actor_person_id',''),
               ('bridge_current_actor_school_id',''),
               ('bridge_current_actor_auth_identity_id',''),
               ('bridge_current_request_id',''),
               ('bridge_actor_has_role','p_role_key text'),
               ('bridge_actor_has_person_permission','p_target_person_id uuid, p_permission_key text')
           )
    LOOP
        RAISE EXCEPTION 'unreviewed SECURITY DEFINER function executable by member: %(%)', r.proname, r.args;
    END LOOP;

    -- The combined dormant server principal adds exactly the trusted context
    -- establishment entry point to the ordinary member surface.
    FOR r IN
        SELECT p.oid,
               p.proname,
               pg_get_function_identity_arguments(p.oid) AS args
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
         WHERE n.nspname='public'
           AND p.prosecdef
           AND has_function_privilege('bridge_school_member_principal',p.oid,'EXECUTE')
           AND (p.proname,pg_get_function_identity_arguments(p.oid)) NOT IN (
               ('bridge_current_actor_person_id',''),
               ('bridge_current_actor_school_id',''),
               ('bridge_current_actor_auth_identity_id',''),
               ('bridge_current_request_id',''),
               ('bridge_actor_has_role','p_role_key text'),
               ('bridge_actor_has_person_permission','p_target_person_id uuid, p_permission_key text'),
               ('bridge_establish_verified_actor_context','p_auth_identity_id uuid, p_school_id uuid, p_request_id uuid')
           )
    LOOP
        RAISE EXCEPTION 'unreviewed SECURITY DEFINER function executable by member server principal: %(%)', r.proname, r.args;
    END LOOP;
END $$;

ROLLBACK;
