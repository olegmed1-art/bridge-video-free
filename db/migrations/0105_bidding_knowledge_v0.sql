-- 0105_bidding_knowledge_v0.sql
-- Executable bidding-knowledge layer for issue #609.
--
-- Authority remains in public.knowledge_version:
--   school_canon  -> active SCHOOL CANON only through public.canon_activation
--   external      -> opt-in WORLD / EXTERNAL runtime lane
--
-- This migration intentionally contains no bridge-system meanings or bids.
-- It creates only representation, provenance, safety gates, traces and retrieval.

CREATE SCHEMA IF NOT EXISTS bidding AUTHORIZATION neondb_owner;

COMMENT ON SCHEMA bidding IS
  'Executable bidding knowledge and runtime trace layer. SCHOOL CANON and WORLD/EXTERNAL remain authority-separated through public.knowledge_version.';

CREATE OR REPLACE FUNCTION bidding.contains_forbidden_hidden_key(payload jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS 'WITH RECURSIVE walk(value) AS (
  SELECT COALESCE(payload, ''null''::jsonb)
  UNION ALL
  SELECT child.value
  FROM walk AS w
  CROSS JOIN LATERAL (
    SELECT e.value
    FROM jsonb_each(CASE WHEN jsonb_typeof(w.value) = ''object'' THEN w.value ELSE ''{}''::jsonb END) AS e
    UNION ALL
    SELECT a.value
    FROM jsonb_array_elements(CASE WHEN jsonb_typeof(w.value) = ''array'' THEN w.value ELSE ''[]''::jsonb END) AS a
  ) AS child
), forbidden AS (
  SELECT 1
  FROM walk AS w
  CROSS JOIN LATERAL jsonb_object_keys(CASE WHEN jsonb_typeof(w.value) = ''object'' THEN w.value ELSE ''{}''::jsonb END) AS k(key)
  WHERE lower(k.key) = ANY (ARRAY[
    ''partner_hand'', ''opponent_hand'', ''opponent_hands'',
    ''north_hand'', ''east_hand'', ''south_hand'', ''west_hand'',
    ''full_deal'', ''hidden_cards'', ''actual_partner_hand'',
    ''actual_opponent_hand'', ''actual_opponent_hands''
  ])
  LIMIT 1
)
SELECT EXISTS (SELECT 1 FROM forbidden);';

COMMENT ON FUNCTION bidding.contains_forbidden_hidden_key(jsonb) IS
  'Recursively detects hidden-hand/full-deal keys that are forbidden in bidding runtime inputs, executable rules and decision traces.';

CREATE TABLE bidding.rule (
  rule_id uuid PRIMARY KEY DEFAULT uuidv7(),
  knowledge_version_id uuid NOT NULL UNIQUE
    REFERENCES public.knowledge_version(knowledge_version_id) ON DELETE RESTRICT,
  rule_key text NOT NULL UNIQUE CHECK (btrim(rule_key) <> ''),
  rule_kind text NOT NULL
    CHECK (rule_kind IN ('bid', 'inference', 'priority', 'exception', 'fallback')),
  auction_pattern jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(auction_pattern) = 'object'),
  hand_constraints jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(hand_constraints) = 'object'),
  public_context_constraints jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(public_context_constraints) = 'object'),
  action jsonb NOT NULL CHECK (jsonb_typeof(action) = 'object'),
  meaning jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(meaning) = 'object'),
  public_inference jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(public_inference) = 'object'),
  alert_semantics jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(alert_semantics) = 'object'),
  forcing_semantics jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(forcing_semantics) = 'object'),
  priority integer NOT NULL DEFAULT 0,
  specificity integer NOT NULL DEFAULT 0 CHECK (specificity >= 0),
  explanation jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(explanation) = 'object'),
  compiled_payload jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(compiled_payload) = 'object'),
  lifecycle_status text NOT NULL DEFAULT 'candidate'
    CHECK (lifecycle_status IN ('candidate', 'validated', 'retired')),
  method_version text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (updated_at >= created_at),
  CHECK (NOT bidding.contains_forbidden_hidden_key(auction_pattern)),
  CHECK (NOT bidding.contains_forbidden_hidden_key(hand_constraints)),
  CHECK (NOT bidding.contains_forbidden_hidden_key(public_context_constraints)),
  CHECK (NOT bidding.contains_forbidden_hidden_key(action)),
  CHECK (NOT bidding.contains_forbidden_hidden_key(meaning)),
  CHECK (NOT bidding.contains_forbidden_hidden_key(public_inference)),
  CHECK (NOT bidding.contains_forbidden_hidden_key(alert_semantics)),
  CHECK (NOT bidding.contains_forbidden_hidden_key(forcing_semantics)),
  CHECK (NOT bidding.contains_forbidden_hidden_key(explanation)),
  CHECK (NOT bidding.contains_forbidden_hidden_key(compiled_payload))
);

