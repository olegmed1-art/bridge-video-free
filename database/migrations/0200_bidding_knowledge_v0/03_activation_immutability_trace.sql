-- 0200_bidding_knowledge_v0 / part 03
-- Included transactionally by ../0200_bidding_knowledge_v0.sql.

CREATE OR REPLACE FUNCTION bidding.prevent_runtime_activation_overlap()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status <> 'active' THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        SELECT 1
          FROM bidding.runtime_activation AS ra
         WHERE ra.school_id=NEW.school_id
           AND ra.rule_id=NEW.rule_id
           AND ra.authority_lane=NEW.authority_lane
           AND ra.scope_key=NEW.scope_key
           AND ra.status='active'
           AND ra.runtime_activation_id <> NEW.runtime_activation_id
           AND tstzrange(ra.valid_from,ra.valid_to,'[)') && tstzrange(NEW.valid_from,NEW.valid_to,'[)')
    ) THEN
        RAISE EXCEPTION 'BID_RUNTIME_ACTIVATION_OVERLAP' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER runtime_activation_overlap_guard
BEFORE INSERT OR UPDATE OF school_id, rule_id, authority_lane, scope_key, valid_from, valid_to, status
ON bidding.runtime_activation
FOR EACH ROW EXECUTE FUNCTION bidding.prevent_runtime_activation_overlap();

CREATE OR REPLACE FUNCTION bidding.latest_test_result(p_rule_test_id uuid)
RETURNS text
LANGUAGE sql
STABLE
AS $$
SELECT r.result
  FROM bidding.rule_test_run AS r
 WHERE r.rule_test_id=p_rule_test_id
 ORDER BY r.created_at DESC, r.rule_test_run_id DESC
 LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION bidding.rule_passes_activation_gates(p_rule_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
WITH required(test_type) AS (
    VALUES ('positive'::text),('negative'),('boundary'),('hidden_information')
)
SELECT
    EXISTS (
        SELECT 1
          FROM bidding.rule AS r
          JOIN public.knowledge_version AS kv
            ON kv.knowledge_version_id=r.knowledge_version_id
         WHERE r.rule_id=p_rule_id
           AND r.lifecycle_status='validated'
           AND kv.review_status IN ('reviewed','approved')
           AND kv.status IN ('candidate','active','approved')
           AND EXISTS (
               SELECT 1
                 FROM public.knowledge_version_source AS kvs
                 JOIN public.source AS s ON s.source_id=kvs.source_id
                WHERE kvs.knowledge_version_id=r.knowledge_version_id
                  AND s.status='active'
           )
    )
    AND NOT EXISTS (
        SELECT 1
          FROM required AS req
         WHERE NOT EXISTS (
             SELECT 1
               FROM bidding.rule_test AS t
              WHERE t.rule_id=p_rule_id
                AND t.enabled
                AND t.test_type=req.test_type
                AND bidding.latest_test_result(t.rule_test_id)='pass'
         )
    )
    AND NOT EXISTS (
        SELECT 1
          FROM bidding.rule_test AS t
         WHERE t.rule_id=p_rule_id
           AND t.enabled
           AND COALESCE(bidding.latest_test_result(t.rule_test_id),'missing') <> 'pass'
    )
    AND NOT EXISTS (
        SELECT 1
          FROM bidding.rule_conflict AS c
         WHERE c.status='open'
           AND (c.left_rule_id=p_rule_id OR c.right_rule_id=p_rule_id)
    );
$$;

CREATE OR REPLACE FUNCTION bidding.enforce_runtime_activation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_rule_school uuid;
    v_knowledge_version_id uuid;
    v_authority_class text;
BEGIN
    IF NEW.status <> 'active' THEN
        RETURN NEW;
    END IF;

    -- Evidence gates must observe commits made while waiting for the shared
    -- rule lock. A REPEATABLE READ/SERIALIZABLE snapshot can remain stale
    -- after that wait, so activation is fail-closed outside READ COMMITTED.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'BID_ACTIVATION_REQUIRES_READ_COMMITTED'
            USING ERRCODE='55000';
    END IF;

    -- Shared row-lock protocol: activation and every mutable rule-dependent
    -- definition serialize on the owning bidding.rule row.
    PERFORM 1
      FROM bidding.rule
     WHERE rule_id=NEW.rule_id
     FOR UPDATE;

    SELECT r.school_id, r.knowledge_version_id, kv.authority_class
      INTO v_rule_school, v_knowledge_version_id, v_authority_class
      FROM bidding.rule AS r
      JOIN public.knowledge_version AS kv
        ON kv.knowledge_version_id=r.knowledge_version_id
     WHERE r.rule_id=NEW.rule_id;

    IF v_rule_school IS NULL THEN
        RAISE EXCEPTION 'BID_ACTIVATION_RULE_NOT_FOUND' USING ERRCODE='23514';
    END IF;
    IF v_rule_school <> NEW.school_id THEN
        RAISE EXCEPTION 'BID_ACTIVATION_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF NOT bidding.rule_passes_activation_gates(NEW.rule_id) THEN
        RAISE EXCEPTION 'BID_ACTIVATION_GATES_NOT_SATISFIED' USING ERRCODE='23514';
    END IF;

    IF v_authority_class='school_canon' THEN
        IF NEW.authority_lane <> 'school_canon' THEN
            RAISE EXCEPTION 'BID_ACTIVATION_CANON_LANE_MISMATCH' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1
              FROM public.canon_activation AS ca
             WHERE ca.canon_activation_id=NEW.canon_activation_id
               AND ca.knowledge_version_id=v_knowledge_version_id
               AND ca.scope_key=NEW.scope_key
               AND ca.status='active'
               AND ca.valid_from <= NEW.valid_from
               AND (ca.valid_to IS NULL OR (NEW.valid_to IS NOT NULL AND NEW.valid_to <= ca.valid_to))
        ) THEN
            RAISE EXCEPTION 'BID_ACTIVATION_CANON_APPROVAL_REQUIRED' USING ERRCODE='23514';
        END IF;
    ELSIF v_authority_class='external' THEN
        IF NEW.authority_lane <> 'world_external' THEN
            RAISE EXCEPTION 'BID_ACTIVATION_WORLD_LANE_MISMATCH' USING ERRCODE='23514';
        END IF;
        IF NEW.canon_activation_id IS NOT NULL THEN
            RAISE EXCEPTION 'BID_ACTIVATION_EXTERNAL_CANNOT_REFERENCE_CANON' USING ERRCODE='23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'BID_ACTIVATION_AUTHORITY_NOT_RUNTIME_ELIGIBLE' USING ERRCODE='23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER runtime_activation_guard
