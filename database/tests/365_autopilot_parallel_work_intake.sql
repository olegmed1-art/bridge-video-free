\set ON_ERROR_STOP on
BEGIN;

DO $test$
DECLARE
 manifest jsonb;
 manifest_text text;
 manifest_hash text;
 receipt record;
BEGIN
 manifest:=jsonb_build_object(
   'schema_version','AUTOPILOT_PARALLEL_WORK_V1',
   'repository','olegmed1-art/bridge-video-free',
   'source','REVIEWED_WORKER_RELEASE',
   'items',jsonb_build_array(jsonb_build_object(
     'work_key','sql-0365-parallel-autopilot-audit',
     'role','AUTOPILOT',
     'task_kind','REPOSITORY_AUDIT',
     'objective','Audit one exact repository head without external mutation.',
     'target_pr',999990,
     'priority',20,
     'task_spec_json',jsonb_build_object(
       'execution_scope','REPOSITORY',
       'parallel_safe',true,
       'production_mutation',false,
       'repair_policy','DISABLED',
       'expected_changed_files','[]'::jsonb,
       'required_checks',jsonb_build_array('exact-head repository checks'),
       'forbidden_actions',jsonb_build_array(
         'canon_mutation','credential_access','deploy','drive_write',
         'force_push','main_write','merge','neon_write','paid_action',
         'production_write','real_media_processing','server_write'
       )
     )
   ))
 );
 manifest_text:=manifest::text;
 manifest_hash:=encode(digest(convert_to(manifest_text,'UTF8'),'sha256'),'hex');

 SELECT * INTO STRICT receipt
 FROM autopilot.register_parallel_work_manifest(manifest_text,manifest_hash);
 IF receipt.item_count<>1 OR receipt.registered_count<>1 OR receipt.replayed THEN
   RAISE EXCEPTION 'AUTOPILOT_0365_FIRST_RECEIPT_INVALID';
 END IF;
 IF NOT EXISTS (
   SELECT 1 FROM autopilot.project_work_item
   WHERE work_key='sql-0365-parallel-autopilot-audit'
     AND target_pr=999990
     AND role='AUTOPILOT'
     AND state='READY'
     AND depends_on_work_item_id IS NULL
     AND task_spec_json->>'execution_scope'='REPOSITORY'
     AND task_spec_json->'parallel_safe'='true'::jsonb
     AND task_spec_json->'production_mutation'='false'::jsonb
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0365_WORK_ITEM_INVALID';
 END IF;

 SELECT * INTO STRICT receipt
 FROM autopilot.register_parallel_work_manifest(manifest_text,manifest_hash);
 IF receipt.registered_count<>1 OR NOT receipt.replayed THEN
   RAISE EXCEPTION 'AUTOPILOT_0365_REPLAY_INVALID';
 END IF;

 BEGIN
   manifest:=jsonb_set(
     manifest,'{items,0,task_spec_json,execution_scope}',
     '"OWNER_GATED"'::jsonb
   );
   manifest_text:=manifest::text;
   manifest_hash:=encode(digest(convert_to(manifest_text,'UTF8'),'sha256'),'hex');
   PERFORM * FROM autopilot.register_parallel_work_manifest(
     manifest_text,manifest_hash
   );
   RAISE EXCEPTION 'AUTOPILOT_0365_UNSAFE_SCOPE_ACCEPTED';
 EXCEPTION WHEN raise_exception THEN
   IF SQLERRM NOT LIKE '%AUTOPILOT_PARALLEL_SPEC_INVALID%' THEN
     RAISE;
   END IF;
 END;
END;
$test$;

ROLLBACK;