COMMENT ON TABLE bidding.rule IS
  'Executable projection of one versioned knowledge object. Authority and provenance remain in public.knowledge_version and related public tables.';

CREATE INDEX bidding_rule_runtime_lookup_idx
  ON bidding.rule (lifecycle_status, priority DESC, specificity DESC, rule_key);

CREATE TABLE bidding.rule_relation (
  rule_relation_id uuid PRIMARY KEY DEFAULT uuidv7(),
  from_rule_id uuid NOT NULL REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
  to_rule_id uuid NOT NULL REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
  relation_type text NOT NULL
    CHECK (relation_type IN ('depends_on', 'overrides', 'excludes', 'continues_to', 'implies')),
  conditions jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(conditions) = 'object'),
  method_version text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (from_rule_id <> to_rule_id),
  CHECK (NOT bidding.contains_forbidden_hidden_key(conditions)),
  UNIQUE (from_rule_id, to_rule_id, relation_type)
);

CREATE INDEX bidding_rule_relation_to_idx
  ON bidding.rule_relation (to_rule_id, relation_type);

CREATE TABLE bidding.rule_test (
  rule_test_id uuid PRIMARY KEY DEFAULT uuidv7(),
  rule_id uuid NOT NULL REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
  test_key text NOT NULL CHECK (btrim(test_key) <> ''),
  test_type text NOT NULL
    CHECK (test_type IN (
      'positive', 'negative', 'boundary', 'interference',
      'hidden_information', 'conflict', 'regression'
    )),
  fixture jsonb NOT NULL CHECK (jsonb_typeof(fixture) = 'object'),
  expected jsonb NOT NULL CHECK (jsonb_typeof(expected) = 'object'),
  last_result text NOT NULL DEFAULT 'not_run'
    CHECK (last_result IN ('not_run', 'pass', 'fail')),
  last_result_details jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(last_result_details) = 'object'),
  evidence_id uuid REFERENCES public.evidence(evidence_id) ON DELETE SET NULL,
  method_version text,
  executed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    (last_result = 'not_run' AND executed_at IS NULL)
    OR (last_result IN ('pass', 'fail') AND executed_at IS NOT NULL)
  ),
  UNIQUE (rule_id, test_key)
);

CREATE INDEX bidding_rule_test_gate_idx
  ON bidding.rule_test (rule_id, test_type, last_result);

CREATE TABLE bidding.rule_conflict (
  rule_conflict_id uuid PRIMARY KEY DEFAULT uuidv7(),
  left_rule_id uuid NOT NULL REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
  right_rule_id uuid NOT NULL REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
  conflict_type text NOT NULL
    CHECK (conflict_type IN (
      'overlap', 'contradiction', 'priority_tie',
      'inference_mismatch', 'activation_collision'
    )),
  context_scope jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(context_scope) = 'object'),
  details jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(details) = 'object'),
  evidence_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
  status text NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'resolved', 'accepted_risk', 'invalidated')),
  resolved_by_person_id uuid REFERENCES public.person(person_id) ON DELETE SET NULL,
  resolved_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (left_rule_id <> right_rule_id),
  CHECK (
    (status = 'open' AND resolved_at IS NULL)
    OR (status <> 'open' AND resolved_at IS NOT NULL)
  ),
  CHECK (NOT bidding.contains_forbidden_hidden_key(context_scope)),
  CHECK (NOT bidding.contains_forbidden_hidden_key(details))
);

CREATE UNIQUE INDEX bidding_rule_conflict_pair_uidx
  ON bidding.rule_conflict (
    LEAST(left_rule_id, right_rule_id),
    GREATEST(left_rule_id, right_rule_id),
    conflict_type
  );

CREATE INDEX bidding_rule_conflict_open_idx
  ON bidding.rule_conflict (left_rule_id, right_rule_id, conflict_type)
  WHERE status = 'open';