BEFORE INSERT OR UPDATE ON bidding.runtime_activation
FOR EACH ROW EXECUTE FUNCTION bidding.enforce_runtime_activation();

CREATE OR REPLACE FUNCTION bidding.rule_is_currently_active(p_rule_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
SELECT EXISTS (
    SELECT 1 FROM bidding.runtime_activation
     WHERE rule_id=p_rule_id
       AND status='active'
       AND (valid_to IS NULL OR valid_to > now())
);
$$;

COMMENT ON FUNCTION bidding.rule_is_currently_active(uuid) IS
  'Returns true while a rule has a non-expired active activation, including a future-dated activation, so scheduled rules cannot be mutated after gate evaluation.';

CREATE OR REPLACE FUNCTION bidding.reject_active_rule_test_run_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_rule_id uuid;
BEGIN
    -- Lock the mutable test row before resolving its owner. A concurrent
    -- reassignment must commit first, so this trigger cannot cache a stale
    -- owner and then append evidence to a newly activated rule.
    SELECT rule_id
      INTO v_rule_id
      FROM bidding.rule_test
     WHERE rule_test_id=NEW.rule_test_id
     FOR UPDATE;

    -- Test evidence and activation must serialize on the same owner row. If
    -- this insert wins the lock, a later activation sees its result; if the
    -- activation wins, the evidence insert is rejected after waiting.
    PERFORM 1
      FROM bidding.rule
     WHERE rule_id=v_rule_id
     FOR UPDATE;

    IF v_rule_id IS NOT NULL AND bidding.rule_is_currently_active(v_rule_id) THEN
        RAISE EXCEPTION 'BID_ACTIVE_RULE_TEST_RUN_IMMUTABLE' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER active_rule_test_run_immutable
BEFORE INSERT ON bidding.rule_test_run
FOR EACH ROW EXECUTE FUNCTION bidding.reject_active_rule_test_run_insert();

CREATE OR REPLACE FUNCTION bidding.reject_active_rule_conflict_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_rule_ids uuid[];
BEGIN
    IF NEW.status <> 'open' THEN
        RETURN NEW;
    END IF;

    IF TG_OP='UPDATE' THEN
        v_rule_ids := ARRAY[
            OLD.left_rule_id,OLD.right_rule_id,
            NEW.left_rule_id,NEW.right_rule_id
        ];
    ELSE
        v_rule_ids := ARRAY[NEW.left_rule_id,NEW.right_rule_id];
    END IF;

    -- Open conflict evidence participates in activation eligibility, so lock
    -- every old/new owner deterministically before accepting it.
    PERFORM 1
      FROM bidding.rule
     WHERE rule_id=ANY(v_rule_ids)
     ORDER BY rule_id
     FOR UPDATE;

    IF EXISTS (
        SELECT 1
          FROM unnest(v_rule_ids) AS x(rule_id)
         WHERE bidding.rule_is_currently_active(x.rule_id)
    ) THEN
        RAISE EXCEPTION 'BID_ACTIVE_RULE_CONFLICT_IMMUTABLE' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER active_rule_conflict_immutable
BEFORE INSERT OR UPDATE ON bidding.rule_conflict
FOR EACH ROW EXECUTE FUNCTION bidding.reject_active_rule_conflict_mutation();

CREATE OR REPLACE FUNCTION bidding.reject_active_rule_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_rule_id uuid;
BEGIN
    IF TG_OP='DELETE' THEN
        v_rule_id := OLD.rule_id;
    ELSE
        v_rule_id := NEW.rule_id;
    END IF;
    IF bidding.rule_is_currently_active(v_rule_id) THEN
        RAISE EXCEPTION 'BID_ACTIVE_RULE_IMMUTABLE' USING ERRCODE='55000';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER active_rule_immutable
BEFORE UPDATE OR DELETE ON bidding.rule
FOR EACH ROW EXECUTE FUNCTION bidding.reject_active_rule_mutation();

CREATE OR REPLACE FUNCTION bidding.reject_active_rule_test_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_rule_ids uuid[];
BEGIN
    IF TG_OP='INSERT' THEN
        v_rule_ids := ARRAY[NEW.rule_id];
    ELSIF TG_OP='UPDATE' THEN
        v_rule_ids := ARRAY[OLD.rule_id,NEW.rule_id];
    ELSE
        v_rule_ids := ARRAY[OLD.rule_id];
    END IF;

    -- Lock both the old and new owners for reassignment updates. Deterministic
    -- ordering prevents deadlocks when two rules are involved.
    PERFORM 1
      FROM bidding.rule
     WHERE rule_id=ANY(v_rule_ids)
     ORDER BY rule_id
     FOR UPDATE;

    IF EXISTS (
        SELECT 1
          FROM unnest(v_rule_ids) AS x(rule_id)
         WHERE bidding.rule_is_currently_active(x.rule_id)
    ) THEN
        RAISE EXCEPTION 'BID_ACTIVE_RULE_TEST_IMMUTABLE' USING ERRCODE='55000';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER active_rule_test_immutable
BEFORE INSERT OR UPDATE OR DELETE ON bidding.rule_test
FOR EACH ROW EXECUTE FUNCTION bidding.reject_active_rule_test_mutation();

CREATE OR REPLACE FUNCTION bidding.reject_active_rule_relation_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_rule_ids uuid[];
BEGIN
    IF TG_OP='INSERT' THEN
        v_rule_ids := ARRAY[NEW.from_rule_id,NEW.to_rule_id];
    ELSIF TG_OP='UPDATE' THEN
        v_rule_ids := ARRAY[
            OLD.from_rule_id,OLD.to_rule_id,
            NEW.from_rule_id,NEW.to_rule_id
        ];
    ELSE
        v_rule_ids := ARRAY[OLD.from_rule_id,OLD.to_rule_id];
    END IF;

    -- Relation reassignment must serialize with activations of every old and
    -- new endpoint, not only the post-update owners.
    PERFORM 1
      FROM bidding.rule
     WHERE rule_id=ANY(v_rule_ids)
     ORDER BY rule_id
     FOR UPDATE;

    IF EXISTS (
        SELECT 1
          FROM unnest(v_rule_ids) AS x(rule_id)
         WHERE bidding.rule_is_currently_active(x.rule_id)
    ) THEN
        RAISE EXCEPTION 'BID_ACTIVE_RULE_RELATION_IMMUTABLE' USING ERRCODE='55000';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER active_rule_relation_immutable
BEFORE INSERT OR UPDATE OR DELETE ON bidding.rule_relation
FOR EACH ROW EXECUTE FUNCTION bidding.reject_active_rule_relation_mutation();

CREATE TABLE bidding.decision_trace (
    decision_trace_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    decision_key text NOT NULL CHECK (btrim(decision_key) <> ''),
    request_fingerprint text NOT NULL CHECK (btrim(request_fingerprint) <> ''),
    acting_seat text NOT NULL CHECK (acting_seat IN ('N','E','S','W')),
    acting_hand jsonb NOT NULL CHECK (jsonb_typeof(acting_hand)='object'),
    public_auction jsonb NOT NULL CHECK (jsonb_typeof(public_auction)='object'),
    public_context jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(public_context)='object'),
    scope_key text NOT NULL DEFAULT 'default' CHECK (btrim(scope_key) <> ''),
    knowledge_version_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    candidate_rule_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    rejected_candidates jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(rejected_candidates)='array'),
    selected_rule_id uuid REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
    selected_call text,
    outcome text NOT NULL CHECK (outcome IN ('bid','gap','conflict','error','no_action')),
    knowledge_gap_id uuid REFERENCES public.knowledge_gap(knowledge_gap_id) ON DELETE RESTRICT,
    explanation jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(explanation)='object'),
    resolver_version text NOT NULL CHECK (btrim(resolver_version) <> ''),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (outcome='bid' AND selected_rule_id IS NOT NULL AND selected_call IS NOT NULL AND btrim(selected_call)<>'')
        OR (outcome<>'bid' AND selected_rule_id IS NULL AND selected_call IS NULL)
    ),
    CHECK (
        (outcome='gap' AND knowledge_gap_id IS NOT NULL)
        OR (outcome<>'gap' AND knowledge_gap_id IS NULL)
    ),
    CHECK (NOT bidding.contains_forbidden_hidden_key(acting_hand)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(public_auction)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(public_context)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(rejected_candidates)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(explanation)),
    UNIQUE (school_id, decision_key),
    UNIQUE (school_id, request_fingerprint)
);

