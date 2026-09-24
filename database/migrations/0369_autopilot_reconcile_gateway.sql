\set ON_ERROR_STOP on
BEGIN;

DO $pre$
BEGIN
 IF NOT EXISTS(SELECT FROM public.schema_migration WHERE migration_key='0368_autopilot_next_step') THEN
   RAISE EXCEPTION 'RECONCILE_GATEWAY_REQUIRES_0368';
 END IF;
 -- Repair reachability of four already-granted RPCs without granting access
 -- to the internal schema or the older non-CAS reconciliation entrypoint.
 IF NOT (has_function_privilege('bridge_school_worker','autopilot.paused_reconcile_candidates(integer)','EXECUTE')
   AND has_function_privilege('bridge_school_worker','autopilot.project_progress_candidates(integer)','EXECUTE')
   AND has_function_privilege('bridge_school_worker','autopilot.reconcile_paused_project_work_cas(uuid,timestamptz,text,text,text)','EXECUTE')
   AND has_function_privilege('bridge_school_worker','autopilot.reconcile_project_progress(uuid,timestamptz,text,timestamptz)','EXECUTE')) THEN
   RAISE EXCEPTION 'RECONCILE_GATEWAY_EXISTING_CAPABILITY_REQUIRED';
 END IF;
END $pre$;

CREATE SCHEMA autopilot_reconcile;
REVOKE ALL ON SCHEMA autopilot_reconcile FROM PUBLIC;

CREATE FUNCTION autopilot_reconcile.paused_candidates(p_limit integer)
RETURNS SETOF jsonb LANGUAGE sql SECURITY DEFINER
SET search_path TO 'pg_catalog'
AS $f$ SELECT to_jsonb(c) FROM autopilot.paused_reconcile_candidates(p_limit) c $f$;

CREATE FUNCTION autopilot_reconcile.progress_candidates(p_limit integer)
RETURNS SETOF jsonb LANGUAGE sql SECURITY DEFINER
SET search_path TO 'pg_catalog'
AS $f$ SELECT * FROM autopilot.project_progress_candidates(p_limit) $f$;

CREATE FUNCTION autopilot_reconcile.apply_paused(
 p_work_item_id uuid,p_observed_updated_at timestamptz,p_evidence_token text,
 p_target_disposition text,p_summary text
) RETURNS text LANGUAGE sql SECURITY DEFINER
SET search_path TO 'pg_catalog'
AS $f$ SELECT autopilot.reconcile_paused_project_work_cas(
 p_work_item_id,p_observed_updated_at,p_evidence_token,p_target_disposition,p_summary) $f$;

CREATE FUNCTION autopilot_reconcile.apply_progress(
 p_work_item_id uuid,p_observed_updated_at timestamptz,p_head_sha text,p_checks_completed_at timestamptz
) RETURNS text LANGUAGE sql SECURITY DEFINER
SET search_path TO 'pg_catalog'
AS $f$ SELECT autopilot.reconcile_project_progress(
 p_work_item_id,p_observed_updated_at,p_head_sha,p_checks_completed_at) $f$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA autopilot_reconcile FROM PUBLIC;
GRANT USAGE ON SCHEMA autopilot_reconcile TO bridge_school_worker;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA autopilot_reconcile TO bridge_school_worker;

INSERT INTO public.schema_migration(migration_key) VALUES('0369_autopilot_reconcile_gateway');
COMMIT;
