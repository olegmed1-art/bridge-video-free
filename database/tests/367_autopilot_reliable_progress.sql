\set ON_ERROR_STOP on
BEGIN;
DO $test$
DECLARE
 manifest jsonb;
 changed jsonb;
 item jsonb;
 items jsonb:='[]';
 r record;
 i integer;
 old_rows jsonb;
 current_rows jsonb;
 wid uuid;
 observed timestamptz;
 action text;
BEGIN
 FOR i IN 1..4 LOOP
   item:=jsonb_build_object(
     'work_key','sql-0367-item-'||i,'role',(ARRAY['VIDEO_QUEUE','VIDEO','AUTOPILOT','SECURITY'])[i],
     'task_kind','REPOSITORY_AUDIT','objective','Read-only SQL regression audit.',
     'target_pr',999970+i,'priority',20,'task_spec_json',jsonb_build_object(
       'execution_scope','REPOSITORY','parallel_safe',true,'production_mutation',false,
       'repair_policy','DISABLED','expected_changed_files','[]'::jsonb,
       'required_checks',jsonb_build_array('exact head'),
       'forbidden_actions',jsonb_build_array('canon_mutation','credential_access','deploy',
        'drive_write','force_push','main_write','merge','neon_write','paid_action',
        'production_write','real_media_processing','server_write')));
   items:=items||jsonb_build_array(item);
 END LOOP;
 manifest:=jsonb_build_object('schema_version','AUTOPILOT_PARALLEL_WORK_V1',
   'repository','olegmed1-art/bridge-video-free','source','REVIEWED_WORKER_RELEASE','items',items);
 SELECT * INTO STRICT r FROM autopilot.register_parallel_work_manifest(
   manifest::text,encode(digest(manifest::text,'sha256'),'hex'));
 IF r.registered_count<>4 THEN RAISE EXCEPTION '0367_INITIAL_BATCH'; END IF;
 UPDATE autopilot.project_work_item SET state='PAUSED',hold_reason='OWNER_HOLD',
   result_code='AUTOPILOT_UNCLASSIFIED_FAILURE',progress_token=repeat('c',64)
 WHERE work_key LIKE 'sql-0367-item-%';
 UPDATE autopilot.project_work_item SET state='DONE',completed_at=now()
 WHERE work_key='sql-0367-item-4';
 SELECT jsonb_agg(to_jsonb(w) ORDER BY work_key) INTO old_rows
 FROM autopilot.project_work_item w WHERE work_key LIKE 'sql-0367-item-%';

 -- Change only the first slot. Others must reuse original provenance exactly.
 changed:=jsonb_set(manifest,'{items,0,work_key}','"sql-0367-new"');
 changed:=jsonb_set(changed,'{items,0,target_pr}','999980');
 SELECT * INTO STRICT r FROM autopilot.register_parallel_work_manifest(
   changed::text,encode(digest(changed::text,'sha256'),'hex'));
 IF r.item_count<>4 OR r.registered_count<>1 OR r.replayed THEN
   RAISE EXCEPTION '0367_UPGRADE_RECEIPT';
 END IF;
 SELECT jsonb_agg(to_jsonb(w) ORDER BY work_key) INTO current_rows
 FROM autopilot.project_work_item w WHERE work_key LIKE 'sql-0367-item-%';
 IF current_rows IS DISTINCT FROM old_rows THEN RAISE EXCEPTION '0367_REUSE_MUTATED_ROWS'; END IF;
 SELECT * INTO STRICT r FROM autopilot.register_parallel_work_manifest(
   changed::text,encode(digest(changed::text,'sha256'),'hex'));
 IF NOT r.replayed THEN RAISE EXCEPTION '0367_REPLAY'; END IF;
 SELECT * INTO STRICT r FROM autopilot.parallel_work_manifest_status(
   encode(digest(changed::text,'sha256'),'hex'));
 IF r.item_count<>4 OR r.registered_count<>1 THEN RAISE EXCEPTION '0367_READ_RECEIPT'; END IF;

 -- A later semantic conflict rolls back an earlier new item in the same batch.
 changed:=jsonb_set(changed,'{items,0,work_key}','"sql-0367-rolled-back"');
 changed:=jsonb_set(changed,'{items,0,target_pr}','999981');
 changed:=jsonb_set(changed,'{items,1,objective}','"Changed semantics."');
 BEGIN
   PERFORM * FROM autopilot.register_parallel_work_manifest(
     changed::text,encode(digest(changed::text,'sha256'),'hex'));
   RAISE EXCEPTION '0367_ACCEPTED_CONFLICT';
 EXCEPTION WHEN raise_exception THEN
   IF SQLERRM<>'AUTOPILOT_PROJECT_WORK_IDEMPOTENCY_CONFLICT' THEN RAISE; END IF;
 END;
 IF EXISTS (SELECT 1 FROM autopilot.project_work_item WHERE work_key='sql-0367-rolled-back') THEN
   RAISE EXCEPTION '0367_NONATOMIC_BATCH';
 END IF;

 -- Alternate keys cannot escape another lineage's PAUSED owner hold.
 changed:=jsonb_set(manifest,'{items,0,work_key}','"sql-0367-no-hold-bypass"');
 SELECT * INTO STRICT r FROM autopilot.register_parallel_work_manifest(
   changed::text,encode(digest(changed::text,'sha256'),'hex'));
 IF r.registered_count<>0 OR EXISTS (SELECT 1 FROM autopilot.project_work_item
   WHERE work_key='sql-0367-no-hold-bypass') THEN RAISE EXCEPTION '0367_HOLD_BYPASS'; END IF;

 SELECT work_item_id,updated_at INTO wid,observed
 FROM autopilot.project_work_item WHERE work_key='sql-0367-item-2';
 action:=autopilot.reconcile_paused_project_work_cas(
   wid,observed-interval '1 second',repeat('d',64),'MERGED','Stale evidence');
 IF action<>'NO_CHANGE' THEN RAISE EXCEPTION '0367_CAS_FAILED'; END IF;
 -- Even old clients cannot replace an owner reason with stale retryable code.
 action:=autopilot.reconcile_paused_project_work(
   wid,repeat('e',64),'MERGED','BOUNDED_DEFECT','Stale retry reason');
 IF action<>'OWNER_HOLD' THEN RAISE EXCEPTION '0367_STALE_REASON_BYPASS'; END IF;
 IF (SELECT state FROM autopilot.project_work_item WHERE work_item_id=wid)<>'PAUSED' THEN
   RAISE EXCEPTION '0367_OWNER_HOLD_LOST';
 END IF;
 IF has_table_privilege('autopilot_runtime','autopilot.project_work_manifest_receipt','SELECT')
    OR NOT has_function_privilege('autopilot_runtime',
      'autopilot.parallel_work_manifest_status(text)','EXECUTE') THEN
   RAISE EXCEPTION '0367_RECEIPT_PRIVILEGES';
 END IF;
END $test$;
ROLLBACK;
