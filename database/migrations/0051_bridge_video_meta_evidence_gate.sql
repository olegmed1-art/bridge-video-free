\set ON_ERROR_STOP on
BEGIN;

-- Independent, fail-closed publication authority for Bridge Video candidates.
-- A worker may record technical output, but cannot confirm quality or authorize use.

DO $$
DECLARE
    r record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_school_meta') THEN
        CREATE ROLE bridge_school_meta NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_school_meta_principal') THEN
        CREATE ROLE bridge_school_meta_principal NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;
    SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication
      INTO r FROM pg_roles WHERE rolname='bridge_school_meta';
    IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication THEN
        RAISE EXCEPTION 'bridge_school_meta has unsafe attributes';
    END IF;
END $$;

COMMENT ON ROLE bridge_school_meta IS
    'Independent quality authority; deliberately not inherited by Bridge Video workers';
GRANT bridge_school_reader TO bridge_school_meta;
GRANT bridge_school_meta TO bridge_school_meta_principal;
GRANT bridge_school_meta TO CURRENT_USER;
REVOKE CREATE ON SCHEMA public FROM bridge_school_meta;
GRANT USAGE ON SCHEMA public TO bridge_school_meta;

WITH target_algorithm AS (
    SELECT algorithm_id
      FROM algorithm a
      JOIN school s USING (school_id)
     WHERE s.stable_name='Школа спортивного бриджа'
       AND a.stable_key='bridge-video-master-analysis'
), next_version AS (
    SELECT algorithm_id, COALESCE(max(version_no),0)+1 AS version_no
      FROM target_algorithm
      LEFT JOIN algorithm_version USING (algorithm_id)
     GROUP BY algorithm_id
)
INSERT INTO algorithm_version(
    algorithm_id, version_no, version_label, configuration, status
)
SELECT
    algorithm_id,
    version_no,
    '3.1-free-r25.12-meta',
    '{"registration_basis":"meta_candidate","runtime_module":"bridge_runtime_hardening_r25_12_meta.py","production_allowed":false}'::jsonb,
    'candidate'
FROM next_version
ON CONFLICT (algorithm_id, version_no) DO NOTHING;

DO $$
DECLARE
    v_algorithm uuid;
    v_count integer;
BEGIN
    SELECT a.algorithm_id INTO v_algorithm
      FROM algorithm a JOIN school s USING (school_id)
     WHERE s.stable_name='Школа спортивного бриджа'
       AND a.stable_key='bridge-video-master-analysis';
    SELECT count(*) INTO v_count
      FROM algorithm_version
     WHERE algorithm_id=v_algorithm
       AND version_label='3.1-free-r25.12-meta'
       AND status='candidate';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'r25.12 META candidate registration is not unique';
    END IF;
END $$;

ALTER TABLE quality_issue
    ADD COLUMN IF NOT EXISTS evidence_ids uuid[] NOT NULL DEFAULT '{}';

ALTER TABLE analysis_run
    ADD COLUMN IF NOT EXISTS technical_record_status text NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS quality_confirmation_status text NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS publication_authorization_status text NOT NULL DEFAULT 'blocked';

ALTER TABLE analysis_run DROP CONSTRAINT IF EXISTS analysis_run_technical_record_status_ck;
ALTER TABLE analysis_run ADD CONSTRAINT analysis_run_technical_record_status_ck
    CHECK (technical_record_status IN ('pending','recorded','quarantined','failed'));
ALTER TABLE analysis_run DROP CONSTRAINT IF EXISTS analysis_run_quality_confirmation_status_ck;
ALTER TABLE analysis_run ADD CONSTRAINT analysis_run_quality_confirmation_status_ck
    CHECK (quality_confirmation_status IN ('pending','confirmed','rejected'));
ALTER TABLE analysis_run DROP CONSTRAINT IF EXISTS analysis_run_publication_authorization_status_ck;
ALTER TABLE analysis_run ADD CONSTRAINT analysis_run_publication_authorization_status_ck
    CHECK (publication_authorization_status IN ('blocked','allowed','revoked'));

CREATE TABLE bridge_video_evidence_gate (
    bridge_video_evidence_gate_id uuid PRIMARY KEY,
    school_id uuid NOT NULL REFERENCES school(school_id),
    analysis_run_id uuid NOT NULL REFERENCES analysis_run(analysis_run_id),
    quality_assessment_id uuid NOT NULL REFERENCES quality_assessment(quality_assessment_id),
    assessment_status text NOT NULL,
    assessor_authority text NOT NULL,
    self_reported boolean NOT NULL DEFAULT false,
    evidence_ids uuid[] NOT NULL,
    checks jsonb NOT NULL DEFAULT '{}'::jsonb,
    publication_allowed boolean NOT NULL DEFAULT false,
    idempotency_key text NOT NULL,
    assessed_at timestamptz NOT NULL DEFAULT now(),
    CHECK (assessment_status IN ('pass','fail','quarantined')),
    CHECK (assessor_authority='independent_meta'),
    CHECK (NOT self_reported),
    CHECK (cardinality(evidence_ids) > 0),
    CHECK ((assessment_status='pass') = publication_allowed),
    UNIQUE (analysis_run_id, idempotency_key)
);
CREATE INDEX bridge_video_evidence_gate_latest_idx
    ON bridge_video_evidence_gate(analysis_run_id, assessed_at DESC);

