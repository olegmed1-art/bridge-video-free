\set ON_ERROR_STOP on
BEGIN;

-- Extend the existing independent Bridge Video META evidence gate to the
-- r25.13 checkpoint candidate. r25.13 remains diagnostic-only: independent
-- META may fail/quarantine it, but PASS/publication authority stays disabled.

-- Register the checkpoint identity before allowing AnalysisRun rows to target it.
-- Registration is provenance only; candidate status does not activate production.
WITH target_algorithm AS (
    SELECT a.algorithm_id
      FROM algorithm a
      JOIN school s USING (school_id)
     WHERE s.stable_name='Школа спортивного бриджа'
       AND a.stable_key='bridge-video-master-analysis'
), next_version AS (
    SELECT ta.algorithm_id, COALESCE(max(av.version_no),0)+1 AS version_no
      FROM target_algorithm ta
      LEFT JOIN algorithm_version av USING (algorithm_id)
     GROUP BY ta.algorithm_id
)
INSERT INTO algorithm_version(
    algorithm_id, version_no, version_label, configuration, status
)
SELECT
    nv.algorithm_id,
    nv.version_no,
    '3.1-free-r25.13-checkpoint',
    '{"registration_basis":"meta_diagnostic_checkpoint","runtime_module":"bridge_runtime_hardening_r25_13_checkpoint.py","production_allowed":false,"meta_pass_allowed":false}'::jsonb,
    'candidate'
FROM next_version nv
WHERE NOT EXISTS (
    SELECT 1
      FROM algorithm_version av
     WHERE av.algorithm_id=nv.algorithm_id
       AND av.version_label='3.1-free-r25.13-checkpoint'
)
ON CONFLICT DO NOTHING;

DO $$
DECLARE
    v_algorithm uuid;
    v_count integer;
BEGIN
    SELECT a.algorithm_id INTO v_algorithm
      FROM algorithm a
      JOIN school s USING (school_id)
     WHERE s.stable_name='Школа спортивного бриджа'
       AND a.stable_key='bridge-video-master-analysis';
    IF v_algorithm IS NULL THEN
        RAISE EXCEPTION 'canonical Bridge Video algorithm registry row missing';
    END IF;
    SELECT count(*) INTO v_count
      FROM algorithm_version
     WHERE algorithm_id=v_algorithm
       AND version_label='3.1-free-r25.13-checkpoint'
       AND status='candidate';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'r25.13 checkpoint candidate registration is not unique';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION guard_bridge_video_analysis_state()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.algorithm_key <> 'bridge-video-master-analysis'
       OR NEW.algorithm_version NOT IN (
            '3.1-free-r25.12-meta',
            '3.1-free-r25.13-checkpoint'
       ) THEN
        RETURN NEW;
    END IF;
    IF NEW.algorithm_version_id IS NULL THEN
        RAISE EXCEPTION 'Bridge Video candidate requires algorithm_version_id';
    END IF;
    IF TG_OP='INSERT' AND NEW.run_status NOT IN ('running','failed') THEN
        RAISE EXCEPTION 'Bridge Video candidate must first be technically recorded';
    END IF;
    IF NEW.run_status='success' OR NEW.quality_confirmation_status='confirmed'
       OR NEW.publication_authorization_status='allowed' THEN
        IF NOT bridge_video_has_independent_pass(NEW.analysis_run_id) THEN
            RAISE EXCEPTION 'independent META PASS required before success/publication';
        END IF;
    END IF;
    IF NEW.run_status='success'
       AND (NEW.technical_record_status <> 'recorded'
            OR NEW.quality_confirmation_status <> 'confirmed'
            OR NEW.publication_authorization_status <> 'allowed') THEN
        RAISE EXCEPTION 'Bridge Video success requires recorded/confirmed/allowed states';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION guard_bridge_video_publication()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_key text;
    v_version text;
    v_status text;
BEGIN
    IF NEW.analysis_run_id IS NULL OR NEW.status NOT IN ('validated','published') THEN
        RETURN NEW;
    END IF;
    SELECT algorithm_key, algorithm_version, run_status
      INTO v_key, v_version, v_status
      FROM analysis_run WHERE analysis_run_id=NEW.analysis_run_id;
    IF v_key='bridge-video-master-analysis'
       AND v_version IN ('3.1-free-r25.12-meta','3.1-free-r25.13-checkpoint')
       AND (v_status <> 'success' OR NOT bridge_video_has_independent_pass(NEW.analysis_run_id)) THEN
        RAISE EXCEPTION 'independent META PASS required before output publication';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION guard_bridge_video_artifact_activation()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_key text;
    v_version text;