CREATE TABLE bidding.runtime_activation (
  runtime_activation_id uuid PRIMARY KEY DEFAULT uuidv7(),
  rule_id uuid NOT NULL REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
  authority_lane text NOT NULL
    CHECK (authority_lane IN ('school_canon', 'world_external')),
  canon_activation_id uuid
    REFERENCES public.canon_activation(canon_activation_id) ON DELETE RESTRICT,
  scope_key text NOT NULL DEFAULT 'default' CHECK (btrim(scope_key) <> ''),
  valid_from timestamptz NOT NULL DEFAULT now(),
  valid_to timestamptz,
  status text NOT NULL DEFAULT 'candidate'
    CHECK (status IN ('candidate', 'active', 'superseded', 'revoked')),
  activation_provenance jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(activation_provenance) = 'object'),
  activated_by_person_id uuid REFERENCES public.person(person_id) ON DELETE SET NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  CHECK (valid_to IS NULL OR valid_to > valid_from),
  CHECK (
    (authority_lane = 'school_canon' AND canon_activation_id IS NOT NULL)
    OR (authority_lane = 'world_external' AND canon_activation_id IS NULL)
  ),
  CHECK (NOT bidding.contains_forbidden_hidden_key(activation_provenance))
);

CREATE UNIQUE INDEX bidding_runtime_activation_open_uidx
  ON bidding.runtime_activation (rule_id, authority_lane, scope_key)
  WHERE status = 'active' AND valid_to IS NULL;

CREATE INDEX bidding_runtime_activation_lookup_idx
  ON bidding.runtime_activation (
    authority_lane, scope_key, status, valid_from, valid_to
  );

CREATE TABLE bidding.decision_trace (
  decision_trace_id uuid PRIMARY KEY DEFAULT uuidv7(),
  school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
  decision_key text NOT NULL UNIQUE CHECK (btrim(decision_key) <> ''),
  acting_seat text NOT NULL CHECK (acting_seat IN ('N', 'E', 'S', 'W')),
  acting_hand jsonb NOT NULL CHECK (jsonb_typeof(acting_hand) = 'object'),
  public_auction jsonb NOT NULL CHECK (jsonb_typeof(public_auction) = 'object'),
  public_context jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(public_context) = 'object'),
  scope_key text NOT NULL DEFAULT 'default' CHECK (btrim(scope_key) <> ''),
  knowledge_version_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
  candidate_rule_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
  rejected_candidates jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(rejected_candidates) = 'array'),
  selected_rule_id uuid REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
  selected_call text,
  outcome text NOT NULL
    CHECK (outcome IN ('bid', 'gap', 'conflict', 'error', 'no_action')),
  knowledge_gap_id uuid REFERENCES public.knowledge_gap(knowledge_gap_id) ON DELETE RESTRICT,
  explanation jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(explanation) = 'object'),
  resolver_version text NOT NULL CHECK (btrim(resolver_version) <> ''),
  recorded_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    (outcome = 'bid'
      AND selected_rule_id IS NOT NULL
      AND selected_call IS NOT NULL
      AND btrim(selected_call) <> '')
    OR (outcome <> 'bid'
      AND selected_rule_id IS NULL
      AND selected_call IS NULL)
  ),
  CHECK (
    (outcome = 'gap' AND knowledge_gap_id IS NOT NULL)
    OR (outcome <> 'gap' AND knowledge_gap_id IS NULL)
  ),
  CHECK (NOT bidding.contains_forbidden_hidden_key(public_auction)),
  CHECK (NOT bidding.contains_forbidden_hidden_key(public_context)),
  CHECK (NOT bidding.contains_forbidden_hidden_key(rejected_candidates)),
  CHECK (NOT bidding.contains_forbidden_hidden_key(explanation))
);

COMMENT ON TABLE bidding.decision_trace IS
  'Append-only bidding decision trace containing only the acting hand and public information. Gaps point to public.knowledge_gap.';

CREATE INDEX bidding_decision_trace_school_time_idx
  ON bidding.decision_trace (school_id, recorded_at DESC);

CREATE INDEX bidding_decision_trace_outcome_idx
  ON bidding.decision_trace (school_id, outcome, recorded_at DESC);

CREATE OR REPLACE FUNCTION bidding.enforce_runtime_activation()
RETURNS trigger
LANGUAGE plpgsql
AS '
DECLARE
  v_knowledge_version_id uuid;
  v_authority_class text;
  v_review_status text;
  v_version_status text;
  v_rule_status text;