CREATE OR REPLACE FUNCTION reject_bridge_video_gate_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Bridge Video evidence-gate history is append-only';
END;
$$;
CREATE TRIGGER bridge_video_gate_append_only
BEFORE UPDATE OR DELETE ON bridge_video_evidence_gate
FOR EACH ROW EXECUTE FUNCTION reject_bridge_video_gate_mutation();

CREATE OR REPLACE FUNCTION bridge_video_has_independent_pass(p_run_id uuid)
RETURNS boolean
LANGUAGE sql STABLE
SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (
        SELECT 1
          FROM bridge_video_evidence_gate g
         WHERE g.analysis_run_id=p_run_id
           AND g.assessment_status='pass'
           AND g.assessor_authority='independent_meta'
           AND NOT g.self_reported
           AND g.publication_allowed
           AND cardinality(g.evidence_ids) > 0
    );
$$;

CREATE OR REPLACE FUNCTION guard_bridge_video_analysis_state()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.algorithm_key <> 'bridge-video-master-analysis'
       OR NEW.algorithm_version <> '3.1-free-r25.12-meta' THEN
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
CREATE TRIGGER bridge_video_analysis_state_guard
BEFORE INSERT OR UPDATE ON analysis_run
FOR EACH ROW EXECUTE FUNCTION guard_bridge_video_analysis_state();

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
    IF v_key='bridge-video-master-analysis' AND v_version='3.1-free-r25.12-meta'
       AND (v_status <> 'success' OR NOT bridge_video_has_independent_pass(NEW.analysis_run_id)) THEN
        RAISE EXCEPTION 'independent META PASS required before output publication';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER bridge_video_output_publication_guard
BEFORE INSERT OR UPDATE OF analysis_run_id, status ON output_publication
FOR EACH ROW EXECUTE FUNCTION guard_bridge_video_publication();

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
    IF v_key='bridge-video-master-analysis' AND v_version='3.1-free-r25.12-meta'
       AND NOT bridge_video_has_independent_pass(NEW.generated_by_analysis_run_id) THEN
        RAISE EXCEPTION 'independent META PASS required before artifact activation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER bridge_video_artifact_activation_guard
BEFORE INSERT OR UPDATE OF generated_by_analysis_run_id, status ON artifact_version
FOR EACH ROW EXECUTE FUNCTION guard_bridge_video_artifact_activation();

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
    v_version text;
    v_gate_id uuid := md5('bridge-video-gate:' || p_analysis_run_id::text || ':' || p_idempotency_key)::uuid;
    v_assessment_id uuid := md5('bridge-video-assessment:' || p_analysis_run_id::text || ':' || p_idempotency_key)::uuid;
    v_changeset_id uuid := md5('bridge-video-meta-changeset:' || p_analysis_run_id::text || ':' || p_idempotency_key)::uuid;
    v_command_id uuid := md5('bridge-video-meta-command:' || p_analysis_run_id::text || ':' || p_idempotency_key)::uuid;
    v_event_id uuid := md5('bridge-video-meta-event:' || p_analysis_run_id::text || ':' || p_idempotency_key)::uuid;
    v_payload jsonb;
    v_payload_hash text;
    v_pass boolean;
BEGIN
    -- Database owners may own several capability roles for migrations, so authority is
    -- decided by the dedicated EXECUTE grant. Runtime workers receive no membership and
    -- no EXECUTE privilege; the separate META principal receives only this capability.
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

    SELECT school_id, algorithm_version INTO v_school, v_version
      FROM analysis_run WHERE analysis_run_id=p_analysis_run_id FOR UPDATE;
    IF v_school IS NULL OR v_version <> '3.1-free-r25.12-meta' THEN
        RAISE EXCEPTION 'META assessment target is not the r25.12 candidate';
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

    v_payload := jsonb_build_object(
        'analysis_run_id',p_analysis_run_id,
        'assessment_status',p_assessment_status,
        'evidence_ids',p_evidence_ids,
        'checks',COALESCE(p_checks,'{}'::jsonb),
        'idempotency_key',p_idempotency_key
    );
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
        upper(p_assessment_status),'bridge-video-meta-gate-v1',p_evidence_ids
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
            jsonb_build_object('checks',COALESCE(p_checks,'{}'::jsonb)),
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
    VALUES (v_changeset_id,v_event_id);
    UPDATE changeset SET status='committed',committed_at=now()
     WHERE changeset_id=v_changeset_id;
    RETURN v_gate_id;
END;
$$;

REVOKE ALL ON FUNCTION record_bridge_video_meta_assessment(uuid,text,uuid[],jsonb,text)
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker;
GRANT EXECUTE ON FUNCTION record_bridge_video_meta_assessment(uuid,text,uuid[],jsonb,text)
TO bridge_school_meta;

GRANT SELECT ON bridge_video_evidence_gate TO bridge_school_meta;
REVOKE INSERT, UPDATE, DELETE ON bridge_video_evidence_gate
FROM bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_meta;
REVOKE ALL ON FUNCTION reject_bridge_video_gate_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_bridge_video_analysis_state() FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_bridge_video_publication() FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_bridge_video_artifact_activation() FROM PUBLIC;

INSERT INTO schema_migration(migration_key)
VALUES ('0051_bridge_video_meta_evidence_gate')
ON CONFLICT DO NOTHING;

COMMIT;