BEGIN
    IF NEW.status <> 'active' OR NEW.generated_by_analysis_run_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT algorithm_key, algorithm_version INTO v_key, v_version
      FROM analysis_run WHERE analysis_run_id=NEW.generated_by_analysis_run_id;
    IF v_key='bridge-video-master-analysis'
       AND v_version IN ('3.1-free-r25.12-meta','3.1-free-r25.13-checkpoint')
       AND NOT bridge_video_has_independent_pass(NEW.generated_by_analysis_run_id) THEN
        RAISE EXCEPTION 'independent META PASS required before artifact activation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION record_bridge_video_meta_assessment(
    p_analysis_run_id uuid,
    p_assessment_status text,
    p_evidence_ids uuid[],
    p_checks jsonb,
    p_idempotency_key text
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_school uuid;
    v_key text;
    v_version text;
    v_gate_id uuid := md5('bridge-video-gate:' || p_analysis_run_id::text || ':' || p_idempotency_key)::uuid;
    v_assessment_id uuid := md5('bridge-video-assessment:' || p_analysis_run_id::text || ':' || p_idempotency_key)::uuid;
    v_changeset_id uuid := md5('bridge-video-meta-changeset:' || p_analysis_run_id::text || ':' || p_idempotency_key)::uuid;
    v_command_id uuid := md5('bridge-video-meta-command:' || p_analysis_run_id::text || ':' || p_idempotency_key)::uuid;
    v_event_id uuid := md5('bridge-video-meta-event:' || p_analysis_run_id::text || ':' || p_idempotency_key)::uuid;
    v_outbox_id uuid;
    v_payload jsonb;
    v_payload_hash text;
    v_pass boolean;