BEGIN
  IF NEW.status <> ''active'' THEN
    RETURN NEW;
  END IF;

  SELECT r.knowledge_version_id,
         kv.authority_class,
         kv.review_status,
         kv.status,
         r.lifecycle_status
    INTO v_knowledge_version_id,
         v_authority_class,
         v_review_status,
         v_version_status,
         v_rule_status
  FROM bidding.rule AS r
  JOIN public.knowledge_version AS kv
    ON kv.knowledge_version_id = r.knowledge_version_id
  WHERE r.rule_id = NEW.rule_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION ''BID_ACTIVATION_RULE_NOT_FOUND''
      USING ERRCODE = ''23514'';
  END IF;

  IF v_rule_status <> ''validated'' THEN
    RAISE EXCEPTION ''BID_ACTIVATION_RULE_NOT_VALIDATED''
      USING ERRCODE = ''23514'';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.knowledge_version_source AS kvs
    JOIN public.source AS s ON s.source_id = kvs.source_id
    WHERE kvs.knowledge_version_id = v_knowledge_version_id
      AND s.status = ''active''
  ) THEN
    RAISE EXCEPTION ''BID_ACTIVATION_SOURCE_REQUIRED''
      USING ERRCODE = ''23514'';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM unnest(
      ARRAY[''positive'', ''negative'', ''boundary'', ''hidden_information'']::text[]
    ) AS req(test_type)
    WHERE NOT EXISTS (
      SELECT 1
      FROM bidding.rule_test AS t
      WHERE t.rule_id = NEW.rule_id
        AND t.test_type = req.test_type
        AND t.last_result = ''pass''
    )
  ) THEN
    RAISE EXCEPTION ''BID_ACTIVATION_REQUIRED_TEST_COVERAGE_MISSING''
      USING ERRCODE = ''23514'';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM bidding.rule_test AS t
    WHERE t.rule_id = NEW.rule_id
      AND t.last_result = ''fail''
  ) THEN
    RAISE EXCEPTION ''BID_ACTIVATION_FAILED_TEST_PRESENT''
      USING ERRCODE = ''23514'';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM bidding.rule_conflict AS c
    WHERE c.status = ''open''
      AND (c.left_rule_id = NEW.rule_id OR c.right_rule_id = NEW.rule_id)
  ) THEN
    RAISE EXCEPTION ''BID_ACTIVATION_OPEN_CONFLICT''
      USING ERRCODE = ''23514'';
  END IF;

  IF v_authority_class = ''school_canon'' THEN
    IF NEW.authority_lane <> ''school_canon'' THEN
      RAISE EXCEPTION ''BID_ACTIVATION_CANON_LANE_MISMATCH''
        USING ERRCODE = ''23514'';
    END IF;

    IF v_review_status IS NULL
       OR v_review_status NOT IN (''reviewed'', ''approved'') THEN
      RAISE EXCEPTION ''BID_ACTIVATION_CANON_REVIEW_REQUIRED''
        USING ERRCODE = ''23514'';
    END IF;

    IF v_version_status IS NULL
       OR v_version_status NOT IN (''candidate'', ''active'', ''approved'') THEN
      RAISE EXCEPTION ''BID_ACTIVATION_CANON_VERSION_INELIGIBLE''
        USING ERRCODE = ''23514'';
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM public.canon_activation AS ca
      WHERE ca.canon_activation_id = NEW.canon_activation_id
        AND ca.knowledge_version_id = v_knowledge_version_id
        AND ca.scope_key = NEW.scope_key
        AND ca.status = ''active''
        AND ca.valid_from <= NEW.valid_from
        AND (
          ca.valid_to IS NULL
          OR (NEW.valid_to IS NOT NULL AND NEW.valid_to <= ca.valid_to)
        )
    ) THEN
      RAISE EXCEPTION ''BID_ACTIVATION_CANON_APPROVAL_REQUIRED''
        USING ERRCODE = ''23514'';
    END IF;

  ELSIF v_authority_class = ''external'' THEN
    IF NEW.authority_lane <> ''world_external'' THEN
      RAISE EXCEPTION ''BID_ACTIVATION_WORLD_LANE_MISMATCH''
        USING ERRCODE = ''23514'';
    END IF;

    IF NEW.canon_activation_id IS NOT NULL THEN
      RAISE EXCEPTION ''BID_ACTIVATION_EXTERNAL_CANNOT_REFERENCE_CANON''
        USING ERRCODE = ''23514'';
    END IF;

    IF v_review_status IS NULL
       OR v_review_status NOT IN (''reviewed'', ''approved'') THEN
      RAISE EXCEPTION ''BID_ACTIVATION_WORLD_REVIEW_REQUIRED''
        USING ERRCODE = ''23514'';
    END IF;

    IF v_version_status IS NULL
       OR v_version_status NOT IN (''candidate'', ''active'', ''approved'') THEN
      RAISE EXCEPTION ''BID_ACTIVATION_WORLD_VERSION_INELIGIBLE''
        USING ERRCODE = ''23514'';
    END IF;

  ELSE
    RAISE EXCEPTION ''BID_ACTIVATION_AUTHORITY_NOT_RUNTIME_ELIGIBLE''
      USING ERRCODE = ''23514'';
  END IF;

  RETURN NEW;
