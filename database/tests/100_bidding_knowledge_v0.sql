\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_other_school uuid;
    v_source uuid;
    v_other_source uuid;
    v_other_evidence uuid;
    v_item uuid;
    v_version uuid;
    v_rule uuid;
    v_canon_activation uuid;
    v_runtime_activation uuid;
    v_test uuid;
    v_ingestion uuid;
    v_external_item uuid;
    v_external_version uuid;
    v_external_rule uuid;
    v_other_item uuid;
    v_other_version uuid;
    v_gap uuid;
    v_failed boolean;
    v_count integer;
BEGIN
    SELECT school_id INTO v_school
      FROM public.school
     WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'SMOKE_SCHOOL_MISSING'; END IF;

    INSERT INTO public.school(stable_name,status)
    VALUES ('CI bidding isolated school 20260827','active')
    RETURNING school_id INTO v_other_school;

    INSERT INTO public.source(
        school_id,source_type,title,canonical_locator,trust_class,status
    ) VALUES (
        v_school,'document','CI approved bidding source','ci://bidding-source','director_approved','active'
    ) RETURNING source_id INTO v_source;

    INSERT INTO public.source(
        school_id,source_type,title,canonical_locator,trust_class,status
    ) VALUES (
        v_other_school,'document','CI other-school source','ci://other-source','test','active'
    ) RETURNING source_id INTO v_other_source;

    INSERT INTO public.evidence(
        school_id,evidence_type,source_id,locator,confidence_class,quality_status
    ) VALUES (
        v_other_school,'document_fragment',v_other_source,'{"page":1}'::jsonb,'HIGH','verified'
    ) RETURNING evidence_id INTO v_other_evidence;

    INSERT INTO public.knowledge_item(
        school_id,stable_key,knowledge_type,title,status
    ) VALUES (
        v_school,'ci-bidding-rule','bidding_rule','CI bidding rule','active'
    ) RETURNING knowledge_item_id INTO v_item;

    INSERT INTO public.knowledge_version(
        knowledge_item_id,version_no,content,authority_class,review_status,
        bidding_system_key,agreement_scope,level_scope,method_version,provenance,status
    ) VALUES (
        v_item,1,'{"source_block":"CI"}'::jsonb,'school_canon','reviewed',
        'natural-v1','{"scope":"ci"}'::jsonb,'{}'::jsonb,'ci-smoke-v1',
        '{"class":"DIRECT"}'::jsonb,'candidate'
    ) RETURNING knowledge_version_id INTO v_version;

    INSERT INTO public.knowledge_version_source(
        knowledge_version_id,source_id,relation_type,source_locator
    ) VALUES (v_version,v_source,'derived_from','{"page":1,"block":"CI"}'::jsonb);

    INSERT INTO bidding.rule(
        school_id,knowledge_version_id,rule_key,rule_kind,auction_pattern,
        hand_constraints,public_context_constraints,action,meaning,public_inference,
        forcing_semantics,priority,specificity,explanation,lifecycle_status,method_version
    ) VALUES (
        v_school,v_version,'ci.rule.1','bid','{"calls":[]}'::jsonb,
        '{"hcp":{"min":12,"max":22}}'::jsonb,'{}'::jsonb,'{"call":"1H"}'::jsonb,
        '{"source":"ci"}'::jsonb,'{"hearts":{"min":5}}'::jsonb,
        '{"state":"F1"}'::jsonb,100,10,'{"why":"ci"}'::jsonb,'validated','ci-smoke-v1'
    ) RETURNING rule_id INTO v_rule;

    INSERT INTO public.canon_activation(
        knowledge_version_id,scope_key,valid_from,approval_provenance,status
    ) VALUES (
        v_version,'ci',now(),'{"decision":"ci"}'::jsonb,'active'
    ) RETURNING canon_activation_id INTO v_canon_activation;

    v_failed := false;
    BEGIN
        INSERT INTO bidding.runtime_activation(
            school_id,rule_id,authority_lane,canon_activation_id,scope_key,status
        ) VALUES (v_school,v_rule,'school_canon',v_canon_activation,'ci','active');
    EXCEPTION WHEN check_violation THEN v_failed := true;
    END;
    IF NOT v_failed THEN RAISE EXCEPTION 'SMOKE_ACTIVATION_WITHOUT_TESTS_NOT_BLOCKED'; END IF;

    INSERT INTO bidding.rule_test(school_id,rule_id,test_key,test_type,fixture,expected,method_version)
    VALUES (v_school,v_rule,'positive','positive','{"hand":"positive"}'::jsonb,'{"applicable":true}'::jsonb,'ci-smoke-v1')
    RETURNING rule_test_id INTO v_test;
    INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,result_details,method_version)
    VALUES (v_school,v_test,'pass','{"ok":true}'::jsonb,'ci-smoke-v1');

    INSERT INTO bidding.rule_test(school_id,rule_id,test_key,test_type,fixture,expected,method_version)
    VALUES (v_school,v_rule,'negative','negative','{"hand":"negative"}'::jsonb,'{"applicable":false}'::jsonb,'ci-smoke-v1')
    RETURNING rule_test_id INTO v_test;
    INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,result_details,method_version)
    VALUES (v_school,v_test,'pass','{"ok":true}'::jsonb,'ci-smoke-v1');

    INSERT INTO bidding.rule_test(school_id,rule_id,test_key,test_type,fixture,expected,method_version)
    VALUES (v_school,v_rule,'boundary','boundary','{"hand":"boundary"}'::jsonb,'{"applicable":true}'::jsonb,'ci-smoke-v1')
    RETURNING rule_test_id INTO v_test;
    INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,result_details,method_version)
    VALUES (v_school,v_test,'pass','{"ok":true}'::jsonb,'ci-smoke-v1');

    INSERT INTO bidding.rule_test(school_id,rule_id,test_key,test_type,fixture,expected,method_version)
    VALUES (
        v_school,v_rule,'hidden','hidden_information',
        '{"partner_hand":{"cards":["AS"]}}'::jsonb,
        '{"rejected":true}'::jsonb,'ci-smoke-v1'
    ) RETURNING rule_test_id INTO v_test;
    INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,result_details,method_version)
    VALUES (v_school,v_test,'pass','{"ok":true}'::jsonb,'ci-smoke-v1');

    v_failed := false;
    BEGIN
        INSERT INTO bidding.rule_test_run(
            school_id,rule_test_id,result,result_details,evidence_id,method_version
        ) VALUES (v_school,v_test,'pass','{}'::jsonb,v_other_evidence,'ci-smoke-v1');
    EXCEPTION WHEN check_violation THEN v_failed := true;
    END;
    IF NOT v_failed THEN RAISE EXCEPTION 'SMOKE_CROSS_SCHOOL_TEST_EVIDENCE_NOT_BLOCKED'; END IF;

    INSERT INTO bidding.runtime_activation(
        school_id,rule_id,authority_lane,canon_activation_id,scope_key,status
    ) VALUES (v_school,v_rule,'school_canon',v_canon_activation,'ci','active')
    RETURNING runtime_activation_id INTO v_runtime_activation;

    SELECT count(*) INTO v_count
      FROM bidding.active_school_canon_rule_v
     WHERE school_id=v_school AND scope_key='ci' AND rule_id=v_rule;
    IF v_count <> 1 THEN RAISE EXCEPTION 'SMOKE_CANON_VIEW_COUNT_%',v_count; END IF;

    SELECT count(*) INTO v_count
      FROM bidding.get_school_runtime_rule_catalog(v_school,'ci');
    IF v_count <> 1 THEN RAISE EXCEPTION 'SMOKE_CANON_CATALOG_COUNT_%',v_count; END IF;

    v_failed := false;
    BEGIN
        UPDATE bidding.rule SET priority=priority+1 WHERE rule_id=v_rule;
    EXCEPTION WHEN object_not_in_prerequisite_state THEN v_failed := true;
    END;
    IF NOT v_failed THEN RAISE EXCEPTION 'SMOKE_ACTIVE_RULE_MUTATION_NOT_BLOCKED'; END IF;

    v_failed := false;
    BEGIN
        INSERT INTO bidding.rule_test(
            school_id,rule_id,test_key,test_type,fixture,expected,method_version
        ) VALUES (v_school,v_rule,'late-test','regression','{}'::jsonb,'{}'::jsonb,'ci-smoke-v1');
    EXCEPTION WHEN object_not_in_prerequisite_state THEN v_failed := true;
    END;
    IF NOT v_failed THEN RAISE EXCEPTION 'SMOKE_ACTIVE_RULE_TEST_INSERT_NOT_BLOCKED'; END IF;

    v_failed := false;
    BEGIN
        INSERT INTO bidding.runtime_activation(
            school_id,rule_id,authority_lane,canon_activation_id,scope_key,valid_from,valid_to,status
        ) VALUES (
            v_school,v_rule,'school_canon',v_canon_activation,'ci',now(),now()+interval '1 day','active'
        );
    EXCEPTION WHEN check_violation THEN v_failed := true;
    END;
    IF NOT v_failed THEN RAISE EXCEPTION 'SMOKE_RUNTIME_OVERLAP_NOT_BLOCKED'; END IF;

    INSERT INTO public.knowledge_gap(
        school_id,question,context_scope,priority,status
    ) VALUES (v_school,'CI gap','{"auction":[]}'::jsonb,'low','open')
    RETURNING knowledge_gap_id INTO v_gap;

    INSERT INTO bidding.decision_trace(
        school_id,decision_key,request_fingerprint,acting_seat,acting_hand,
        public_auction,public_context,scope_key,knowledge_version_ids,
        candidate_rule_ids,rejected_candidates,outcome,knowledge_gap_id,
        explanation,resolver_version
    ) VALUES (
        v_school,'ci-decision','ci-fingerprint','N','{"cards":["AS"]}'::jsonb,
        '{"calls":[]}'::jsonb,'{}'::jsonb,'ci',ARRAY[v_version],ARRAY[v_rule],
        '[]'::jsonb,'gap',v_gap,'{"reason":"ci"}'::jsonb,'ci-resolver-v0'
    );

    v_failed := false;
    BEGIN
        INSERT INTO bidding.decision_trace(
            school_id,decision_key,request_fingerprint,acting_seat,acting_hand,
            public_auction,public_context,scope_key,outcome,explanation,resolver_version
        ) VALUES (
            v_school,'ci-hidden-decision','ci-hidden-fingerprint','N',
            '{"partner_hand":{"cards":["AS"]}}'::jsonb,
            '{"calls":[]}'::jsonb,'{}'::jsonb,'ci','no_action','{}'::jsonb,'ci-resolver-v0'
        );
    EXCEPTION WHEN check_violation THEN v_failed := true;
    END;
    IF NOT v_failed THEN RAISE EXCEPTION 'SMOKE_HIDDEN_DECISION_NOT_BLOCKED'; END IF;

    v_failed := false;
    BEGIN
        UPDATE bidding.decision_trace SET explanation='{}'::jsonb
         WHERE school_id=v_school AND decision_key='ci-decision';
    EXCEPTION WHEN object_not_in_prerequisite_state THEN v_failed := true;
    END;
    IF NOT v_failed THEN RAISE EXCEPTION 'SMOKE_DECISION_TRACE_MUTATION_NOT_BLOCKED'; END IF;

    INSERT INTO bidding.ingestion_run(
        school_id,source_id,source_manifest_key,source_sha256,repository_ref,metadata
    ) VALUES (
        v_school,v_source,'ci-manifest',repeat('a',64),'ci-ref','{"stage":"ci"}'::jsonb
    ) RETURNING ingestion_run_id INTO v_ingestion;

    INSERT INTO bidding.ingestion_event(
        ingestion_run_id,event_no,role_key,action_key,details
    ) VALUES (v_ingestion,1,'curator','source_registered','{"ok":true}'::jsonb);

    v_failed := false;
    BEGIN
        INSERT INTO bidding.ingestion_event(
            ingestion_run_id,event_no,role_key,action_key,details
        ) VALUES (v_ingestion,3,'compiler','out_of_order','{}'::jsonb);
    EXCEPTION WHEN check_violation THEN v_failed := true;
    END;
    IF NOT v_failed THEN RAISE EXCEPTION 'SMOKE_INGESTION_SEQUENCE_NOT_BLOCKED'; END IF;

    v_failed := false;
    BEGIN
        UPDATE bidding.ingestion_event SET details='{}'::jsonb
         WHERE ingestion_run_id=v_ingestion AND event_no=1;
    EXCEPTION WHEN object_not_in_prerequisite_state THEN v_failed := true;
    END;
    IF NOT v_failed THEN RAISE EXCEPTION 'SMOKE_INGESTION_EVENT_MUTATION_NOT_BLOCKED'; END IF;

    UPDATE bidding.ingestion_run
       SET status='completed',finished_at=now()
     WHERE ingestion_run_id=v_ingestion;

    v_failed := false;
    BEGIN
        INSERT INTO bidding.ingestion_event(
            ingestion_run_id,event_no,role_key,action_key,details
        ) VALUES (v_ingestion,2,'compiler','after_terminal','{}'::jsonb);
    EXCEPTION WHEN object_not_in_prerequisite_state THEN v_failed := true;
    END;
    IF NOT v_failed THEN RAISE EXCEPTION 'SMOKE_EVENT_AFTER_TERMINAL_NOT_BLOCKED'; END IF;

    v_failed := false;
    BEGIN
        UPDATE bidding.ingestion_run SET status='running',finished_at=NULL
         WHERE ingestion_run_id=v_ingestion;
    EXCEPTION WHEN object_not_in_prerequisite_state THEN v_failed := true;
    END;
    IF NOT v_failed THEN RAISE EXCEPTION 'SMOKE_TERMINAL_RUN_MUTATION_NOT_BLOCKED'; END IF;

    INSERT INTO public.knowledge_item(
        school_id,stable_key,knowledge_type,title,status
    ) VALUES (
        v_school,'ci-world-rule','bidding_rule','CI world rule','active'
    ) RETURNING knowledge_item_id INTO v_external_item;

    INSERT INTO public.knowledge_version(
        knowledge_item_id,version_no,content,authority_class,review_status,
        bidding_system_key,method_version,provenance,status
    ) VALUES (
        v_external_item,1,'{"source_block":"WORLD"}'::jsonb,'external','reviewed',
        'external-ci','ci-smoke-v1','{"class":"RECONSTRUCTED"}'::jsonb,'candidate'
    ) RETURNING knowledge_version_id INTO v_external_version;

    INSERT INTO public.knowledge_version_source(knowledge_version_id,source_id,relation_type)
    VALUES (v_external_version,v_source,'derived_from');

    INSERT INTO bidding.rule(
        school_id,knowledge_version_id,rule_key,rule_kind,auction_pattern,
        hand_constraints,action,lifecycle_status,method_version
    ) VALUES (
        v_school,v_external_version,'ci.world.1','bid','{"calls":[]}'::jsonb,
        '{"hcp":{"min":10}}'::jsonb,'{"call":"1S"}'::jsonb,'validated','ci-smoke-v1'
    ) RETURNING rule_id INTO v_external_rule;

    INSERT INTO bidding.rule_test(school_id,rule_id,test_key,test_type,fixture,expected,method_version)
    VALUES (v_school,v_external_rule,'positive','positive','{}'::jsonb,'{}'::jsonb,'ci-smoke-v1')
    RETURNING rule_test_id INTO v_test;

    -- The external fixture tests are inserted explicitly because PL/pgSQL cannot use
    -- INSERT ... RETURNING as an array expression portably.
    INSERT INTO bidding.rule_test(school_id,rule_id,test_key,test_type,fixture,expected,method_version)
    VALUES (v_school,v_external_rule,'negative','negative','{}'::jsonb,'{}'::jsonb,'ci-smoke-v1')
    RETURNING rule_test_id INTO v_test;
    INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,method_version)
    VALUES (v_school,v_test,'pass','ci-smoke-v1');

    INSERT INTO bidding.rule_test(school_id,rule_id,test_key,test_type,fixture,expected,method_version)
    VALUES (v_school,v_external_rule,'boundary','boundary','{}'::jsonb,'{}'::jsonb,'ci-smoke-v1')
    RETURNING rule_test_id INTO v_test;
    INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,method_version)
    VALUES (v_school,v_test,'pass','ci-smoke-v1');

    INSERT INTO bidding.rule_test(school_id,rule_id,test_key,test_type,fixture,expected,method_version)
    VALUES (v_school,v_external_rule,'hidden','hidden_information','{"partner_hand":{}}'::jsonb,'{}'::jsonb,'ci-smoke-v1')
    RETURNING rule_test_id INTO v_test;
    INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,method_version)
    VALUES (v_school,v_test,'pass','ci-smoke-v1');

    SELECT rule_test_id INTO v_test FROM bidding.rule_test
     WHERE rule_id=v_external_rule AND test_key='positive';
    INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,method_version)
    VALUES (v_school,v_test,'pass','ci-smoke-v1');

    INSERT INTO bidding.runtime_activation(
        school_id,rule_id,authority_lane,scope_key,status
    ) VALUES (v_school,v_external_rule,'world_external','ci','active');

    SELECT count(*) INTO v_count FROM bidding.get_school_runtime_rule_catalog(v_school,'ci');
    IF v_count <> 1 THEN RAISE EXCEPTION 'SMOKE_WORLD_LEAKED_TO_CANON_%',v_count; END IF;

    SELECT count(*) INTO v_count FROM bidding.get_research_rule_catalog(v_school,'ci',true);
    IF v_count <> 2 THEN RAISE EXCEPTION 'SMOKE_RESEARCH_CATALOG_COUNT_%',v_count; END IF;

    INSERT INTO public.knowledge_item(
        school_id,stable_key,knowledge_type,title,status
    ) VALUES (
        v_other_school,'ci-other-rule','bidding_rule','Other rule','active'
    ) RETURNING knowledge_item_id INTO v_other_item;
    INSERT INTO public.knowledge_version(
        knowledge_item_id,version_no,content,authority_class,review_status,status
    ) VALUES (
        v_other_item,1,'{}'::jsonb,'school_canon','reviewed','candidate'
    ) RETURNING knowledge_version_id INTO v_other_version;

    v_failed := false;
    BEGIN
        INSERT INTO bidding.rule(
            school_id,knowledge_version_id,rule_key,rule_kind,action
        ) VALUES (v_school,v_other_version,'ci.cross-school','bid','{"call":"P"}'::jsonb);
    EXCEPTION WHEN check_violation THEN v_failed := true;
    END;
    IF NOT v_failed THEN RAISE EXCEPTION 'SMOKE_CROSS_SCHOOL_RULE_NOT_BLOCKED'; END IF;

    IF has_table_privilege('bridge_school_worker','bidding.runtime_activation','INSERT') THEN
        RAISE EXCEPTION 'SMOKE_WORKER_CAN_ACTIVATE';
    END IF;
    IF has_column_privilege('bridge_school_worker','bidding.rule_conflict','status','INSERT') THEN
        RAISE EXCEPTION 'SMOKE_WORKER_CAN_PRE_RESOLVE_CONFLICT';
    END IF;
    IF NOT has_column_privilege('bridge_school_worker','bidding.rule_test_run','result','INSERT')
       OR has_column_privilege('bridge_school_worker','bidding.rule_test_run','created_at','INSERT') THEN
        RAISE EXCEPTION 'SMOKE_TEST_RUN_TIMESTAMP_PRIVILEGE_WRONG';
    END IF;
    IF has_function_privilege('bridge_school_reader','bidding.get_research_rule_catalog(uuid,text,boolean)','EXECUTE') THEN
        RAISE EXCEPTION 'SMOKE_READER_HAS_RESEARCH_ACCESS';
    END IF;
    IF NOT has_function_privilege('bridge_school_worker','bidding.get_research_rule_catalog(uuid,text,boolean)','EXECUTE') THEN
        RAISE EXCEPTION 'SMOKE_WORKER_LACKS_RESEARCH_ACCESS';
    END IF;
END;
$$;

ROLLBACK;
