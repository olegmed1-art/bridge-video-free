\set ON_ERROR_STOP on
BEGIN;

DO $pre$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migration WHERE migration_key='0347_autopilot_planner_loop_guard')
 THEN RAISE EXCEPTION 'AUTOPILOT_BLOCKER_REMEDIATION_REQUIRES_0347'; END IF;
END $pre$;

CREATE OR REPLACE FUNCTION autopilot.blocker_remediation_action(p_result_code text)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
SELECT CASE
 WHEN p_result_code IS NULL THEN 'HOLD_UNKNOWN'
 WHEN autopilot.role_blocker_requires_owner(p_result_code) THEN 'OWNER_HOLD'
 WHEN p_result_code IN ('CODEX_PROVIDER_GENERIC_FAILURE','CODEX_ACK_DEADLINE_EXCEEDED','CODEX_RESULT_DEADLINE_EXCEEDED') THEN 'PROVIDER_HOLD'
 WHEN p_result_code IN ('TARGET_SUPERSEDED_BY_CURRENT_MAIN','CURRENT_MAIN_SUBSUMES_TARGET','TARGET_PR_NOT_UPDATED') THEN 'RECONCILE_TARGET'
 WHEN p_result_code IN ('SERVER_EVIDENCE_INCOMPLETE','SERVER_PARITY_EVIDENCE_INCOMPLETE','PRODUCTION_ACCEPTANCE_PLAN_INCOMPLETE','WORLD_EVIDENCE_NOT_VERSION_BOUND') THEN 'EVIDENCE_REMEDIATION'
 WHEN p_result_code IN ('QA_GATES_FAILED','FINDINGS_REQUIRE_REPAIR','VIRTUALENV_INTERPRETER_REQUIRED','HISTORICAL_RULE_CONTENT_MUTABLE','FINDING_STALE_BLOCK_LEDGER','RECOGNIZER_READINESS_GAP') THEN 'REPOSITORY_REPAIR'
 WHEN p_result_code IN ('PROJECT_HEAD_BROKER_TRANSIENT_ERROR','ROLE_DISPATCH_RESPONSE_INVALID','GITHUB_API_TRANSIENT_ERROR','AUTOPILOT_TRANSIENT_DATABASE_ERROR') THEN 'TRANSPORT_REMEDIATION'
 WHEN p_result_code IN ('ROOT_CAUSE_IDENTIFIED','BOUNDED_REPAIR_UNIT_SELECTED','NEXT_REPAIR_UNIT_CONFIRMED') THEN 'REPOSITORY_REPAIR'
 ELSE 'HOLD_UNKNOWN' END
$$;

-- Admission gate for existing repair controller. Unknown/evidence/provider/owner
-- blockers cannot accidentally become repository repair.
CREATE OR REPLACE FUNCTION autopilot.blocker_repository_repair_allowed(p_result_code text)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
 SELECT autopilot.blocker_remediation_action(p_result_code)='REPOSITORY_REPAIR'
$$;

-- Wire deterministic classification into the existing repair controller.
DO $patch$
DECLARE
 original text;
 anchor text := $a$    -- REPAIR_ADMISSION_V1: authority and target disposition before side effects.$a$;
 guard_sql text := $g$    -- BLOCKER_REMEDIATION_ADMISSION_V1
    IF NOT autopilot.blocker_repository_repair_allowed(p_result_code) THEN
        RETURN NULL;
    END IF;

$g$;
BEGIN
 original := pg_get_functiondef('autopilot.materialize_role_repair(uuid,text,text)'::regprocedure);
 IF original IS NULL OR strpos(original,'BLOCKER_REMEDIATION_ADMISSION_V1')>0 OR strpos(original,anchor)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_BLOCKER_REMEDIATION_SOURCE_DRIFT';
 END IF;
 EXECUTE replace(original,anchor,guard_sql||anchor);
END $patch$;

INSERT INTO public.schema_migration(migration_key) VALUES ('0348_autopilot_blocker_remediation');
COMMIT;
