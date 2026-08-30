-- 0200_bidding_knowledge_v0 / part 02
-- Included transactionally by ../0200_bidding_knowledge_v0.sql.

CREATE INDEX bidding_rule_test_gate_idx
    ON bidding.rule_test (school_id, rule_id, enabled, test_type);

CREATE OR REPLACE FUNCTION bidding.validate_rule_test_school_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_rule_school uuid;
BEGIN
    SELECT school_id INTO v_rule_school FROM bidding.rule WHERE rule_id=NEW.rule_id;
    IF v_rule_school IS NULL OR v_rule_school <> NEW.school_id THEN
        RAISE EXCEPTION 'BID_RULE_TEST_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER rule_test_school_scope_guard
BEFORE INSERT OR UPDATE OF school_id, rule_id ON bidding.rule_test
FOR EACH ROW EXECUTE FUNCTION bidding.validate_rule_test_school_scope();

CREATE TABLE bidding.rule_test_run (
    rule_test_run_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    rule_test_id uuid NOT NULL REFERENCES bidding.rule_test(rule_test_id) ON DELETE RESTRICT,
    result text NOT NULL CHECK (result IN ('pass','fail','error')),
    result_details jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(result_details)='object'),
    evidence_id uuid REFERENCES public.evidence(evidence_id) ON DELETE SET NULL,
    method_version text NOT NULL CHECK (btrim(method_version) <> ''),
    executed_at timestamptz NOT NULL DEFAULT now(),
    -- Evidence recency is insertion order, not transaction start time.  now()
    -- is stable for a transaction and can make a later row look older.
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (NOT bidding.contains_forbidden_hidden_key(result_details))
);

CREATE INDEX bidding_rule_test_run_latest_idx
    ON bidding.rule_test_run (rule_test_id, created_at DESC, rule_test_run_id DESC);

CREATE OR REPLACE FUNCTION bidding.validate_rule_test_run_school_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_test_school uuid;
    v_evidence_school uuid;
BEGIN
    SELECT school_id INTO v_test_school FROM bidding.rule_test WHERE rule_test_id=NEW.rule_test_id;
    IF v_test_school IS NULL OR v_test_school <> NEW.school_id THEN
        RAISE EXCEPTION 'BID_RULE_TEST_RUN_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF NEW.evidence_id IS NOT NULL THEN
        SELECT school_id INTO v_evidence_school FROM public.evidence WHERE evidence_id=NEW.evidence_id;
        IF v_evidence_school IS NULL OR v_evidence_school <> NEW.school_id THEN
            RAISE EXCEPTION 'BID_RULE_TEST_RUN_EVIDENCE_SCHOOL_MISMATCH' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER rule_test_run_school_scope_guard
BEFORE INSERT ON bidding.rule_test_run
FOR EACH ROW EXECUTE FUNCTION bidding.validate_rule_test_run_school_scope();

CREATE TABLE bidding.rule_conflict (
    rule_conflict_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    left_rule_id uuid NOT NULL REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
    right_rule_id uuid NOT NULL REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
    conflict_type text NOT NULL CHECK (conflict_type IN (
        'overlap','contradiction','priority_tie','inference_mismatch','activation_collision'
    )),
    context_scope jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(context_scope)='object'),
    details jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(details)='object'),
    evidence_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','resolved','accepted_risk','invalidated')),
    resolved_by_person_id uuid REFERENCES public.person(person_id) ON DELETE SET NULL,
    resolved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (left_rule_id <> right_rule_id),
    CHECK ((status='open' AND resolved_at IS NULL) OR (status<>'open' AND resolved_at IS NOT NULL)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(context_scope)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(details))
);

CREATE UNIQUE INDEX bidding_rule_conflict_pair_uidx
    ON bidding.rule_conflict (
        school_id,
        LEAST(left_rule_id,right_rule_id),
        GREATEST(left_rule_id,right_rule_id),
        conflict_type
    );

CREATE INDEX bidding_rule_conflict_open_idx
    ON bidding.rule_conflict (school_id, left_rule_id, right_rule_id, conflict_type)
    WHERE status='open';

CREATE OR REPLACE FUNCTION bidding.validate_rule_conflict_school_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_left_school uuid;
    v_right_school uuid;
BEGIN
    SELECT school_id INTO v_left_school FROM bidding.rule WHERE rule_id=NEW.left_rule_id;
    SELECT school_id INTO v_right_school FROM bidding.rule WHERE rule_id=NEW.right_rule_id;
    IF v_left_school IS NULL OR v_right_school IS NULL
       OR v_left_school <> NEW.school_id OR v_right_school <> NEW.school_id THEN
        RAISE EXCEPTION 'BID_RULE_CONFLICT_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM unnest(NEW.evidence_ids) AS e(evidence_id)
          LEFT JOIN public.evidence AS ev ON ev.evidence_id=e.evidence_id
         WHERE ev.evidence_id IS NULL OR ev.school_id <> NEW.school_id
    ) THEN
        RAISE EXCEPTION 'BID_RULE_CONFLICT_EVIDENCE_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER rule_conflict_school_scope_guard
BEFORE INSERT OR UPDATE OF school_id, left_rule_id, right_rule_id, evidence_ids ON bidding.rule_conflict
FOR EACH ROW EXECUTE FUNCTION bidding.validate_rule_conflict_school_scope();

CREATE TABLE bidding.runtime_activation (
    runtime_activation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    rule_id uuid NOT NULL REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
    authority_lane text NOT NULL CHECK (authority_lane IN ('school_canon','world_external')),
    canon_activation_id uuid REFERENCES public.canon_activation(canon_activation_id) ON DELETE RESTRICT,
    scope_key text NOT NULL DEFAULT 'default' CHECK (btrim(scope_key) <> ''),
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    status text NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate','active','superseded','revoked')),
    activation_provenance jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(activation_provenance)='object'),
    activated_by_person_id uuid REFERENCES public.person(person_id) ON DELETE SET NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (
        (authority_lane='school_canon' AND canon_activation_id IS NOT NULL)
        OR (authority_lane='world_external' AND canon_activation_id IS NULL)
    ),
    CHECK (NOT bidding.contains_forbidden_hidden_key(activation_provenance))
);

CREATE UNIQUE INDEX bidding_runtime_activation_open_uidx
    ON bidding.runtime_activation (school_id, rule_id, authority_lane, scope_key)
    WHERE status='active' AND valid_to IS NULL;

CREATE INDEX bidding_runtime_activation_lookup_idx
    ON bidding.runtime_activation (school_id, authority_lane, scope_key, status, valid_from, valid_to);

ALTER TABLE bidding.runtime_activation
ADD CONSTRAINT bidding_runtime_activation_active_no_overlap
EXCLUDE USING gist (
    school_id WITH =,
    rule_id WITH =,
    authority_lane WITH =,
    scope_key WITH =,
    tstzrange(valid_from,valid_to,'[)') WITH &&
)
WHERE (status='active');