END;
';

CREATE TRIGGER runtime_activation_guard
BEFORE INSERT OR UPDATE ON bidding.runtime_activation
FOR EACH ROW
EXECUTE FUNCTION bidding.enforce_runtime_activation();

CREATE OR REPLACE FUNCTION bidding.reject_decision_trace_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS '
BEGIN
  RAISE EXCEPTION ''BID_DECISION_TRACE_APPEND_ONLY''
    USING ERRCODE = ''55000'';
END;
';

CREATE TRIGGER decision_trace_append_only
BEFORE UPDATE OR DELETE ON bidding.decision_trace
FOR EACH ROW
EXECUTE FUNCTION bidding.reject_decision_trace_mutation();

CREATE OR REPLACE FUNCTION bidding.reject_worker_active_rule_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS '
BEGIN
  IF current_user = ''bridge_school_worker''
     AND EXISTS (
       SELECT 1
       FROM bidding.runtime_activation AS ra
       WHERE ra.rule_id = OLD.rule_id
         AND ra.status = ''active''
         AND ra.valid_from <= now()
         AND (ra.valid_to IS NULL OR ra.valid_to > now())
     ) THEN
    RAISE EXCEPTION ''BID_ACTIVE_RULE_WORKER_IMMUTABLE''
      USING ERRCODE = ''42501'';
  END IF;

  IF TG_OP = ''DELETE'' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
';

CREATE TRIGGER worker_active_rule_immutable
BEFORE UPDATE OR DELETE ON bidding.rule
FOR EACH ROW
EXECUTE FUNCTION bidding.reject_worker_active_rule_mutation();

CREATE OR REPLACE VIEW bidding.active_school_canon_rule_v AS
SELECT
  ki.school_id,
  ra.scope_key,
  ra.runtime_activation_id,
  ra.valid_from,
  ra.valid_to,
  r.rule_id,
  r.knowledge_version_id,
  r.rule_key,
  r.rule_kind,
  r.auction_pattern,
  r.hand_constraints,
  r.public_context_constraints,
  r.action,
  r.meaning,
  r.public_inference,
  r.alert_semantics,
  r.forcing_semantics,
  r.priority,
  r.specificity,
  r.explanation,
  r.compiled_payload,
  r.method_version
FROM bidding.runtime_activation AS ra
JOIN bidding.rule AS r ON r.rule_id = ra.rule_id
JOIN public.knowledge_version AS kv
  ON kv.knowledge_version_id = r.knowledge_version_id
JOIN public.knowledge_item AS ki
  ON ki.knowledge_item_id = kv.knowledge_item_id
JOIN public.canon_activation AS ca
  ON ca.canon_activation_id = ra.canon_activation_id