BEGIN
    IF NOT pg_has_role(session_user, 'bridge_school_meta', 'member') THEN
        RAISE EXCEPTION 'independent META authority required';
    END IF;
    IF p_assessment_status NOT IN ('pass','fail','quarantined') THEN
        RAISE EXCEPTION 'unsupported META assessment status';
    END IF;
    IF p_idempotency_key IS NULL OR btrim(p_idempotency_key)='' THEN
        RAISE EXCEPTION 'META assessment requires idempotency key';
    END IF;
    IF p_evidence_ids IS NULL OR cardinality(p_evidence_ids)=0 THEN
        RAISE EXCEPTION 'META assessment requires evidence_ids';
    END IF;

    SELECT school_id, algorithm_key, algorithm_version
      INTO v_school, v_key, v_version
      FROM analysis_run
     WHERE analysis_run_id=p_analysis_run_id
     FOR UPDATE;
    IF v_school IS NULL
       OR v_key <> 'bridge-video-master-analysis'
       OR v_version NOT IN ('3.1-free-r25.12-meta','3.1-free-r25.13-checkpoint') THEN
        RAISE EXCEPTION 'META assessment target is not a supported Bridge Video gated candidate';
    END IF;
    IF v_version='3.1-free-r25.13-checkpoint' AND p_assessment_status='pass' THEN
        RAISE EXCEPTION 'r25.13 PASS is intentionally disabled; production authority remains outside this diagnostic candidate';
    END IF;
    IF EXISTS (
        SELECT 1 FROM unnest(p_evidence_ids) e(id)
        LEFT JOIN evidence ev ON ev.evidence_id=e.id AND ev.school_id=v_school
        WHERE ev.evidence_id IS NULL
    ) THEN
        RAISE EXCEPTION 'META assessment contains missing or cross-school evidence';
    END IF;

    v_pass := p_assessment_status='pass';
    IF v_pass AND (
        COALESCE((p_checks->>'independent')::boolean,false) IS NOT TRUE
        OR COALESCE((p_checks->>'selfReportedApproval')::boolean,true) IS NOT FALSE
        OR COALESCE((p_checks->>'baseCoveragePassed')::boolean,false) IS NOT TRUE
        OR COALESCE((p_checks->>'unreliableDerivedEvidenceCount')::integer,-1) <> 0
        OR jsonb_typeof(p_checks->'hallucinationBlocks') <> 'array'
        OR jsonb_array_length(p_checks->'hallucinationBlocks') <> 0
        OR COALESCE((p_checks->>'block11RegressionPassed')::boolean,false) IS NOT TRUE
        OR COALESCE((p_checks->>'priorGoodReportRegressionPassed')::boolean,false) IS NOT TRUE
    ) THEN
        RAISE EXCEPTION 'independent PASS checks are incomplete or unsafe';
    END IF;

    -- Preserve the exact r25.12 event payload shape so already-recorded
    -- idempotency keys retain their historical payload hashes.
    IF v_version='3.1-free-r25.12-meta' THEN
        v_payload := jsonb_build_object(
            'analysis_run_id',p_analysis_run_id,
            'assessment_status',p_assessment_status,
            'evidence_ids',p_evidence_ids,
            'checks',COALESCE(p_checks,'{}'::jsonb),
            'idempotency_key',p_idempotency_key
        );
    ELSE
        v_payload := jsonb_build_object(
            'analysis_run_id',p_analysis_run_id,
            'algorithm_version',v_version,
            'assessment_status',p_assessment_status,
            'evidence_ids',p_evidence_ids,
            'checks',COALESCE(p_checks,'{}'::jsonb),
            'idempotency_key',p_idempotency_key
        );
    END IF;
    v_payload_hash := encode(digest(convert_to(v_payload::text,'UTF8'),'sha256'),'hex');

    IF EXISTS (
        SELECT 1 FROM bridge_video_evidence_gate
         WHERE analysis_run_id=p_analysis_run_id AND idempotency_key=p_idempotency_key
    ) THEN
        IF NOT EXISTS (
            SELECT 1 FROM domain_event
             WHERE event_id=v_event_id AND payload_hash=v_payload_hash
        ) THEN
            RAISE EXCEPTION 'META idempotency key reused with different payload';
        END IF;
        SELECT outbox_id INTO v_outbox_id
          FROM outbox_message
         WHERE event_id=v_event_id;
        IF v_outbox_id IS NOT NULL THEN
            PERFORM publish_outbox_event(v_outbox_id);
        END IF;
        RETURN v_gate_id;
    END IF;

    INSERT INTO changeset(changeset_id,command_id,school_id,status,correlation_id)
    VALUES (v_changeset_id,v_command_id,v_school,'started',v_changeset_id);

    INSERT INTO quality_assessment(
        quality_assessment_id,school_id,target_entity_id,target_entity_type,
        dimension,score,quality_class,method_version,evidence_ids
    ) VALUES (
        v_assessment_id,v_school,p_analysis_run_id,'analysis_run',
        'independent_meta_evidence_gate',CASE WHEN v_pass THEN 1 ELSE 0 END,
        upper(p_assessment_status),
        CASE WHEN v_version='3.1-free-r25.12-meta'
             THEN 'bridge-video-meta-gate-v1'
             ELSE 'bridge-video-meta-gate-v2' END,
        p_evidence_ids
    );

    INSERT INTO bridge_video_evidence_gate(
        bridge_video_evidence_gate_id,school_id,analysis_run_id,
        quality_assessment_id,assessment_status,assessor_authority,
        self_reported,evidence_ids,checks,publication_allowed,idempotency_key
    ) VALUES (
        v_gate_id,v_school,p_analysis_run_id,v_assessment_id,p_assessment_status,
        'independent_meta',false,p_evidence_ids,COALESCE(p_checks,'{}'::jsonb),
        v_pass,p_idempotency_key
    );

    IF v_pass THEN
        UPDATE analysis_run
           SET run_status='success', completed_at=now(),
               technical_record_status='recorded',
               quality_confirmation_status='confirmed',
               publication_authorization_status='allowed'
         WHERE analysis_run_id=p_analysis_run_id;
    ELSE
        INSERT INTO quality_issue(
            quality_issue_id,school_id,target_entity_id,target_entity_type,
            issue_type,severity,locator,description,status,evidence_ids
        ) VALUES (
            md5('bridge-video-quality-issue:' || p_analysis_run_id::text || ':' || p_idempotency_key)::uuid,
            v_school,p_analysis_run_id,'analysis_run','META_EVIDENCE_GATE_FAILED','critical',
            jsonb_build_object('checks',COALESCE(p_checks,'{}'::jsonb),'algorithm_version',v_version),
            'Independent META Evidence Gate rejected the candidate','confirmed',p_evidence_ids
        );
        UPDATE analysis_run
           SET run_status='failed', completed_at=now(),
               technical_record_status='quarantined',
               quality_confirmation_status='rejected',
               publication_authorization_status='blocked'
         WHERE analysis_run_id=p_analysis_run_id;
    END IF;

    INSERT INTO domain_event(
        event_id,school_id,partition_key,event_type,aggregate_id,aggregate_type,
        aggregate_version,changeset_id,correlation_id,idempotency_namespace,
        idempotency_key,payload_hash,payload
    ) VALUES (
        v_event_id,v_school,'bridge-video-meta','BridgeVideoMetaAssessed',
        p_analysis_run_id,'analysis_run',2,v_changeset_id,v_changeset_id,
        'bridge-video-meta-assessment',p_idempotency_key,v_payload_hash,v_payload
    );
    INSERT INTO outbox_message(changeset_id,event_id)
    VALUES (v_changeset_id,v_event_id)
    RETURNING outbox_id INTO v_outbox_id;
    UPDATE changeset SET status='committed',committed_at=now()
     WHERE changeset_id=v_changeset_id;
    PERFORM publish_outbox_event(v_outbox_id);
    RETURN v_gate_id;
END;
$$;

REVOKE ALL ON FUNCTION record_bridge_video_meta_assessment(uuid,text,uuid[],jsonb,text)
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker;
GRANT EXECUTE ON FUNCTION record_bridge_video_meta_assessment(uuid,text,uuid[],jsonb,text)
TO bridge_school_meta;

REVOKE ALL ON FUNCTION guard_bridge_video_analysis_state() FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_bridge_video_publication() FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_bridge_video_artifact_activation() FROM PUBLIC;

INSERT INTO schema_migration(migration_key)
VALUES ('0053_bridge_video_meta_gate_revision_scope')
ON CONFLICT DO NOTHING;

COMMIT;