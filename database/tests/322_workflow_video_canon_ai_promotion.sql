\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE r record; v_failed boolean;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.schema_migration WHERE migration_key='0322_workflow_video_canon_ai_promotion') THEN
    RAISE EXCEPTION 'VIDEO_CANON_MIGRATION_MISSING';
  END IF;
  FOR r IN SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication
             FROM pg_roles WHERE rolname=ANY(ARRAY[
               'bridge_school_canon_verifier','bridge_school_canon_semantic_verifier',
               'bridge_school_canon_bridge_verifier','bridge_school_canon_firewall_verifier',
               'bridge_school_canon_control_verifier','bridge_school_canon_promoter',
               'bridge_school_canon_restorer'
             ]) LOOP
    IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication THEN
      RAISE EXCEPTION 'VIDEO_CANON_UNSAFE_ROLE_%',r.rolname;
    END IF;
  END LOOP;
  IF has_function_privilege('bridge_school_worker',
       'bidding.activate_ai_verified_video_canon(uuid,uuid,text)','EXECUTE')
     OR has_function_privilege('bridge_school_canon_verifier',
       'bidding.activate_ai_verified_video_canon(uuid,uuid,text)','EXECUTE')
     OR NOT has_function_privilege('bridge_school_canon_promoter',
       'bidding.activate_ai_verified_video_canon(uuid,uuid,text)','EXECUTE')
     OR has_function_privilege('bridge_school_worker',
       'bidding.restore_ai_verified_video_canon(uuid,text,text)','EXECUTE')
     OR has_function_privilege('bridge_school_canon_promoter',
       'bidding.restore_ai_verified_video_canon(uuid,text,text)','EXECUTE')
     OR NOT has_function_privilege('bridge_school_canon_restorer',
       'bidding.restore_ai_verified_video_canon(uuid,text,text)','EXECUTE') THEN
    RAISE EXCEPTION 'VIDEO_CANON_PROMOTION_RPC_ACL_INVALID';
  END IF;
  IF has_table_privilege('bridge_school_worker','bidding.video_canon_ai_verification','INSERT')
     OR has_table_privilege('bridge_school_canon_promoter','bidding.video_canon_ai_verification','INSERT')
     OR has_table_privilege('bridge_school_canon_verifier','bidding.video_canon_ai_verification','INSERT')
     OR NOT has_table_privilege('bridge_school_canon_semantic_verifier','bidding.video_canon_ai_verification','INSERT')
     OR NOT has_table_privilege('bridge_school_canon_bridge_verifier','bidding.video_canon_ai_verification','INSERT')
     OR NOT has_table_privilege('bridge_school_canon_firewall_verifier','bidding.video_canon_ai_verification','INSERT')
     OR NOT has_table_privilege('bridge_school_canon_control_verifier','bidding.video_canon_ai_verification','INSERT')
     OR has_table_privilege('bridge_school_worker','bidding.video_canon_ai_verification_bundle','INSERT')
     OR has_table_privilege('bridge_school_canon_promoter','bidding.video_canon_ai_verification_bundle','INSERT')
     OR has_table_privilege('bridge_school_canon_semantic_verifier','bidding.video_canon_ai_verification_bundle','INSERT')
     OR NOT has_table_privilege('bridge_school_canon_verifier','bidding.video_canon_ai_verification_bundle','INSERT') THEN
    RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_ACL_INVALID';
  END IF;
  IF (SELECT count(*) FROM bidding.video_canon_verifier_registry WHERE status='active')<>4 THEN
    RAISE EXCEPTION 'VIDEO_CANON_VERIFIER_REGISTRY_INVALID';
  END IF;
  IF NOT EXISTS (
       SELECT 1 FROM information_schema.columns
        WHERE table_schema='bidding' AND table_name='video_canon_ai_verification'
          AND column_name='execution_principal' AND is_nullable='NO'
     ) OR NOT EXISTS (
       SELECT 1 FROM information_schema.columns
        WHERE table_schema='bidding' AND table_name='video_canon_ai_verification'
          AND column_name='canon_snapshot_sha256'
     ) THEN
    RAISE EXCEPTION 'VIDEO_CANON_EXECUTION_OR_SNAPSHOT_BINDING_MISSING';
  END IF;
  IF pg_has_role('bridge_school_canon_semantic_verifier','bridge_school_reader','member')
     OR pg_has_role('bridge_school_canon_bridge_verifier','bridge_school_reader','member')
     OR pg_has_role('bridge_school_canon_firewall_verifier','bridge_school_reader','member')
     OR pg_has_role('bridge_school_canon_control_verifier','bridge_school_reader','member')
     OR pg_has_role('bridge_school_canon_promoter','bridge_school_reader','member')
     OR pg_has_role('bridge_school_canon_restorer','bridge_school_reader','member')
     OR has_table_privilege('bridge_school_canon_semantic_verifier','public.person','SELECT')
     OR has_table_privilege('bridge_school_canon_promoter','public.person','SELECT')
     OR has_table_privilege('bridge_school_canon_verifier','public.analysis_candidate','SELECT')
     OR has_table_privilege('bridge_school_canon_semantic_verifier','public.analysis_candidate','SELECT')
     OR has_table_privilege('bridge_school_canon_bridge_verifier','public.analysis_candidate','SELECT')
     OR has_table_privilege('bridge_school_canon_firewall_verifier','public.analysis_candidate','SELECT')
     OR has_table_privilege('bridge_school_canon_control_verifier','public.analysis_candidate','SELECT')
     OR NOT has_table_privilege('bridge_school_canon_semantic_verifier','bidding.video_canon_bound_candidate','SELECT')
     OR NOT has_table_privilege('bridge_school_canon_bridge_verifier','bidding.video_canon_bound_candidate','SELECT')
     OR NOT has_table_privilege('bridge_school_canon_firewall_verifier','bidding.video_canon_bound_candidate','SELECT')
     OR NOT has_table_privilege('bridge_school_canon_control_verifier','bidding.video_canon_bound_candidate','SELECT') THEN
    RAISE EXCEPTION 'VIDEO_CANON_VERIFIER_OVERBROAD_READ_ACCESS';
  END IF;
  IF has_table_privilege('bridge_school_canon_promoter','public.canon_activation','INSERT')
     OR has_table_privilege('bridge_school_canon_promoter','bidding.runtime_activation','INSERT')
     OR has_table_privilege('bridge_school_canon_promoter','bidding.video_canon_ai_promotion_receipt','INSERT')
     OR has_table_privilege('bridge_school_canon_restorer','public.canon_activation','UPDATE')
     OR has_table_privilege('bridge_school_canon_restorer','bidding.runtime_activation','UPDATE')
     OR has_table_privilege('bridge_school_canon_restorer','bidding.video_canon_ai_restore_receipt','INSERT') THEN
    RAISE EXCEPTION 'VIDEO_CANON_DIRECT_WRITE_NOT_BLOCKED';
  END IF;
  IF has_table_privilege('bridge_school_worker','bidding.video_correction_review_receipt','INSERT')
     OR has_table_privilege('bridge_school_app','bidding.video_correction_review_receipt','INSERT')
     OR NOT has_table_privilege('bridge_school_worker','bidding.video_correction_review_receipt','SELECT')
     OR NOT has_table_privilege('bridge_school_canon_control_verifier','bidding.video_correction_review_receipt','INSERT') THEN
    RAISE EXCEPTION 'VIDEO_CORRECTION_REVIEW_ACL_INVALID';
  END IF;
  IF NOT (bidding.contains_forbidden_hidden_value(
       '{"notes":"N:AKQJ.T98.765.432 E:T987.654.32.AKQ"}'::jsonb
     )) OR NOT (bidding.contains_forbidden_hidden_value(
       '{"notes":"North''s hand is AKQJ.T98.765.432; East’s hand is JT9.AKQ.JT9.876"}'::jsonb
     )) OR bidding.contains_forbidden_hidden_value(
       '{"meaning":"shows at least five hearts"}'::jsonb
     ) THEN
    RAISE EXCEPTION 'VIDEO_CANON_HIDDEN_VALUE_FIREWALL_INVALID';
  END IF;
  IF bidding.current_school_canon_snapshot_sha256(
       (SELECT school_id FROM public.school ORDER BY school_id LIMIT 1)
     ) !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'VIDEO_CANON_SNAPSHOT_DIGEST_INVALID';
  END IF;

  v_failed:=false;
  BEGIN
    PERFORM bidding.activate_ai_verified_video_canon(
      uuidv7(),uuidv7(),repeat('a',64)
    );
  EXCEPTION WHEN check_violation THEN v_failed:=true;
  END;
  IF NOT v_failed THEN RAISE EXCEPTION 'VIDEO_CANON_MISSING_CANDIDATE_NOT_BLOCKED'; END IF;
