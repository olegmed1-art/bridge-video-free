\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_other_school uuid;
    v_person uuid;
    v_other_person uuid;
    v_student uuid;
    v_other_student uuid;
    v_skill uuid;
    v_topic uuid;
    v_metric_definition uuid;
    v_metric_version uuid;
    v_analysis_published uuid;
    v_analysis_unpublished uuid;
    v_publication uuid;
    v_skill_assessment uuid;
    v_unpublished_skill_assessment uuid;
    v_metric_observation uuid;
    v_decision uuid;
    v_decision_assessment uuid;
    v_error uuid;
    v_success uuid;
    v_policy uuid;
    v_projection_run uuid;
    v_generation uuid;
    v_snapshot uuid;
    v_activation uuid;
    v_recommendation uuid;
    v_interaction uuid;
    v_session uuid;
    v_plan uuid;
    v_source uuid;
    v_source_identity uuid;
    v_resolution uuid;
    v_tournament uuid;
    v_participation uuid;
    v_member uuid;
    v_attribution uuid;
    v_board uuid;
    v_result uuid;
    v_tournament_error uuid;
    failed boolean;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'school seed missing'; END IF;

    INSERT INTO school(stable_name) VALUES ('CI profile other school')
    RETURNING school_id INTO v_other_school;

    INSERT INTO person(preferred_name) VALUES ('CI profile student') RETURNING person_id INTO v_person;
    INSERT INTO student(school_id, person_id) VALUES (v_school, v_person) RETURNING student_id INTO v_student;
    INSERT INTO person(preferred_name) VALUES ('CI profile other student') RETURNING person_id INTO v_other_person;
    INSERT INTO student(school_id, person_id) VALUES (v_other_school, v_other_person) RETURNING student_id INTO v_other_student;

    INSERT INTO skill(school_id, stable_key, name, description)
    VALUES (v_school, 'ci-profile-skill', 'CI profile skill', 'Projection test skill')
    RETURNING skill_id INTO v_skill;
    INSERT INTO topic(school_id, name, domain)
    VALUES (v_school, 'CI profile topic', 'general')
    RETURNING topic_id INTO v_topic;

    INSERT INTO metric_definition(school_id, stable_key, name, semantic_description, value_type)
    VALUES (v_school, 'ci-profile-metric', 'CI profile metric', 'Projection test metric', 'json')
    RETURNING metric_definition_id INTO v_metric_definition;
    INSERT INTO metric_version(metric_definition_id, version_no, formula_or_method_ref, inputs_definition, status)
    VALUES (v_metric_definition, 1, 'ci-profile-method-v1', '{}'::jsonb, 'active')
    RETURNING metric_version_id INTO v_metric_version;

    -- Published and unpublished analytical observations test the publication boundary.
    INSERT INTO analysis_run(school_id, algorithm_key, algorithm_version, run_status)
    VALUES (v_school, 'ci-profile-analysis', '1', 'success')
    RETURNING analysis_run_id INTO v_analysis_published;
    INSERT INTO analysis_run(school_id, algorithm_key, algorithm_version, run_status)
    VALUES (v_school, 'ci-profile-analysis-unpublished', '1', 'success')
    RETURNING analysis_run_id INTO v_analysis_unpublished;

    INSERT INTO skill_assessment(
        school_id, student_id, skill_id, assessment_value, confidence_class,
        generated_by_analysis_run_id, method_version
    ) VALUES (
        v_school, v_student, v_skill, '{"level":"independent"}'::jsonb, 'HIGH',
        v_analysis_published, 'ci-skill-assessment-v1'
    ) RETURNING skill_assessment_id INTO v_skill_assessment;

    INSERT INTO skill_assessment(
        school_id, student_id, skill_id, assessment_value, confidence_class,
        generated_by_analysis_run_id, method_version
    ) VALUES (
        v_school, v_student, v_skill, '{"level":"candidate"}'::jsonb, 'MEDIUM',
        v_analysis_unpublished, 'ci-skill-assessment-v1'
    ) RETURNING skill_assessment_id INTO v_unpublished_skill_assessment;

    INSERT INTO output_publication(school_id, analysis_run_id, publication_type, manifest, status, published_at)
    VALUES (v_school, v_analysis_published, 'profile_inputs', '{}'::jsonb, 'published', now())
    RETURNING publication_id INTO v_publication;
    INSERT INTO analysis_run_output(
        analysis_run_id, output_entity_id, output_entity_type, publication_id, output_role, status
    ) VALUES (
        v_analysis_published, v_skill_assessment, 'skill_assessment', v_publication, 'derived', 'published'
    );

    INSERT INTO metric_observation(
        school_id, subject_type, student_id, metric_version_id, value, confidence_class
    ) VALUES (
        v_school, 'student', v_student, v_metric_version, '{"value":0.7}'::jsonb, 'HIGH'
    ) RETURNING metric_observation_id INTO v_metric_observation;

    INSERT INTO decision(
        school_id, actor_person_id, student_id, decision_type, action_taken, available_information, occurred_at
    ) VALUES (
        v_school, v_person, v_student, 'answer', '{"answer":"A"}'::jsonb, '{}'::jsonb, now()
    ) RETURNING decision_id INTO v_decision;

    INSERT INTO decision_assessment(
        decision_id, action_quality, reasoning_quality, confidence_class, agreement_context
    ) VALUES (
        v_decision, 'good', 'mixed', 'MEDIUM', '{"resolution_type":"unknown"}'::jsonb
    ) RETURNING decision_assessment_id INTO v_decision_assessment;

    -- Correct action and an error/success signal may coexist; outcome is not conflated
    -- with reasoning or mastery.
    INSERT INTO error_observation(
        school_id, student_id, decision_id, skill_id, topic_id, error_type,
        causal_hypothesis, severity, recurrence_group_key, confidence_class
    ) VALUES (
        v_school, v_student, v_decision, v_skill, v_topic, 'reasoning_gap',
        '{"hypothesis":"ci"}'::jsonb, 'medium', 'ci-recurrence', 'MEDIUM'
    ) RETURNING error_observation_id INTO v_error;

    INSERT INTO success_observation(
        school_id, student_id, decision_id, skill_id, topic_id, success_type,
        independence_level, confidence_class
    ) VALUES (
        v_school, v_student, v_decision, v_skill, v_topic, 'correct_action',
        'independent', 'HIGH'
    ) RETURNING success_observation_id INTO v_success;

    INSERT INTO projection_policy_version(school_id, stable_key, version_no, policy, status)
    VALUES (v_school, 'ci-student-profile-policy', 1, '{"selection":"ci"}'::jsonb, 'active')
    RETURNING projection_policy_version_id INTO v_policy;

    INSERT INTO projection_run(
        school_id, projection_key, method_version, projection_policy_version_id,
        scope, input_watermark, status
    ) VALUES (
        v_school, 'student_profile', 'ci-profile-projection-v1', v_policy,
        '{"scope":"ci"}'::jsonb, 100, 'running'
    ) RETURNING projection_run_id, generation_id INTO v_projection_run, v_generation;

    INSERT INTO student_profile_snapshot(
        school_id, student_id, as_of_time, projection_policy_version_id,
        projection_run_id, generation_id, input_watermark, computed_profile,
        profile_schema_version, status
    ) VALUES (
        v_school, v_student, now(), v_policy,
        v_projection_run, v_generation, 100, '{"summary":"ci"}'::jsonb,
        'ci-v1', 'staging'
    ) RETURNING snapshot_id INTO v_snapshot;

    failed := false;
    BEGIN
        INSERT INTO student_profile_snapshot(
            school_id, student_id, as_of_time, projection_policy_version_id,
            projection_run_id, generation_id, computed_profile
        ) VALUES (
            v_school, v_student, now(), v_policy, v_projection_run, v_generation, '{}'::jsonb
        );
    EXCEPTION WHEN unique_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'one snapshot per student/generation invariant failed'; END IF;

    failed := false;
    BEGIN
        INSERT INTO student_profile_snapshot(
            school_id, student_id, as_of_time, projection_policy_version_id,
            generation_id, computed_profile
        ) VALUES (
            v_school, v_other_student, now(), v_policy, uuidv7(), '{}'::jsonb
        );
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'snapshot cross-school student guard failed'; END IF;

    INSERT INTO student_profile_snapshot_state_event(snapshot_id, state_type)
    VALUES (v_snapshot, 'created');
    INSERT INTO student_profile_snapshot_metric(
        snapshot_id, metric_version_id, value, confidence_class, observation_count, last_observed_at
    ) VALUES (
        v_snapshot, v_metric_version, '{"value":0.7}'::jsonb, 'HIGH', 1, now()
    );
    INSERT INTO student_profile_snapshot_skill(
        snapshot_id, skill_id, state_value, mastery_state, training_state,
        training_priority, confidence_class, recurrence_signal_count
    ) VALUES (
        v_snapshot, v_skill, '{"trajectory":"ci"}'::jsonb, 'independent', 'reactivated',
        0.8, 'HIGH', 1
    );
    INSERT INTO student_profile_snapshot_topic(
        snapshot_id, topic_id, state_value, mastery_state, training_state,
        training_priority, reactivation_reason, confidence_class
    ) VALUES (
        v_snapshot, v_topic, '{"trajectory":"ci"}'::jsonb, 'independent', 'reactivated',
        0.9, 'recurring error signal', 'MEDIUM'
    );

    INSERT INTO student_profile_inference(
        school_id, student_id, snapshot_id, inference_type, topic_id, skill_id,
        inference_value, confidence_class, method_version
    ) VALUES (
        v_school, v_student, v_snapshot, 'learning_need', v_topic, v_skill,
        '{"need":"repeat"}'::jsonb, 'MEDIUM', 'ci-inference-v1'
    );

    INSERT INTO student_profile_input(snapshot_id, skill_assessment_id, input_role)
    VALUES (v_snapshot, v_skill_assessment, 'selected');
    INSERT INTO student_profile_input(snapshot_id, metric_observation_id, input_role)
    VALUES (v_snapshot, v_metric_observation, 'selected');
    INSERT INTO student_profile_input(snapshot_id, error_observation_id, input_role)
    VALUES (v_snapshot, v_error, 'selected');
    INSERT INTO student_profile_input(snapshot_id, success_observation_id, input_role)
    VALUES (v_snapshot, v_success, 'selected');
    INSERT INTO student_profile_input(snapshot_id, decision_assessment_id, input_role)
    VALUES (v_snapshot, v_decision_assessment, 'selected');

    failed := false;
    BEGIN
        INSERT INTO student_profile_input(snapshot_id, skill_assessment_id, input_role)
        VALUES (v_snapshot, v_unpublished_skill_assessment, 'selected');
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'unpublished analysis output leaked into profile'; END IF;

    -- Tournament path: source result can affect a Student only with the exact explicit
    -- identity attribution/resolution that associates this tournament entry with them.
    INSERT INTO source(school_id, source_type, title)
    VALUES (v_school, 'tournament_server', 'CI profile tournament source')
    RETURNING source_id INTO v_source;
    INSERT INTO source_identity(source_id, source_native_key, display_name)
    VALUES (v_source, 'ci-profile-player', 'CI profile student')
    RETURNING source_identity_id INTO v_source_identity;
    INSERT INTO entity_resolution_decision(
        source_identity_id, target_person_id, decision_type, confidence_class, status
    ) VALUES (
        v_source_identity, v_person, 'link', 'HIGH', 'active'
    ) RETURNING resolution_id INTO v_resolution;

    INSERT INTO tournament(school_id, source_id, provider_native_key, name, scoring_type)
    VALUES (v_school, v_source, 'ci-profile-event', 'CI profile tournament', 'matchpoints')
    RETURNING tournament_id INTO v_tournament;
    INSERT INTO tournament_participation(tournament_id, source_native_key, entry_type, pair_number)
    VALUES (v_tournament, 'ci-profile-entry', 'pair', '1')
    RETURNING tournament_participation_id INTO v_participation;
    INSERT INTO tournament_participant_member(
        tournament_participation_id, source_identity_id, member_no
    ) VALUES (v_participation, v_source_identity, 1)
    RETURNING tournament_participant_member_id INTO v_member;
    INSERT INTO tournament_identity_attribution(
        tournament_participant_member_id, entity_resolution_decision_id,
        person_id, student_id, confidence_class
    ) VALUES (
        v_member, v_resolution, v_person, v_student, 'HIGH'
    ) RETURNING tournament_identity_attribution_id INTO v_attribution;
    INSERT INTO tournament_board(tournament_id, source_native_key, board_number)
    VALUES (v_tournament, 'ci-profile-board-1', '1')
    RETURNING tournament_board_id INTO v_board;
    INSERT INTO table_result(
        school_id, tournament_board_id, source_id, provider_native_key, payload_hash,
        ns_participation_id, contract, declarer, raw_score_ns
    ) VALUES (
        v_school, v_board, v_source, 'ci-profile-result-1', 'ci-profile-hash-1',
        v_participation, '3NT', 'N', 400
    ) RETURNING result_id INTO v_result;

    failed := false;
    BEGIN
        INSERT INTO error_observation(
            school_id, student_id, table_result_id, skill_id, error_type
        ) VALUES (
            v_school, v_student, v_result, v_skill, 'tournament_error_without_identity'
        );
    EXCEPTION WHEN check_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'tournament observation accepted without identity provenance'; END IF;

    INSERT INTO error_observation(
        school_id, student_id, table_result_id, tournament_identity_attribution_id,
        entity_resolution_decision_id, skill_id, topic_id, error_type, confidence_class
    ) VALUES (
        v_school, v_student, v_result, v_attribution,
        v_resolution, v_skill, v_topic, 'tournament_decision_error', 'MEDIUM'
    ) RETURNING error_observation_id INTO v_tournament_error;

    INSERT INTO student_profile_input(snapshot_id, error_observation_id, input_role)
    VALUES (v_snapshot, v_tournament_error, 'selected');

    failed := false;
    BEGIN
        INSERT INTO student_profile_input(snapshot_id, table_result_id, input_role)
        VALUES (v_snapshot, v_result, 'selected');
    EXCEPTION WHEN check_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'direct tournament profile input accepted without identity provenance'; END IF;

    INSERT INTO student_profile_input(
        snapshot_id, table_result_id, tournament_identity_attribution_id,
        entity_resolution_decision_id, input_role
    ) VALUES (
        v_snapshot, v_result, v_attribution, v_resolution, 'selected'
    );

    -- A running/incomplete generation cannot become current.
    failed := false;
    BEGIN
        PERFORM activate_projection_generation(v_projection_run, 'ci');
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'incomplete projection generation was activated'; END IF;

    UPDATE projection_run
       SET status='success', completed_at=now(), validation_summary='{"ci":"ok"}'::jsonb
     WHERE projection_run_id=v_projection_run;

    SELECT activate_projection_generation(v_projection_run, 'ci') INTO v_activation;
    IF v_activation IS NULL THEN RAISE EXCEPTION 'projection activation did not return id'; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM projection_generation_current
         WHERE school_id=v_school AND projection_key='student_profile' AND scope_key='ci'
           AND generation_id=v_generation AND activation_id=v_activation
    ) THEN
        RAISE EXCEPTION 'current projection generation pointer not switched atomically';
    END IF;

    INSERT INTO student_profile_snapshot_state_event(snapshot_id, state_type)
    VALUES (v_snapshot, 'validated');

    INSERT INTO recommendation(
        school_id, student_id, source_snapshot_id, projection_run_id,
        recommendation_type, priority_class, priority_value, rationale,
        recommendation_payload, target_topic_id, target_skill_id, method_version
    ) VALUES (
        v_school, v_student, v_snapshot, v_projection_run,
        'next_lesson_focus', 'high', 0.9, 'CI recommendation',
        '{"action":"repeat"}'::jsonb, v_topic, v_skill, 'ci-recommendation-v1'
    ) RETURNING recommendation_id INTO v_recommendation;
    INSERT INTO recommendation_state_event(recommendation_id, state_type)
    VALUES (v_recommendation, 'created');
    INSERT INTO recommendation_state_event(recommendation_id, state_type, reason)
    VALUES (v_recommendation, 'applied', 'CI plan linkage');

    INSERT INTO learning_interaction(
        school_id, interaction_type, primary_student_id, status
    ) VALUES (
        v_school, 'live_lesson', v_student, 'planned'
    ) RETURNING interaction_id INTO v_interaction;
    INSERT INTO session(
        school_id, interaction_id, planned_start_at, planned_end_at, format, status
    ) VALUES (
        v_school, v_interaction, now() + interval '1 day', now() + interval '1 day 90 minutes', 'online', 'planned'
    ) RETURNING session_id INTO v_session;
    INSERT INTO session_plan(session_id, version_no, plan, generated_by, method_version, status)
    VALUES (v_session, 1, '{"ci":"plan"}'::jsonb, 'ai', 'ci-plan-v1', 'active')
    RETURNING session_plan_id INTO v_plan;
    INSERT INTO session_plan_recommendation(session_plan_id, recommendation_id)
    VALUES (v_plan, v_recommendation);

    -- Runtime privilege boundaries.
    IF NOT has_table_privilege('bridge_school_worker','skill_assessment','INSERT')
       OR NOT has_table_privilege('bridge_school_worker','metric_observation','INSERT')
       OR NOT has_table_privilege('bridge_school_worker','student_profile_input','INSERT')
       OR NOT has_table_privilege('bridge_school_worker','recommendation','INSERT') THEN
        RAISE EXCEPTION 'worker profile projection insert privileges missing';
    END IF;

    IF has_table_privilege('bridge_school_worker','skill_assessment','UPDATE')
       OR has_table_privilege('bridge_school_worker','error_observation','UPDATE')
       OR has_table_privilege('bridge_school_worker','student_profile_snapshot','UPDATE')
       OR has_table_privilege('bridge_school_worker','recommendation','UPDATE') THEN
        RAISE EXCEPTION 'append-only profile boundary failed';
    END IF;

    IF has_table_privilege('bridge_school_worker','projection_generation_current','UPDATE')
       OR has_table_privilege('bridge_school_worker','projection_generation_activation','INSERT') THEN
        RAISE EXCEPTION 'worker can bypass guarded projection activation';
    END IF;

    IF NOT has_function_privilege('bridge_school_worker','activate_projection_generation(uuid,text)','EXECUTE')
       OR has_function_privilege('bridge_school_app','activate_projection_generation(uuid,text)','EXECUTE') THEN
        RAISE EXCEPTION 'projection activation function permissions incorrect';
    END IF;

    IF has_table_privilege('bridge_school_app','skill_assessment','INSERT')
       OR has_table_privilege('bridge_school_app','student_profile_inference','INSERT')
       OR has_table_privilege('bridge_school_app','recommendation','INSERT') THEN
        RAISE EXCEPTION 'interactive app crossed derived-profile boundary';
    END IF;

    IF has_function_privilege('bridge_school_worker','validate_student_profile_input()','EXECUTE')
       OR has_function_privilege('bridge_school_worker','validate_learning_observation_scope()','EXECUTE')
       OR has_function_privilege('bridge_school_app','validate_recommendation_scope()','EXECUTE') THEN
        RAISE EXCEPTION 'internal profile validation function exposed to runtime';
    END IF;
END $$;

ROLLBACK;
