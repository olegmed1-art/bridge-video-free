\set ON_ERROR_STOP on
BEGIN;

-- Repository reconciliation for the Bridge Decision Engine schema.
-- Production first received this schema through the audited external migration
-- `2026-08-20-ai-decision-layer-v1`. That historical SQL is not present in the
-- repository, so this forward migration reconstructs the exact structural shape
-- observed in production without copying production rows or methodology content.
-- On production the CREATE IF NOT EXISTS statements are no-ops and the fingerprint
-- below proves structural parity before this migration can register itself.

CREATE SCHEMA IF NOT EXISTS ai;

CREATE TABLE IF NOT EXISTS ai.knowledge_fact (
    fact_id uuid DEFAULT gen_random_uuid() NOT NULL,
    school_id uuid NOT NULL,
    stable_key text NOT NULL,
    source_id uuid,
    skill_id uuid,
    source_locator text,
    original_statement text NOT NULL,
    normalized_statement text,
    fact_type text NOT NULL,
    provenance_class text NOT NULL,
    system_dependency text,
    review_status text DEFAULT 'NOT_REVIEWED'::text NOT NULL,
    reviewer_person_id uuid,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT knowledge_fact_pkey PRIMARY KEY (fact_id),
    CONSTRAINT knowledge_fact_school_id_stable_key_key UNIQUE (school_id, stable_key),
    CONSTRAINT knowledge_fact_provenance_class_check CHECK (provenance_class = ANY (ARRAY['EXPLICIT'::text, 'DERIVED_FROM_EXPLICIT'::text, 'EXTERNAL'::text, 'AI_INFERENCE'::text])),
    CONSTRAINT knowledge_fact_reviewer_person_id_fkey FOREIGN KEY (reviewer_person_id) REFERENCES person(person_id),
    CONSTRAINT knowledge_fact_school_id_fkey FOREIGN KEY (school_id) REFERENCES school(school_id),
    CONSTRAINT knowledge_fact_skill_id_fkey FOREIGN KEY (skill_id) REFERENCES skill(skill_id),
    CONSTRAINT knowledge_fact_source_id_fkey FOREIGN KEY (source_id) REFERENCES source(source_id)
);

CREATE TABLE IF NOT EXISTS ai.system_rule (
    rule_id uuid DEFAULT gen_random_uuid() NOT NULL,
    school_id uuid NOT NULL,
    stable_key text NOT NULL,
    skill_id uuid,
    system_version text NOT NULL,
    rule_type text NOT NULL,
    trigger_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    conditions_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    meaning_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    constraints_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    action_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    forcing_status text,
    alert_status text,
    certainty text NOT NULL,
    status text DEFAULT 'DRAFT'::text NOT NULL,
    reviewer_person_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    supersedes_rule_id uuid,
    CONSTRAINT system_rule_pkey PRIMARY KEY (rule_id),
    CONSTRAINT system_rule_school_id_stable_key_system_version_key UNIQUE (school_id, stable_key, system_version),
    CONSTRAINT system_rule_certainty_check CHECK (certainty = ANY (ARRAY['EXPLICIT'::text, 'DERIVED_FROM_EXPLICIT'::text, 'EXTERNAL'::text, 'AI_INFERENCE'::text, 'NOT_SPECIFIED'::text])),
    CONSTRAINT system_rule_rule_type_check CHECK (rule_type = ANY (ARRAY['MEANING'::text, 'CONSTRAINT'::text, 'DECISION'::text, 'PRINCIPLE'::text, 'EXCEPTION'::text, 'DEFINITION'::text, 'SAFETY'::text])),
    CONSTRAINT system_rule_reviewer_person_id_fkey FOREIGN KEY (reviewer_person_id) REFERENCES person(person_id),
    CONSTRAINT system_rule_school_id_fkey FOREIGN KEY (school_id) REFERENCES school(school_id),
    CONSTRAINT system_rule_skill_id_fkey FOREIGN KEY (skill_id) REFERENCES skill(skill_id),
    CONSTRAINT system_rule_supersedes_rule_id_fkey FOREIGN KEY (supersedes_rule_id) REFERENCES ai.system_rule(rule_id)
);