END $$;

DO $$
DECLARE
  v_school uuid:=uuidv7();
  v_source uuid:=uuidv7();
  v_orphan_source uuid:=uuidv7();
  v_future_source uuid:=uuidv7();
  v_item uuid:=uuidv7();
  v_orphan_item uuid:=uuidv7();
  v_old_version uuid:=uuidv7();
  v_new_version uuid:=uuidv7();
  v_orphan_version uuid:=uuidv7();
  v_old_candidate uuid:=uuidv7();
  v_candidate uuid:=uuidv7();
  v_old_rule uuid:=uuidv7();
  v_new_rule uuid:=uuidv7();
  v_orphan_rule uuid:=uuidv7();
  v_old_canon uuid:=uuidv7();
  v_new_canon uuid:=uuidv7();
  v_orphan_canon uuid:=uuidv7();
  v_old_runtime uuid:=uuidv7();
  v_new_runtime uuid:=uuidv7();
  v_promotion uuid:=uuidv7();
  v_old_promotion uuid:=uuidv7();
  v_policy uuid:=uuidv7();
  v_future_policy uuid:=uuidv7();
  v_restore uuid;
  v_repeat uuid;
  v_rule_id uuid;
  v_test_id uuid;
  v_test_type text;
  v_snapshot_before text;
  v_snapshot_after text;
  v_restore_failed boolean:=false;
  v_policy_failed boolean:=false;
  v_bad_bundle_failed boolean:=false;
  v_late_verification_failed boolean:=false;
  v_bundle_id uuid:=uuidv7();
  v_good_bundle jsonb;
  v_bad_bundle jsonb;
  v_good_bundle_hash text;
  v_new_from timestamptz:=statement_timestamp()-interval '1 hour';
