\set ON_ERROR_STOP on
BEGIN;

-- Stage-1 foundation exit gate: one deterministic provenance chain must survive all
-- relational/scope guards from Source -> Deal -> AnalysisRun -> ArtifactVersion.
DO $$
DECLARE
    v_school uuid;
    v_source uuid;
    v_source_asset uuid;
    v_output_asset uuid;
    v_evidence uuid;
    v_deal uuid;
    v_run uuid;
    v_artifact uuid;
    v_artifact_version uuid;
    v_count integer;
BEGIN
    SELECT school_id INTO v_school
      FROM school
     WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN
        RAISE EXCEPTION 'canonical school missing';
    END IF;

    INSERT INTO source(
        school_id, source_type, title, canonical_locator, trust_class
    ) VALUES (
        v_school,
        'foundation_e2e_test',
        'Stage-1 provenance test source',
        'test:foundation/source-1',
        'test'
    ) RETURNING source_id INTO v_source;

    INSERT INTO asset(
        school_id, asset_type, mime_type, byte_size,
        checksum_algorithm, checksum_value, immutable_flag
    ) VALUES (
        v_school,
        'foundation_source_payload',
        'text/plain',
        52,
        'sha256',
        repeat('b',64),
        true
    ) RETURNING asset_id INTO v_source_asset;

    INSERT INTO source_asset(source_id,asset_id,relation_type)
    VALUES (v_source,v_source_asset,'embodies');

    INSERT INTO evidence(
        school_id, evidence_type, source_id, asset_id,
        locator, confidence_class, quality_status
    ) VALUES (
        v_school,
        'foundation_source_observation',
        v_source,
        v_source_asset,
        jsonb_build_object('native_key','foundation/source-1'),
        'HIGH',
        'verified'
    ) RETURNING evidence_id INTO v_evidence;

    INSERT INTO deal(
        school_id,
        canonical_pbn,
        dealer,
        vulnerability,
        reconstruction_status,
        deal_fingerprint,
        source_id
    ) VALUES (
        v_school,
        'N:AKQJ.T987.654.32 T987.654.32.AKQJ 654.32.AKQJ.T987 32.AKQJ.T987.654',
        'N',
        'None',
        'VERIFIED',
        'foundation-e2e-deal-v1',
        v_source
    ) RETURNING deal_id INTO v_deal;

    INSERT INTO analysis_run(
        school_id,
        algorithm_key,
        algorithm_version,
        run_status,
        parameters_snapshot,
        qc_summary
    ) VALUES (
        v_school,
        'foundation-provenance-e2e',
        'v1',
        'running',
        jsonb_build_object('deal_id',v_deal,'evidence_id',v_evidence),
        '{"gate":"stage1"}'::jsonb
    ) RETURNING analysis_run_id INTO v_run;

    INSERT INTO analysis_run_input(
        analysis_run_id, source_id, input_role, metadata
    ) VALUES (
        v_run,
        v_source,
        'primary',
        jsonb_build_object('deal_id',v_deal,'evidence_id',v_evidence)
    );

    PERFORM record_run_checkpoint(
        'analysis',
        v_run,
        'foundation-chain',
        'completed',
        jsonb_build_object(
            'source_id',v_source,
            'deal_id',v_deal,
            'evidence_id',v_evidence
        ),
        NULL,
        '{"gate":"stage1"}'::jsonb
    );

    UPDATE analysis_run
       SET run_status='success', completed_at=now()
     WHERE analysis_run_id=v_run;

    INSERT INTO asset(
        school_id, asset_type, mime_type, byte_size,
        checksum_algorithm, checksum_value, immutable_flag
    ) VALUES (
        v_school,
        'foundation_analysis_output',
        'application/json',
        1,
        'sha256',
        repeat('c',64),
        true
    ) RETURNING asset_id INTO v_output_asset;

    INSERT INTO artifact(
        school_id, artifact_type, title, status
    ) VALUES (
        v_school,
        'foundation_e2e_result',
        'Stage-1 provenance test artifact',
        'active'
    ) RETURNING artifact_id INTO v_artifact;

    INSERT INTO artifact_version(
        artifact_id,
        version_no,
        version_label,
        asset_id,
        generated_by_analysis_run_id,
        generation_method,
        provenance,
        status
    ) VALUES (
        v_artifact,
        1,
        'foundation-e2e-v1',
        v_output_asset,
        v_run,
        'foundation-provenance-e2e',
        jsonb_build_object(
            'source_id',v_source,
            'source_asset_id',v_source_asset,
            'deal_id',v_deal,
            'evidence_id',v_evidence,
            'analysis_run_id',v_run
        ),
        'candidate'
    ) RETURNING artifact_version_id INTO v_artifact_version;

    INSERT INTO analysis_run_output(
        analysis_run_id,
        output_entity_id,
        output_entity_type,
        artifact_version_id,
        output_role,
        status
    ) VALUES (
        v_run,
        v_artifact,
        'artifact',
        v_artifact_version,
        'derived',
        'validated'
    );

    -- Verify the complete chain with no name/folder inference.
    SELECT count(*) INTO v_count
      FROM source s
      JOIN deal d
        ON d.source_id=s.source_id
       AND d.school_id=s.school_id
      JOIN analysis_run ar
        ON ar.analysis_run_id=v_run
       AND ar.school_id=s.school_id
      JOIN analysis_run_input ari
        ON ari.analysis_run_id=ar.analysis_run_id
       AND ari.source_id=s.source_id
      JOIN artifact_version av
        ON av.generated_by_analysis_run_id=ar.analysis_run_id
       AND av.artifact_version_id=v_artifact_version
      JOIN artifact a
        ON a.artifact_id=av.artifact_id
       AND a.school_id=s.school_id
      JOIN analysis_run_output aro
        ON aro.analysis_run_id=ar.analysis_run_id
       AND aro.artifact_version_id=av.artifact_version_id
       AND aro.output_entity_id=a.artifact_id
     WHERE s.source_id=v_source
       AND d.deal_id=v_deal
       AND ar.run_status='success'
       AND av.provenance->>'source_id'=v_source::text
       AND av.provenance->>'deal_id'=v_deal::text
       AND av.provenance->>'evidence_id'=v_evidence::text
       AND av.provenance->>'analysis_run_id'=v_run::text;

    IF v_count <> 1 THEN
        RAISE EXCEPTION 'Stage-1 Source->Deal->Run->Artifact provenance chain is incomplete';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM latest_run_checkpoint
         WHERE run_type='analysis'
           AND run_id=v_run
           AND checkpoint_state='completed'
           AND checkpoint->>'deal_id'=v_deal::text
    ) THEN
        RAISE EXCEPTION 'Stage-1 provenance run lacks durable completed checkpoint';
    END IF;
END $$;

ROLLBACK;