CREATE TABLE IF NOT EXISTS ai.rule_fact (
    rule_id uuid NOT NULL,
    fact_id uuid NOT NULL,
    CONSTRAINT rule_fact_pkey PRIMARY KEY (rule_id, fact_id),
    CONSTRAINT rule_fact_fact_id_fkey FOREIGN KEY (fact_id) REFERENCES ai.knowledge_fact(fact_id) ON DELETE CASCADE,
    CONSTRAINT rule_fact_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES ai.system_rule(rule_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai.decision_position (
    position_id uuid DEFAULT gen_random_uuid() NOT NULL,
    school_id uuid NOT NULL,
    stable_key text NOT NULL,
    source_id uuid,
    deal_id uuid,
    exercise_id uuid,
    decision_type text NOT NULL,
    seat text,
    dealer text,
    vulnerability text,
    scoring text,
    hand_pbn text,
    auction_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    cards_played_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    dummy_pbn text,
    system_us text,
    system_them text,
    input_status text DEFAULT 'COMPLETE'::text NOT NULL,
    position_fingerprint text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT decision_position_pkey PRIMARY KEY (position_id),
    CONSTRAINT decision_position_school_id_stable_key_key UNIQUE (school_id, stable_key),
    CONSTRAINT decision_position_decision_type_check CHECK (decision_type = ANY (ARRAY['BIDDING'::text, 'OPENING_LEAD'::text, 'PLAY'::text, 'DEFENSE'::text, 'CLAIM'::text, 'OTHER'::text])),
    CONSTRAINT decision_position_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES deal(deal_id),
    CONSTRAINT decision_position_exercise_id_fkey FOREIGN KEY (exercise_id) REFERENCES exercise(exercise_id),
    CONSTRAINT decision_position_school_id_fkey FOREIGN KEY (school_id) REFERENCES school(school_id),
    CONSTRAINT decision_position_source_id_fkey FOREIGN KEY (source_id) REFERENCES source(source_id)
);

CREATE TABLE IF NOT EXISTS ai.decision_position_skill (
    position_id uuid NOT NULL,
    skill_id uuid NOT NULL,
    mastery_target numeric,
    CONSTRAINT decision_position_skill_pkey PRIMARY KEY (position_id, skill_id),
    CONSTRAINT decision_position_skill_position_id_fkey FOREIGN KEY (position_id) REFERENCES ai.decision_position(position_id) ON DELETE CASCADE,
    CONSTRAINT decision_position_skill_skill_id_fkey FOREIGN KEY (skill_id) REFERENCES skill(skill_id)
);

CREATE TABLE IF NOT EXISTS ai.hand_features (
    position_id uuid NOT NULL,
    feature_version text NOT NULL,
    hcp integer,
    shape text,
    controls integer,
    features_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT hand_features_pkey PRIMARY KEY (position_id, feature_version),
    CONSTRAINT hand_features_position_id_fkey FOREIGN KEY (position_id) REFERENCES ai.decision_position(position_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai.teacher_output (
    teacher_output_id uuid DEFAULT gen_random_uuid() NOT NULL,
    position_id uuid NOT NULL,
    teacher_key text NOT NULL,
    teacher_version text,
    teacher_system text,
    action text,
    confidence numeric,
    candidate_scores_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    explanation text,
    raw_output_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT teacher_output_pkey PRIMARY KEY (teacher_output_id),
    CONSTRAINT teacher_output_position_id_fkey FOREIGN KEY (position_id) REFERENCES ai.decision_position(position_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai.policy_run (
    policy_run_id uuid DEFAULT gen_random_uuid() NOT NULL,
    position_id uuid NOT NULL,
    model_key text NOT NULL,
    model_version text NOT NULL,
    distribution_json jsonb NOT NULL,
    top_action text,
    margin numeric,
    entropy numeric,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT policy_run_pkey PRIMARY KEY (policy_run_id),
    CONSTRAINT policy_run_position_id_fkey FOREIGN KEY (position_id) REFERENCES ai.decision_position(position_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai.candidate_action (
    candidate_id uuid DEFAULT gen_random_uuid() NOT NULL,
    position_id uuid NOT NULL,
    policy_run_id uuid,
    action text NOT NULL,
    legal boolean DEFAULT true NOT NULL,
    system_compatible boolean,
    hard_rule_status text,
    policy_score numeric,
    teacher_score numeric,
    search_included boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT candidate_action_pkey PRIMARY KEY (candidate_id),
    CONSTRAINT candidate_action_position_id_policy_run_id_action_key UNIQUE (position_id, policy_run_id, action),
    CONSTRAINT candidate_action_policy_run_id_fkey FOREIGN KEY (policy_run_id) REFERENCES ai.policy_run(policy_run_id),
    CONSTRAINT candidate_action_position_id_fkey FOREIGN KEY (position_id) REFERENCES ai.decision_position(position_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai.inference_run (
    inference_run_id uuid DEFAULT gen_random_uuid() NOT NULL,
    position_id uuid NOT NULL,
    model_key text NOT NULL,
    model_version text NOT NULL,
    system_version text,
    hard_constraints_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    distributions_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    confidence numeric,
    provenance_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT inference_run_pkey PRIMARY KEY (inference_run_id),
    CONSTRAINT inference_run_position_id_fkey FOREIGN KEY (position_id) REFERENCES ai.decision_position(position_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai.search_run (
    search_run_id uuid DEFAULT gen_random_uuid() NOT NULL,
    position_id uuid NOT NULL,
    inference_run_id uuid,
    sampler_key text NOT NULL,
    sampler_version text NOT NULL,
    rollout_policy text,
    evaluator_key text,
    scoring text,
    samples_generated integer,
    samples_accepted integer,
    effective_sample_size numeric,
    sample_quality numeric,
    worlds_object_uri text,
    status text DEFAULT 'QUEUED'::text NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT search_run_pkey PRIMARY KEY (search_run_id),
    CONSTRAINT search_run_inference_run_id_fkey FOREIGN KEY (inference_run_id) REFERENCES ai.inference_run(inference_run_id),
    CONSTRAINT search_run_position_id_fkey FOREIGN KEY (position_id) REFERENCES ai.decision_position(position_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai.candidate_evaluation (
    evaluation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    search_run_id uuid NOT NULL,
    candidate_id uuid NOT NULL,
    rollout_policy text,
    raw_score_ev numeric,
    imp_ev numeric,
    mp_ev numeric,
    variance numeric,
    downside numeric,
    upside numeric,
    make_probability numeric,
    robustness numeric,
    contracts_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    metrics_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT candidate_evaluation_pkey PRIMARY KEY (evaluation_id),
    CONSTRAINT candidate_evaluation_search_run_id_candidate_id_rollout_pol_key UNIQUE (search_run_id, candidate_id, rollout_policy),
    CONSTRAINT candidate_evaluation_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES ai.candidate_action(candidate_id) ON DELETE CASCADE,
    CONSTRAINT candidate_evaluation_search_run_id_fkey FOREIGN KEY (search_run_id) REFERENCES ai.search_run(search_run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai.final_decision (
    final_decision_id uuid DEFAULT gen_random_uuid() NOT NULL,
    position_id uuid NOT NULL,
    engine_version text NOT NULL,
    system_version text,
    chosen_action text NOT NULL,
    second_action text,
    decision_path text NOT NULL,
    confidence numeric,
    robustness numeric,
    evidence_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    explanation_short text,
    explanation_detailed text,
    human_review_status text DEFAULT 'NOT_REVIEWED'::text NOT NULL,
    reviewer_person_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT final_decision_pkey PRIMARY KEY (final_decision_id),
    CONSTRAINT final_decision_decision_path_check CHECK (decision_path = ANY (ARRAY['CACHE'::text, 'HARD_RULE'::text, 'POLICY_ONLY'::text, 'SEARCH'::text, 'TEACHER_OVERRIDE'::text, 'HUMAN_OVERRIDE'::text, 'UNKNOWN'::text])),
    CONSTRAINT final_decision_position_id_fkey FOREIGN KEY (position_id) REFERENCES ai.decision_position(position_id) ON DELETE CASCADE,
    CONSTRAINT final_decision_reviewer_person_id_fkey FOREIGN KEY (reviewer_person_id) REFERENCES person(person_id)
);

CREATE TABLE IF NOT EXISTS ai.sync_state (
    sync_key text NOT NULL,
    source_locator text,
    source_revision text,
    last_seen_at timestamp with time zone,
    last_success_at timestamp with time zone,
    status text DEFAULT 'NEW'::text NOT NULL,
    details_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT sync_state_pkey PRIMARY KEY (sync_key)
);

CREATE INDEX IF NOT EXISTS idx_ai_final_position ON ai.final_decision (position_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_inference_position ON ai.inference_run (position_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_position_deal ON ai.decision_position (deal_id);
CREATE INDEX IF NOT EXISTS idx_ai_position_exercise ON ai.decision_position (exercise_id);
CREATE INDEX IF NOT EXISTS idx_ai_position_fingerprint ON ai.decision_position (position_fingerprint);
CREATE INDEX IF NOT EXISTS idx_ai_rule_status ON ai.system_rule (school_id, status, system_version);
CREATE INDEX IF NOT EXISTS idx_ai_search_position ON ai.search_run (position_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_teacher_position ON ai.teacher_output (position_id, teacher_key);

CREATE OR REPLACE VIEW ai.v_latest_final_decision AS
SELECT DISTINCT ON (position_id)
    final_decision_id,
    position_id,
    engine_version,
    system_version,
    chosen_action,
    second_action,
    decision_path,
    confidence,
    robustness,
    evidence_json,
    explanation_short,
    explanation_detailed,
    human_review_status,
    reviewer_person_id,
    created_at
FROM ai.final_decision fd
ORDER BY position_id, created_at DESC;

CREATE OR REPLACE VIEW ai.v_position_overview AS
SELECT
    p.position_id,
    p.stable_key,
    p.decision_type,
    p.seat,
    p.dealer,
    p.vulnerability,
    p.scoring,
    p.hand_pbn,
    p.auction_json,
    p.system_us,
    p.system_them,
    p.input_status,
    p.position_fingerprint,
    array_remove(array_agg(DISTINCT sk.stable_key), NULL::text) AS skill_keys,
    max(fd.created_at) AS last_decision_at
FROM ai.decision_position p
LEFT JOIN ai.decision_position_skill ps ON ps.position_id = p.position_id
LEFT JOIN skill sk ON sk.skill_id = ps.skill_id
LEFT JOIN ai.final_decision fd ON fd.position_id = p.position_id
GROUP BY p.position_id;

CREATE OR REPLACE VIEW ai.v_rule_provenance AS
SELECT
    r.rule_id,
    r.stable_key AS rule_key,
    r.system_version,
    r.rule_type,
    r.status AS rule_status,
    r.certainty,
    s.stable_key AS skill_key,
    f.fact_id,
    f.stable_key AS fact_key,
    f.provenance_class,
    f.review_status AS fact_review_status,
    src.title AS source_title,
    src.canonical_locator
FROM ai.system_rule r
LEFT JOIN skill s ON s.skill_id = r.skill_id
LEFT JOIN ai.rule_fact rf ON rf.rule_id = r.rule_id
LEFT JOIN ai.knowledge_fact f ON f.fact_id = rf.fact_id
LEFT JOIN source src ON src.source_id = f.source_id;

CREATE OR REPLACE VIEW ai.v_work_queue AS
SELECT
    p.position_id,
    p.stable_key,
    p.input_status,
    CASE
        WHEN p.input_status <> 'COMPLETE'::text THEN 'INPUT_INCOMPLETE'::text
        WHEN fd.final_decision_id IS NULL THEN 'NEEDS_DECISION'::text
        WHEN fd.human_review_status = 'NOT_REVIEWED'::text THEN 'NEEDS_REVIEW'::text
        ELSE 'DONE'::text
    END AS work_status
FROM ai.decision_position p
LEFT JOIN ai.v_latest_final_decision fd ON fd.position_id = p.position_id;

-- Fail closed if the reconstructed schema differs from the production structure
-- audited on 2026-08-20. Data rows and owner/grantee identities are deliberately
-- excluded from this fingerprint.
DO $$
DECLARE
    v_line_count integer;
    v_fingerprint text;
BEGIN
    WITH column_lines AS (
        SELECT format(
            'C|%s|%s|%s|%s|%s|%s',
            c.table_name,c.ordinal_position,c.column_name,c.udt_name,
            c.is_nullable,COALESCE(c.column_default,'')
        ) AS line
          FROM information_schema.columns c
         WHERE c.table_schema='ai'
           AND c.table_name IN (SELECT tablename FROM pg_tables WHERE schemaname='ai')
    ), constraint_lines AS (
        SELECT format(
            'K|%s|%s|%s|%s',
            conrelid::regclass::text,conname,contype,pg_get_constraintdef(oid,true)
        ) AS line
          FROM pg_constraint
         WHERE connamespace='ai'::regnamespace
           AND contype IN ('p','u','f','c')
    ), index_lines AS (
        SELECT format('I|%s|%s',indexname,indexdef) AS line
          FROM pg_indexes
         WHERE schemaname='ai'
           AND indexname LIKE 'idx_ai_%'
    ), view_lines AS (
        SELECT format(
            'V|%s|%s',c.relname,
            regexp_replace(pg_get_viewdef(c.oid,true),'\s+',' ','g')
        ) AS line
          FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
         WHERE n.nspname='ai'
           AND c.relkind='v'
    ), all_lines AS (
        SELECT line FROM column_lines
        UNION ALL SELECT line FROM constraint_lines
        UNION ALL SELECT line FROM index_lines
        UNION ALL SELECT line FROM view_lines
    )
    SELECT count(*), encode(digest(string_agg(line,E'\n' ORDER BY line),'sha256'),'hex')
      INTO v_line_count, v_fingerprint
      FROM all_lines;

    IF v_line_count <> 225 OR v_fingerprint <> '02a74f7aa59f1c428728b55facf6ba3ed9394d0d550bb50fe4e3913f4cf387dc' THEN
        RAISE EXCEPTION 'AI decision-layer schema fingerprint mismatch: lines %, fingerprint %',
            v_line_count, v_fingerprint;
    END IF;
END $$;

INSERT INTO schema_migration(migration_key)
VALUES ('0054_ai_decision_layer_reconciliation')
ON CONFLICT DO NOTHING;

COMMIT;