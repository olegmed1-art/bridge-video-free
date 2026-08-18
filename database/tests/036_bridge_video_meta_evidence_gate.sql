\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_bad_run uuid;
    v_good_run uuid;
    v_source uuid;
    v_bad_evidence uuid;
    v_good_evidence uuid;
    v_gate uuid;
    v_gate_again uuid;
    v_count integer;
BEGIN
    SELECT school_id INTO v_school
      FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN
        RAISE EXCEPTION 'canonical school missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM algorithm a JOIN algorithm_version av USING (algorithm_id)
         WHERE a.school_id=v_school
           AND a.stable_key='bridge-video-master-analysis'
           AND av.version_label='3.1-free-r25.12-meta'
           AND av.status='candidate'
    ) THEN
        RAISE EXCEPTION 'r25.12 candidate is not registered';
    END IF;

    INSERT INTO source(
        school_id,source_type,title,canonical_locator,trust_class
    ) VALUES (
        v_school,'meta_gate_regression','r25.12 META gate fixtures',
        'test:bridge-video-r25.12-meta-gate','test'
    ) RETURNING source_id INTO v_source;

    INSERT INTO evidence(
        school_id,evidence_type,source_id,locator,confidence_class,quality_status
    ) VALUES (
        v_school,'asr_regression_fixture',v_source,
        '{"fixture":"tests/fixtures/asr_block_11_r25_11.json","block":11}'::jsonb,
        'HIGH','verified'
    ) RETURNING evidence_id INTO v_bad_evidence;

    INSERT INTO evidence(
        school_id,evidence_type,source_id,locator,confidence_class,quality_status
    ) VALUES (
        v_school,'prior_good_report_regression',v_source,
        '{"report":"known-r25.6-good"}'::jsonb,'HIGH','verified'
    ) RETURNING evidence_id INTO v_good_evidence;

    INSERT INTO analysis_run(
        school_id,algorithm_key,algorithm_version,run_status,
        technical_record_status,quality_confirmation_status,
        publication_authorization_status
    ) VALUES (
        v_school,'bridge-video-master-analysis','3.1-free-r25.12-meta','running',
        'recorded','pending','blocked'
    ) RETURNING analysis_run_id INTO v_bad_run;

    IF NOT EXISTS (
        SELECT 1 FROM analysis_run
         WHERE analysis_run_id=v_bad_run AND algorithm_version_id IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'candidate AnalysisRun lacks algorithm_version_id';
    END IF;

    PERFORM record_run_checkpoint(
        'analysis',v_bad_run,'technical-record','completed',
        '{"resume_token":"block-11"}'::jsonb,NULL,'{}'::jsonb
    );
    PERFORM record_run_checkpoint(
        'analysis',v_bad_run,'meta-assessment','started',
        '{"resume_token":"meta-gate"}'::jsonb,NULL,'{}'::jsonb
    );
    IF (SELECT max(sequence_no) FROM run_checkpoint_event
         WHERE run_type='analysis' AND run_id=v_bad_run) <> 2 THEN
        RAISE EXCEPTION 'checkpoint/resume history was not append-only';
    END IF;

    BEGIN
        UPDATE analysis_run
           SET run_status='success',quality_confirmation_status='confirmed',
               publication_authorization_status='allowed'
         WHERE analysis_run_id=v_bad_run;
        RAISE EXCEPTION 'candidate succeeded without independent PASS';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='candidate succeeded without independent PASS' THEN RAISE; END IF;
    END;

    v_gate := record_bridge_video_meta_assessment(
        v_bad_run,'quarantined',ARRAY[v_bad_evidence],
        '{"independent":true,"selfReportedApproval":false,"baseCoveragePassed":true,"unreliableDerivedEvidenceCount":0,"hallucinationBlocks":[11],"block11RegressionPassed":false,"priorGoodReportRegressionPassed":true}'::jsonb,
        'block-11-permanent-regression-v1'
    );
    v_gate_again := record_bridge_video_meta_assessment(
        v_bad_run,'quarantined',ARRAY[v_bad_evidence],
        '{"independent":true,"selfReportedApproval":false,"baseCoveragePassed":true,"unreliableDerivedEvidenceCount":0,"hallucinationBlocks":[11],"block11RegressionPassed":false,"priorGoodReportRegressionPassed":true}'::jsonb,
        'block-11-permanent-regression-v1'
    );
    IF v_gate IS DISTINCT FROM v_gate_again THEN
        RAISE EXCEPTION 'META assessment rerun was not idempotent';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM analysis_run
         WHERE analysis_run_id=v_bad_run AND run_status='failed'
           AND technical_record_status='quarantined'
           AND quality_confirmation_status='rejected'
           AND publication_authorization_status='blocked'
    ) THEN
        RAISE EXCEPTION 'failed gate did not quarantine candidate';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM quality_issue
         WHERE target_entity_id=v_bad_run AND status='confirmed'
           AND evidence_ids=ARRAY[v_bad_evidence]
    ) THEN
        RAISE EXCEPTION 'failed gate lacks independent quality_issue evidence';
    END IF;
    SELECT count(*) INTO v_count FROM bridge_video_evidence_gate
     WHERE analysis_run_id=v_bad_run;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'idempotent assessment created % gate rows',v_count;
    END IF;

    BEGIN
        PERFORM record_bridge_video_meta_assessment(
            v_bad_run,'fail',ARRAY[v_bad_evidence],
            '{"independent":true,"selfReportedApproval":false,"baseCoveragePassed":false}'::jsonb,
            'block-11-permanent-regression-v1'
        );
        RAISE EXCEPTION 'idempotency key accepted a different payload';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='idempotency key accepted a different payload' THEN RAISE; END IF;
    END;

    BEGIN
        UPDATE bridge_video_evidence_gate SET checks='{}'::jsonb
         WHERE bridge_video_evidence_gate_id=v_gate;
        RAISE EXCEPTION 'append-only gate row was mutable';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='append-only gate row was mutable' THEN RAISE; END IF;
    END;

    INSERT INTO analysis_run(
        school_id,algorithm_key,algorithm_version,run_status,
        technical_record_status,quality_confirmation_status,
        publication_authorization_status
    ) VALUES (
        v_school,'bridge-video-master-analysis','3.1-free-r25.12-meta','running',
        'recorded','pending','blocked'
    ) RETURNING analysis_run_id INTO v_good_run;

    PERFORM record_bridge_video_meta_assessment(
        v_good_run,'pass',ARRAY[v_good_evidence],
        '{"independent":true,"selfReportedApproval":false,"baseCoveragePassed":true,"unreliableDerivedEvidenceCount":0,"hallucinationBlocks":[],"block11RegressionPassed":true,"priorGoodReportRegressionPassed":true}'::jsonb,
        'known-good-r25.6-regression-v1'
    );
    IF NOT EXISTS (
        SELECT 1 FROM analysis_run
         WHERE analysis_run_id=v_good_run AND run_status='success'
           AND algorithm_version_id IS NOT NULL
           AND technical_record_status='recorded'
           AND quality_confirmation_status='confirmed'
           AND publication_authorization_status='allowed'
    ) THEN
        RAISE EXCEPTION 'independent PASS did not unlock success';
    END IF;

    INSERT INTO output_publication(
        school_id,analysis_run_id,publication_type,status,published_at
    ) VALUES (v_school,v_good_run,'lesson_master_analysis_pdf','published',now());

    IF EXISTS (
        SELECT 1 FROM bridge_video_evidence_gate g
        LEFT JOIN analysis_run ar ON ar.analysis_run_id=g.analysis_run_id
        LEFT JOIN quality_assessment qa ON qa.quality_assessment_id=g.quality_assessment_id
        WHERE ar.analysis_run_id IS NULL OR qa.quality_assessment_id IS NULL
    ) THEN
        RAISE EXCEPTION 'orphan META gate reference detected';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM changeset c JOIN domain_event de USING (changeset_id)
         WHERE de.aggregate_id=v_good_run AND c.status='committed'
           AND de.event_type='BridgeVideoMetaAssessed'
    ) THEN
        RAISE EXCEPTION 'META gate lacks committed changeset/domain_event';
    END IF;

    IF has_function_privilege(
        'bridge_school_worker',
        'record_bridge_video_meta_assessment(uuid,text,uuid[],jsonb,text)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'worker can self-approve META gate';
    END IF;
    IF NOT has_function_privilege(
        'bridge_school_meta',
        'record_bridge_video_meta_assessment(uuid,text,uuid[],jsonb,text)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'independent META role cannot record assessment';
    END IF;
END $$;

ROLLBACK;
