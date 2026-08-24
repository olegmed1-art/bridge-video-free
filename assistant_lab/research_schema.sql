-- Durable ResearchJob ledger for the school-wide research pipeline.
-- Additive only: no curriculum/canon tables are touched.

CREATE TABLE IF NOT EXISTS assistant_lab.research_job (
    research_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL DEFAULT 'CHAT',
    kind text NOT NULL CHECK (kind IN ('DDS3','BEN','VIDEO','COMPOSITE')),
    stage text NOT NULL DEFAULT 'QUEUED'
        CHECK (stage IN ('QUEUED','ACCEPTED','RUNNING','CHECKPOINTED','VALIDATING','COMPLETED','FAILED','CANCELLED')),
    research_key text NOT NULL UNIQUE,
    payload_json jsonb NOT NULL,
    child_job_id uuid REFERENCES assistant_lab.job(job_id),
    checkpoint_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    validation_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    provenance_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    artifact_json jsonb,
    artifact_sha256 text CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'),
    methodical_json jsonb,
    canonical_promotion boolean NOT NULL DEFAULT false CHECK (canonical_promotion = false),
    error_text text,
    completed_at timestamptz
);

CREATE INDEX IF NOT EXISTS research_job_stage_idx
    ON assistant_lab.research_job(stage, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS research_job_child_unique_idx
    ON assistant_lab.research_job(child_job_id) WHERE child_job_id IS NOT NULL;

CREATE OR REPLACE FUNCTION assistant_lab.touch_research_job_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS assistant_lab_research_job_touch ON assistant_lab.research_job;
CREATE TRIGGER assistant_lab_research_job_touch
BEFORE UPDATE ON assistant_lab.research_job
FOR EACH ROW EXECUTE FUNCTION assistant_lab.touch_research_job_updated_at();

REVOKE ALL ON assistant_lab.research_job FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON assistant_lab.research_job TO bridge_school_app;

COMMENT ON TABLE assistant_lab.research_job IS
'Durable non-canonical ResearchJob ledger. Completed evidence never promotes school canon automatically.';