BEGIN
  INSERT INTO public.school(school_id,stable_name)
  VALUES (v_school,'video-canon-restore-test-'||v_school::text);
  INSERT INTO public.knowledge_item(knowledge_item_id,school_id,stable_key,knowledge_type,title)
  VALUES (v_item,v_school,'restore-item-'||v_item::text,'bidding_rule','restore test');
  INSERT INTO public.knowledge_item(knowledge_item_id,school_id,stable_key,knowledge_type,title)
  VALUES (v_orphan_item,v_school,'orphan-item-'||v_orphan_item::text,'bidding_rule','orphan Canon test');
  INSERT INTO public.knowledge_version(
    knowledge_version_id,knowledge_item_id,version_no,content,authority_class,
    review_status,bidding_system_key,level_scope,status
  ) VALUES
    (v_old_version,v_item,1,'{}','school_canon','approved','natural-v1',
      '{"level_key":"beginner-1"}','approved'),
    (v_new_version,v_item,2,'{}','school_canon','approved','natural-v1',
      '{"level_key":"beginner-1"}','approved');
  INSERT INTO public.knowledge_version(
    knowledge_version_id,knowledge_item_id,version_no,content,authority_class,
    review_status,bidding_system_key,level_scope,status
  ) VALUES (
    v_orphan_version,v_orphan_item,1,'{}','school_canon','approved','natural-v1',
    '{"level_key":"beginner-1"}','approved'
  );
  INSERT INTO public.source(source_id,school_id,source_type,title,status)
  VALUES (v_source,v_school,'video','restore test source','active');
  INSERT INTO public.source(source_id,school_id,source_type,title,status)
  VALUES (v_orphan_source,v_school,'video','orphan Canon source','active');
  INSERT INTO public.source(source_id,school_id,source_type,title,status)
  VALUES (v_future_source,v_school,'video','future policy source','active');
  INSERT INTO public.knowledge_version_source(knowledge_version_id,source_id)
  VALUES (v_old_version,v_source),(v_new_version,v_source),
    (v_orphan_version,v_orphan_source);
  INSERT INTO bidding.video_canon_source_policy(
    video_canon_source_policy_id,school_id,source_id,source_sha256,video_file_id,
    teacher_ids,semantic_scopes,system_profile,learner_level,policy_version,
    authorization_evidence_sha256,status,valid_from
  ) VALUES (
    v_policy,v_school,v_source,repeat('e',64),'restore-video-old',
    ARRAY['teacher:restore'],ARRAY['restore-scope'],'natural-v1','beginner-1',
    'school-video-auto-canon-v1',repeat('9',64),'active',
    statement_timestamp()-interval '2 years'
  );
  INSERT INTO bidding.video_canon_source_policy(
    video_canon_source_policy_id,school_id,source_id,source_sha256,video_file_id,
    teacher_ids,semantic_scopes,system_profile,learner_level,policy_version,
    authorization_evidence_sha256,status,valid_from
  ) VALUES (
    v_future_policy,v_school,v_future_source,repeat('d',64),'future-video',
    ARRAY['teacher:restore'],ARRAY['future-scope'],'natural-v1','beginner-1',
    'school-video-auto-canon-v1',repeat('8',64),'active',
    statement_timestamp()+interval '1 day'
  );
  UPDATE bidding.video_canon_source_policy SET status='revoked'
   WHERE video_canon_source_policy_id=v_future_policy;
  IF NOT EXISTS (
      SELECT 1 FROM bidding.video_canon_source_policy
       WHERE video_canon_source_policy_id=v_future_policy AND status='revoked'
         AND valid_to IS NULL AND retired_at=statement_timestamp()
  ) THEN
    RAISE EXCEPTION 'VIDEO_CANON_FUTURE_SOURCE_POLICY_REVOCATION_FAILED';
  END IF;
  INSERT INTO bidding.rule(
    rule_id,school_id,knowledge_version_id,rule_key,rule_kind,action,lifecycle_status
  ) VALUES
    (v_old_rule,v_school,v_old_version,'restore-old-'||v_old_rule::text,'bid','{"call":"1H"}','validated'),
    (v_new_rule,v_school,v_new_version,'restore-new-'||v_new_rule::text,'bid','{"call":"2H"}','validated'),
    (v_orphan_rule,v_school,v_orphan_version,'orphan-'||v_orphan_rule::text,'bid','{"call":"1S"}','validated');
  FOREACH v_rule_id IN ARRAY ARRAY[v_old_rule,v_new_rule,v_orphan_rule]
  LOOP
    FOREACH v_test_type IN ARRAY ARRAY['positive','negative','boundary','hidden_information']
    LOOP
      v_test_id:=uuidv7();
      INSERT INTO bidding.rule_test(
        rule_test_id,school_id,rule_id,test_key,test_type,fixture,expected,enabled
      ) VALUES (
        v_test_id,v_school,v_rule_id,v_test_type||'-'||v_rule_id::text,
        v_test_type,'{}','{}',true
      );
      INSERT INTO bidding.rule_test_run(
        school_id,rule_test_id,result,result_details,method_version
      ) VALUES (v_school,v_test_id,'pass','{}','restore-test-v1');
    END LOOP;
  END LOOP;
  INSERT INTO public.analysis_candidate(
    analysis_candidate_id,school_id,candidate_type,stable_key,input_fingerprint,
    quality_status,promotion_status,payload,payload_hash,method_version,source_id
  ) VALUES
  (
    v_old_candidate,v_school,'video_school_canon_candidate','restore-'||v_old_candidate::text,
    repeat('f',64),'AI_VERIFIED','promoted',jsonb_build_object(
      'source_class','SCHOOL_PRIMARY_EVIDENCE',
      'source',jsonb_build_object('source_sha256',repeat('e',64),'video_file_id','restore-video-old'),
      'teacher_assertion',jsonb_build_object('speaker_id','teacher:restore'),
      'source_authorization',jsonb_build_object(
        'policy_version','school-video-auto-canon-v1',
        'authorization_evidence_sha256',repeat('9',64)
      )
    ),repeat('1',64),'video-canon-evidence-v2',v_source
  ),(
    v_candidate,v_school,'video_school_canon_candidate','restore-'||v_candidate::text,
    repeat('f',64),'AI_VERIFIED','promoted','{}',repeat('a',64),'video-canon-evidence-v2',v_source
  );
  SELECT jsonb_build_object(
    'schema','video-canon-ai-promotion-v1',
    'policy_version','school-video-auto-canon-v1',
    'candidate_payload_hash',repeat('a',64),'candidate_payload','{}'::jsonb,
    'canon_snapshot_sha256',repeat('0',64),
    'checks',jsonb_agg(jsonb_build_object(
      'check_id',check_id,'result','PASS','execution_principal',session_user
    ) ORDER BY ordinal),
    'effective_period','{}'::jsonb,'rollback','{}'::jsonb
  ) INTO v_good_bundle
  FROM (VALUES
    (1,'SOURCE_AUTHORITY'),(2,'SOURCE_BINDING'),(3,'SPEAKER_IDENTITY'),
    (4,'TRANSCRIPT_BINDING'),(5,'SEMANTIC_PARSE'),(6,'EXPLANATION_COMPLETENESS'),
    (7,'BRIDGE_LOGIC'),(8,'HIDDEN_INFORMATION_FIREWALL'),(9,'POSITIVE_TESTS'),
    (10,'NEGATIVE_TESTS'),(11,'BOUNDARY_TESTS'),(12,'INTERFERENCE_TESTS'),
    (13,'CANON_REGRESSION'),(14,'CANON_INTEGRITY'),(15,'CANON_CONFLICT_SCAN'),
    (16,'ROLLBACK_RESTORE')
  ) AS required_checks(ordinal,check_id);
  v_good_bundle_hash:=encode(public.digest(convert_to(v_good_bundle::text,'UTF8'),'sha256'),'hex');
  INSERT INTO bidding.video_canon_ai_verification_bundle(
    video_canon_ai_verification_bundle_id,school_id,analysis_candidate_id,
    candidate_payload_hash,verification_bundle_sha256,bundle_canonical_json,bundle_payload
  ) VALUES (
    v_bundle_id,v_school,v_candidate,repeat('a',64),v_good_bundle_hash,
    v_good_bundle::text,v_good_bundle
  );
  v_bad_bundle:=jsonb_set(v_good_bundle,'{checks,0,result}','"FAIL"'::jsonb);
  BEGIN
    INSERT INTO bidding.video_canon_ai_verification_bundle(
      school_id,analysis_candidate_id,candidate_payload_hash,
      verification_bundle_sha256,bundle_canonical_json,bundle_payload
    ) VALUES (
      v_school,v_candidate,repeat('a',64),
      encode(public.digest(convert_to(v_bad_bundle::text,'UTF8'),'sha256'),'hex'),
      v_bad_bundle::text,v_bad_bundle
    );
  EXCEPTION WHEN check_violation THEN
    v_bad_bundle_failed:=true;
  END;
  IF NOT v_bad_bundle_failed THEN
    RAISE EXCEPTION 'VIDEO_CANON_NONPASS_BUNDLE_NOT_BLOCKED';
  END IF;
  INSERT INTO public.canon_activation(
    canon_activation_id,knowledge_version_id,scope_key,valid_from,valid_to,status
  ) VALUES
    (v_old_canon,v_old_version,'restore-scope',statement_timestamp()-interval '1 year',v_new_from,'superseded'),
    (v_new_canon,v_new_version,'restore-scope',v_new_from,NULL,'active');
  INSERT INTO public.canon_activation(
    canon_activation_id,knowledge_version_id,scope_key,valid_from,valid_to,status
  ) VALUES (
    v_orphan_canon,v_orphan_version,'orphan-scope',v_new_from,NULL,'active'
  );
  INSERT INTO bidding.runtime_activation(
    runtime_activation_id,school_id,rule_id,authority_lane,canon_activation_id,
    scope_key,valid_from,valid_to,status
  ) VALUES
    (v_old_runtime,v_school,v_old_rule,'school_canon',v_old_canon,'restore-scope',
      statement_timestamp()-interval '1 year',v_new_from,'superseded'),
    (v_new_runtime,v_school,v_new_rule,'school_canon',v_new_canon,'restore-scope',
      v_new_from,NULL,'active');
  INSERT INTO bidding.video_canon_ai_promotion_receipt(
    video_canon_ai_promotion_receipt_id,school_id,analysis_candidate_id,
    candidate_payload_hash,verification_bundle_sha256,policy_version,scope_key,
    rule_content_sha256,rule_id,canon_activation_id,runtime_activation_id,
    promotion_mode,human_approval_required
  ) VALUES (
    v_old_promotion,v_school,v_old_candidate,repeat('1',64),repeat('2',64),
    'school-video-auto-canon-v1','restore-scope',repeat('3',64),v_old_rule,
    v_old_canon,v_old_runtime,'AI_VERIFIED_TEACHER_VIDEO',false
  );
  v_snapshot_before:=bidding.current_school_canon_snapshot_sha256(v_school);
  UPDATE public.source SET status='revoked' WHERE source_id=v_orphan_source;
  v_snapshot_after:=bidding.current_school_canon_snapshot_sha256(v_school);
  IF v_snapshot_before=v_snapshot_after THEN
    RAISE EXCEPTION 'VIDEO_CANON_SNAPSHOT_IGNORES_CANON_WITHOUT_RUNTIME_SOURCE';
  END IF;
  UPDATE public.source SET status='active' WHERE source_id=v_orphan_source;
  v_snapshot_before:=bidding.current_school_canon_snapshot_sha256(v_school);
  UPDATE bidding.rule SET action='{"call":"2S"}' WHERE rule_id=v_orphan_rule;
  v_snapshot_after:=bidding.current_school_canon_snapshot_sha256(v_school);
  IF v_snapshot_before=v_snapshot_after THEN
    RAISE EXCEPTION 'VIDEO_CANON_SNAPSHOT_IGNORES_CANON_WITHOUT_RUNTIME_RULE';
  END IF;
  v_snapshot_before:=v_snapshot_after;
  SELECT rule_test_id INTO v_test_id FROM bidding.rule_test
   WHERE rule_id=v_orphan_rule AND test_type='positive';
  INSERT INTO bidding.rule_test_run(
    school_id,rule_test_id,result,result_details,method_version
  ) VALUES (v_school,v_test_id,'fail','{}','restore-test-v1');
  v_snapshot_after:=bidding.current_school_canon_snapshot_sha256(v_school);
  IF v_snapshot_before=v_snapshot_after THEN
    RAISE EXCEPTION 'VIDEO_CANON_SNAPSHOT_IGNORES_CANON_WITHOUT_RUNTIME_TEST_STATE';
  END IF;
  INSERT INTO bidding.video_canon_ai_promotion_receipt(
    video_canon_ai_promotion_receipt_id,school_id,analysis_candidate_id,
    candidate_payload_hash,verification_bundle_sha256,policy_version,scope_key,
    rule_content_sha256,rule_id,canon_activation_id,runtime_activation_id,
    superseded_canon_activation_id,superseded_canon_valid_to,
    superseded_runtime_activation_ids,superseded_runtime_state,
    promotion_mode,human_approval_required
  ) VALUES (
    v_promotion,v_school,v_candidate,repeat('a',64),v_good_bundle_hash,
    'school-video-auto-canon-v1','restore-scope',repeat('b',64),v_new_rule,
    v_new_canon,v_new_runtime,v_old_canon,NULL,ARRAY[v_old_runtime],
    jsonb_build_array(jsonb_build_object(
      'runtime_activation_id',v_old_runtime,'valid_to',NULL
    )),'AI_VERIFIED_TEACHER_VIDEO',false
  );
  BEGIN
    INSERT INTO bidding.video_canon_ai_verification(
      school_id,analysis_candidate_id,video_canon_ai_verification_bundle_id,
      candidate_payload_hash,verification_bundle_sha256,check_id,result,
      verifier_family,verifier_version,execution_principal,assurance_level,evidence_sha256
    ) VALUES (
      v_school,v_candidate,v_bundle_id,repeat('a',64),v_good_bundle_hash,
      'SOURCE_AUTHORITY','PASS','formal-checker','late-v1',session_user,'I1',repeat('7',64)
    );
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    v_late_verification_failed:=true;
  END;
  IF NOT v_late_verification_failed THEN
    RAISE EXCEPTION 'VIDEO_CANON_LATE_VERIFICATION_INSERT_NOT_BLOCKED';
  END IF;

  BEGIN
    UPDATE bidding.video_canon_source_policy
       SET status='revoked',valid_to=statement_timestamp()
     WHERE video_canon_source_policy_id=v_policy;
    PERFORM bidding.restore_ai_verified_video_canon(
      v_promotion,v_good_bundle_hash,repeat('d',64)
    );
  EXCEPTION WHEN check_violation THEN
    v_policy_failed:=true;
  END;
  IF NOT v_policy_failed OR EXISTS (
       SELECT 1 FROM bidding.video_canon_ai_restore_receipt
        WHERE video_canon_ai_promotion_receipt_id=v_promotion
     ) THEN
    RAISE EXCEPTION 'VIDEO_CANON_REVOKED_SOURCE_RESTORE_NOT_BLOCKED';
  END IF;

  SELECT rule_test_id INTO v_test_id FROM bidding.rule_test
   WHERE rule_id=v_old_rule AND test_type='positive';
  INSERT INTO bidding.rule_test_run(
    school_id,rule_test_id,result,result_details,method_version
  ) VALUES (v_school,v_test_id,'fail','{}','restore-test-v1');
  BEGIN
    PERFORM bidding.restore_ai_verified_video_canon(
      v_promotion,v_good_bundle_hash,repeat('d',64)
    );
  EXCEPTION WHEN check_violation THEN
    v_restore_failed:=true;
  END;
  IF NOT v_restore_failed OR EXISTS (
       SELECT 1 FROM bidding.video_canon_ai_restore_receipt
        WHERE video_canon_ai_promotion_receipt_id=v_promotion
     ) THEN
    RAISE EXCEPTION 'VIDEO_CANON_RESTORE_VALIDATION_FAILURE_NOT_ATOMIC';
  END IF;
  INSERT INTO bidding.rule_test_run(
    school_id,rule_test_id,result,result_details,method_version
  ) VALUES (v_school,v_test_id,'pass','{}','restore-test-v1');

  v_restore:=bidding.restore_ai_verified_video_canon(
    v_promotion,v_good_bundle_hash,repeat('d',64)
  );
  v_repeat:=bidding.restore_ai_verified_video_canon(
    v_promotion,v_good_bundle_hash,repeat('d',64)
  );
  IF v_restore<>v_repeat
     OR NOT EXISTS (
       SELECT 1 FROM public.canon_activation
        WHERE canon_activation_id=v_new_canon AND status='revoked'
     ) OR NOT EXISTS (
       SELECT 1 FROM bidding.runtime_activation
        WHERE runtime_activation_id=v_new_runtime AND status='revoked'
     ) OR NOT EXISTS (
       SELECT 1 FROM public.canon_activation
        WHERE canon_activation_id=v_old_canon AND status='active' AND valid_to IS NULL
     ) OR NOT EXISTS (
       SELECT 1 FROM bidding.runtime_activation
        WHERE runtime_activation_id=v_old_runtime AND status='active' AND valid_to IS NULL
     ) OR NOT EXISTS (
       SELECT 1 FROM bidding.video_canon_ai_restore_receipt
        WHERE video_canon_ai_restore_receipt_id=v_restore
          AND restored_canon_activation_id=v_old_canon
          AND restored_runtime_activation_ids=ARRAY[v_old_runtime]
     ) THEN
    RAISE EXCEPTION 'VIDEO_CANON_RECEIPT_BOUND_RESTORE_FAILED';
  END IF;
END $$;

ROLLBACK;