WHERE ra.status = 'active'
  AND ra.authority_lane = 'school_canon'
  AND ra.valid_from <= now()
  AND (ra.valid_to IS NULL OR ra.valid_to > now())
  AND r.lifecycle_status = 'validated'
  AND kv.authority_class = 'school_canon'
  AND kv.review_status IN ('reviewed', 'approved')
  AND kv.status IN ('candidate', 'active', 'approved')
  AND ca.status = 'active'
  AND ca.scope_key = ra.scope_key
  AND ca.valid_from <= now()
  AND (ca.valid_to IS NULL OR ca.valid_to > now())
  AND EXISTS (
    SELECT 1
    FROM public.knowledge_version_source AS kvs
    JOIN public.source AS s ON s.source_id = kvs.source_id
    WHERE kvs.knowledge_version_id = r.knowledge_version_id
      AND s.status = 'active'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM bidding.rule_test AS t
    WHERE t.rule_id = r.rule_id
      AND t.last_result = 'fail'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM unnest(
      ARRAY['positive', 'negative', 'boundary', 'hidden_information']::text[]
    ) AS req(test_type)
    WHERE NOT EXISTS (
      SELECT 1
      FROM bidding.rule_test AS t
      WHERE t.rule_id = r.rule_id
        AND t.test_type = req.test_type
        AND t.last_result = 'pass'
    )
  )
  AND NOT EXISTS (
    SELECT 1
    FROM bidding.rule_conflict AS c
    WHERE c.status = 'open'
      AND (c.left_rule_id = r.rule_id OR c.right_rule_id = r.rule_id)
  );

CREATE OR REPLACE VIEW bidding.active_world_rule_v AS
SELECT
  ki.school_id,
  ra.scope_key,
  ra.runtime_activation_id,
  ra.valid_from,
  ra.valid_to,
  r.rule_id,
  r.knowledge_version_id,
  r.rule_key,
  r.rule_kind,
  r.auction_pattern,
  r.hand_constraints,
  r.public_context_constraints,
  r.action,
  r.meaning,
  r.public_inference,
  r.alert_semantics,
  r.forcing_semantics,
  r.priority,
  r.specificity,
  r.explanation,
  r.compiled_payload,
  r.method_version
FROM bidding.runtime_activation AS ra
JOIN bidding.rule AS r ON r.rule_id = ra.rule_id
JOIN public.knowledge_version AS kv
  ON kv.knowledge_version_id = r.knowledge_version_id
JOIN public.knowledge_item AS ki
  ON ki.knowledge_item_id = kv.knowledge_item_id
WHERE ra.status = 'active'
  AND ra.authority_lane = 'world_external'
  AND ra.canon_activation_id IS NULL
  AND ra.valid_from <= now()
  AND (ra.valid_to IS NULL OR ra.valid_to > now())
  AND r.lifecycle_status = 'validated'
  AND kv.authority_class = 'external'
  AND kv.review_status IN ('reviewed', 'approved')
  AND kv.status IN ('candidate', 'active', 'approved')
  AND EXISTS (
    SELECT 1
    FROM public.knowledge_version_source AS kvs
    JOIN public.source AS s ON s.source_id = kvs.source_id
    WHERE kvs.knowledge_version_id = r.knowledge_version_id
      AND s.status = 'active'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM bidding.rule_test AS t
    WHERE t.rule_id = r.rule_id
      AND t.last_result = 'fail'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM unnest(
      ARRAY['positive', 'negative', 'boundary', 'hidden_information']::text[]
    ) AS req(test_type)
    WHERE NOT EXISTS (
      SELECT 1
      FROM bidding.rule_test AS t
      WHERE t.rule_id = r.rule_id
        AND t.test_type = req.test_type
        AND t.last_result = 'pass'
    )
  )
  AND NOT EXISTS (
    SELECT 1
    FROM bidding.rule_conflict AS c
    WHERE c.status = 'open'
      AND (c.left_rule_id = r.rule_id OR c.right_rule_id = r.rule_id)
  );

CREATE OR REPLACE VIEW bidding.canon_world_link_v AS
SELECT
  kr.knowledge_relation_id,
  kr.school_id,
  CASE
    WHEN fkv.authority_class = 'school_canon'
      THEN kr.from_version_id
    ELSE kr.to_version_id
  END AS canon_version_id,
  CASE
    WHEN fkv.authority_class = 'external'
      THEN kr.from_version_id
    ELSE kr.to_version_id
  END AS world_version_id,
  CASE
    WHEN fkv.authority_class = 'school_canon'
      THEN fr.rule_id
    ELSE tr.rule_id
  END AS canon_rule_id,
  CASE
    WHEN fkv.authority_class = 'external'
      THEN fr.rule_id
    ELSE tr.rule_id
  END AS world_rule_id,
  kr.relation_type,
  kr.scope,
  kr.preconditions,
  kr.confidence_class,
  kr.evidence_ids,
  kr.method_version,
  kr.created_at
FROM public.knowledge_relation AS kr
JOIN public.knowledge_version AS fkv
  ON fkv.knowledge_version_id = kr.from_version_id
