\set ON_ERROR_STOP on
BEGIN;

DO $pre$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0364_autopilot_repair_head_rebind'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_PARALLEL_INTAKE_REQUIRES_0364';
 END IF;
 IF EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0365_autopilot_parallel_work_intake'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_PARALLEL_INTAKE_ALREADY_APPLIED';
 END IF;
END $pre$;

CREATE TABLE autopilot.migration_0365_function_backup (
 function_key text PRIMARY KEY,
 definition text NOT NULL CHECK (length(definition) BETWEEN 100 AND 100000)
);

INSERT INTO autopilot.migration_0365_function_backup(function_key,definition)
SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid)
FROM pg_proc AS p
JOIN pg_namespace AS n ON n.oid=p.pronamespace
WHERE p.oid='autopilot.claim_project_work_probe(text,integer)'::regprocedure;

CREATE TABLE autopilot.project_work_manifest_receipt (
 manifest_sha256 text PRIMARY KEY CHECK (manifest_sha256~'^[0-9a-f]{64}$'),
 schema_version text NOT NULL CHECK (
   schema_version='AUTOPILOT_PARALLEL_WORK_V1'
 ),
 source text NOT NULL CHECK (source='REVIEWED_WORKER_RELEASE'),
 item_count smallint NOT NULL CHECK (item_count BETWEEN 1 AND 5),
 registered_count smallint NOT NULL CHECK (
   registered_count BETWEEN 0 AND item_count
 ),
 applied_at timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON TABLE autopilot.project_work_manifest_receipt
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

CREATE OR REPLACE FUNCTION autopilot.register_parallel_work_manifest(
 p_manifest_text text,
 p_manifest_sha256 text
)
RETURNS TABLE(
 manifest_sha256 text,
 item_count integer,
 registered_count integer,
 replayed boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,autopilot
AS $function$
DECLARE
 manifest jsonb;
 candidate jsonb;
 spec jsonb;
 existing_receipt autopilot.project_work_manifest_receipt;
 registration record;
 candidate_work_key text;
 candidate_role text;
 candidate_task_kind text;
 candidate_objective text;
 candidate_target_pr integer;
 candidate_priority integer;
 repair_policy text;
 candidate_count integer;
 created_count integer:=0;
 seen_work_keys text[]:='{}'::text[];
 seen_roles text[]:='{}'::text[];
 seen_targets integer[]:='{}'::integer[];
BEGIN
 IF length(COALESCE(p_manifest_text,'')) NOT BETWEEN 1 AND 65536
    OR COALESCE(p_manifest_sha256,'')!~'^[0-9a-f]{64}$'
    OR pg_catalog.encode(
         public.digest(convert_to(p_manifest_text,'UTF8'),'sha256'),'hex'
       ) IS DISTINCT FROM p_manifest_sha256 THEN
   RAISE EXCEPTION 'AUTOPILOT_PARALLEL_MANIFEST_DIGEST_INVALID';
 END IF;

 PERFORM pg_advisory_xact_lock(
   hashtextextended('autopilot-parallel-work-manifest-v1',0)
 );
 SELECT * INTO existing_receipt
 FROM autopilot.project_work_manifest_receipt AS receipt
 WHERE receipt.manifest_sha256=p_manifest_sha256;
 IF FOUND THEN
   RETURN QUERY SELECT existing_receipt.manifest_sha256,
     existing_receipt.item_count::integer,
     existing_receipt.registered_count::integer,true;
   RETURN;
 END IF;

 BEGIN
   manifest:=p_manifest_text::jsonb;
 EXCEPTION WHEN others THEN
   RAISE EXCEPTION 'AUTOPILOT_PARALLEL_MANIFEST_JSON_INVALID';
 END;
 IF jsonb_typeof(manifest)<>'object'
    OR (SELECT array_agg(key ORDER BY key)
        FROM jsonb_object_keys(manifest) AS keys(key))
       IS DISTINCT FROM ARRAY['items','repository','schema_version','source']::text[]
    OR manifest->>'schema_version'<>'AUTOPILOT_PARALLEL_WORK_V1'
    OR manifest->>'repository'<>'olegmed1-art/bridge-video-free'
    OR manifest->>'source'<>'REVIEWED_WORKER_RELEASE'
    OR jsonb_typeof(manifest->'items')<>'array'
    OR jsonb_array_length(manifest->'items') NOT BETWEEN 1 AND 5 THEN
   RAISE EXCEPTION 'AUTOPILOT_PARALLEL_MANIFEST_SHAPE_INVALID';
 END IF;
 candidate_count:=jsonb_array_length(manifest->'items');

 FOR candidate IN SELECT value FROM jsonb_array_elements(manifest->'items')
 LOOP
   IF jsonb_typeof(candidate)<>'object'
      OR (SELECT array_agg(key ORDER BY key)
          FROM jsonb_object_keys(candidate) AS keys(key))
         IS DISTINCT FROM ARRAY[
           'objective','priority','role','target_pr','task_kind',
           'task_spec_json','work_key'
         ]::text[] THEN
     RAISE EXCEPTION 'AUTOPILOT_PARALLEL_ITEM_SHAPE_INVALID';
   END IF;

   candidate_work_key:=candidate->>'work_key';
   candidate_role:=candidate->>'role';
   candidate_task_kind:=candidate->>'task_kind';
   candidate_objective:=candidate->>'objective';
   IF COALESCE(candidate->>'target_pr','')!~'^[1-9][0-9]{0,6}$'
      OR (candidate->>'target_pr')::bigint>1000000
      OR COALESCE(candidate->>'priority','')!~'^(0|10|20|30)$' THEN
     RAISE EXCEPTION 'AUTOPILOT_PARALLEL_ITEM_NUMERIC_INVALID';
   END IF;
   candidate_target_pr:=(candidate->>'target_pr')::integer;
   candidate_priority:=(candidate->>'priority')::integer;
   spec:=candidate->'task_spec_json';

   IF COALESCE(candidate_work_key,'')!~'^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$'
      OR COALESCE(candidate_role,'')!~'^[A-Z][A-Z0-9_]{0,63}$'
      OR COALESCE(candidate_task_kind,'')!~'^[A-Z][A-Z0-9_]{0,63}$'
      OR length(COALESCE(candidate_objective,'')) NOT BETWEEN 1 AND 1000
      OR candidate_objective~'[[:cntrl:]]'
      OR candidate_work_key=ANY(seen_work_keys)
      OR candidate_role=ANY(seen_roles)
      OR candidate_target_pr=ANY(seen_targets) THEN
     RAISE EXCEPTION 'AUTOPILOT_PARALLEL_ITEM_IDENTITY_INVALID';
   END IF;
   seen_work_keys:=array_append(seen_work_keys,candidate_work_key);
   seen_roles:=array_append(seen_roles,candidate_role);
   seen_targets:=array_append(seen_targets,candidate_target_pr);

   IF jsonb_typeof(spec)<>'object'
      OR (SELECT array_agg(key ORDER BY key)
          FROM jsonb_object_keys(spec) AS keys(key))
         IS DISTINCT FROM ARRAY[
           'execution_scope','expected_changed_files','forbidden_actions',
           'parallel_safe','production_mutation','repair_policy',
           'required_checks'
         ]::text[]
      OR spec->>'execution_scope'<>'REPOSITORY'
      OR spec->'parallel_safe'<>'true'::jsonb
      OR spec->'production_mutation'<>'false'::jsonb
      OR spec->>'repair_policy' NOT IN ('DISABLED','BOUNDED') THEN
     RAISE EXCEPTION 'AUTOPILOT_PARALLEL_SPEC_INVALID';
   END IF;
   repair_policy:=spec->>'repair_policy';

   IF jsonb_typeof(spec->'expected_changed_files')<>'array'
      OR jsonb_array_length(spec->'expected_changed_files')>64
      OR (repair_policy='DISABLED'
          AND jsonb_array_length(spec->'expected_changed_files')<>0)
      OR (repair_policy='BOUNDED'
          AND jsonb_array_length(spec->'expected_changed_files')<1)
      OR EXISTS (
        SELECT 1
        FROM jsonb_array_elements_text(spec->'expected_changed_files') AS file(path)
        WHERE length(path) NOT BETWEEN 1 AND 200
           OR path!~'^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$'
           OR path~'(^|/)\.\.(/|$)'
           OR path LIKE '.github/%'
           OR path LIKE 'database/migrations/%'
           OR path LIKE 'database/rollbacks/%'
           OR path LIKE 'deploy/%'
           OR path LIKE 'docs/canon/%'
      ) THEN
     RAISE EXCEPTION 'AUTOPILOT_PARALLEL_REPAIR_SCOPE_INVALID';
   END IF;

   IF jsonb_typeof(spec->'required_checks')<>'array'
      OR jsonb_array_length(spec->'required_checks') NOT BETWEEN 1 AND 32
      OR EXISTS (
        SELECT 1
        FROM jsonb_array_elements_text(spec->'required_checks') AS check_name(value)
        WHERE length(value) NOT BETWEEN 1 AND 200 OR value~'[[:cntrl:]]'
      )
      OR jsonb_typeof(spec->'forbidden_actions')<>'array'
      OR jsonb_array_length(spec->'forbidden_actions')<>12
      OR (SELECT count(DISTINCT action)
          FROM jsonb_array_elements_text(spec->'forbidden_actions') AS actions(action))<>12
      OR EXISTS (
        SELECT 1
        FROM jsonb_array_elements_text(spec->'forbidden_actions') AS actions(action)
        WHERE action<>ALL(ARRAY[
          'canon_mutation','credential_access','deploy','drive_write',
          'force_push','main_write','merge','neon_write','paid_action',
          'production_write','real_media_processing','server_write'
        ]::text[])
      ) THEN
     RAISE EXCEPTION 'AUTOPILOT_PARALLEL_GUARDS_INVALID';
   END IF;

   PERFORM 1 FROM autopilot.role_registry AS role
   WHERE role.role_id=candidate_role
     AND role.enabled
     AND role.execution_scope='REPOSITORY'
     AND (repair_policy='DISABLED' OR role.can_repair)
   FOR SHARE;
   IF NOT FOUND THEN
     RAISE EXCEPTION 'AUTOPILOT_PARALLEL_ROLE_SCOPE_INVALID';
   END IF;

   IF EXISTS (
     SELECT 1 FROM autopilot.project_work_item AS work
     WHERE work.mailbox_pr=candidate_target_pr
   ) OR EXISTS (
     SELECT 1 FROM autopilot.role_dispatch_outbox AS outbox
     WHERE outbox.mailbox_pr=candidate_target_pr
        OR outbox.github_dispatch_comment_id=candidate_target_pr::bigint
        OR outbox.codex_command_pr=candidate_target_pr
   ) THEN
     RAISE EXCEPTION 'AUTOPILOT_PARALLEL_CONTROL_PR_FORBIDDEN';
   END IF;

   -- Another nonterminal lineage owns this target.  Skip rather than creating
   -- duplicate work; the manifest receipt still makes the whole batch durable.
   IF EXISTS (
     SELECT 1 FROM autopilot.project_work_item AS work
     WHERE work.target_pr=candidate_target_pr
       AND work.state NOT IN ('DONE','PAUSED')
   ) THEN
     CONTINUE;
   END IF;

   SELECT * INTO registration
   FROM autopilot.register_universal_work_item(
     candidate_work_key,candidate_role,candidate_task_kind,
     candidate_objective,candidate_target_pr,candidate_priority,spec,NULL,
     'AUTOPILOT_PARALLEL_INTAKE',
     'REVIEWED_WORKER_RELEASE:'||p_manifest_sha256
   );
   IF registration.created THEN
     created_count:=created_count+1;
   END IF;
 END LOOP;

 INSERT INTO autopilot.project_work_manifest_receipt(
   manifest_sha256,schema_version,source,item_count,registered_count
 ) VALUES (
   p_manifest_sha256,'AUTOPILOT_PARALLEL_WORK_V1',
   'REVIEWED_WORKER_RELEASE',candidate_count,created_count
 );
 RETURN QUERY SELECT p_manifest_sha256,candidate_count,created_count,false;
END;
$function$;

REVOKE ALL ON FUNCTION autopilot.register_parallel_work_manifest(text,text)
FROM PUBLIC,autopilot_callback;
GRANT EXECUTE ON FUNCTION autopilot.register_parallel_work_manifest(text,text)
TO autopilot_runtime,autopilot_runtime_principal;

-- Keep the global 5+1 fence and add a per-role slot.  Independent work can
-- fill free capacity, while one busy role cannot monopolize every executor.
CREATE OR REPLACE FUNCTION autopilot.claim_project_work_probe(
 p_worker_id text,
 p_lease_seconds integer DEFAULT 60
)
RETURNS TABLE(
 work_item_id uuid,work_key text,repository text,role text,target_pr integer,
 prior_state text,prior_head_sha text,lease_epoch bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,autopilot
AS $function$
DECLARE
 selected autopilot.project_work_item;
 capacity record;
BEGIN
 IF length(COALESCE(p_worker_id,'')) NOT BETWEEN 1 AND 256
    OR p_lease_seconds NOT BETWEEN 30 AND 300 THEN
   RAISE EXCEPTION 'AUTOPILOT_PROJECT_PROBE_LEASE_INVALID';
 END IF;
 IF NOT (SELECT enabled FROM autopilot.project_planner_state WHERE singleton) THEN
   RETURN;
 END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
   RAISE EXCEPTION 'AUTOPILOT_ROLE_WORKER_ISOLATION_UNSUPPORTED';
 END IF;

 PERFORM pg_advisory_xact_lock(
   hashtextextended('autopilot.role-worker-capacity-v1',0)
 );
 SELECT * INTO capacity FROM autopilot.role_worker_capacity_snapshot();
 IF capacity.active_workers+capacity.probe_reservations
      >=capacity.max_active_workers THEN
   UPDATE autopilot.project_planner_state
   SET decision_count=decision_count+1,
       last_decision_code='WAITING_FOR_WORKER_CAPACITY',
       last_work_item_id=NULL,last_decision_at=now()
   WHERE singleton;
   RETURN;
 END IF;

 SELECT item.* INTO selected
 FROM autopilot.project_work_item AS item
 JOIN autopilot.role_registry AS role
   ON role.role_id=item.role AND role.enabled
 WHERE item.state IN ('READY','BLOCKED')
   AND item.not_before<=now()
   AND (item.probe_lease_until IS NULL OR item.probe_lease_until<now())
   AND (item.priority=0 OR
        capacity.active_normal_workers+capacity.normal_probe_reservations
          <capacity.max_normal_workers)
   AND NOT EXISTS (
     SELECT 1 FROM autopilot.task AS active_task
     WHERE active_task.goal_type IN (
       'CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1'
     )
       AND active_task.status IN (
         'NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING'
       )
       AND active_task.goal_json->>'role'=item.role
   )
   AND NOT EXISTS (
     SELECT 1 FROM autopilot.project_work_item AS reserved
     WHERE reserved.work_item_id<>item.work_item_id
       AND reserved.role=item.role
       AND reserved.state IN ('READY','BLOCKED')
       AND reserved.probe_lease_owner IS NOT NULL
       AND reserved.probe_lease_until>=now()
   )
 ORDER BY CASE item.state WHEN 'READY' THEN 0 ELSE 1 END,
          item.priority,item.not_before,item.created_at
 FOR UPDATE OF item SKIP LOCKED
 LIMIT 1;

 IF NOT FOUND THEN
   UPDATE autopilot.project_planner_state
   SET decision_count=decision_count+1,
       last_decision_code=CASE
         WHEN capacity.active_normal_workers+capacity.normal_probe_reservations
                >=capacity.max_normal_workers
          AND EXISTS (
            SELECT 1 FROM autopilot.project_work_item AS item
            JOIN autopilot.role_registry AS role
              ON role.role_id=item.role AND role.enabled
            WHERE item.state IN ('READY','BLOCKED')
              AND item.priority<>0 AND item.not_before<=now()
              AND (item.probe_lease_until IS NULL
                   OR item.probe_lease_until<now())
          ) THEN 'WAITING_FOR_P0_RESERVED_WORKER'
         WHEN EXISTS (
           SELECT 1 FROM autopilot.project_work_item AS item
           JOIN autopilot.role_registry AS role
             ON role.role_id=item.role AND role.enabled
           WHERE item.state IN ('READY','BLOCKED')
             AND item.not_before<=now()
             AND (item.probe_lease_until IS NULL
                  OR item.probe_lease_until<now())
             AND (
               EXISTS (
                 SELECT 1 FROM autopilot.task AS active_task
                 WHERE active_task.goal_type IN (
                   'CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1'
                 )
                   AND active_task.status IN (
                     'NEW','VALIDATING','READY','RUNNING',
                     'WAITING_EXTERNAL','EVALUATING'
                   )
                   AND active_task.goal_json->>'role'=item.role
               )
               OR EXISTS (
                 SELECT 1 FROM autopilot.project_work_item AS reserved
                 WHERE reserved.work_item_id<>item.work_item_id
                   AND reserved.role=item.role
                   AND reserved.state IN ('READY','BLOCKED')
                   AND reserved.probe_lease_owner IS NOT NULL
                   AND reserved.probe_lease_until>=now()
               )
             )
         ) THEN 'WAITING_FOR_ROLE_CAPACITY'
         WHEN NOT EXISTS (
           SELECT 1 FROM autopilot.project_work_item
         ) THEN 'IDLE_NO_REGISTERED_WORK'
         WHEN EXISTS (
           SELECT 1 FROM autopilot.project_work_item WHERE state='ACTIVE'
         ) THEN 'WAITING_FOR_ACTIVE_WORK_ITEM'
         WHEN EXISTS (
           SELECT 1 FROM autopilot.project_work_item
           WHERE state='WAITING_DEPENDENCY'
         ) THEN 'WAITING_FOR_DEPENDENCY'
         WHEN EXISTS (
           SELECT 1 FROM autopilot.project_work_item
           WHERE state IN ('READY','BLOCKED') AND not_before>now()
         ) THEN 'WAITING_FOR_RETRY_WINDOW'
         WHEN EXISTS (
           SELECT 1 FROM autopilot.project_work_item
           WHERE state IN ('READY','BLOCKED') AND probe_lease_until>=now()
         ) THEN 'WAITING_FOR_PROBE_LEASE'
         WHEN NOT EXISTS (
           SELECT 1 FROM autopilot.project_work_item
           WHERE state NOT IN ('DONE','PAUSED')
         ) THEN CASE WHEN EXISTS (
           SELECT 1 FROM autopilot.project_work_item WHERE state='PAUSED'
         ) THEN 'PROJECT_DONE_WITH_PAUSED_BACKLOG' ELSE 'PROJECT_DONE' END
         ELSE 'IDLE_NO_ELIGIBLE_TASK'
       END,
       last_work_item_id=NULL,last_decision_at=now()
   WHERE singleton;
   RETURN;
 END IF;

 UPDATE autopilot.project_work_item AS item
 SET probe_lease_owner=p_worker_id,
     probe_lease_epoch=item.probe_lease_epoch+1,
     probe_lease_until=now()+make_interval(secs=>p_lease_seconds),
     updated_at=now()
 WHERE item.work_item_id=selected.work_item_id
 RETURNING * INTO selected;
 UPDATE autopilot.project_planner_state
 SET decision_count=decision_count+1,last_decision_code='PROBE_CLAIMED',
     last_work_item_id=selected.work_item_id,last_decision_at=now()
 WHERE singleton;
 RETURN QUERY SELECT selected.work_item_id,selected.work_key,
   selected.repository,selected.role,selected.target_pr,selected.state,
   selected.last_observed_head_sha,selected.probe_lease_epoch;
END;
$function$;

REVOKE ALL ON FUNCTION autopilot.claim_project_work_probe(text,integer)
FROM PUBLIC,autopilot_callback;
GRANT EXECUTE ON FUNCTION autopilot.claim_project_work_probe(text,integer)
TO autopilot_runtime,autopilot_runtime_principal;

INSERT INTO public.schema_migration(migration_key)
VALUES('0365_autopilot_parallel_work_intake');

COMMIT;
