\set ON_ERROR_STOP on
BEGIN;
\ir fixtures/codex_send_fixture.sql

DO $test$
DECLARE did uuid; other uuid; attempt uuid:=gen_random_uuid(); binding jsonb;
 original jsonb; blocked_role text;
BEGIN
 FOREACH blocked_role IN ARRAY ARRAY['autopilot_runtime','autopilot_runtime_principal',
   'autopilot_callback','bridge_school_worker'] LOOP
   IF has_function_privilege(blocked_role,'autopilot.claim_codex_command_send(uuid,uuid,jsonb,text)','EXECUTE')
   OR has_function_privilege(blocked_role,'autopilot.codex_command_send_binding(uuid)','EXECUTE')
   OR has_table_privilege(blocked_role,'autopilot.codex_command_send_intent','INSERT')
   OR has_table_privilege(blocked_role,'autopilot.codex_command_send_intent','SELECT') THEN
     RAISE EXCEPTION '0370_EXCESS_PRIVILEGE';
   END IF;
 END LOOP;
 IF EXISTS(SELECT FROM pg_proc p CROSS JOIN LATERAL
     aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
     WHERE p.oid IN ('autopilot.claim_codex_command_send(uuid,uuid,jsonb,text)'::regprocedure,
       'autopilot.codex_command_send_binding(uuid)'::regprocedure)
       AND (a.grantee<>p.proowner OR p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog'])) THEN
   RAISE EXCEPTION '0370_FUNCTION_SECURITY_INVALID';
 END IF;
 did:=pg_temp.codex_send_fixture('once');
 binding:=autopilot.codex_command_send_binding(did);
 IF binding IS NULL THEN RAISE EXCEPTION '0370_VALID_BINDING_MISSING'; END IF;
 SELECT to_jsonb(o) INTO original FROM autopilot.role_dispatch_outbox o WHERE dispatch_id=did;
 IF autopilot.claim_codex_command_send(did,attempt,binding||'{"target_pr":999972}',repeat('e',64)) THEN
   RAISE EXCEPTION '0370_BINDING_CONFLICT_ACCEPTED';
 END IF;
 IF autopilot.claim_codex_command_send(did,attempt,binding,repeat('e',64)) IS DISTINCT FROM true
 OR autopilot.claim_codex_command_send(did,attempt,binding,repeat('e',64)) IS DISTINCT FROM false
 OR autopilot.claim_codex_command_send(did,gen_random_uuid(),binding,repeat('f',64)) IS DISTINCT FROM false THEN
   RAISE EXCEPTION '0370_SEND_RIGHT_REGRANTED';
 END IF;
 IF (SELECT to_jsonb(o) FROM autopilot.role_dispatch_outbox o WHERE dispatch_id=did) IS DISTINCT FROM original
 OR (SELECT count(*) FROM autopilot.codex_command_send_intent WHERE dispatch_id=did)<>1 THEN
   RAISE EXCEPTION '0370_INTENT_CHANGED_DELIVERY_STATE';
 END IF;
 other:=pg_temp.codex_send_fixture('other');
 IF autopilot.claim_codex_command_send(other,attempt,autopilot.codex_command_send_binding(other),repeat('e',64)) THEN
   RAISE EXCEPTION '0370_ATTEMPT_REUSED_FOR_OTHER_DISPATCH';
 END IF;
 binding:=autopilot.codex_command_send_binding(other);
 UPDATE autopilot.project_work_item SET state='PAUSED' WHERE last_task_id=(binding->>'task_id')::uuid;
 IF autopilot.claim_codex_command_send(other,gen_random_uuid(),binding,repeat('e',64)) THEN
   RAISE EXCEPTION '0370_STALE_AUTHORITY_ACCEPTED';
 END IF;
 UPDATE autopilot.project_work_item SET state='ACTIVE',hold_reason='OWNER_HOLD' WHERE last_task_id=(binding->>'task_id')::uuid;
 IF autopilot.codex_command_send_binding(other) IS NOT NULL THEN RAISE EXCEPTION '0370_OWNER_HOLD_BYPASSED'; END IF;
 UPDATE autopilot.project_work_item SET hold_reason=NULL WHERE last_task_id=(binding->>'task_id')::uuid;
 UPDATE autopilot.role_registry SET enabled=false WHERE role_id='AUTOPILOT';
 IF autopilot.codex_command_send_binding(other) IS NOT NULL THEN RAISE EXCEPTION '0370_ROLE_GATE_BYPASSED'; END IF;
 UPDATE autopilot.role_registry SET enabled=true WHERE role_id='AUTOPILOT';
 UPDATE autopilot.role_dispatch_outbox SET delivery_deadline_at=clock_timestamp()+interval '10 seconds' WHERE dispatch_id=other;
 IF autopilot.claim_codex_command_send(other,gen_random_uuid(),binding,repeat('e',64)) THEN
   RAISE EXCEPTION '0370_DEADLINE_MARGIN_BYPASSED';
 END IF;
 IF autopilot.claim_codex_command_send(gen_random_uuid(),gen_random_uuid(),binding,repeat('e',64)) THEN
   RAISE EXCEPTION '0370_UNKNOWN_DISPATCH_ACCEPTED';
 END IF;
 BEGIN
   PERFORM autopilot.claim_codex_command_send(did,gen_random_uuid(),NULL,repeat('e',64));
   RAISE EXCEPTION '0370_NULL_BINDING_ACCEPTED';
 EXCEPTION WHEN raise_exception THEN
   IF SQLERRM<>'CODEX_SEND_INTENT_INVALID' THEN RAISE; END IF;
 END;
END $test$;
ROLLBACK;