CREATE OR REPLACE FUNCTION bidding.validate_decision_trace_school_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.selected_rule_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM bidding.rule
         WHERE rule_id=NEW.selected_rule_id AND school_id=NEW.school_id
    ) THEN
        RAISE EXCEPTION 'BID_DECISION_SELECTED_RULE_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF NEW.knowledge_gap_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.knowledge_gap
         WHERE knowledge_gap_id=NEW.knowledge_gap_id AND school_id=NEW.school_id
    ) THEN
        RAISE EXCEPTION 'BID_DECISION_GAP_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM unnest(NEW.knowledge_version_ids) AS x(knowledge_version_id)
          LEFT JOIN public.knowledge_version AS kv ON kv.knowledge_version_id=x.knowledge_version_id
          LEFT JOIN public.knowledge_item AS ki ON ki.knowledge_item_id=kv.knowledge_item_id
         WHERE ki.school_id IS NULL OR ki.school_id <> NEW.school_id
    ) THEN
        RAISE EXCEPTION 'BID_DECISION_KNOWLEDGE_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM unnest(NEW.candidate_rule_ids) AS x(rule_id)
          LEFT JOIN bidding.rule AS r ON r.rule_id=x.rule_id
         WHERE r.school_id IS NULL OR r.school_id <> NEW.school_id
    ) THEN
        RAISE EXCEPTION 'BID_DECISION_CANDIDATE_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
