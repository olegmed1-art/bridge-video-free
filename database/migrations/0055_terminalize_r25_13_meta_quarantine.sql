\set ON_ERROR_STOP on
BEGIN;

-- One-time, evidence-backed repair for the r25.13 diagnostic run that was left
-- in `running` before migration 0053 taught the independent META gate how to
-- terminalize that revision. Fresh databases have no matching AnalysisRun and
-- therefore execute only the validation-free no-op path plus migration registry.
DO $$
DECLARE
    v_run_id constant uuid := 'fd23b047-db2e-5c29-aaa6-adf6d54f27c1'::uuid;
    v_audio_evidence constant uuid := 'bb36e652-93ac-5da0-19f0-c85598a527be'::uuid;
    v_asr_evidence constant uuid := 'f436c1c0-63ee-f5c6-8c3c-782b0bec4b7d'::uuid;
    v_idempotency constant text := 'r25-13-independent-terminal-quarantine-20260820-v1';
    v_run analysis_run%ROWTYPE;
    v_gate_id uuid;
BEGIN
    SELECT * INTO v_run
      FROM analysis_run
     WHERE analysis_run_id=v_run_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RETURN;
    END IF;

    IF v_run.algorithm_key <> 'bridge-video-master-analysis'
       OR v_run.algorithm_version <> '3.1-free-r25.13-checkpoint'
       OR v_run.parameters_snapshot->>'job_id' <> '86e814014cabee88785a53340ab85666'
       OR v_run.parameters_snapshot->>'source_drive_id' <> '1rGX92YskXRtXHc53lyj9JMU3g24H5vCI' THEN
        RAISE EXCEPTION 'r25.13 remediation target identity mismatch';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM evidence e
         WHERE e.evidence_id=v_audio_evidence
           AND e.school_id=v_run.school_id
           AND e.evidence_type='audio_quality_control'
           AND e.quality_status='verified'
           AND e.locator->>'block'='11'
           AND e.locator->>'mean_rms'='0'
           AND e.locator->>'peak_absolute_sample'='0'
           AND e.locator->>'active_frame_ratio'='0'
           AND e.locator->>'receipt_drive_id'='1aw5OSpDP48DsNX--WuOdDl8uhf893Taa'
           AND e.locator->>'source_sha256'='fa053465e27662225c223218d88e6c4c9623319e8a830eed8e5b9ac54e610ab0'
           AND COALESCE((e.locator->>'source_integrity_reverified')::boolean,false)
    ) THEN
        RAISE EXCEPTION 'r25.13 audio evidence mismatch';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM evidence e
         WHERE e.evidence_id=v_asr_evidence
           AND e.school_id=v_run.school_id
           AND e.evidence_type='asr_quality_control'
           AND e.quality_status='quarantined'
           AND e.locator->>'block'='11'
           AND e.locator->>'failure_reason'='REPEATED_NONSPEECH_HALLUCINATION'
           AND e.locator->>'receipt_drive_id'='1aw5OSpDP48DsNX--WuOdDl8uhf893Taa'
           AND COALESCE((e.locator->>'forced_no_vad_output_rejected_on_zero_pcm')::boolean,false)
           AND e.locator->'strict_vad_passes' @> '[{"model":"medium","word_count":0},{"model":"small","word_count":0}]'::jsonb
           AND e.locator->'hallucination_passes' @> '[{"mode":"no-vad","model":"medium","word_count":13},{"mode":"no-vad","model":"small","word_count":10}]'::jsonb
    ) THEN
        RAISE EXCEPTION 'r25.13 ASR evidence mismatch';
    END IF;

    IF v_run.run_status='running'
       AND v_run.technical_record_status='recorded'
       AND v_run.quality_confirmation_status='pending'
       AND v_run.publication_authorization_status='blocked' THEN
        v_gate_id := record_bridge_video_meta_assessment(
            v_run_id,
            'quarantined',
            ARRAY[v_audio_evidence,v_asr_evidence],
            '{"independent":true,"selfReportedApproval":false,"baseCoveragePassed":false,"publicationAllowed":false,"hallucinationBlocks":[11],"diagnosticReceiptDriveId":"1aw5OSpDP48DsNX--WuOdDl8uhf893Taa","block11DigitalSilenceConfirmed":true,"block11StrictVadNoSpeech":true,"block11ForcedNoVadOutputRejected":true,"failureReason":"REPEATED_NONSPEECH_HALLUCINATION"}'::jsonb,
            v_idempotency
        );
    ELSIF NOT (
        v_run.run_status='failed'
        AND v_run.technical_record_status='quarantined'
        AND v_run.quality_confirmation_status='rejected'
        AND v_run.publication_authorization_status='blocked'
    ) THEN
        RAISE EXCEPTION 'r25.13 remediation target is in an unexpected state: %/%/%/%',
            v_run.run_status,
            v_run.technical_record_status,
            v_run.quality_confirmation_status,
            v_run.publication_authorization_status;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM analysis_run ar
         WHERE ar.analysis_run_id=v_run_id
           AND ar.run_status='failed'
           AND ar.completed_at IS NOT NULL
           AND ar.technical_record_status='quarantined'
           AND ar.quality_confirmation_status='rejected'
           AND ar.publication_authorization_status='blocked'
    ) THEN
        RAISE EXCEPTION 'r25.13 remediation did not reach the expected terminal state';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM bridge_video_evidence_gate g
         WHERE g.analysis_run_id=v_run_id
           AND g.assessment_status='quarantined'
           AND g.assessor_authority='independent_meta'
           AND NOT g.self_reported
           AND NOT g.publication_allowed
           AND g.idempotency_key=v_idempotency
           AND g.evidence_ids=ARRAY[v_audio_evidence,v_asr_evidence]
    ) THEN
        RAISE EXCEPTION 'r25.13 remediation lacks the expected independent META gate';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM quality_issue qi
         WHERE qi.target_entity_id=v_run_id
           AND qi.issue_type='META_EVIDENCE_GATE_FAILED'
           AND qi.severity='critical'
           AND qi.status='confirmed'
           AND qi.evidence_ids=ARRAY[v_audio_evidence,v_asr_evidence]
    ) THEN
        RAISE EXCEPTION 'r25.13 remediation lacks the expected quality issue';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM outbox_message o
          JOIN domain_event de ON de.event_id=o.event_id
         WHERE de.aggregate_id=v_run_id
           AND de.event_type='BridgeVideoMetaAssessed'
           AND de.idempotency_key=v_idempotency
           AND o.status='published'
           AND o.published_at IS NOT NULL
           AND de.event_position IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'r25.13 remediation META event is not durably published';
    END IF;
END $$;

INSERT INTO schema_migration(migration_key)
VALUES ('0055_terminalize_r25_13_meta_quarantine')
ON CONFLICT DO NOTHING;

COMMIT;
