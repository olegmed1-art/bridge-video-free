ALTER TABLE assistant_lab.job DROP CONSTRAINT IF EXISTS job_kind_check;
ALTER TABLE assistant_lab.job DROP CONSTRAINT IF EXISTS assistant_lab_job_kind_check;
ALTER TABLE assistant_lab.job ADD CONSTRAINT assistant_lab_job_kind_check CHECK (kind IN ('DDS3_COMPUTE','BEN_COMPUTE','NOOP'));

CREATE TABLE IF NOT EXISTS assistant_lab.research_job (
    research_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL DEFAULT 'CHAT', kind text NOT NULL CHECK (kind IN ('DDS3','BEN','VIDEO','COMPOSITE')),
    stage text NOT NULL DEFAULT 'QUEUED' CHECK (stage IN ('QUEUED','ACCEPTED','RUNNING','CHECKPOINTED','VALIDATING','COMPLETED','FAILED','CANCELLED')),
    research_key text NOT NULL UNIQUE, payload_json jsonb NOT NULL,
    child_job_id uuid REFERENCES assistant_lab.job(job_id), checkpoint_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    validation_json jsonb NOT NULL DEFAULT '{}'::jsonb, provenance_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    artifact_json jsonb, artifact_sha256 text CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'),
    methodical_json jsonb, canonical_promotion boolean NOT NULL DEFAULT false CHECK (canonical_promotion = false),
    error_text text, completed_at timestamptz
);
CREATE INDEX IF NOT EXISTS research_job_stage_idx ON assistant_lab.research_job(stage, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS research_job_child_unique_idx ON assistant_lab.research_job(child_job_id) WHERE child_job_id IS NOT NULL;

CREATE OR REPLACE FUNCTION assistant_lab.touch_research_job_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END; $$;
DROP TRIGGER IF EXISTS assistant_lab_research_job_touch ON assistant_lab.research_job;
CREATE TRIGGER assistant_lab_research_job_touch BEFORE UPDATE ON assistant_lab.research_job FOR EACH ROW EXECUTE FUNCTION assistant_lab.touch_research_job_updated_at();

CREATE OR REPLACE FUNCTION assistant_lab.enqueue_research_job(
    p_kind text,p_payload jsonb,p_research_key text,p_child_kind text,p_child_payload jsonb,p_child_idempotency_key text,
    p_priority smallint DEFAULT 20,p_source text DEFAULT 'CHAT')
RETURNS assistant_lab.research_job LANGUAGE plpgsql AS $$
DECLARE v_child assistant_lab.job; v_row assistant_lab.research_job;
BEGIN
    IF upper(p_kind) NOT IN ('DDS3','BEN') THEN RAISE EXCEPTION 'only executable DDS3/BEN research jobs may use this function'; END IF;
    IF upper(p_child_kind) NOT IN ('DDS3_COMPUTE','BEN_COMPUTE') THEN RAISE EXCEPTION 'invalid child compute kind'; END IF;
    SELECT * INTO v_row FROM assistant_lab.research_job WHERE research_key=p_research_key; IF FOUND THEN RETURN v_row; END IF;
    SELECT * INTO v_child FROM assistant_lab.enqueue_job(upper(p_child_kind),p_child_payload,p_priority,p_child_idempotency_key,'RESEARCH_JOB',jsonb_build_object('research_key',p_research_key),NULL,NULL);
    INSERT INTO assistant_lab.research_job(source,kind,stage,research_key,payload_json,child_job_id,provenance_json,canonical_promotion)
    VALUES(p_source,upper(p_kind),'ACCEPTED',p_research_key,p_payload,v_child.job_id,jsonb_build_object('child_kind',upper(p_child_kind),'child_idempotency_key',p_child_idempotency_key),false)
    ON CONFLICT(research_key) DO UPDATE SET research_key=EXCLUDED.research_key RETURNING * INTO v_row; RETURN v_row;
END; $$;
REVOKE ALL ON assistant_lab.research_job FROM PUBLIC;
REVOKE ALL ON FUNCTION assistant_lab.enqueue_research_job(text,jsonb,text,text,jsonb,text,smallint,text) FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON assistant_lab.research_job TO bridge_school_app;
GRANT EXECUTE ON FUNCTION assistant_lab.enqueue_research_job(text,jsonb,text,text,jsonb,text,smallint,text) TO bridge_school_app;
