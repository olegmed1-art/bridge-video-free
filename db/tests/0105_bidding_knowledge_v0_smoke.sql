-- Transactional smoke test for db/migrations/0105_bidding_knowledge_v0.sql
-- Run only after the migration. No rows persist because the script rolls back.

BEGIN;

DO '
DECLARE
  v_school uuid;
  v_source_canon uuid := uuidv7();
  v_source_world uuid := uuidv7();
  v_item_canon uuid := uuidv7();
  v_item_world uuid := uuidv7();
  v_version_canon uuid := uuidv7();
  v_version_world uuid := uuidv7();
  v_canon_activation uuid := uuidv7();
  v_rule_canon uuid := uuidv7();
  v_rule_world uuid := uuidv7();
  v_runtime_canon uuid := uuidv7();
  v_runtime_world uuid := uuidv7();
  v_conflict uuid := uuidv7();
  v_relation uuid := uuidv7();
  v_trace uuid := uuidv7();
  v_count integer;
BEGIN
  SELECT school_id
    INTO v_school
  FROM public.school
  WHERE status = ''active''
  ORDER BY created_at
  LIMIT 1;

  IF v_school IS NULL THEN
    RAISE EXCEPTION ''BID_V0_SMOKE_NO_ACTIVE_SCHOOL'';
  END IF;

  INSERT INTO public.source (
    source_id, school_id, source_type, title, author_owner,
    canonical_locator, trust_class, rights_notes, status
  ) VALUES
    (v_source_canon, v_school, ''test_fixture'', ''Bidding v0 canonical smoke'',
      ''transactional smoke test'', ''test://bidding-v0/canon'', ''test_only'',
      ''rolled back'', ''active''),
    (v_source_world, v_school, ''test_fixture'', ''Bidding v0 world smoke'',
      ''transactional smoke test'', ''test://bidding-v0/world'', ''test_only'',
      ''rolled back'', ''active'');

  INSERT INTO public.knowledge_item (
    knowledge_item_id, school_id, stable_key, knowledge_type, title, status
  ) VALUES
    (v_item_canon, v_school, ''test.bidding.v0.canon.'' || v_item_canon::text,
      ''bidding_rule'', ''Synthetic canonical smoke rule'', ''active''),
    (v_item_world, v_school, ''test.bidding.v0.world.'' || v_item_world::text,
      ''bidding_rule'', ''Synthetic world smoke rule'', ''active'');

  INSERT INTO public.knowledge_version (
    knowledge_version_id, knowledge_item_id, version_no, content,
    authority_class, review_status, bidding_system_key,
    agreement_scope, level_scope, method_version, provenance, status
  ) VALUES
    (v_version_canon, v_item_canon, 1, jsonb_build_object(''fixture'', true),
      ''school_canon'', ''reviewed'', ''test-v0'', ''{}''::jsonb, ''{}''::jsonb,
      ''bidding-v0-smoke'', jsonb_build_object(''source'', ''smoke''), ''candidate''),
    (v_version_world, v_item_world, 1, jsonb_build_object(''fixture'', true),
      ''external'', ''reviewed'', ''test-v0'', ''{}''::jsonb, ''{}''::jsonb,
      ''bidding-v0-smoke'', jsonb_build_object(''source'', ''smoke''), ''candidate'');

  INSERT INTO public.knowledge_version_source (
    knowledge_version_id, source_id, relation_type, source_locator
  ) VALUES
    (v_version_canon, v_source_canon, ''derived_from'', jsonb_build_object(''fixture'', true)),
    (v_version_world, v_source_world, ''derived_from'', jsonb_build_object(''fixture'', true));

  INSERT INTO public.canon_activation (
    canon_activation_id, knowledge_version_id, scope_key, valid_from,
    approval_provenance, status
  ) VALUES (
    v_canon_activation, v_version_canon, ''test-v0'', now() - interval ''1 minute'',
    jsonb_build_object(''fixture'', true, ''approval'', ''synthetic-test-only''),
    ''active''
  );

  INSERT INTO bidding.rule (
    rule_id, knowledge_version_id, rule_key, rule_kind,
    auction_pattern, hand_constraints, public_context_constraints,
    action, meaning, public_inference, priority, specificity,
    explanation, compiled_payload, lifecycle_status, method_version
  ) VALUES
    (v_rule_canon, v_version_canon, ''test.canon.'' || v_rule_canon::text, ''bid'',
      jsonb_build_object(''calls'', jsonb_build_array(''1C'', ''P'')),
      jsonb_build_object(''hcp_min'', 6, ''hearts_min'', 4),
      jsonb_build_object(''vulnerability'', ''none''),
      jsonb_build_object(''call'', ''1H''),
      jsonb_build_object(''summary'', ''synthetic canonical fixture''),
      jsonb_build_object(''hearts_min'', 4),
      100, 10, jsonb_build_object(''template'', ''fixture''), ''{}''::jsonb,
      ''validated'', ''bidding-v0-smoke''),
    (v_rule_world, v_version_world, ''test.world.'' || v_rule_world::text, ''bid'',
      jsonb_build_object(''calls'', jsonb_build_array(''1C'', ''P'')),
      jsonb_build_object(''hcp_min'', 6, ''spades_min'', 4),
      jsonb_build_object(''vulnerability'', ''none''),
      jsonb_build_object(''call'', ''1S''),
      jsonb_build_object(''summary'', ''synthetic world fixture''),
      jsonb_build_object(''spades_min'', 4),
      50, 8, jsonb_build_object(''template'', ''fixture''), ''{}''::jsonb,
      ''validated'', ''bidding-v0-smoke'');

  BEGIN
    INSERT INTO bidding.runtime_activation (
      rule_id, authority_lane, canon_activation_id, scope_key,
      valid_from, status, activation_provenance
    ) VALUES (
      v_rule_canon, ''school_canon'', v_canon_activation, ''test-v0'',
      now(), ''active'', jsonb_build_object(''fixture'', true)
    );
    RAISE EXCEPTION ''BID_V0_SMOKE_MISSING_TEST_GATE_DID_NOT_FAIL'';
  EXCEPTION WHEN check_violation THEN
    IF position(''BID_ACTIVATION_REQUIRED_TEST_COVERAGE_MISSING'' in SQLERRM) = 0 THEN
      RAISE;
    END IF;
  END;

  BEGIN
    UPDATE bidding.rule
    SET public_context_constraints = jsonb_build_object(''partner_hand'', ''forbidden'')
    WHERE rule_id = v_rule_canon;
    RAISE EXCEPTION ''BID_V0_SMOKE_HIDDEN_RULE_KEY_DID_NOT_FAIL'';
  EXCEPTION WHEN check_violation THEN
    NULL;
  END;

  INSERT INTO bidding.rule_test (
    rule_id, test_key, test_type, fixture, expected,
    last_result, last_result_details, method_version, executed_at
  )
  SELECT v_rule_canon,
         ''canon-'' || x.test_type,
         x.test_type,
         jsonb_build_object(''case'', x.test_type, ''full_deal'',
           CASE WHEN x.test_type = ''hidden_information'' THEN ''test-oracle'' ELSE NULL END),
         jsonb_build_object(''verified'', true),
         ''pass'', jsonb_build_object(''verified'', true),
         ''bidding-v0-smoke'', now()
  FROM unnest(ARRAY[''positive'', ''negative'', ''boundary'', ''hidden_information'']::text[])
       AS x(test_type);

  INSERT INTO bidding.rule_test (
    rule_id, test_key, test_type, fixture, expected,
    last_result, last_result_details, method_version, executed_at
  )
  SELECT v_rule_world,
         ''world-'' || x.test_type,
         x.test_type,
         jsonb_build_object(''case'', x.test_type, ''full_deal'',
           CASE WHEN x.test_type = ''hidden_information'' THEN ''test-oracle'' ELSE NULL END),
         jsonb_build_object(''verified'', true),
         ''pass'', jsonb_build_object(''verified'', true),
         ''bidding-v0-smoke'', now()
  FROM unnest(ARRAY[''positive'', ''negative'', ''boundary'', ''hidden_information'']::text[])
       AS x(test_type);

  BEGIN
    INSERT INTO bidding.runtime_activation (
      rule_id, authority_lane, canon_activation_id, scope_key,
      valid_from, status, activation_provenance
    ) VALUES (
      v_rule_world, ''school_canon'', v_canon_activation, ''wrong-lane'',
      now(), ''active'', jsonb_build_object(''fixture'', true)
    );
    RAISE EXCEPTION ''BID_V0_SMOKE_WORLD_LANE_MISMATCH_DID_NOT_FAIL'';
  EXCEPTION WHEN check_violation THEN
    IF position(''BID_ACTIVATION_WORLD_LANE_MISMATCH'' in SQLERRM) = 0 THEN
      RAISE;
    END IF;
  END;

  INSERT INTO bidding.runtime_activation (
    runtime_activation_id, rule_id, authority_lane, canon_activation_id,
    scope_key, valid_from, status, activation_provenance
  ) VALUES
    (v_runtime_canon, v_rule_canon, ''school_canon'', v_canon_activation,
      ''test-v0'', now(), ''active'', jsonb_build_object(''fixture'', true)),
    (v_runtime_world, v_rule_world, ''world_external'', NULL,
      ''test-v0'', now(), ''active'', jsonb_build_object(''fixture'', true));

  INSERT INTO public.knowledge_relation (
    knowledge_relation_id, school_id, from_version_id, to_version_id,
    relation_type, scope, preconditions, confidence_class,
    evidence_ids, method_version
  ) VALUES (
    v_relation, v_school, v_version_canon, v_version_world,
    ''supports'', jsonb_build_object(''fixture'', true), ''{}''::jsonb,
    ''HIGH'', ''{}''::uuid[], ''bidding-v0-smoke''
  );

  SELECT count(*) INTO v_count
  FROM bidding.get_runtime_rule_catalog(v_school, ''test-v0'', false);
  IF v_count <> 1 THEN
    RAISE EXCEPTION ''BID_V0_SMOKE_CANON_CATALOG_EXPECTED_1_GOT_%'', v_count;
  END IF;

  SELECT count(*) INTO v_count
  FROM bidding.get_runtime_rule_catalog(v_school, ''test-v0'', true);
  IF v_count <> 2 THEN
    RAISE EXCEPTION ''BID_V0_SMOKE_RESEARCH_CATALOG_EXPECTED_2_GOT_%'', v_count;
  END IF;

  SELECT count(*) INTO v_count
  FROM bidding.active_school_canon_rule_v
  WHERE rule_id = v_rule_world;
  IF v_count <> 0 THEN
    RAISE EXCEPTION ''BID_V0_SMOKE_WORLD_RULE_LEAKED_INTO_CANON'';
  END IF;

  SELECT count(*) INTO v_count
  FROM bidding.canon_world_link_v
  WHERE knowledge_relation_id = v_relation;
  IF v_count <> 1 THEN
    RAISE EXCEPTION ''BID_V0_SMOKE_CANON_WORLD_LINK_MISSING'';
  END IF;

  UPDATE bidding.rule_test
  SET last_result = ''fail'', executed_at = now()
  WHERE rule_id = v_rule_world AND test_type = ''positive'';

  SELECT count(*) INTO v_count
  FROM bidding.active_world_rule_v
  WHERE rule_id = v_rule_world;
  IF v_count <> 0 THEN
    RAISE EXCEPTION ''BID_V0_SMOKE_WORLD_VIEW_NOT_FAIL_CLOSED_ON_TEST'';
  END IF;

  UPDATE bidding.rule_test
  SET last_result = ''pass'', executed_at = now()
  WHERE rule_id = v_rule_world AND test_type = ''positive'';

  INSERT INTO bidding.rule_conflict (
    rule_conflict_id, left_rule_id, right_rule_id,
    conflict_type, context_scope, details, status
  ) VALUES (
    v_conflict, v_rule_canon, v_rule_world, ''overlap'',
    jsonb_build_object(''fixture'', true),
    jsonb_build_object(''reason'', ''synthetic open conflict''), ''open''
  );

  SELECT count(*) INTO v_count
  FROM bidding.active_school_canon_rule_v
  WHERE rule_id = v_rule_canon;
  IF v_count <> 0 THEN
    RAISE EXCEPTION ''BID_V0_SMOKE_CANON_VIEW_NOT_FAIL_CLOSED_ON_CONFLICT'';
  END IF;

  SELECT count(*) INTO v_count
  FROM bidding.active_world_rule_v
  WHERE rule_id = v_rule_world;
  IF v_count <> 0 THEN
    RAISE EXCEPTION ''BID_V0_SMOKE_WORLD_VIEW_NOT_FAIL_CLOSED_ON_CONFLICT'';
  END IF;

  UPDATE bidding.rule_conflict
  SET status = ''resolved'', resolved_at = now()
  WHERE rule_conflict_id = v_conflict;

  UPDATE public.source SET status = ''inactive'' WHERE source_id = v_source_canon;
  SELECT count(*) INTO v_count
  FROM bidding.active_school_canon_rule_v
  WHERE rule_id = v_rule_canon;
  IF v_count <> 0 THEN
    RAISE EXCEPTION ''BID_V0_SMOKE_CANON_VIEW_NOT_FAIL_CLOSED_ON_SOURCE'';
  END IF;
  UPDATE public.source SET status = ''active'' WHERE source_id = v_source_canon;

  INSERT INTO bidding.decision_trace (
    decision_trace_id, school_id, decision_key, acting_seat,
    acting_hand, public_auction, public_context, scope_key,
    knowledge_version_ids, candidate_rule_ids, rejected_candidates,
    selected_rule_id, selected_call, outcome, explanation, resolver_version
  ) VALUES (
    v_trace, v_school, ''test-bidding-v0-'' || v_trace::text, ''S'',
    jsonb_build_object(''hcp'', 12, ''suit_lengths'', jsonb_build_array(3,4,3,3)),
    jsonb_build_object(''calls'', jsonb_build_array(''1C'', ''P'')),
    jsonb_build_object(''dealer'', ''N'', ''vulnerability'', ''none''),
    ''test-v0'', ARRAY[v_version_canon], ARRAY[v_rule_canon], ''[]''::jsonb,
    v_rule_canon, ''1H'', ''bid'', jsonb_build_object(''fixture'', true),
    ''bidding-v0-smoke''
  );

  BEGIN
    UPDATE bidding.decision_trace
    SET explanation = jsonb_build_object(''mutated'', true)
    WHERE decision_trace_id = v_trace;
    RAISE EXCEPTION ''BID_V0_SMOKE_APPEND_ONLY_DID_NOT_FAIL'';
  EXCEPTION WHEN SQLSTATE ''55000'' THEN
    IF position(''BID_DECISION_TRACE_APPEND_ONLY'' in SQLERRM) = 0 THEN
      RAISE;
    END IF;
  END;

  BEGIN
    INSERT INTO bidding.decision_trace (
      school_id, decision_key, acting_seat, acting_hand,
      public_auction, public_context, scope_key,
      selected_rule_id, selected_call, outcome, explanation, resolver_version
    ) VALUES (
      v_school, ''test-bidding-v0-hidden-'' || uuidv7()::text, ''S'',
      jsonb_build_object(''hcp'', 12),
      jsonb_build_object(''calls'', jsonb_build_array(''1C'')),
      jsonb_build_object(''partner_hand'', ''forbidden''),
      ''test-v0'', v_rule_canon, ''1H'', ''bid'', ''{}''::jsonb,
      ''bidding-v0-smoke''
    );
    RAISE EXCEPTION ''BID_V0_SMOKE_HIDDEN_TRACE_DID_NOT_FAIL'';
  EXCEPTION WHEN check_violation THEN
    NULL;
  END;

  IF has_table_privilege(
       ''bridge_school_worker'', ''bidding.runtime_activation'', ''INSERT''
     ) THEN
    RAISE EXCEPTION ''BID_V0_SMOKE_WORKER_CAN_ACTIVATE'';
  END IF;

  IF NOT has_table_privilege(
       ''bridge_school_worker'', ''bidding.decision_trace'', ''INSERT''
     ) THEN
    RAISE EXCEPTION ''BID_V0_SMOKE_WORKER_CANNOT_APPEND_TRACE'';
  END IF;

  IF has_table_privilege(
       ''bridge_school_worker'', ''bidding.decision_trace'', ''UPDATE''
     ) THEN
    RAISE EXCEPTION ''BID_V0_SMOKE_WORKER_CAN_MUTATE_TRACE'';
  END IF;

  IF has_table_privilege(
       ''bridge_school_worker'', ''bidding.rule_conflict'', ''UPDATE''
     ) THEN
    RAISE EXCEPTION ''BID_V0_SMOKE_WORKER_CAN_RESOLVE_CONFLICT'';
  END IF;

  RAISE NOTICE ''BID_V0_SMOKE_OK'';
END;
' LANGUAGE plpgsql;

ROLLBACK;
