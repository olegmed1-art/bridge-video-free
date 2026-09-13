\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- META reliability core.
--
-- This migration adds generic infrastructure required by the accepted META
-- architecture. It does not define bidding or teaching methodology. Methodology-class
-- corrections are explicitly protected and cannot be marked resolved without recorded
-- teacher approval.
-- -----------------------------------------------------------------------------

CREATE TABLE correction_record (
    correction_record_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    target_entity_id uuid NOT NULL,
    target_entity_type text NOT NULL,
    correction_class text NOT NULL,
    summary text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    severity text NOT NULL DEFAULT 'medium',
    material boolean NOT NULL DEFAULT true,
    regression_required boolean NOT NULL DEFAULT true,
    protected_methodology boolean NOT NULL DEFAULT false,
    teacher_approval_state text NOT NULL DEFAULT 'not_required',
    approved_by_person_id uuid REFERENCES person(person_id),
    approved_at timestamptz,
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    analysis_run_id uuid REFERENCES analysis_run(analysis_run_id),
    created_by_person_id uuid REFERENCES person(person_id),
    status text NOT NULL DEFAULT 'observed',
    resolution_notes text,
    resolved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (correction_class IN ('technical','data','analysis','methodology','presentation','security','other')),
    CHECK (severity IN ('low','medium','high','critical')),
    CHECK (teacher_approval_state IN ('not_required','pending','approved','rejected')),
    CHECK (status IN ('observed','confirmed','resolved','rejected')),
    CHECK (btrim(summary) <> ''),
    CHECK ((teacher_approval_state <> 'approved') OR (approved_by_person_id IS NOT NULL AND approved_at IS NOT NULL)),
    CHECK ((status = 'resolved' AND resolved_at IS NOT NULL) OR (status <> 'resolved' AND resolved_at IS NULL))
);
CREATE INDEX correction_record_target_idx
    ON correction_record(school_id, target_entity_type, target_entity_id, status, created_at DESC);
CREATE INDEX correction_record_open_material_idx
    ON correction_record(school_id, severity, created_at DESC)
    WHERE material AND status IN ('observed','confirmed');

CREATE TABLE regression_case (
    regression_case_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    correction_record_id uuid NOT NULL REFERENCES correction_record(correction_record_id),
    stable_key text NOT NULL,
    target_component text NOT NULL,
    test_reference text NOT NULL,
    expected_contract jsonb NOT NULL DEFAULT '{}'::jsonb,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'candidate',
    created_by_person_id uuid REFERENCES person(person_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('candidate','active','retired','invalid')),
    CHECK (btrim(stable_key) <> ''),
    CHECK (btrim(target_component) <> ''),
    CHECK (btrim(test_reference) <> ''),
    UNIQUE (school_id, stable_key)
);
CREATE INDEX regression_case_correction_idx
    ON regression_case(correction_record_id, status, created_at DESC);

CREATE TABLE regression_execution (
    regression_execution_id uuid PRIMARY KEY DEFAULT uuidv7(),
    regression_case_id uuid NOT NULL REFERENCES regression_case(regression_case_id),
    analysis_run_id uuid REFERENCES analysis_run(analysis_run_id),
    algorithm_version_id uuid REFERENCES algorithm_version(algorithm_version_id),
    result text NOT NULL,
    observed_contract jsonb NOT NULL DEFAULT '{}'::jsonb,
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    executed_at timestamptz NOT NULL DEFAULT now(),
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (result IN ('pass','fail','error','skipped'))
);
CREATE INDEX regression_execution_case_time_idx
    ON regression_execution(regression_case_id, executed_at DESC);

CREATE OR REPLACE FUNCTION validate_regression_case_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_correction_school uuid;
BEGIN
    SELECT school_id INTO v_correction_school
      FROM correction_record
     WHERE correction_record_id=NEW.correction_record_id;
    IF v_correction_school IS NULL OR v_correction_school <> NEW.school_id THEN
        RAISE EXCEPTION 'regression case school does not match correction record';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER regression_case_scope_guard
BEFORE INSERT OR UPDATE OF school_id, correction_record_id
ON regression_case
FOR EACH ROW EXECUTE FUNCTION validate_regression_case_scope();

