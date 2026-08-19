\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_source uuid;
    v_evidence uuid;
    v_run uuid;
    v_gate uuid;
    v_gate_again uuid;
    v_gate_count integer;
    v_outbox_count integer;
BEGIN
    SELECT school_id INTO v_school
      FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN
        RAISE EXCEPTION 'canonical school missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM algorithm a
          JOIN algorithm_version av USING (algorithm_id)
         WHERE a.school_id=v_school
           AND a.stable_key='bridge-video-master-analysis'
           AND av.version_label='3.1-free-r25.13-checkpoint'
           AND av.status='candidate'
    ) THEN
        RAISE EXCEPTION 'r25.13 checkpoint candidate is not registered';
    END IF;

    INSERT INTO source(
        school_id,source_type,title,canonical_locator,trust_class
    ) VALUES (
        v_school,'meta_gate_regression','r25.13 META lifecycle fixture',
        'test:bridge-video-r25.13-meta-lifecycle','test'
    ) RETURNING source_id INTO v_source;

    INSERT INTO evidence(
        school_id,evidence_type,source_id,locator,confidence_class,quality_status
    ) VALUES (
        v_school,'asr_regression_fixture',v_source,
        '{"candidate":"r25.13","failure":"independent-meta-regression"}'::jsonb,
        'HIGH','verified'
    ) RETURNING evidence_id INTO v_evidence;

    INSERT INTO analysis_run(
        school_id,algorithm_key,algorithm_version,run_status,
        parameters_snapshot,qc_summary,
        technical_record_status,quality_confirmation_status,
        publication_authorization_status
    ) VALUES (
        v_school,'bridge-video-master-analysis','3.1-free-r25.13-checkpoint','running',
        '{"job_id":"r25-13-meta-lifecycle-test","source_drive_id":"fixture-source"}'::jsonb,
        '{"meta_evidence_gate":{"status":"FAIL","publicationAllowed":false}}'::jsonb,
        'recorded','pending','blocked'
    ) RETURNING analysis_run_id INTO v_run;

    IF NOT EXISTS (
        SELECT 1 FROM analysis_run
         WHERE analysis_run_id=v_run
           AND algorithm_version_id IS NOT NULL
           AND run_status='running'
    ) THEN
        RAISE EXCEPTION 'r25.13 technical record was not staged correctly';
    END IF;

    BEGIN
        PERFORM record_bridge_video_meta_assessment(
            v_run,'pass',ARRAY[v_evidence],
            '{"independent":true,"selfReportedApproval":false,"baseCoveragePassed":true,"unreliableDerivedEvidenceCount":0,"hallucinationBlocks":[],"block11RegressionPassed":true,"priorGoodReportRegressionPassed":true}'::jsonb,
            'r25-13-pass-must-remain-disabled-v1'
        );
        RAISE EXCEPTION 'r25.13 PASS unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='r25.13 PASS unexpectedly accepted' THEN
            RAISE;
        END IF;
        IF position('r25.13 PASS is intentionally disabled' in SQLERRM)=0 THEN
            RAISE;
        END IF;
    END;

    v_gate := record_bridge_video_meta_assessment(
        v_run,'quarantined',ARRAY[v_evidence],
        '{"independent":true,"selfReportedApproval":false,"baseCoveragePassed":false,"unreliableDerivedEvidenceCount":0,"hallucinationBlocks":[10,11],"diagnosticReceiptDriveId":"fixture-receipt","block11DigitalSilenceConfirmed":false,"block11NoVadHallucinationsRejected":true,"block11ResumeAsNoSpeechAllowed":false,"publicationAllowed":false}'::jsonb,
        'r25-13-independent-terminal-quarantine-v1'
    );

    v_gate_again := record_bridge_video_meta_assessment(
        v_run,'quarantined',ARRAY[v_evidence],
        '{"independent":true,"selfReportedApproval":false,"baseCoveragePassed":false,"unreliableDerivedEvidenceCount":0,"hallucinationBlocks":[10,11],"diagnosticReceiptDriveId":"fixture-receipt","block11DigitalSilenceConfirmed":false,"block11NoVadHallucinationsRejected":true,"block11ResumeAsNoSpeechAllowed":false,"publicationAllowed":false}'::jsonb,
        'r25-13-independent-terminal-quarantine-v1'
    );

    IF v_gate IS DISTINCT FROM v_gate_again THEN
        RAISE EXCEPTION 'r25.13 META quarantine rerun was not idempotent';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM analysis_run
         WHERE analysis_run_id=v_run
           AND run_status='failed'
           AND completed_at IS NOT NULL
           AND technical_record_status='quarantined'
           AND quality_confirmation_status='rejected'
           AND publication_authorization_status='blocked'
    ) THEN
        RAISE EXCEPTION 'r25.13 independent quarantine did not terminalize the run';
    END IF;

    SELECT count(*) INTO v_gate_count
      FROM bridge_video_evidence_gate
     WHERE analysis_run_id=v_run
       AND assessment_status='quarantined'
       AND assessor_authority='independent_meta'
       AND NOT self_reported
       AND NOT publication_allowed
       AND evidence_ids=ARRAY[v_evidence];
    IF v_gate_count <> 1 THEN
        RAISE EXCEPTION 'expected one r25.13 META gate row, found %',v_gate_count;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM quality_issue
         WHERE target_entity_id=v_run
           AND issue_type='META_EVIDENCE_GATE_FAILED'
           AND severity='critical'
           AND status='confirmed'
           AND evidence_ids=ARRAY[v_evidence]
    ) THEN
        RAISE EXCEPTION 'r25.13 terminal quarantine lacks quality-issue evidence';
    END IF;

    SELECT count(*) INTO v_outbox_count
      FROM outbox_message o
      JOIN domain_event de ON de.event_id=o.event_id
     WHERE de.aggregate_id=v_run
       AND de.event_type='BridgeVideoMetaAssessed'
       AND o.status='published'
       AND o.published_at IS NOT NULL
       AND de.event_position IS NOT NULL;
    IF v_outbox_count <> 1 THEN
        RAISE EXCEPTION 'r25.13 META event was not synchronously published exactly once';
    END IF;

    BEGIN
        INSERT INTO output_publication(
            school_id,analysis_run_id,publication_type,status,published_at
        ) VALUES (
            v_school,v_run,'lesson_master_analysis_pdf','published',now()
        );
        RAISE EXCEPTION 'r25.13 quarantined output was published';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='r25.13 quarantined output was published' THEN
            RAISE;
        END IF;
    END;

    IF has_function_privilege(
        'bridge_school_worker',
        'record_bridge_video_meta_assessment(uuid,text,uuid[],jsonb,text)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'worker can self-approve/quarantine META gate';
    END IF;
END $$;

ROLLBACK;