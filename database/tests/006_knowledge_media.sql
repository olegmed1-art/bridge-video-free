\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_other_school uuid;
    v_source uuid;
    v_other_source uuid;
    v_asset uuid;
    v_other_asset uuid;
    v_media uuid;
    v_transcript uuid;
    v_knowledge uuid;
    v_kv1 uuid;
    v_kv2 uuid;
    v_artifact uuid;
    v_artifact_version uuid;
    v_evidence uuid;
    v_run uuid;
    failed boolean;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'school seed missing'; END IF;

    INSERT INTO school(stable_name) VALUES ('CI knowledge other school')
    RETURNING school_id INTO v_other_school;

    INSERT INTO source(school_id, source_type, title)
    VALUES (v_school, 'video', 'CI knowledge source')
    RETURNING source_id INTO v_source;

    INSERT INTO source(school_id, source_type, title)
    VALUES (v_other_school, 'video', 'CI other source')
    RETURNING source_id INTO v_other_source;

    INSERT INTO asset(school_id, asset_type, mime_type, checksum_value)
    VALUES (v_school, 'video', 'video/mp4', 'ci-knowledge-media-sha')
    RETURNING asset_id INTO v_asset;

    INSERT INTO asset(school_id, asset_type, mime_type, checksum_value)
    VALUES (v_other_school, 'video', 'video/mp4', 'ci-knowledge-other-sha')
    RETURNING asset_id INTO v_other_asset;

    INSERT INTO knowledge_item(school_id, stable_key, knowledge_type, title)
    VALUES (v_school, 'ci-knowledge-rule', 'rule', 'CI rule')
    RETURNING knowledge_item_id INTO v_knowledge;

    INSERT INTO knowledge_version(
        knowledge_item_id, version_no, content, authority_class, review_status, status
    ) VALUES (
        v_knowledge, 1, '{"rule":"first"}'::jsonb, 'school_canon', 'reviewed', 'candidate'
    ) RETURNING knowledge_version_id INTO v_kv1;

    INSERT INTO knowledge_version(
        knowledge_item_id, version_no, content, authority_class, review_status, status
    ) VALUES (
        v_knowledge, 2, '{"rule":"second"}'::jsonb, 'school_canon', 'reviewed', 'candidate'
    ) RETURNING knowledge_version_id INTO v_kv2;

    INSERT INTO knowledge_version_source(knowledge_version_id, source_id)
    VALUES (v_kv1, v_source);

    failed := false;
    BEGIN
        INSERT INTO knowledge_version_source(knowledge_version_id, source_id)
        VALUES (v_kv2, v_other_source);
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'cross-school knowledge source guard failed'; END IF;

    -- Canon activation is a separate administrative action, and overlapping active
    -- versions of one knowledge item/scope are rejected.
    INSERT INTO canon_activation(knowledge_version_id, scope_key, valid_from, status)
    VALUES (v_kv1, 'default', now(), 'active');

    failed := false;
    BEGIN
        INSERT INTO canon_activation(knowledge_version_id, scope_key, valid_from, status)
        VALUES (v_kv2, 'default', now() + interval '1 second', 'active');
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'overlapping canon activation guard failed'; END IF;

    INSERT INTO knowledge_relation(
        school_id, from_version_id, to_version_id, relation_type, confidence_class
    ) VALUES (v_school, v_kv2, v_kv1, 'refines', 'HIGH');

    -- Artifact bytes and logical artifact must share a school.
    INSERT INTO artifact(school_id, artifact_type, title)
    VALUES (v_school, 'handout', 'CI handout')
    RETURNING artifact_id INTO v_artifact;

    INSERT INTO artifact_version(artifact_id, version_no, asset_id, status)
    VALUES (v_artifact, 1, v_asset, 'active')
    RETURNING artifact_version_id INTO v_artifact_version;

    INSERT INTO artifact_version_source(artifact_version_id, source_id)
    VALUES (v_artifact_version, v_source);
    INSERT INTO artifact_version_knowledge(artifact_version_id, knowledge_version_id)
    VALUES (v_artifact_version, v_kv1);

    failed := false;
    BEGIN
        INSERT INTO artifact_version(artifact_id, version_no, asset_id)
        VALUES (v_artifact, 2, v_other_asset);
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'artifact/asset school guard failed'; END IF;

    -- MediaAsset is a subtype of Asset; Transcript/segments preserve raw/corrected layers.
    INSERT INTO media_asset(media_asset_id, school_id, duration_seconds, media_metadata)
    VALUES (v_asset, v_school, 3600, '{"codec":"ci"}'::jsonb)
    RETURNING media_asset_id INTO v_media;

    failed := false;
    BEGIN
        INSERT INTO media_asset(media_asset_id, school_id)
        VALUES (v_other_asset, v_school);
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'media asset school guard failed'; END IF;

    INSERT INTO transcript(school_id, media_asset_id, transcript_type, language, source_id, status)
    VALUES (v_school, v_media, 'raw_asr', 'ru', v_source, 'active')
    RETURNING transcript_id INTO v_transcript;

    INSERT INTO transcript_segment(
        transcript_id, sequence_no, start_seconds, end_seconds, speaker_label, text, confidence_class
    ) VALUES (v_transcript, 1, 10, 20, 'Teacher', 'CI transcript segment', 'HIGH');

    -- Evidence must point only to Source/Asset within its school.
    INSERT INTO evidence(
        school_id, evidence_type, source_id, asset_id, start_seconds, end_seconds, confidence_class
    ) VALUES (v_school, 'video_range', v_source, v_asset, 10, 20, 'HIGH')
    RETURNING evidence_id INTO v_evidence;

    INSERT INTO evidence_link(evidence_id, target_entity_id, target_entity_type, relation_type)
    VALUES (v_evidence, v_kv1, 'knowledge_version', 'supports');

    failed := false;
    BEGIN
        INSERT INTO evidence(school_id, evidence_type, source_id)
        VALUES (v_school, 'file_range', v_other_source);
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'evidence cross-school source guard failed'; END IF;

    INSERT INTO quality_assessment(
        school_id, target_entity_id, target_entity_type, dimension, score, quality_class
    ) VALUES (v_school, v_transcript, 'transcript', 'semantic', 0.95, 'good');

    INSERT INTO quality_issue(
        school_id, target_entity_id, target_entity_type, issue_type, severity, description
    ) VALUES (v_school, v_transcript, 'transcript', 'speaker_uncertain', 'low', 'CI issue');

    -- AnalysisRun explicit input must reference exactly one typed input object.
    INSERT INTO analysis_run(school_id, algorithm_key, algorithm_version, run_status)
    VALUES (v_school, 'ci-analysis', '1', 'running')
    RETURNING analysis_run_id INTO v_run;

    INSERT INTO analysis_run_input(analysis_run_id, asset_id, input_role)
    VALUES (v_run, v_asset, 'primary');

    failed := false;
    BEGIN
        INSERT INTO analysis_run_input(analysis_run_id, source_id, asset_id)
        VALUES (v_run, v_source, v_asset);
    EXCEPTION WHEN check_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'analysis input one-of guard failed'; END IF;

    INSERT INTO analysis_run_output(
        analysis_run_id, output_entity_id, output_entity_type, artifact_version_id, status
    ) VALUES (v_run, v_artifact_version, 'artifact_version', v_artifact_version, 'staging');

    -- Runtime boundaries.
    IF NOT has_table_privilege('bridge_school_worker','knowledge_item','INSERT')
       OR NOT has_table_privilege('bridge_school_worker','knowledge_version','INSERT')
       OR NOT has_table_privilege('bridge_school_worker','transcript_segment','INSERT')
       OR NOT has_table_privilege('bridge_school_worker','evidence','INSERT')
       OR NOT has_table_privilege('bridge_school_worker','artifact_version','INSERT') THEN
        RAISE EXCEPTION 'worker knowledge/media privileges missing';
    END IF;

    IF has_table_privilege('bridge_school_worker','canon_activation','INSERT')
       OR has_table_privilege('bridge_school_worker','algorithm','INSERT')
       OR has_table_privilege('bridge_school_worker','algorithm_version','UPDATE') THEN
        RAISE EXCEPTION 'worker crossed canon/algorithm administration boundary';
    END IF;

    IF has_table_privilege('bridge_school_app','knowledge_item','INSERT')
       OR has_table_privilege('bridge_school_app','transcript','UPDATE')
       OR has_table_privilege('bridge_school_app','evidence','INSERT') THEN
        RAISE EXCEPTION 'interactive app crossed knowledge/media worker boundary';
    END IF;

    IF has_table_privilege('bridge_school_worker','transcript_segment','UPDATE')
       OR has_table_privilege('bridge_school_worker','evidence','UPDATE')
       OR has_table_privilege('bridge_school_worker','quality_assessment','UPDATE')
       OR has_table_privilege('bridge_school_worker','artifact_version_source','UPDATE') THEN
        RAISE EXCEPTION 'append-only knowledge/media provenance boundary failed';
    END IF;

    IF NOT has_column_privilege('bridge_school_worker','knowledge_version','status','UPDATE')
       OR NOT has_column_privilege('bridge_school_worker','knowledge_version','review_status','UPDATE')
       OR has_column_privilege('bridge_school_worker','knowledge_version','content','UPDATE') THEN
        RAISE EXCEPTION 'knowledge version lifecycle/content column permissions incorrect';
    END IF;

    IF has_function_privilege('bridge_school_worker','prevent_canon_activation_overlap()','EXECUTE')
       OR has_function_privilege('bridge_school_worker','validate_evidence_scope()','EXECUTE')
       OR has_function_privilege('bridge_school_app','validate_media_asset_scope()','EXECUTE') THEN
        RAISE EXCEPTION 'internal knowledge/media validation function exposed to runtime';
    END IF;
END $$;

ROLLBACK;