CREATE OR REPLACE FUNCTION validate_correction_authority_and_resolution()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.correction_class='methodology' THEN
        NEW.protected_methodology := true;
        IF NEW.teacher_approval_state='not_required' THEN
            NEW.teacher_approval_state := 'pending';
        END IF;
    END IF;

    IF NEW.protected_methodology AND NEW.teacher_approval_state='not_required' THEN
        RAISE EXCEPTION 'protected methodology correction requires explicit teacher approval state';
    END IF;

    IF NEW.teacher_approval_state='approved'
       AND (NEW.approved_by_person_id IS NULL OR NEW.approved_at IS NULL) THEN
        RAISE EXCEPTION 'approved correction requires approving person and timestamp';
    END IF;

    IF NEW.status='resolved' THEN
        IF NEW.protected_methodology AND NEW.teacher_approval_state <> 'approved' THEN
            RAISE EXCEPTION 'protected methodology correction cannot resolve without teacher approval';
        END IF;
        IF NEW.regression_required AND NOT EXISTS (
            SELECT 1
              FROM regression_case rc
             WHERE rc.correction_record_id=NEW.correction_record_id
               AND rc.status IN ('candidate','active')
        ) THEN
            RAISE EXCEPTION 'material correction cannot resolve without a regression case';
        END IF;
        IF NEW.resolved_at IS NULL THEN
            NEW.resolved_at := now();
        END IF;
    ELSE
        NEW.resolved_at := NULL;
    END IF;

    RETURN NEW;
END;
$$;
CREATE TRIGGER correction_authority_resolution_guard
BEFORE INSERT OR UPDATE ON correction_record
FOR EACH ROW EXECUTE FUNCTION validate_correction_authority_and_resolution();

-- -----------------------------------------------------------------------------
-- Append-only run checkpoint history. Existing checkpoint JSON columns remain the
-- current snapshot; this event stream supplies durable sequence/history for resume and
-- failure diagnosis across Analysis/Ingestion/Projection runs.
-- -----------------------------------------------------------------------------
CREATE TABLE run_checkpoint_event (
    run_checkpoint_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    run_type text NOT NULL,
    run_id uuid NOT NULL,
    sequence_no bigint NOT NULL CHECK (sequence_no > 0),
    stage_key text NOT NULL,
    checkpoint_state text NOT NULL,
    checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_class text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (run_type IN ('analysis','ingestion','projection')),
    CHECK (checkpoint_state IN ('started','progress','completed','failed','cancelled')),
    CHECK (btrim(stage_key) <> ''),
    UNIQUE (run_type, run_id, sequence_no)
);
CREATE INDEX run_checkpoint_latest_idx
    ON run_checkpoint_event(run_type, run_id, sequence_no DESC);

CREATE VIEW latest_run_checkpoint AS
SELECT DISTINCT ON (run_type, run_id)
    run_checkpoint_event_id,
    school_id,
    run_type,
    run_id,
    sequence_no,
    stage_key,
    checkpoint_state,
    checkpoint,
    error_class,
    details,
    recorded_at
FROM run_checkpoint_event
ORDER BY run_type, run_id, sequence_no DESC;

