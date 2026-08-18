\set ON_ERROR_STOP on
BEGIN;

CREATE TABLE IF NOT EXISTS public.analysis_candidate (
    analysis_candidate_id uuid PRIMARY KEY,
    school_id uuid NOT NULL REFERENCES public.school(school_id),
    analysis_run_id uuid REFERENCES public.analysis_run(analysis_run_id),
    source_id uuid REFERENCES public.source(source_id),
    candidate_type text NOT NULL,
    stable_key text NOT NULL,
    input_fingerprint text NOT NULL,
    quality_status text NOT NULL,
    promotion_status text NOT NULL DEFAULT 'staging',
    payload jsonb NOT NULL,
    payload_hash text NOT NULL,
    evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    rejection_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    method_version text NOT NULL,
    supersedes_candidate_id uuid REFERENCES public.analysis_candidate(analysis_candidate_id),
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT analysis_candidate_promotion_status_chk
        CHECK (promotion_status IN ('staging','review_queue','promoted','rejected','superseded')),
    CONSTRAINT analysis_candidate_status_chk
        CHECK (status IN ('active','superseded','rejected')),
    CONSTRAINT analysis_candidate_evidence_array_chk
        CHECK (jsonb_typeof(evidence_refs) = 'array'),
    CONSTRAINT analysis_candidate_reasons_array_chk
        CHECK (jsonb_typeof(rejection_reasons) = 'array'),
    CONSTRAINT analysis_candidate_payload_object_chk
        CHECK (jsonb_typeof(payload) = 'object')
);

CREATE UNIQUE INDEX IF NOT EXISTS analysis_candidate_identity_uk
    ON public.analysis_candidate(
        school_id,
        candidate_type,
        stable_key,
        method_version,
        input_fingerprint
    );

CREATE INDEX IF NOT EXISTS analysis_candidate_run_type_idx
    ON public.analysis_candidate(analysis_run_id, candidate_type, created_at DESC);

CREATE INDEX IF NOT EXISTS analysis_candidate_review_queue_idx
    ON public.analysis_candidate(promotion_status, quality_status, created_at)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS analysis_candidate_stable_key_idx
    ON public.analysis_candidate(school_id, stable_key, created_at DESC);

COMMENT ON TABLE public.analysis_candidate IS
    'Evidence-linked staging candidates from analysis. Rows cannot activate canon, curriculum, methodology or production student profiles.';
COMMENT ON COLUMN public.analysis_candidate.promotion_status IS
    'Workflow state only; promotion into authoritative tables requires a separate guarded process.';
COMMENT ON COLUMN public.analysis_candidate.input_fingerprint IS
    'Deterministic fingerprint of source analysis and quality method for idempotent semantic-only rebuilds.';

GRANT SELECT ON public.analysis_candidate TO bridge_school_reader;
GRANT INSERT, UPDATE ON public.analysis_candidate TO bridge_school_worker;
REVOKE DELETE ON public.analysis_candidate FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0014_analysis_candidate_staging')
ON CONFLICT DO NOTHING;

COMMIT;
