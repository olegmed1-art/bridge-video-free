\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_student uuid;
    v_skill uuid;
    v_topic uuid;
    v_decision uuid;
    v_error uuid;
    v_policy uuid;
    v_run1 uuid;
    v_generation1 uuid;
    v_snapshot1 uuid;
    v_recommendation uuid;
    v_interaction uuid;
    v_session uuid;
    v_plan uuid;
    v_batch1 uuid;
    v_batch2 uuid;
    v_request uuid;
    v_claimed uuid;
    v_run2 uuid;
    v_generation2 uuid;
    v_snapshot2 uuid;
    failed boolean;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'school seed missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('CI invalidation student') RETURNING person_id INTO v_person;
    INSERT INTO student(school_id, person_id) VALUES (v_school, v_person) RETURNING student_id INTO v_student;
    INSERT INTO skill(school_id, name, description)
    VALUES (v_school, 'CI invalidation skill', 'Dependency invalidation test')
    RETURNING skill_id INTO v_skill;
    INSERT INTO topic(school_id, name, domain)
    VALUES (v_school, 'CI invalidation topic', 'general')
    RETURNING topic_id INTO v_topic;

    INSERT INTO decision(
        school_id, actor_person_id, student_id, decision_type,
        action_taken, available_information, occurred_at
    ) VALUES (
        v_school, v_person, v_student, 'answer',
        '{"answer":"ci"}'::jsonb, '{}'::jsonb, now()
    ) RETURNING decision_id INTO v_decision;

    INSERT INTO error_observation(
        school_id, student_id, decision_id, skill_id, topic_id,
        error_type, causal_hypothesis, confidence_class, method_version
    ) VALUES (
        v_school, v_student, v_decision, v_skill, v_topic,
        'ci_invalidation_error', '{"cause":"ci"}'::jsonb, 'HIGH', 'ci-invalidation-v1'
    ) RETURNING error_observation_id INTO v_error;

    -- Observation dependencies are registered automatically.
    IF NOT EXISTS (
        SELECT 1 FROM dependency_edge
         WHERE school_id=v_school AND parent_entity_id=v_decision
           AND child_entity_id=v_error AND dependency_type='derived_from'
    ) OR NOT EXISTS (
        SELECT 1 FROM dependency_edge
         WHERE school_id=v_school AND parent_entity_id=v_skill
           AND child_entity_id=v_error AND dependency_type='depends_on'
    ) THEN
        RAISE EXCEPTION 'learning observation dependency registration failed';
    END IF;

    INSERT INTO projection_policy_version(school_id, stable_key, version_no, policy, status)
    VALUES (v_school, 'ci-invalidation-policy', 1, '{"ci":true}'::jsonb, 'active')
    RETURNING projection_policy_version_id INTO v_policy;

    INSERT INTO projection_run(
        school_id, projection_key, method_version, projection_policy_version_id,
        scope, input_watermark, status
    ) VALUES (
        v_school, 'student_profile', 'ci-invalidation-projection-v1', v_policy,
        jsonb_build_object('student_id',v_student), 10, 'running'
    ) RETURNING projection_run_id, generation_id INTO v_run1, v_generation1;

    INSERT INTO student_profile_snapshot(
        school_id, student_id, as_of_time, projection_policy_version_id,
        projection_run_id, generation_id, input_watermark, computed_profile,
        profile_schema_version, status
    ) VALUES (
        v_school, v_student, now(), v_policy,
        v_run1, v_generation1, 10, '{"generation":1}'::jsonb,
        'ci-v1', 'staging'
    ) RETURNING snapshot_id INTO v_snapshot1;

    INSERT INTO student_profile_snapshot_state_event(snapshot_id, state_type)
    VALUES (v_snapshot1, 'created');

    INSERT INTO student_profile_input(snapshot_id, error_observation_id, input_role)
    VALUES (v_snapshot1, v_error, 'selected');

    IF NOT EXISTS (
        SELECT 1 FROM projection_output_entity
         WHERE school_id=v_school AND entity_id=v_snapshot1
           AND entity_type='student_profile_snapshot' AND generation_id=v_generation1
    ) THEN
        RAISE EXCEPTION 'student profile projection output registration failed';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM dependency_edge
         WHERE school_id=v_school AND parent_entity_id=v_error
           AND child_entity_id=v_snapshot1 AND dependency_type='depends_on'
    ) THEN
        RAISE EXCEPTION 'profile input dependency registration failed';
    END IF;

    UPDATE projection_run
       SET status='success', completed_at=now(), validation_summary='{"ci":"ok"}'::jsonb
     WHERE projection_run_id=v_run1;
    PERFORM activate_projection_generation(v_run1, 'ci-invalidation');
    INSERT INTO student_profile_snapshot_state_event(snapshot_id, state_type)
    VALUES (v_snapshot1, 'validated');

    INSERT INTO recommendation(
        school_id, student_id, source_snapshot_id, projection_run_id,
        recommendation_type, priority_class, priority_value, rationale,
        recommendation_payload, target_skill_id, target_topic_id, method_version
    ) VALUES (
        v_school, v_student, v_snapshot1, v_run1,
        'next_lesson_focus', 'high', 1.0, 'CI invalidation recommendation',
        '{"action":"repeat"}'::jsonb, v_skill, v_topic, 'ci-rec-v1'
    ) RETURNING recommendation_id INTO v_recommendation;
    INSERT INTO recommendation_state_event(recommendation_id, state_type)
    VALUES (v_recommendation, 'created');

    INSERT INTO learning_interaction(school_id, interaction_type, primary_student_id, status)
    VALUES (v_school, 'live_lesson', v_student, 'planned')
    RETURNING interaction_id INTO v_interaction;
    INSERT INTO session(
        school_id, interaction_id, planned_start_at, planned_end_at, format, status
    ) VALUES (
        v_school, v_interaction, now()+interval '1 day', now()+interval '1 day 90 minutes', 'online', 'planned'
    ) RETURNING session_id INTO v_session;
    INSERT INTO session_plan(session_id, version_no, plan, generated_by, method_version, status)
    VALUES (v_session, 1, '{"ci":"plan"}'::jsonb, 'ai', 'ci-plan-v1', 'active')
    RETURNING session_plan_id INTO v_plan;
    INSERT INTO session_plan_recommendation(session_plan_id, recommendation_id)
    VALUES (v_plan, v_recommendation);

    IF NOT EXISTS (
        SELECT 1 FROM dependency_edge
         WHERE parent_entity_id=v_snapshot1 AND child_entity_id=v_recommendation
           AND dependency_type='derived_from'
    ) OR NOT EXISTS (
        SELECT 1 FROM dependency_edge
         WHERE parent_entity_id=v_recommendation AND child_entity_id=v_plan
           AND dependency_type='depends_on'
    ) THEN
        RAISE EXCEPTION 'recommendation/plan dependency registration failed';
    END IF;

    SELECT invalidate_dependency_subgraph(
        v_school, v_error, 'CI source correction', '{"ci":1}'::jsonb, 'ci-test'
    ) INTO v_batch1;

    IF v_batch1 IS NULL THEN RAISE EXCEPTION 'invalidation batch not created'; END IF;
    IF (SELECT count(*) FROM invalidation_record WHERE invalidation_batch_id=v_batch1) <> 3 THEN
        RAISE EXCEPTION 'dependency cascade did not invalidate snapshot, recommendation and session plan';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM invalidation_record
         WHERE invalidation_batch_id=v_batch1 AND target_entity_id=v_snapshot1 AND dependency_depth=1
    ) OR NOT EXISTS (
        SELECT 1 FROM invalidation_record
         WHERE invalidation_batch_id=v_batch1 AND target_entity_id=v_recommendation AND dependency_depth=2
    ) OR NOT EXISTS (
        SELECT 1 FROM invalidation_record
         WHERE invalidation_batch_id=v_batch1 AND target_entity_id=v_plan AND dependency_depth=3
    ) THEN
        RAISE EXCEPTION 'invalidation dependency depths are incorrect';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM student_profile_snapshot_state_event
         WHERE snapshot_id=v_snapshot1 AND state_type='stale' AND cause_entity_id=v_error
    ) THEN
        RAISE EXCEPTION 'current profile was not marked stale';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM recommendation_state_event
         WHERE recommendation_id=v_recommendation AND state_type='invalidated'
    ) THEN
        RAISE EXCEPTION 'downstream recommendation was not invalidated';
    END IF;

    SELECT projection_recompute_request_id INTO v_request
      FROM projection_recompute_request
     WHERE school_id=v_school AND projection_key='student_profile'
       AND scope_key='ci-invalidation' AND status='pending';
    IF v_request IS NULL THEN RAISE EXCEPTION 'current projection recompute was not queued'; END IF;

    -- A second invalidation while the same scope is pending must coalesce into one queue
    -- request while preserving its additional causal link.
    SELECT invalidate_dependency_subgraph(
        v_school, v_error, 'CI second correction', '{"ci":2}'::jsonb, 'ci-test'
    ) INTO v_batch2;
    IF v_batch2 IS NULL OR v_batch2=v_batch1 THEN RAISE EXCEPTION 'second invalidation batch missing'; END IF;
    IF (SELECT count(*) FROM projection_recompute_request
         WHERE school_id=v_school AND projection_key='student_profile'
           AND scope_key='ci-invalidation' AND status IN ('pending','running')) <> 1 THEN
        RAISE EXCEPTION 'active recompute request deduplication failed';
    END IF;
    IF (SELECT count(*) FROM projection_recompute_cause WHERE projection_recompute_request_id=v_request) <> 2 THEN
        RAISE EXCEPTION 'coalesced recompute request did not preserve both causes';
    END IF;

    SELECT claim_projection_recompute('ci-worker') INTO v_claimed;
    IF v_claimed <> v_request THEN RAISE EXCEPTION 'worker did not claim expected recompute request'; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM projection_recompute_request
         WHERE projection_recompute_request_id=v_request AND status='running'
           AND claimed_by='ci-worker' AND attempt_count=1
    ) THEN
        RAISE EXCEPTION 'recompute claim state incorrect';
    END IF;

    -- Failure and retry are guarded lifecycle transitions with preserved history.
    PERFORM fail_projection_recompute(v_request, 'CI transient failure');
    IF NOT EXISTS (
        SELECT 1 FROM projection_recompute_request
         WHERE projection_recompute_request_id=v_request AND status='failed'
    ) THEN
        RAISE EXCEPTION 'recompute failure state incorrect';
    END IF;
    PERFORM retry_projection_recompute(v_request);
    SELECT claim_projection_recompute('ci-worker-retry') INTO v_claimed;
    IF v_claimed <> v_request THEN RAISE EXCEPTION 'retried request was not reclaimed'; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM projection_recompute_request
         WHERE projection_recompute_request_id=v_request AND status='running' AND attempt_count=2
    ) THEN
        RAISE EXCEPTION 'recompute retry attempt count incorrect';
    END IF;

    INSERT INTO projection_run(
        school_id, projection_key, method_version, projection_policy_version_id,
        scope, input_watermark, status, completed_at, validation_summary
    ) VALUES (
        v_school, 'student_profile', 'ci-invalidation-projection-v2', v_policy,
        jsonb_build_object('student_id',v_student), 20, 'success', now(), '{"ci":"recomputed"}'::jsonb
    ) RETURNING projection_run_id, generation_id INTO v_run2, v_generation2;

    INSERT INTO student_profile_snapshot(
        school_id, student_id, as_of_time, projection_policy_version_id,
        projection_run_id, generation_id, input_watermark, computed_profile,
        profile_schema_version, status, supersedes_snapshot_id
    ) VALUES (
        v_school, v_student, now(), v_policy,
        v_run2, v_generation2, 20, '{"generation":2}'::jsonb,
        'ci-v1', 'staging', v_snapshot1
    ) RETURNING snapshot_id INTO v_snapshot2;
    INSERT INTO student_profile_snapshot_state_event(snapshot_id, state_type)
    VALUES (v_snapshot2, 'validated');

    failed := false;
    BEGIN
        PERFORM complete_projection_recompute(v_request, v_run2);
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN
        RAISE EXCEPTION 'recompute completed before new generation activation';
    END IF;

    PERFORM activate_projection_generation(v_run2, 'ci-invalidation');
    PERFORM complete_projection_recompute(v_request, v_run2);

    IF NOT EXISTS (
        SELECT 1 FROM projection_recompute_request
         WHERE projection_recompute_request_id=v_request AND status='succeeded'
           AND result_projection_run_id=v_run2
    ) THEN
        RAISE EXCEPTION 'recompute completion state incorrect';
    END IF;
    IF (SELECT count(*) FROM projection_recompute_state_event
         WHERE projection_recompute_request_id=v_request AND state_type IN ('claimed','failed','retried','succeeded')) < 5 THEN
        RAISE EXCEPTION 'recompute lifecycle history incomplete';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM projection_generation_current
         WHERE school_id=v_school AND projection_key='student_profile'
           AND scope_key='ci-invalidation' AND generation_id=v_generation2
    ) THEN
        RAISE EXCEPTION 'new recomputed generation is not current';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM current_student_profile_status
         WHERE student_id=v_student AND snapshot_id=v_snapshot2 AND generation_id=v_generation2
    ) THEN
        RAISE EXCEPTION 'current student profile read model did not switch to new generation';
    END IF;

    -- Only invalidation records that directly caused this projection request are marked
    -- recomputed; downstream recommendation/plan invalidations remain explicit pending work.
    IF (SELECT count(*) FROM invalidation_record ir
        JOIN projection_recompute_cause c ON c.invalidation_id=ir.invalidation_id
        WHERE c.projection_recompute_request_id=v_request AND ir.recomputation_status='recomputed') <> 2 THEN
        RAISE EXCEPTION 'projection-cause invalidations were not marked recomputed';
    END IF;

    -- Runtime roles cannot bypass the guarded queue/invalidation functions.
    IF has_table_privilege('bridge_school_worker','invalidation_record','INSERT')
       OR has_table_privilege('bridge_school_worker','projection_recompute_request','UPDATE')
       OR has_table_privilege('bridge_school_worker','projection_output_entity','INSERT') THEN
        RAISE EXCEPTION 'worker can bypass guarded invalidation/recompute tables';
    END IF;
    IF NOT has_function_privilege('bridge_school_worker','invalidate_dependency_subgraph(uuid,uuid,text,jsonb,text)','EXECUTE')
       OR NOT has_function_privilege('bridge_school_worker','claim_projection_recompute(text)','EXECUTE')
       OR NOT has_function_privilege('bridge_school_worker','complete_projection_recompute(uuid,uuid)','EXECUTE') THEN
        RAISE EXCEPTION 'worker lacks guarded invalidation/recompute functions';
    END IF;
    IF has_function_privilege('bridge_school_app','invalidate_dependency_subgraph(uuid,uuid,text,jsonb,text)','EXECUTE')
       OR has_function_privilege('bridge_school_app','claim_projection_recompute(text)','EXECUTE') THEN
        RAISE EXCEPTION 'interactive app crossed invalidation/recompute boundary';
    END IF;
    IF has_function_privilege('bridge_school_worker','register_student_profile_input_dependencies()','EXECUTE')
       OR has_function_privilege('bridge_school_worker','register_learning_observation_dependencies()','EXECUTE') THEN
        RAISE EXCEPTION 'internal dependency-registration trigger function exposed to worker';
    END IF;
END $$;

ROLLBACK;