CREATE OR REPLACE FUNCTION record_run_checkpoint(
    p_run_type text,
    p_run_id uuid,
    p_stage_key text,
    p_checkpoint_state text,
    p_checkpoint jsonb DEFAULT '{}'::jsonb,
    p_error_class text DEFAULT NULL,
    p_details jsonb DEFAULT '{}'::jsonb
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_school_id uuid;
    v_sequence bigint;
    v_event_id uuid;
BEGIN
    IF p_run_type NOT IN ('analysis','ingestion','projection') THEN
        RAISE EXCEPTION 'unsupported checkpoint run type: %', p_run_type;
    END IF;
    IF p_checkpoint_state NOT IN ('started','progress','completed','failed','cancelled') THEN
        RAISE EXCEPTION 'unsupported checkpoint state: %', p_checkpoint_state;
    END IF;
    IF p_run_id IS NULL OR p_stage_key IS NULL OR btrim(p_stage_key)='' THEN
        RAISE EXCEPTION 'checkpoint requires run id and stage key';
    END IF;

    -- Serialize sequence assignment per run while allowing unrelated runs to proceed.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_run_type || ':' || p_run_id::text, 0));

    IF p_run_type='analysis' THEN
        SELECT school_id INTO v_school_id FROM analysis_run WHERE analysis_run_id=p_run_id;
    ELSIF p_run_type='ingestion' THEN
        SELECT school_id INTO v_school_id FROM ingestion_run WHERE ingestion_run_id=p_run_id;
    ELSE
        SELECT school_id INTO v_school_id FROM projection_run WHERE projection_run_id=p_run_id;
    END IF;

    IF v_school_id IS NULL THEN
        RAISE EXCEPTION 'checkpoint target run does not exist';
    END IF;

    SELECT COALESCE(max(sequence_no),0)+1 INTO v_sequence
      FROM run_checkpoint_event
     WHERE run_type=p_run_type AND run_id=p_run_id;

    INSERT INTO run_checkpoint_event(
        school_id, run_type, run_id, sequence_no, stage_key,
        checkpoint_state, checkpoint, error_class, details
    ) VALUES (
        v_school_id, p_run_type, p_run_id, v_sequence, p_stage_key,
        p_checkpoint_state, COALESCE(p_checkpoint,'{}'::jsonb), p_error_class,
        COALESCE(p_details,'{}'::jsonb)
    ) RETURNING run_checkpoint_event_id INTO v_event_id;

    IF p_run_type='analysis' THEN
        UPDATE analysis_run SET checkpoint=COALESCE(p_checkpoint,'{}'::jsonb)
         WHERE analysis_run_id=p_run_id;
    ELSIF p_run_type='ingestion' THEN
        UPDATE ingestion_run SET checkpoint=COALESCE(p_checkpoint,'{}'::jsonb)
         WHERE ingestion_run_id=p_run_id;
    ELSE
        UPDATE projection_run SET checkpoint=COALESCE(p_checkpoint,'{}'::jsonb)
         WHERE projection_run_id=p_run_id;
    END IF;

    RETURN v_event_id;
END;
$$;

