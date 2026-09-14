-- 0200_bidding_knowledge_v0 / part 04
-- Included transactionally by ../0200_bidding_knowledge_v0.sql.

CREATE TRIGGER decision_trace_school_scope_guard
BEFORE INSERT ON bidding.decision_trace
FOR EACH ROW EXECUTE FUNCTION bidding.validate_decision_trace_school_scope();

COMMENT ON TABLE bidding.decision_trace IS
  'Append-only decision trace containing the acting hand and public information only.';

CREATE INDEX bidding_decision_trace_school_time_idx
    ON bidding.decision_trace (school_id, recorded_at DESC);

CREATE INDEX bidding_decision_trace_outcome_idx
    ON bidding.decision_trace (school_id, outcome, recorded_at DESC);

CREATE TABLE bidding.ingestion_run (
    ingestion_run_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    source_id uuid REFERENCES public.source(source_id) ON DELETE RESTRICT,
    source_manifest_key text NOT NULL CHECK (btrim(source_manifest_key) <> ''),
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    repository_ref text,
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','completed','failed','stopped')),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(metadata)='object'),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    created_by_person_id uuid REFERENCES public.person(person_id) ON DELETE SET NULL,
    CHECK ((status='running' AND finished_at IS NULL) OR (status<>'running' AND finished_at IS NOT NULL)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(metadata))
);

CREATE OR REPLACE FUNCTION bidding.enforce_ingestion_run_integrity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_source_school uuid;
BEGIN
    IF NEW.source_id IS NOT NULL THEN
        SELECT school_id INTO v_source_school FROM public.source WHERE source_id=NEW.source_id;
        IF v_source_school IS NULL OR v_source_school <> NEW.school_id THEN
            RAISE EXCEPTION 'BID_INGESTION_SOURCE_SCHOOL_MISMATCH' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='UPDATE' THEN
        IF OLD.status <> 'running' THEN
            RAISE EXCEPTION 'BID_INGESTION_TERMINAL_IMMUTABLE' USING ERRCODE='55000';
        END IF;
        IF NEW.school_id <> OLD.school_id
           OR NEW.source_id IS DISTINCT FROM OLD.source_id
           OR NEW.source_manifest_key <> OLD.source_manifest_key
           OR NEW.source_sha256 <> OLD.source_sha256
           OR NEW.repository_ref IS DISTINCT FROM OLD.repository_ref
           OR NEW.metadata <> OLD.metadata
           OR NEW.started_at <> OLD.started_at
           OR NEW.created_by_person_id IS DISTINCT FROM OLD.created_by_person_id THEN
            RAISE EXCEPTION 'BID_INGESTION_IDENTITY_IMMUTABLE' USING ERRCODE='55000';
        END IF;
    END IF;
    IF NEW.status='completed' AND NEW.source_id IS NULL THEN
        RAISE EXCEPTION 'BID_INGESTION_COMPLETION_REQUIRES_SOURCE' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER ingestion_run_integrity_guard
BEFORE INSERT OR UPDATE ON bidding.ingestion_run
FOR EACH ROW EXECUTE FUNCTION bidding.enforce_ingestion_run_integrity();

CREATE INDEX bidding_ingestion_run_source_idx
    ON bidding.ingestion_run (school_id, source_manifest_key, started_at DESC);

CREATE TABLE bidding.ingestion_event (
    ingestion_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    ingestion_run_id uuid NOT NULL REFERENCES bidding.ingestion_run(ingestion_run_id) ON DELETE RESTRICT,
    event_no integer NOT NULL CHECK (event_no > 0),
    role_key text NOT NULL CHECK (btrim(role_key) <> ''),
    action_key text NOT NULL CHECK (btrim(action_key) <> ''),
    target_type text,
    target_id uuid,
    details jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(details)='object'),
    evidence_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT bidding.contains_forbidden_hidden_key(details)),
    UNIQUE (ingestion_run_id, event_no)
);

CREATE OR REPLACE FUNCTION bidding.validate_ingestion_event()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_run_school uuid;
    v_run_status text;
    v_expected_no integer;
BEGIN
    SELECT school_id,status INTO v_run_school,v_run_status
      FROM bidding.ingestion_run
     WHERE ingestion_run_id=NEW.ingestion_run_id
     FOR UPDATE;
    IF v_run_school IS NULL THEN
        RAISE EXCEPTION 'BID_INGESTION_RUN_NOT_FOUND' USING ERRCODE='23514';
    END IF;
    IF v_run_status <> 'running' THEN
        RAISE EXCEPTION 'BID_INGESTION_RUN_NOT_OPEN' USING ERRCODE='55000';
    END IF;
    SELECT COALESCE(max(event_no),0)+1 INTO v_expected_no
      FROM bidding.ingestion_event
     WHERE ingestion_run_id=NEW.ingestion_run_id;
    IF NEW.event_no <> v_expected_no THEN
        RAISE EXCEPTION 'BID_INGESTION_EVENT_SEQUENCE' USING ERRCODE='23514';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM unnest(NEW.evidence_ids) AS e(evidence_id)
          LEFT JOIN public.evidence AS ev ON ev.evidence_id=e.evidence_id
         WHERE ev.evidence_id IS NULL OR ev.school_id <> v_run_school
    ) THEN
        RAISE EXCEPTION 'BID_INGESTION_EVENT_EVIDENCE_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER ingestion_event_integrity_guard
BEFORE INSERT ON bidding.ingestion_event
FOR EACH ROW EXECUTE FUNCTION bidding.validate_ingestion_event();

CREATE OR REPLACE FUNCTION bidding.reject_append_only_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'BID_APPEND_ONLY' USING ERRCODE='55000';
END;
$$;

CREATE TRIGGER rule_test_run_append_only
BEFORE UPDATE OR DELETE ON bidding.rule_test_run
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();

CREATE TRIGGER decision_trace_append_only
BEFORE UPDATE OR DELETE ON bidding.decision_trace
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();

CREATE TRIGGER ingestion_event_append_only
BEFORE UPDATE OR DELETE ON bidding.ingestion_event
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();