JOIN public.knowledge_version AS tkv
  ON tkv.knowledge_version_id = kr.to_version_id
LEFT JOIN bidding.rule AS fr
  ON fr.knowledge_version_id = kr.from_version_id
LEFT JOIN bidding.rule AS tr
  ON tr.knowledge_version_id = kr.to_version_id
WHERE (fkv.authority_class = 'school_canon' AND tkv.authority_class = 'external')
   OR (fkv.authority_class = 'external' AND tkv.authority_class = 'school_canon');

CREATE OR REPLACE FUNCTION bidding.get_runtime_rule_catalog(
  p_school_id uuid,
  p_scope_key text,
  p_include_world boolean DEFAULT false
)
RETURNS TABLE (
  authority_lane text,
  rule_id uuid,
  knowledge_version_id uuid,
  rule_key text,
  rule_kind text,
  auction_pattern jsonb,
  hand_constraints jsonb,
  public_context_constraints jsonb,
  action jsonb,
  meaning jsonb,
  public_inference jsonb,
  priority integer,
  specificity integer,
  explanation jsonb,
  method_version text
)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS '
  SELECT
    ''school_canon''::text,
    c.rule_id,
    c.knowledge_version_id,
    c.rule_key,
    c.rule_kind,
    c.auction_pattern,
    c.hand_constraints,
    c.public_context_constraints,
    c.action,
    c.meaning,
    c.public_inference,
    c.priority,
    c.specificity,
    c.explanation,
    c.method_version
  FROM bidding.active_school_canon_rule_v AS c
  WHERE c.school_id = p_school_id
    AND c.scope_key = p_scope_key

  UNION ALL

  SELECT
    ''world_external''::text,
    w.rule_id,
    w.knowledge_version_id,
    w.rule_key,
    w.rule_kind,
    w.auction_pattern,
    w.hand_constraints,
    w.public_context_constraints,
    w.action,
    w.meaning,
    w.public_inference,
    w.priority,
    w.specificity,
    w.explanation,
    w.method_version
  FROM bidding.active_world_rule_v AS w
  WHERE p_include_world
    AND w.school_id = p_school_id
    AND w.scope_key = p_scope_key

  ORDER BY priority DESC, specificity DESC, rule_key;
';

COMMENT ON FUNCTION bidding.get_runtime_rule_catalog(uuid, text, boolean) IS
  'Returns only currently gated active rule objects. It does not evaluate applicability or choose a call. WORLD rules are opt-in and never become SCHOOL CANON.';

REVOKE ALL ON SCHEMA bidding FROM PUBLIC;
GRANT USAGE ON SCHEMA bidding TO bridge_school_reader, bridge_school_worker;

GRANT SELECT ON ALL TABLES IN SCHEMA bidding TO bridge_school_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA bidding TO bridge_school_worker;

GRANT INSERT, UPDATE
  ON bidding.rule,
     bidding.rule_relation,
     bidding.rule_test
  TO bridge_school_worker;

GRANT INSERT ON bidding.rule_conflict TO bridge_school_worker;

GRANT INSERT ON bidding.decision_trace TO bridge_school_worker;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE
  ON bidding.runtime_activation
  FROM bridge_school_reader, bridge_school_worker;

REVOKE UPDATE, DELETE, TRUNCATE
  ON bidding.decision_trace
  FROM bridge_school_reader, bridge_school_worker;

REVOKE UPDATE, DELETE, TRUNCATE
  ON bidding.rule_conflict
  FROM bridge_school_reader, bridge_school_worker;

REVOKE ALL ON FUNCTION bidding.contains_forbidden_hidden_key(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bidding.contains_forbidden_hidden_key(jsonb)
  TO bridge_school_worker;

REVOKE ALL ON FUNCTION bidding.get_runtime_rule_catalog(uuid, text, boolean)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bidding.get_runtime_rule_catalog(uuid, text, boolean)
  TO bridge_school_reader, bridge_school_worker;

-- checksum is the SHA-256 of this file up to (but not including) this registry block.
-- The value is filled by the build/check script and reviewed in the PR.
INSERT INTO public.schema_migration (migration_key, checksum)
VALUES ('0105_bidding_knowledge_v0', 'ee06aff89329079e19365a26aed7aac0611b5ea69686b98df1629356cb72ba86')
ON CONFLICT (migration_key) DO NOTHING;