REVOKE ALL ON FUNCTION record_run_checkpoint(text,uuid,text,text,jsonb,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION record_run_checkpoint(text,uuid,text,text,jsonb,text,jsonb)
TO bridge_school_worker;

-- -----------------------------------------------------------------------------
-- Structured source rights/access observations. These are observations with provenance,
-- not legal conclusions. Runtime workers may append source-metadata observations but
-- cannot read or rewrite the protected ACL snapshots after insertion.
-- -----------------------------------------------------------------------------
CREATE TABLE source_rights_snapshot (
    source_rights_snapshot_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    source_id uuid NOT NULL REFERENCES source(source_id),
    rights_state text NOT NULL DEFAULT 'unknown',
    rights_basis text,
    allowed_uses jsonb NOT NULL DEFAULT '[]'::jsonb,
    acl_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    authority_class text NOT NULL DEFAULT 'source_metadata',
    evidence_id uuid REFERENCES evidence(evidence_id),
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (rights_state IN ('unknown','permitted','restricted','prohibited','expired')),
    CHECK (authority_class IN ('source_metadata','owner','teacher','legal','system'))
);
CREATE INDEX source_rights_snapshot_source_time_idx
    ON source_rights_snapshot(source_id, observed_at DESC);

CREATE OR REPLACE FUNCTION validate_source_rights_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_source_school uuid;
    v_evidence_school uuid;
BEGIN
    SELECT school_id INTO v_source_school FROM source WHERE source_id=NEW.source_id;
    IF v_source_school IS NULL OR v_source_school <> NEW.school_id THEN
        RAISE EXCEPTION 'source rights snapshot belongs to another school or source is missing';
    END IF;
    IF NEW.evidence_id IS NOT NULL THEN
        SELECT school_id INTO v_evidence_school FROM evidence WHERE evidence_id=NEW.evidence_id;
        IF v_evidence_school IS NULL OR v_evidence_school <> NEW.school_id THEN
            RAISE EXCEPTION 'source rights evidence belongs to another school or is missing';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER source_rights_scope_guard
BEFORE INSERT OR UPDATE OF school_id, source_id, evidence_id
ON source_rights_snapshot
FOR EACH ROW EXECUTE FUNCTION validate_source_rights_scope();

-- -----------------------------------------------------------------------------
-- Recovery checkpoint and append-only verification evidence. External references are
-- identifiers only; secrets must never be stored here.
-- -----------------------------------------------------------------------------
CREATE TABLE recovery_checkpoint (
    recovery_checkpoint_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    checkpoint_type text NOT NULL,
    provider text NOT NULL,
    external_ref text NOT NULL,
    source_fingerprint jsonb NOT NULL DEFAULT '{}'::jsonb,
    retention_until timestamptz,
    notes text,
    created_by_person_id uuid REFERENCES person(person_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (checkpoint_type IN ('branch','snapshot','export','other')),
    CHECK (btrim(provider) <> ''),
    CHECK (btrim(external_ref) <> ''),
    UNIQUE (school_id, provider, external_ref)
);

CREATE TABLE recovery_verification (
    recovery_verification_id uuid PRIMARY KEY DEFAULT uuidv7(),
    recovery_checkpoint_id uuid NOT NULL REFERENCES recovery_checkpoint(recovery_checkpoint_id),
    verification_type text NOT NULL,
    result text NOT NULL,
    observed_fingerprint jsonb NOT NULL DEFAULT '{}'::jsonb,
    restore_target_ref text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    verified_at timestamptz NOT NULL DEFAULT now(),
    CHECK (verification_type IN ('read','branch_compare','restore_test','checksum','other')),
    CHECK (result IN ('success','failure','partial'))
);
CREATE INDEX recovery_verification_checkpoint_time_idx
    ON recovery_verification(recovery_checkpoint_id, verified_at DESC);

-- -----------------------------------------------------------------------------
-- Runtime permissions.
-- -----------------------------------------------------------------------------
REVOKE INSERT, UPDATE, DELETE ON TABLE
    correction_record,
    regression_case,
    regression_execution,
    run_checkpoint_event
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

-- Internal workers can observe the reliability records through reader inheritance, create
-- candidate corrections/regressions and record executions. Approval fields remain outside
-- their write capability.
GRANT INSERT (
    school_id, target_entity_id, target_entity_type, correction_class,
    summary, details, severity, material, regression_required,
    evidence_ids, analysis_run_id, created_by_person_id
) ON correction_record TO bridge_school_worker;
GRANT UPDATE (status, resolution_notes, resolved_at) ON correction_record TO bridge_school_worker;

GRANT INSERT (
    school_id, correction_record_id, stable_key, target_component,
    test_reference, expected_contract, provenance, created_by_person_id
) ON regression_case TO bridge_school_worker;
GRANT INSERT (
    regression_case_id, analysis_run_id, algorithm_version_id, result,
    observed_contract, evidence_ids, executed_at, details
) ON regression_execution TO bridge_school_worker;

-- Direct checkpoint writes are blocked; the guarded function keeps the event history and
-- current run checkpoint in sync.
REVOKE INSERT, UPDATE, DELETE ON run_checkpoint_event FROM bridge_school_worker;

-- ACL/rights snapshots are protected from runtime reads and mutation. Worker ingestion may
-- append a source-metadata observation only; authority_class stays at its safe default.
REVOKE ALL ON TABLE source_rights_snapshot
FROM bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance, bridge_school_member,
     bridge_school_auth_gateway;
GRANT INSERT (
    school_id, source_id, rights_state, rights_basis, allowed_uses,
    acl_snapshot, evidence_id, provenance, observed_at
) ON source_rights_snapshot TO bridge_school_worker;

-- Recovery identifiers/fingerprints are owner-operated infrastructure records.
REVOKE ALL ON TABLE recovery_checkpoint, recovery_verification
FROM bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance, bridge_school_member,
     bridge_school_auth_gateway;

-- Member-facing capability must never see META internal correction/checkpoint state.
REVOKE ALL ON TABLE
    correction_record,
    regression_case,
    regression_execution,
    run_checkpoint_event,
    latest_run_checkpoint
FROM bridge_school_member, bridge_school_auth_gateway;

-- Immutable evidence/event history cannot be rewritten by runtime.
REVOKE UPDATE, DELETE ON TABLE
    regression_execution,
    run_checkpoint_event,
    source_rights_snapshot,
    recovery_checkpoint,
    recovery_verification
FROM bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance, bridge_school_member,
     bridge_school_auth_gateway;

INSERT INTO schema_migration(migration_key)
VALUES ('0046_meta_reliability_core')
ON CONFLICT DO NOTHING;

COMMIT;
