ALTER TABLE assistant_lab.job DROP CONSTRAINT IF EXISTS job_kind_check;
ALTER TABLE assistant_lab.job DROP CONSTRAINT IF EXISTS assistant_lab_job_kind_check;
ALTER TABLE assistant_lab.job ADD CONSTRAINT assistant_lab_job_kind_check CHECK (kind IN ('DDS3_COMPUTE','BEN_COMPUTE','WORLD_GENERATE','NOOP'));

CREATE TABLE IF NOT EXISTS assistant_lab.research_job (
    research_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL DEFAULT 'CHAT', kind text NOT NULL CHECK (kind IN ('DDS3','BEN','WORLDS','VIDEO','COMPOSITE')),
    stage text NOT NULL DEFAULT 'QUEUED' CHECK (stage IN ('QUEUED','ACCEPTED','RUNNING','CHECKPOINTED','VALIDATING','COMPLETED','FAILED','CANCELLED')),
    research_key text NOT NULL UNIQUE, payload_json jsonb NOT NULL,
    child_job_id uuid REFERENCES assistant_lab.job(job_id), checkpoint_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    validation_json jsonb NOT NULL DEFAULT '{}'::jsonb, provenance_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    artifact_json jsonb, artifact_sha256 text CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'),
    methodical_json jsonb, canonical_promotion boolean NOT NULL DEFAULT false CHECK (canonical_promotion = false),
    error_text text, completed_at timestamptz
);
ALTER TABLE assistant_lab.research_job DROP CONSTRAINT IF EXISTS research_job_kind_check;
ALTER TABLE assistant_lab.research_job ADD CONSTRAINT research_job_kind_check CHECK (kind IN ('DDS3','BEN','WORLDS','VIDEO','COMPOSITE'));
CREATE INDEX IF NOT EXISTS research_job_stage_idx ON assistant_lab.research_job(stage, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS research_job_child_unique_idx ON assistant_lab.research_job(child_job_id) WHERE child_job_id IS NOT NULL;

CREATE OR REPLACE FUNCTION assistant_lab.touch_research_job_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END; $$;
DROP TRIGGER IF EXISTS assistant_lab_research_job_touch ON assistant_lab.research_job;
CREATE TRIGGER assistant_lab_research_job_touch BEFORE UPDATE ON assistant_lab.research_job FOR EACH ROW EXECUTE FUNCTION assistant_lab.touch_research_job_updated_at();

CREATE OR REPLACE FUNCTION assistant_lab.guard_research_job_write()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, assistant_lab
AS $$
BEGIN
    IF OLD.stage IN ('COMPLETED', 'FAILED', 'CANCELLED') THEN
        RAISE EXCEPTION 'terminal ResearchJob rows are immutable';
    END IF;
    IF NEW.research_id IS DISTINCT FROM OLD.research_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.research_key IS DISTINCT FROM OLD.research_key
       OR NEW.payload_json IS DISTINCT FROM OLD.payload_json
       OR NEW.child_job_id IS DISTINCT FROM OLD.child_job_id
       OR NEW.canonical_promotion IS DISTINCT FROM OLD.canonical_promotion THEN
        RAISE EXCEPTION 'ResearchJob identity, payload, child binding, and promotion boundary are immutable';
    END IF;
    IF NEW.canonical_promotion IS DISTINCT FROM false THEN
        RAISE EXCEPTION 'ResearchJob cannot promote canonical school state';
    END IF;
    IF NEW.stage IS DISTINCT FROM OLD.stage AND NOT (
        (OLD.stage = 'QUEUED' AND NEW.stage IN ('ACCEPTED', 'FAILED', 'CANCELLED'))
        OR (OLD.stage = 'ACCEPTED' AND NEW.stage IN ('RUNNING', 'FAILED', 'CANCELLED'))
        OR (OLD.stage = 'RUNNING' AND NEW.stage IN ('CHECKPOINTED', 'VALIDATING', 'FAILED', 'CANCELLED'))
        OR (OLD.stage = 'CHECKPOINTED' AND NEW.stage IN ('RUNNING', 'VALIDATING', 'FAILED', 'CANCELLED'))
        OR (OLD.stage = 'VALIDATING' AND NEW.stage IN ('COMPLETED', 'FAILED'))
    ) THEN
        RAISE EXCEPTION 'invalid ResearchJob transition: % -> %', OLD.stage, NEW.stage;
    END IF;
    IF NEW.stage = 'COMPLETED' AND (
        NEW.completed_at IS NULL
        OR NEW.error_text IS NOT NULL
        OR NEW.validation_json ->> 'validated' IS DISTINCT FROM 'true'
        OR NEW.artifact_json IS NULL
        OR NEW.artifact_sha256 IS NULL
        OR NEW.artifact_json ->> 'sha256' IS DISTINCT FROM NEW.artifact_sha256
        OR NEW.methodical_json IS NULL
        OR NEW.methodical_json ->> 'canonical_promotion' IS DISTINCT FROM 'false'
    ) THEN
        RAISE EXCEPTION 'completed ResearchJob requires bound validation, artifact, and methodical evidence';
    END IF;
    IF NEW.stage = 'FAILED' AND (
        NEW.completed_at IS NULL OR btrim(coalesce(NEW.error_text, '')) = ''
    ) THEN
        RAISE EXCEPTION 'failed ResearchJob requires completion time and error evidence';
    END IF;
    IF NEW.stage = 'CANCELLED' AND NEW.completed_at IS NULL THEN
        RAISE EXCEPTION 'cancelled ResearchJob requires completion time';
    END IF;
    IF NEW.stage NOT IN ('COMPLETED', 'FAILED', 'CANCELLED') AND NEW.completed_at IS NOT NULL THEN
        RAISE EXCEPTION 'active ResearchJob cannot have a completion time';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS assistant_lab_research_job_guard ON assistant_lab.research_job;
CREATE TRIGGER assistant_lab_research_job_guard
BEFORE UPDATE ON assistant_lab.research_job
FOR EACH ROW EXECUTE FUNCTION assistant_lab.guard_research_job_write();

CREATE OR REPLACE FUNCTION assistant_lab.enqueue_research_job(
    p_kind text,p_payload jsonb,p_research_key text,p_child_kind text,p_child_payload jsonb,p_child_idempotency_key text,
    p_priority smallint DEFAULT 20,p_source text DEFAULT 'CHAT')
RETURNS assistant_lab.research_job
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, assistant_lab
AS $$
DECLARE
    v_child assistant_lab.job;
    v_row assistant_lab.research_job;
    v_kind text := upper(btrim(coalesce(p_kind, '')));
    v_child_kind text := upper(btrim(coalesce(p_child_kind, '')));
BEGIN
    IF NOT (
        (v_kind = 'DDS3' AND v_child_kind = 'DDS3_COMPUTE')
        OR (v_kind = 'BEN' AND v_child_kind = 'BEN_COMPUTE')
        OR (v_kind = 'WORLDS' AND v_child_kind = 'WORLD_GENERATE')
    ) THEN
        RAISE EXCEPTION 'ResearchJob kind/child capability mismatch';
    END IF;
    IF p_payload IS NULL OR p_payload IS DISTINCT FROM p_child_payload THEN
        RAISE EXCEPTION 'ResearchJob and child payloads must be identical and non-null';
    END IF;
    IF btrim(coalesce(p_research_key, '')) = '' OR length(p_research_key) > 256 THEN
        RAISE EXCEPTION 'ResearchJob key is missing or too long';
    END IF;
    IF btrim(coalesce(p_child_idempotency_key, '')) = '' OR length(p_child_idempotency_key) > 512 THEN
        RAISE EXCEPTION 'child idempotency key is missing or too long';
    END IF;
    IF btrim(coalesce(p_source, '')) = '' OR length(p_source) > 128 THEN
        RAISE EXCEPTION 'ResearchJob source is missing or too long';
    END IF;
    SELECT * INTO v_row
      FROM assistant_lab.research_job
     WHERE research_key = p_research_key;
    IF FOUND THEN
        RETURN v_row;
    END IF;
    SELECT * INTO v_child
      FROM assistant_lab.enqueue_job(
          v_child_kind, p_child_payload, p_priority, p_child_idempotency_key,
          'RESEARCH_JOB',
          jsonb_build_object(
              'dispatcher', 'research-job-v1',
              'research_key', p_research_key,
              'canonical_promotion', false
          ),
          NULL, NULL
      );
    INSERT INTO assistant_lab.research_job(source,kind,stage,research_key,payload_json,child_job_id,provenance_json,canonical_promotion)
    VALUES(
        p_source, v_kind, 'ACCEPTED', p_research_key, p_payload, v_child.job_id,
        jsonb_build_object(
            'contract', 'bridge-research-job-v1',
            'child_kind', v_child_kind,
            'child_idempotency_key', p_child_idempotency_key
        ),
        false
    )
    ON CONFLICT(research_key) DO NOTHING
    RETURNING * INTO v_row;
    IF NOT FOUND THEN
        SELECT * INTO v_row
          FROM assistant_lab.research_job
         WHERE research_key = p_research_key;
    END IF;
    RETURN v_row;
END;
$$;
REVOKE ALL ON assistant_lab.research_job FROM PUBLIC;
REVOKE ALL ON FUNCTION assistant_lab.touch_research_job_updated_at() FROM PUBLIC;
REVOKE ALL ON FUNCTION assistant_lab.guard_research_job_write() FROM PUBLIC;
REVOKE ALL ON FUNCTION assistant_lab.enqueue_research_job(text,jsonb,text,text,jsonb,text,smallint,text) FROM PUBLIC;
REVOKE ALL ON assistant_lab.research_job FROM bridge_school_app;
GRANT SELECT ON assistant_lab.research_job TO bridge_school_app;
GRANT UPDATE (
    stage, checkpoint_json, validation_json, provenance_json, artifact_json,
    artifact_sha256, methodical_json, error_text, completed_at
) ON assistant_lab.research_job TO bridge_school_app;
GRANT EXECUTE ON FUNCTION assistant_lab.enqueue_research_job(text,jsonb,text,text,jsonb,text,smallint,text) TO bridge_school_app;

COMMENT ON FUNCTION assistant_lab.enqueue_research_job(text,jsonb,text,text,jsonb,text,smallint,text) IS
'Fail-closed ResearchJob enqueue boundary. Binds one validated research request to one Assistant Lab compute child without granting direct INSERT.';
