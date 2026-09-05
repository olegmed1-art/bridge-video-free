\set ON_ERROR_STOP on
BEGIN;

-- A logical bidding rule may have multiple immutable knowledge versions, but a
-- school/profile/level/rule-key tuple must remain bound to exactly one
-- knowledge item. The registry makes that invariant concurrency-safe while
-- corrected versions are staged before atomically superseding their predecessor.
CREATE TABLE bidding.rule_key_identity (
  school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
  system_profile text NOT NULL CHECK (btrim(system_profile)<>''),
  learner_level text NOT NULL CHECK (btrim(learner_level)<>''),
  rule_key text NOT NULL CHECK (btrim(rule_key)<>''),
  knowledge_item_id uuid NOT NULL REFERENCES public.knowledge_item(knowledge_item_id)
    ON DELETE RESTRICT,
  PRIMARY KEY (school_id,system_profile,learner_level,rule_key)
);

INSERT INTO bidding.rule_key_identity(
  school_id,system_profile,learner_level,rule_key,knowledge_item_id
)
SELECT r.school_id,
       COALESCE(NULLIF(btrim(kv.bidding_system_key),''),'__UNSCOPED_SYSTEM__'),
       COALESCE(NULLIF(btrim(kv.level_scope->>'level_key'),''),'__UNSCOPED_LEVEL__'),
       r.rule_key,kv.knowledge_item_id
  FROM bidding.rule r
  JOIN public.knowledge_version kv ON kv.knowledge_version_id=r.knowledge_version_id;

CREATE OR REPLACE FUNCTION bidding.bind_rule_key_identity()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
AS $$
DECLARE
  v_item_id uuid;
  v_bound_item_id uuid;
  v_identity_profile text;
  v_identity_level text;
BEGIN
  SELECT kv.knowledge_item_id,
         COALESCE(NULLIF(btrim(kv.bidding_system_key),''),'__UNSCOPED_SYSTEM__'),
         COALESCE(NULLIF(btrim(kv.level_scope->>'level_key'),''),'__UNSCOPED_LEVEL__')
    INTO v_item_id,v_identity_profile,v_identity_level
    FROM public.knowledge_version kv
    JOIN public.knowledge_item ki ON ki.knowledge_item_id=kv.knowledge_item_id
   WHERE kv.knowledge_version_id=NEW.knowledge_version_id
     AND ki.school_id=NEW.school_id;
  IF v_item_id IS NULL THEN
    RAISE EXCEPTION 'BIDDING_RULE_KEY_IDENTITY_SCOPE_MISMATCH' USING ERRCODE='23514';
  END IF;

  -- The no-op conflict update takes the unique-key lock and returns the
  -- authoritative binding even when two rule revisions are staged together.
  INSERT INTO bidding.rule_key_identity(
    school_id,system_profile,learner_level,rule_key,knowledge_item_id
  ) VALUES (
    NEW.school_id,v_identity_profile,v_identity_level,NEW.rule_key,v_item_id
  )
  ON CONFLICT (school_id,system_profile,learner_level,rule_key) DO UPDATE
    SET rule_key=EXCLUDED.rule_key
  RETURNING knowledge_item_id INTO v_bound_item_id;
  IF v_bound_item_id<>v_item_id THEN
    RAISE EXCEPTION 'BIDDING_RULE_KEY_IDENTITY_MISMATCH' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

REVOKE ALL ON FUNCTION bidding.bind_rule_key_identity() FROM PUBLIC;
CREATE TRIGGER bidding_rule_key_identity_guard
BEFORE INSERT OR UPDATE OF school_id,knowledge_version_id,rule_key ON bidding.rule
FOR EACH ROW EXECUTE FUNCTION bidding.bind_rule_key_identity();

ALTER TABLE bidding.rule DROP CONSTRAINT rule_school_id_rule_key_key;
CREATE INDEX bidding_rule_version_identity_idx
  ON bidding.rule(school_id,rule_key,knowledge_version_id);

-- Capability roles are migration-owned. Refuse collisions instead of mutating
-- pre-existing cluster roles or memberships that rollback cannot reconstruct.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_roles
     WHERE rolname=ANY(ARRAY[
       'bridge_school_canon_verifier','bridge_school_canon_semantic_verifier',
       'bridge_school_canon_bridge_verifier','bridge_school_canon_firewall_verifier',
       'bridge_school_canon_control_verifier','bridge_school_canon_promoter',
       'bridge_school_canon_restorer'
     ])
  ) THEN
    RAISE EXCEPTION 'VIDEO_CANON_ROLE_COLLISION' USING ERRCODE='55000';
  END IF;
END $$;

DO $$
DECLARE r record; v_role text;
BEGIN
    FOREACH v_role IN ARRAY ARRAY[
      'bridge_school_canon_verifier','bridge_school_canon_semantic_verifier',
      'bridge_school_canon_bridge_verifier','bridge_school_canon_firewall_verifier',
      'bridge_school_canon_control_verifier','bridge_school_canon_promoter',
      'bridge_school_canon_restorer'
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=v_role) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION',v_role);
        END IF;
    END LOOP;
    FOR r IN SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication
               FROM pg_roles WHERE rolname=ANY(ARRAY[
                 'bridge_school_canon_verifier','bridge_school_canon_semantic_verifier',
                 'bridge_school_canon_bridge_verifier','bridge_school_canon_firewall_verifier',
                 'bridge_school_canon_control_verifier','bridge_school_canon_promoter',
                 'bridge_school_canon_restorer'
               ]) LOOP
        IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication THEN
            RAISE EXCEPTION 'unsafe Video-to-Canon role: %',r.rolname;
        END IF;
    END LOOP;
END $$;

COMMENT ON ROLE bridge_school_canon_verifier IS
  'NOLOGIN capability that records sealed verification bundles but cannot attest checks or activate Canon';
COMMENT ON ROLE bridge_school_canon_semantic_verifier IS
  'NOLOGIN capability for authenticated semantic-parser attestations only';
COMMENT ON ROLE bridge_school_canon_bridge_verifier IS
  'NOLOGIN capability for authenticated bridge-logic attestations only';
COMMENT ON ROLE bridge_school_canon_firewall_verifier IS
  'NOLOGIN capability for authenticated hidden-information firewall attestations only';
COMMENT ON ROLE bridge_school_canon_control_verifier IS
  'NOLOGIN capability for non-independent control attestations only';
COMMENT ON ROLE bridge_school_canon_promoter IS
  'NOLOGIN capability for the guarded AI-verified teacher-video Canon activation RPC';
COMMENT ON ROLE bridge_school_canon_restorer IS
  'NOLOGIN capability for the guarded receipt-bound Video-to-Canon restoration RPC';

-- Forward-only extension of the already-applied 0200 key firewall. Historical
-- migration 0200 remains byte-stable for production checksum verification.
CREATE OR REPLACE FUNCTION bidding.contains_forbidden_hidden_key(payload jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
WITH RECURSIVE walk(value) AS (
    SELECT COALESCE(payload, 'null'::jsonb)
    UNION ALL
    SELECT child.value
      FROM walk AS w
      CROSS JOIN LATERAL (
          SELECT e.value
            FROM jsonb_each(
                CASE WHEN jsonb_typeof(w.value)='object' THEN w.value ELSE '{}'::jsonb END
            ) AS e
          UNION ALL
          SELECT a.value
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(w.value)='array' THEN w.value ELSE '[]'::jsonb END
            ) AS a
      ) AS child
), forbidden AS (
    SELECT 1
      FROM walk AS w
      CROSS JOIN LATERAL jsonb_object_keys(
          CASE WHEN jsonb_typeof(w.value)='object' THEN w.value ELSE '{}'::jsonb END
      ) AS k(key)
     WHERE lower(k.key) = ANY (ARRAY[
        'partner_hand','opponent_hand','opponent_hands',
        'north_hand','east_hand','south_hand','west_hand','lho_hand','rho_hand',
        'full_deal','hidden_cards','hidden_hand','hidden_hands',
        'hidden_holding','hidden_holdings','concealed_hand','concealed_hands',
        'concealed_holding','concealed_holdings','concealed_card','concealed_cards',
        'hidden_deal','hidden_deals','concealed_deal','concealed_deals',
        'actual_partner_hand','actual_opponent_hand','actual_opponent_hands',
        'partner_cards','opponent_cards','all_hands'
     ])
        OR regexp_replace(lower(k.key),'[^a-z0-9]','','g') ~
           '^(actual)?(partner|opponent|north|east|south|west|leftopponent|rightopponent|lefthandopponent|righthandopponent|[lr](?:[[:space:]]*[.][[:space:]]*)?h(?:[[:space:]]*[.][[:space:]]*)?o[.]?)(s)?(hand|holding|cards?|deals?)+(s)?$'
        OR regexp_replace(lower(k.key),'[^a-z0-9]','','g') ~
           '^(hidden|concealed)(hand|holding|cards?|deals?)+(s)?$'
        OR regexp_replace(lower(k.key),'[^[:alnum:]]','','g') ~
           '^((рук[аи]|карт(а|ы|очки?)|расклад)(партн[её]ра|соперника|оппонента|противника)|(партн[её]ра|соперника|оппонента|противника)(рук[аи]|карт(а|ы|очки?)|расклад))$'
     LIMIT 1
)
SELECT EXISTS (SELECT 1 FROM forbidden);
$$;
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_auth_members m
    JOIN pg_roles member_role ON member_role.oid=m.member
    WHERE member_role.rolname=ANY(ARRAY[
      'bridge_school_canon_verifier','bridge_school_canon_semantic_verifier',
      'bridge_school_canon_bridge_verifier','bridge_school_canon_firewall_verifier',
      'bridge_school_canon_control_verifier','bridge_school_canon_promoter',
      'bridge_school_canon_restorer'
    ])
  ) THEN
    RAISE EXCEPTION 'Video-to-Canon capability role inherits an unexpected role';
  END IF;
END $$;
GRANT USAGE ON SCHEMA public,bidding TO bridge_school_canon_verifier,
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier,
  bridge_school_canon_promoter,bridge_school_canon_restorer;
REVOKE CREATE ON SCHEMA public,bidding FROM bridge_school_canon_verifier,
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier,
  bridge_school_canon_promoter,bridge_school_canon_restorer;

CREATE TABLE bidding.video_canon_source_policy (
    video_canon_source_policy_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    source_id uuid NOT NULL REFERENCES public.source(source_id) ON DELETE RESTRICT,
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    video_file_id text NOT NULL CHECK (btrim(video_file_id)<>''),
    teacher_ids text[] NOT NULL CHECK (cardinality(teacher_ids)>0),
    semantic_scopes text[] NOT NULL CHECK (cardinality(semantic_scopes)>0),
    system_profile text NOT NULL CHECK (btrim(system_profile)<>''),
    learner_level text NOT NULL CHECK (btrim(learner_level)<>''),
    policy_version text NOT NULL CHECK (policy_version='school-video-auto-canon-v1'),
    authorization_evidence_sha256 text NOT NULL CHECK (authorization_evidence_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked','superseded')),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    retired_at timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to>valid_from),
    CHECK ((status='active')=(retired_at IS NULL))
);

CREATE UNIQUE INDEX video_canon_source_policy_active_identity_idx
  ON bidding.video_canon_source_policy(
    school_id,source_id,source_sha256,video_file_id
  ) WHERE status='active';

CREATE TABLE bidding.video_canon_ai_verification_bundle (
    video_canon_ai_verification_bundle_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    analysis_candidate_id uuid NOT NULL REFERENCES public.analysis_candidate(analysis_candidate_id) ON DELETE RESTRICT,
    candidate_payload_hash text NOT NULL CHECK (candidate_payload_hash ~ '^[0-9a-f]{64}$'),
    verification_bundle_sha256 text NOT NULL CHECK (verification_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    bundle_canonical_json text NOT NULL CHECK (btrim(bundle_canonical_json)<>''),
    bundle_payload jsonb NOT NULL CHECK (jsonb_typeof(bundle_payload)='object'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (analysis_candidate_id,candidate_payload_hash,verification_bundle_sha256)
);

CREATE TABLE bidding.video_canon_verifier_registry (
    database_role text PRIMARY KEY,
    verifier_family text NOT NULL UNIQUE CHECK (btrim(verifier_family)<>''),
    allowed_check_ids text[] NOT NULL CHECK (cardinality(allowed_check_ids)>0),
    max_assurance_level text NOT NULL CHECK (max_assurance_level IN ('I1','I2','I3')),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
    registered_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO bidding.video_canon_verifier_registry(
  database_role,verifier_family,allowed_check_ids,max_assurance_level
) VALUES
  ('bridge_school_canon_semantic_verifier','semantic-model-a',ARRAY['SEMANTIC_PARSE'],'I3'),
  ('bridge_school_canon_bridge_verifier','bridge-engine-b',ARRAY['BRIDGE_LOGIC'],'I3'),
  ('bridge_school_canon_firewall_verifier','taint-analyzer',ARRAY['HIDDEN_INFORMATION_FIREWALL'],'I3'),
  ('bridge_school_canon_control_verifier','formal-checker',ARRAY[
    'SOURCE_AUTHORITY','SOURCE_BINDING','SPEAKER_IDENTITY','TRANSCRIPT_BINDING',
    'EXPLANATION_COMPLETENESS','POSITIVE_TESTS','NEGATIVE_TESTS','BOUNDARY_TESTS',
    'INTERFERENCE_TESTS','CANON_REGRESSION','CANON_INTEGRITY','CANON_CONFLICT_SCAN',
    'ROLLBACK_RESTORE','CORRECTION_REVIEW'
  ],'I1');

CREATE TABLE bidding.video_canon_ai_verification (
    video_canon_ai_verification_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    analysis_candidate_id uuid NOT NULL REFERENCES public.analysis_candidate(analysis_candidate_id) ON DELETE RESTRICT,
    video_canon_ai_verification_bundle_id uuid NOT NULL
        REFERENCES bidding.video_canon_ai_verification_bundle(video_canon_ai_verification_bundle_id) ON DELETE RESTRICT,
    candidate_payload_hash text NOT NULL CHECK (candidate_payload_hash ~ '^[0-9a-f]{64}$'),
    verification_bundle_sha256 text NOT NULL CHECK (verification_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    check_id text NOT NULL CHECK (check_id IN (
        'SOURCE_AUTHORITY','SOURCE_BINDING','SPEAKER_IDENTITY','TRANSCRIPT_BINDING',
        'SEMANTIC_PARSE','EXPLANATION_COMPLETENESS','BRIDGE_LOGIC',
        'HIDDEN_INFORMATION_FIREWALL','POSITIVE_TESTS','NEGATIVE_TESTS',
        'BOUNDARY_TESTS','INTERFERENCE_TESTS','CANON_REGRESSION','CANON_INTEGRITY',
        'CANON_CONFLICT_SCAN','ROLLBACK_RESTORE'
    )),
    result text NOT NULL CHECK (result IN ('PASS','FAIL','ERROR')),
    verifier_family text NOT NULL CHECK (btrim(verifier_family)<>''),
    verifier_version text NOT NULL CHECK (btrim(verifier_version)<>''),
    execution_principal text NOT NULL DEFAULT session_user CHECK (btrim(execution_principal)<>''),
    assurance_level text NOT NULL CHECK (assurance_level IN ('I0','I1','I2','I3')),
    evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    canon_snapshot_sha256 text,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
      (check_id IN ('CANON_REGRESSION','CANON_INTEGRITY','CANON_CONFLICT_SCAN','ROLLBACK_RESTORE')
       AND canon_snapshot_sha256 ~ '^[0-9a-f]{64}$')
      OR
      (check_id NOT IN ('CANON_REGRESSION','CANON_INTEGRITY','CANON_CONFLICT_SCAN','ROLLBACK_RESTORE')
       AND canon_snapshot_sha256 IS NULL)
    ),
    UNIQUE (analysis_candidate_id,candidate_payload_hash,verification_bundle_sha256,check_id,verifier_family,verifier_version,execution_principal,evidence_sha256)
);

CREATE TABLE bidding.video_canon_ai_promotion_receipt (
    video_canon_ai_promotion_receipt_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    analysis_candidate_id uuid NOT NULL UNIQUE REFERENCES public.analysis_candidate(analysis_candidate_id) ON DELETE RESTRICT,
    candidate_payload_hash text NOT NULL CHECK (candidate_payload_hash ~ '^[0-9a-f]{64}$'),
    verification_bundle_sha256 text NOT NULL CHECK (verification_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    policy_version text NOT NULL CHECK (policy_version='school-video-auto-canon-v1'),
    semantic_scope text NOT NULL CHECK (btrim(semantic_scope)<>''),
    scope_key text NOT NULL CHECK (btrim(scope_key)<>''),
    rule_content_sha256 text NOT NULL CHECK (rule_content_sha256 ~ '^[0-9a-f]{64}$'),
    knowledge_version_content_sha256 text NOT NULL
      CHECK (knowledge_version_content_sha256 ~ '^[0-9a-f]{64}$'),
    rule_test_state_sha256 text NOT NULL
      CHECK (rule_test_state_sha256 ~ '^[0-9a-f]{64}$'),
    rule_id uuid NOT NULL UNIQUE REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
    canon_activation_id uuid NOT NULL UNIQUE REFERENCES public.canon_activation(canon_activation_id) ON DELETE RESTRICT,
    runtime_activation_id uuid NOT NULL UNIQUE REFERENCES bidding.runtime_activation(runtime_activation_id) ON DELETE RESTRICT,
    superseded_canon_activation_id uuid REFERENCES public.canon_activation(canon_activation_id) ON DELETE RESTRICT,
    superseded_canon_valid_to timestamptz,
    superseded_runtime_activation_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    superseded_runtime_state jsonb NOT NULL DEFAULT '[]'::jsonb
      CHECK (jsonb_typeof(superseded_runtime_state)='array'),
    superseded_rule_state jsonb NOT NULL DEFAULT '[]'::jsonb
      CHECK (jsonb_typeof(superseded_rule_state)='array'),
    superseded_rule_test_state jsonb NOT NULL DEFAULT '[]'::jsonb
      CHECK (jsonb_typeof(superseded_rule_test_state)='array'),
    superseded_source_state jsonb NOT NULL DEFAULT '[]'::jsonb
      CHECK (jsonb_typeof(superseded_source_state)='array'),
    superseded_knowledge_version_content_sha256 text
      CHECK (superseded_knowledge_version_content_sha256 IS NULL
             OR superseded_knowledge_version_content_sha256 ~ '^[0-9a-f]{64}$'),
    superseded_knowledge_item_content_sha256 text
      CHECK (superseded_knowledge_item_content_sha256 IS NULL
             OR superseded_knowledge_item_content_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK ((superseded_canon_activation_id IS NULL)=
           (superseded_knowledge_version_content_sha256 IS NULL)),
    CHECK ((superseded_canon_activation_id IS NULL)=
           (superseded_knowledge_item_content_sha256 IS NULL)),
    promotion_mode text NOT NULL CHECK (promotion_mode='AI_VERIFIED_TEACHER_VIDEO'),
    human_approval_required boolean NOT NULL CHECK (human_approval_required=false),
    promoted_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE bidding.video_canon_ai_restore_receipt (
    video_canon_ai_restore_receipt_id uuid PRIMARY KEY DEFAULT uuidv7(),
    video_canon_ai_promotion_receipt_id uuid NOT NULL UNIQUE
      REFERENCES bidding.video_canon_ai_promotion_receipt(video_canon_ai_promotion_receipt_id) ON DELETE RESTRICT,
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    revoked_canon_activation_id uuid NOT NULL
      REFERENCES public.canon_activation(canon_activation_id) ON DELETE RESTRICT,
    revoked_runtime_activation_id uuid NOT NULL
      REFERENCES bidding.runtime_activation(runtime_activation_id) ON DELETE RESTRICT,
    restored_canon_activation_id uuid REFERENCES public.canon_activation(canon_activation_id) ON DELETE RESTRICT,
    restored_runtime_activation_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    verification_bundle_sha256 text NOT NULL CHECK (verification_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    restore_evidence_sha256 text NOT NULL CHECK (restore_evidence_sha256 ~ '^[0-9a-f]{64}$'),
    restored_by_principal text NOT NULL DEFAULT session_user CHECK (btrim(restored_by_principal)<>''),
    restored_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE bidding.video_correction_review_receipt (
    receipt_sha256 text PRIMARY KEY CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
    receipt_canonical_json text NOT NULL CHECK (btrim(receipt_canonical_json)<>''),
    receipt_payload jsonb NOT NULL CHECK (jsonb_typeof(receipt_payload)='object'),
    recorded_by_role text NOT NULL DEFAULT current_user,
    recorded_by_principal text NOT NULL DEFAULT session_user
      CHECK (btrim(recorded_by_principal)<>''),
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION bidding.is_complete_bridge_hand(p_hand text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
WITH hand AS (
  SELECT regexp_split_to_array(replace(upper(COALESCE(p_hand,'')),'10','T'), E'\\.') AS suits
)
SELECT cardinality(suits)=4
   AND NOT EXISTS (
     SELECT 1 FROM unnest(suits) AS suit(value)
      WHERE value !~ '^(-|(?:(?:10)|[AKQJT2-9X]){0,13})$'
         OR (value<>'-' AND length(replace(value,'X',''))<>(
           SELECT count(DISTINCT rank_char)
             FROM regexp_split_to_table(value,'') AS rank_char
            WHERE rank_char<>'X'
         ))
   )
   AND COALESCE((
     SELECT sum(length(CASE WHEN value='-' THEN '' ELSE value END))
       FROM unnest(suits) AS suit(value)
   ),0)=13
  FROM hand
$$;

CREATE OR REPLACE FUNCTION bidding.contains_forbidden_hidden_value(payload jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
WITH RECURSIVE walk(value,actor_context) AS (
    SELECT COALESCE(payload,'null'::jsonb),false
    UNION ALL
    SELECT child.value,child.actor_context
      FROM walk AS w
      CROSS JOIN LATERAL (
        SELECT e.value,
               w.actor_context OR regexp_replace(
                 lower(e.key),'[^[:alnum:]]','','g'
               ) ~ '^((actual)?(partner|opponent|north|east|south|west|leftopponent|rightopponent|lefthandopponent|righthandopponent|[lr](?:[[:space:]]*[.][[:space:]]*)?h(?:[[:space:]]*[.][[:space:]]*)?o[.]?|n|e|s|w)s?|партн[её]р|партн[её]ра|соперник|соперника|оппонент|оппонента|противник|противника)$'
                 AS actor_context
          FROM jsonb_each(
          CASE WHEN jsonb_typeof(w.value)='object' THEN w.value ELSE '{}'::jsonb END
        ) AS e(key,value)
        UNION ALL
        SELECT a.value,w.actor_context FROM jsonb_array_elements(
          CASE WHEN jsonb_typeof(w.value)='array' THEN w.value ELSE '[]'::jsonb END
        ) AS a
      ) AS child
)
SELECT EXISTS (
  SELECT 1 FROM walk AS w
   WHERE w.actor_context AND jsonb_typeof(w.value)='string'
     AND (
       replace(replace(replace(replace(
         w.value#>>'{}','♠','S:'),'♥','H:'),'♦','D:'),'♣','C:') ~*
         E'^[[:space:]]*(?:(?:the|a|an)[[:space:]]+)?(?:(?:ace|king|queen|jack|ten)(?:[[:space:]]+of[[:space:]]+(?:spades?|hearts?|diamonds?|clubs?))?|(?:two|three|four|five|six|seven|eight|nine)(?:[[:space:]]+of[[:space:]]+(?:spades?|hearts?|diamonds?|clubs?))?(?![-–—])|(?:spades?|hearts?|diamonds?|clubs?)[[:space:]]+(?:ace|king|queen|jack|ten|two|three|four|five|six|seven|eight|nine|10|[KQJT2-9X])(?![-–—])|[SHDC][[:space:]]*:?[[:space:]]*(?:10|[KQJT2-9X])|(?:10|[KQJT2-9X])[[:space:]]*[SHDC]|10|[KQJT2-9X]|[kqjtx]|(?:(?:10)|[KQJT2-9Xkqjtx]){2,13})($|[^[:alnum:]])'
       OR replace(replace(replace(replace(
         w.value#>>'{}','♠','S:'),'♥','H:'),'♦','D:'),'♣','C:') ~
         E'^[[:space:]]*(?:(?:the|a|an)[[:space:]]+)?(?:(?:[Ss]pades?|[Hh]earts?|[Dd]iamonds?|[Cc]lubs?)[[:space:]]+[AKQJT2-9X]|[SHDC][[:space:]]*:?[[:space:]]*(?:10|[AKQJT2-9X])|(?:10|[AKQJT2-9X])[[:space:]]*[SHDC]|10|[AKQJT2-9Xkqjtx]|(?:(?:10)|[AKQJT2-9Xkqjtx]){2,13})($|[^[:alnum:]])'
     )
     AND replace(replace(replace(replace(
           w.value#>>'{}','♠','S:'),'♥','H:'),'♦','D:'),'♣','C:') !~*
         E'^[[:space:]]*(?:10|[2-9]|two|three|four|five|six|seven|eight|nine)[[:space:]]*(?:(?:(?:[-–—]|to)[[:space:]]*(?:[0-9]{1,2}|two|three|four|five|six|seven|eight|nine)|[+])[[:space:]]*(?:cards?|hearts?|spades?|diamonds?|clubs?|trumps?|losers?|points?|hcp|controls?|winners?|stoppers?|suits?)?|(?:cards?|hearts?|spades?|diamonds?|clubs?|trumps?|losers?|points?|hcp|controls?|winners?|stoppers?|suits?))($|[^[:alnum:]_])'
) OR EXISTS (
  SELECT 1 FROM walk AS w
   WHERE jsonb_typeof(w.value)='string'
     AND EXISTS (
       SELECT 1
         FROM regexp_matches(
           w.value#>>'{}',
           E'(?:^|[^[:alnum:]])[NESW][[:space:]]*:[[:space:]]*((?:(?:10)|[AKQJT2-9]){0,13}\\.(?:(?:10)|[AKQJT2-9]){0,13}\\.(?:(?:10)|[AKQJT2-9]){0,13}\\.(?:(?:10)|[AKQJT2-9]){0,13})|(?:(?:^|[^[:alnum:]_])(?:partner|opponent|north|east|south|west|[lr](?:[[:space:]]*[.][[:space:]]*)?h(?:[[:space:]]*[.][[:space:]]*)?o[.]?))[[:space:]]*(?:[''’]s)?[ _-]*(?:hand|cards)[^;]*?((?:(?:10)|[AKQJT2-9]){0,13}\\.(?:(?:10)|[AKQJT2-9]){0,13}\\.(?:(?:10)|[AKQJT2-9]){0,13}\\.(?:(?:10)|[AKQJT2-9]){0,13})|(?:(?:^|[^[:alnum:]_])(?:рука|карты))[[:space:]]+(?:партн[её]ра|соперника)[^;]*?((?:(?:10)|[AKQJT2-9]){0,13}\\.(?:(?:10)|[AKQJT2-9]){0,13}\\.(?:(?:10)|[AKQJT2-9]){0,13}\\.(?:(?:10)|[AKQJT2-9]){0,13})',
           'gi'
         ) AS matched(parts)
        WHERE bidding.is_complete_bridge_hand(
          COALESCE(matched.parts[1],matched.parts[2],matched.parts[3])
        )
     )
) OR EXISTS (
    SELECT 1 FROM walk AS w
     WHERE jsonb_typeof(w.value)='string'
       AND EXISTS (
         SELECT 1
           FROM regexp_matches(
             replace(replace(replace(replace(
               w.value#>>'{}','♠','S:'),'♥','H:'),'♦','D:'),'♣','C:'),
             E'(?:(?:(?:^|[^[:alnum:]_])(?:partner|opponent|north|east|south|west|[lr](?:[[:space:]]*[.][[:space:]]*)?h(?:[[:space:]]*[.][[:space:]]*)?o[.]?))[[:space:]]*(?:[''’]s)?[ _-]*(?:hand|cards)|(?:(?:^|[^[:alnum:]_])(?:рука|карты))[[:space:]]+(?:партн[её]ра|соперника)|(?:^|[^[:alnum:]])[NESW][[:space:]]*:)([^;]*)',
             'gi'
           ) AS matched(parts)
          WHERE matched.parts[1] ~*
                  E'(^|[^[:alnum:]_])[SHDC][[:space:]]*:'
             OR (
                  matched.parts[1] ~
                    E'(^|[^[:alnum:]])(?:10|[AKQJTX]|[kqjtx]|(?:(?:10)|[AKQJT2-9Xakqjtx]){2,13})($|[^[:alnum:]])'
                  AND matched.parts[1] !~*
                    E'^[[:space:]]*(?:(?:was|is)[[:space:]]+|[:,;=\\-][[:space:]]*)?(?:10|[2-9]|two|three|four|five|six|seven|eight|nine)[[:space:]]*(?:(?:(?:[-–—]|to)[[:space:]]*(?:[0-9]{1,2}|two|three|four|five|six|seven|eight|nine)|[+])[[:space:]]*(?:cards?|hearts?|spades?|diamonds?|clubs?|trumps?|losers?|points?|hcp|controls?|winners?|stoppers?|suits?|карт[[:alnum:]_]*|черв[[:alnum:]_]*|пик[[:alnum:]_]*|буб[[:alnum:]_]*|треф[[:alnum:]_]*|козыр[[:alnum:]_]*|взят[[:alnum:]_]*|очк[[:alnum:]_]*|пункт[[:alnum:]_]*|контрол[[:alnum:]_]*)?|(?:cards?|hearts?|spades?|diamonds?|clubs?|trumps?|losers?|points?|hcp|controls?|winners?|stoppers?|suits?|карт[[:alnum:]_]*|черв[[:alnum:]_]*|пик[[:alnum:]_]*|буб[[:alnum:]_]*|треф[[:alnum:]_]*|козыр[[:alnum:]_]*|взят[[:alnum:]_]*|очк[[:alnum:]_]*|пункт[[:alnum:]_]*|контрол[[:alnum:]_]*))($|[^[:alnum:]_])'
                )
             OR (
                  matched.parts[1] ~*
                    E'^[[:space:]]*(?:(?:was|is)[[:space:]]+|[:,;=\\-][[:space:]]*)?(?:[2-9]|two|three|four|five|six|seven|eight|nine)($|[[:space:],./;])'
                  AND matched.parts[1] !~*
                    E'^[[:space:]]*(?:(?:was|is)[[:space:]]+|[:,;=\\-][[:space:]]*)?(?:10|[2-9]|two|three|four|five|six|seven|eight|nine)[[:space:]]*(?:(?:(?:[-–—]|to)[[:space:]]*(?:[0-9]{1,2}|two|three|four|five|six|seven|eight|nine)|[+])[[:space:]]*(?:cards?|hearts?|spades?|diamonds?|clubs?|trumps?|losers?|points?|hcp|controls?|winners?|stoppers?|suits?|карт[[:alnum:]_]*|черв[[:alnum:]_]*|пик[[:alnum:]_]*|буб[[:alnum:]_]*|треф[[:alnum:]_]*|козыр[[:alnum:]_]*|взят[[:alnum:]_]*|очк[[:alnum:]_]*|пункт[[:alnum:]_]*|контрол[[:alnum:]_]*)?|(?:cards?|hearts?|spades?|diamonds?|clubs?|trumps?|losers?|points?|hcp|controls?|winners?|stoppers?|suits?|карт[[:alnum:]_]*|черв[[:alnum:]_]*|пик[[:alnum:]_]*|буб[[:alnum:]_]*|треф[[:alnum:]_]*|козыр[[:alnum:]_]*|взят[[:alnum:]_]*|очк[[:alnum:]_]*|пункт[[:alnum:]_]*|контрол[[:alnum:]_]*))($|[^[:alnum:]_])'
                )
             OR matched.parts[1] ~*
                  E'(^|[^[:alnum:]])(-|(?:(?:10)|[AKQJT2-9X]){1,13})([[:space:],/.]+(-|(?:(?:10)|[AKQJT2-9X]){1,13})){1,3}($|[^[:alnum:]])'
       )
  ) OR EXISTS (
    SELECT 1 FROM walk AS w
     WHERE jsonb_typeof(w.value)='string'
       AND EXISTS (
         SELECT 1
           FROM regexp_matches(
             replace(replace(replace(replace(
               w.value#>>'{}','♠','S:'),'♥','H:'),'♦','D:'),'♣','C:'),
             E'(?:(?:(?:^|[^[:alnum:]_])(?:partner|opponent|north|east|south|west|[lr](?:[[:space:]]*[.][[:space:]]*)?h(?:[[:space:]]*[.][[:space:]]*)?o[.]?))[[:space:]]*(?:[''’]s)?[ _-]*(?:hand|cards)|(?:(?:^|[^[:alnum:]_])(?:рука|карты))[[:space:]]+(?:партн[её]ра|соперника)|(?:^|[^[:alnum:]])[NESW][[:space:]]*:)[^;]*?S[[:space:]]*:[[:space:]]*(-|(?:(?:10)|[AKQJT2-9]){0,13})[[:space:],/]*H[[:space:]]*:[[:space:]]*(-|(?:(?:10)|[AKQJT2-9]){0,13})[[:space:],/]*D[[:space:]]*:[[:space:]]*(-|(?:(?:10)|[AKQJT2-9]){0,13})[[:space:],/]*C[[:space:]]*:[[:space:]]*(-|(?:(?:10)|[AKQJT2-9]){0,13})',
             'gi'
           ) AS matched(parts)
          WHERE bidding.is_complete_bridge_hand(concat_ws(
            '.',matched.parts[1],matched.parts[2],matched.parts[3],matched.parts[4]
          ))
       )
  ) OR EXISTS (
    SELECT 1 FROM walk AS w
     WHERE jsonb_typeof(w.value)='string'
       AND EXISTS (
         SELECT 1
           FROM regexp_matches(
             w.value#>>'{}',
             E'(?:(?:(?:^|[^[:alnum:]_])(?:partner|opponent|north|east|south|west|[lr](?:[[:space:]]*[.][[:space:]]*)?h(?:[[:space:]]*[.][[:space:]]*)?o[.]?))[[:space:]]*(?:[''’]s)?[ _-]*(?:hand|cards)|(?:(?:^|[^[:alnum:]_])(?:рука|карты))[[:space:]]+(?:партн[её]ра|соперника)|(?:^|[^[:alnum:]])[NESW][[:space:]]*:)[^;]*?(-|(?:(?:10)|[AKQJT2-9]){1,13})[[:space:]/,]+(-|(?:(?:10)|[AKQJT2-9]){1,13})[[:space:]/,]+(-|(?:(?:10)|[AKQJT2-9]){1,13})[[:space:]/,]+(-|(?:(?:10)|[AKQJT2-9]){1,13})',
             'gi'
           ) AS matched(parts)
          WHERE bidding.is_complete_bridge_hand(concat_ws(
            '.',matched.parts[1],matched.parts[2],matched.parts[3],matched.parts[4]
          ))
       )
  ) OR EXISTS (
    SELECT 1 FROM walk AS w
     WHERE jsonb_typeof(w.value)='string'
       AND EXISTS (
         SELECT 1
           FROM regexp_matches(
             w.value#>>'{}',
             E'(?:(?:^|[^[:alnum:]_])(?:partner|opponent|north|east|south|west|[lr](?:[[:space:]]*[.][[:space:]]*)?h(?:[[:space:]]*[.][[:space:]]*)?o[.]?)|(?:^|[^[:alnum:]])[NESW])(?:(?:(?:[[:space:]]+(?:(?:held|holds?|has|had|owns?|possesses?|retains?|carries?)|(?:is|was)[[:space:]]+(?:(?:currently|still|now|already|actually|also|presently|temporarily|usually|often|apparently|probably|clearly|just|not)[[:space:]]+){0,2}holding))|(?:[''’]s(?:[[:space:]]+(?:(?:currently|still|now|already|actually|also|presently|temporarily|usually|often|apparently|probably|clearly|just|not)[[:space:]]+){0,2}holding)?))(?:[[:space:]]+|[[:space:]]*[:,;=\\-][[:space:]]*)|(?:[[:space:]]*[:,;=\\-][[:space:]]*))([^;]*)',
             'gi'
           ) AS matched(parts)
          WHERE (
                  matched.parts[1] ~*
                    E'^[[:space:]]*(?:[:,;=\\-][[:space:]]*)?(?:(?:the|a|an)[[:space:]]+)?(?:(?:ace|king|queen|jack|ten)(?:[[:space:]]+of[[:space:]]+(?:spades?|hearts?|diamonds?|clubs?))?|(?:two|three|four|five|six|seven|eight|nine)(?:[[:space:]]+of[[:space:]]+(?:spades?|hearts?|diamonds?|clubs?))?(?![-–—])|(?:spades?|hearts?|diamonds?|clubs?)[[:space:]]+(?:ace|king|queen|jack|ten|two|three|four|five|six|seven|eight|nine|10|[KQJT2-9X])(?![-–—])|[SHDC][[:space:]]*:?[[:space:]]*(?:10|[KQJT2-9X])|(?:10|[KQJT2-9X])[[:space:]]*[SHDC]|10|[KQJT2-9X]|[kqjtx]|(?:(?:10)|[KQJT2-9Xkqjtx]){2,13})($|[^[:alnum:]])'
                  OR matched.parts[1] ~
                    E'^[[:space:]]*(?:[:,;=\\-][[:space:]]*)?(?:(?:the|a|an)[[:space:]]+)?(?:(?:[Ss]pades?|[Hh]earts?|[Dd]iamonds?|[Cc]lubs?)[[:space:]]+[AKQJT2-9X]|[SHDC][[:space:]]*:?[[:space:]]*(?:10|[AKQJT2-9X])|(?:10|[AKQJT2-9X])[[:space:]]*[SHDC]|10|[AKQJT2-9Xkqjtx]|(?:(?:10)|[AKQJT2-9Xkqjtx]){2,13})($|[^[:alnum:]])'
                )
            AND matched.parts[1] !~*
                  E'^[[:space:]]*(?:[:,;=\\-][[:space:]]*)?(?:10|[2-9]|two|three|four|five|six|seven|eight|nine)[[:space:]]*(?:(?:(?:[-–—]|to)[[:space:]]*(?:[0-9]{1,2}|two|three|four|five|six|seven|eight|nine)|[+])[[:space:]]*(?:cards?|hearts?|spades?|diamonds?|clubs?|trumps?|losers?|points?|hcp|controls?|winners?|stoppers?|suits?|карт[[:alnum:]_]*|черв[[:alnum:]_]*|пик[[:alnum:]_]*|буб[[:alnum:]_]*|треф[[:alnum:]_]*|козыр[[:alnum:]_]*|взят[[:alnum:]_]*|очк[[:alnum:]_]*|пункт[[:alnum:]_]*|контрол[[:alnum:]_]*)?|(?:cards?|hearts?|spades?|diamonds?|clubs?|trumps?|losers?|points?|hcp|controls?|winners?|stoppers?|suits?|карт[[:alnum:]_]*|черв[[:alnum:]_]*|пик[[:alnum:]_]*|буб[[:alnum:]_]*|треф[[:alnum:]_]*|козыр[[:alnum:]_]*|взят[[:alnum:]_]*|очк[[:alnum:]_]*|пункт[[:alnum:]_]*|контрол[[:alnum:]_]*))($|[^[:alnum:]_])'
       )
  ) OR EXISTS (
    SELECT 1 FROM walk AS w
     WHERE jsonb_typeof(w.value)='string'
       AND replace(replace(replace(replace(
             w.value#>>'{}','♠','S:'),'♥','H:'),'♦','D:'),'♣','C:') ~*
           E'(?:(?:^|[^[:alnum:]_])(?:partner|opponent|north|east|south|west|[lr](?:[[:space:]]*[.][[:space:]]*)?h(?:[[:space:]]*[.][[:space:]]*)?o[.]?)|(?:^|[^[:alnum:]])[NESW])[[:space:]]+(?:(?:does[[:space:]]+not|doesn[''’]t)[[:space:]]+(?:have|hold)(?:[[:space:]]+any)?|(?:has|holds?|had)[[:space:]]+(?:(?:no|neither)|none[[:space:]]+of)|lacks?)[[:space:]]+(?:any[[:space:]]+)?(?:the[[:space:]]+)?(?:aces?|kings?|queens?|jacks?|tens?|twos?|threes?|fours?|fives?|sixes?|sevens?|eights?|nines?|spades?|hearts?|diamonds?|clubs?|(?:ace|king|queen|jack|ten|two|three|four|five|six|seven|eight|nine)[[:space:]]+of[[:space:]]+(?:spades?|hearts?|diamonds?|clubs?)|(?:spades?|hearts?|diamonds?|clubs?)[[:space:]]+(?:ace|king|queen|jack|ten|two|three|four|five|six|seven|eight|nine|10|[AKQJT2-9])|[SHDC][[:space:]]*:?[[:space:]]*(?:10|[AKQJT2-9])|(?:10|[AKQJT2-9])[[:space:]]*[SHDC])($|[^[:alnum:]_])'
  ) OR EXISTS (
    SELECT 1 FROM walk AS w
     WHERE jsonb_typeof(w.value)='string'
       AND replace(replace(replace(replace(
             w.value#>>'{}','♠','S:'),'♥','H:'),'♦','D:'),'♣','C:') ~*
           E'(?:(?:^|[^[:alnum:]_])(?:partner|opponent|north|east|south|west|[lr](?:[[:space:]]*[.][[:space:]]*)?h(?:[[:space:]]*[.][[:space:]]*)?o[.]?)|(?:^|[^[:alnum:]])[NESW])[[:space:]]+(?:is|was|remains?)[[:space:]]+void[[:space:]]+(?:(?:in|of)[[:space:]]+)?(?:spades?|hearts?|diamonds?|clubs?|[SHDC][[:space:]]*:?)($|[^[:alnum:]_])'
  ) OR EXISTS (
    SELECT 1 FROM walk AS w
     WHERE jsonb_typeof(w.value)='string'
       AND w.value#>>'{}' ~*
           E'(?:(?:^|[^[:alnum:]_])у[[:space:]]+(?:партн[её]ра|соперника|оппонента|противника)|(?:^|[^[:alnum:]_])(?:партн[её]р|соперник|оппонент|противник)[[:space:]]+(?:имеет|держит))[[:space:]]+(?:(?:есть|был[аио]?|имел[аио]?)[[:space:]]+)?(?:(?:туз|корол[ья]|дам[ауы]?|валет[а]?|десятк[аиу]?|двойк[[:alnum:]_]*|тройк[[:alnum:]_]*|четв[её]рк[[:alnum:]_]*|пят[её]рк[[:alnum:]_]*|шест[её]рк[[:alnum:]_]*|сем[её]рк[[:alnum:]_]*|восьм[её]рк[[:alnum:]_]*|девятк[[:alnum:]_]*|10|[AKQJT2-9])[[:space:]]+(?:пик|черв(?:ей|и|а)?|буб(?:ен|ны|на)?|треф(?:ы|а)?)|(?:пиков[[:alnum:]_]*|червов[[:alnum:]_]*|бубнов[[:alnum:]_]*|трефов[[:alnum:]_]*)[[:space:]]+(?:туз[[:alnum:]_]*|корол[[:alnum:]_]*|дам[[:alnum:]_]*|валет[[:alnum:]_]*|десятк[[:alnum:]_]*|двойк[[:alnum:]_]*|тройк[[:alnum:]_]*|четв[её]рк[[:alnum:]_]*|пят[её]рк[[:alnum:]_]*|шест[её]рк[[:alnum:]_]*|сем[её]рк[[:alnum:]_]*|восьм[её]рк[[:alnum:]_]*|девятк[[:alnum:]_]*))($|[^[:alnum:]_])'
  ) OR EXISTS (
    SELECT 1 FROM walk AS w
     WHERE jsonb_typeof(w.value)='string'
       AND replace(replace(replace(replace(
             w.value#>>'{}','♠','S:'),'♥','H:'),'♦','D:'),'♣','C:') ~*
           E'(?:(?:^|[^[:alnum:]_])у[[:space:]]+(?:партн[её]ра|соперника|оппонента|противника)|(?:^|[^[:alnum:]_])(?:партн[её]р|соперник|оппонент|противник)[[:space:]]+(?:имеет|держит))[[:space:]]+(?:(?:есть|был[аио]?|имел[аио]?)[[:space:]]+)?(?:(?:туз[[:alnum:]_]*|корол[[:alnum:]_]*|дам[[:alnum:]_]*|валет[[:alnum:]_]*|десятк[[:alnum:]_]*|двойк[[:alnum:]_]*|тройк[[:alnum:]_]*|четв[её]рк[[:alnum:]_]*|пят[её]рк[[:alnum:]_]*|шест[её]рк[[:alnum:]_]*|сем[её]рк[[:alnum:]_]*|восьм[её]рк[[:alnum:]_]*|девятк[[:alnum:]_]*|10|[AKQJT2-9X])(?:[[:space:]]+(?:пик|черв(?:ей|и|а)?|буб(?:ен|ны|на)?|треф(?:ы|а)?))?|(?:пиков[[:alnum:]_]*|червов[[:alnum:]_]*|бубнов[[:alnum:]_]*|трефов[[:alnum:]_]*)[[:space:]]+(?:туз[[:alnum:]_]*|корол[[:alnum:]_]*|дам[[:alnum:]_]*|валет[[:alnum:]_]*|десятк[[:alnum:]_]*|двойк[[:alnum:]_]*|тройк[[:alnum:]_]*|четв[её]рк[[:alnum:]_]*|пят[её]рк[[:alnum:]_]*|шест[её]рк[[:alnum:]_]*|сем[её]рк[[:alnum:]_]*|восьм[её]рк[[:alnum:]_]*|девятк[[:alnum:]_]*)|(?:10|[AKQJT2-9X])[[:space:]]*[SHDC][[:space:]]*:?|[SHDC][[:space:]]*:?[[:space:]]*(?:10|[AKQJT2-9X]))($|[^[:alnum:]_])'
  ) OR EXISTS (
    SELECT 1 FROM walk AS w
     WHERE jsonb_typeof(w.value)='string'
       AND replace(replace(replace(replace(
             w.value#>>'{}','♠','S:'),'♥','H:'),'♦','D:'),'♣','C:') ~*
           E'(?:^|[^[:alnum:]_])(?:партн[её]р|соперник|оппонент|противник)[[:space:]]*[:,;=\\-][[:space:]]*(?:(?:туз[[:alnum:]_]*|корол[[:alnum:]_]*|дам[[:alnum:]_]*|валет[[:alnum:]_]*|десятк[[:alnum:]_]*|двойк[[:alnum:]_]*|тройк[[:alnum:]_]*|четв[её]рк[[:alnum:]_]*|пят[её]рк[[:alnum:]_]*|шест[её]рк[[:alnum:]_]*|сем[её]рк[[:alnum:]_]*|восьм[её]рк[[:alnum:]_]*|девятк[[:alnum:]_]*|10|[AKQJT2-9X])(?:[[:space:]]+(?:пик|черв(?:ей|и|а)?|буб(?:ен|ны|на)?|треф(?:ы|а)?))?|(?:пиков[[:alnum:]_]*|червов[[:alnum:]_]*|бубнов[[:alnum:]_]*|трефов[[:alnum:]_]*)[[:space:]]+(?:туз[[:alnum:]_]*|корол[[:alnum:]_]*|дам[[:alnum:]_]*|валет[[:alnum:]_]*|десятк[[:alnum:]_]*|двойк[[:alnum:]_]*|тройк[[:alnum:]_]*|четв[её]рк[[:alnum:]_]*|пят[её]рк[[:alnum:]_]*|шест[её]рк[[:alnum:]_]*|сем[её]рк[[:alnum:]_]*|восьм[её]рк[[:alnum:]_]*|девятк[[:alnum:]_]*)|(?:10|[AKQJT2-9X])[[:space:]]*[SHDC][[:space:]]*:?|[SHDC][[:space:]]*:?[[:space:]]*(?:10|[AKQJT2-9X]))($|[^[:alnum:]_])'
  ) OR EXISTS (
    SELECT 1 FROM walk AS w
     WHERE jsonb_typeof(w.value)='string'
       AND w.value#>>'{}' ~*
           E'(?:(?:^|[^[:alnum:]_])у[[:space:]]+(?:партн[её]ра|соперника|оппонента|противника)[[:space:]]+(?:нет|не[[:space:]]+было)|(?:^|[^[:alnum:]_])(?:партн[её]р|соперник|оппонент|противник)[[:space:]]+(?:без|не[[:space:]]+(?:имеет|держит)))[[:space:]]+(?:пик|черв(?:ей|и|а)?|буб(?:ен|ны|на)?|треф(?:ы|а)?|туз[[:alnum:]_]*|корол[[:alnum:]_]*|дам[[:alnum:]_]*|валет[[:alnum:]_]*|десятк[[:alnum:]_]*|двойк[[:alnum:]_]*|тройк[[:alnum:]_]*|четв[её]рк[[:alnum:]_]*|пят[её]рк[[:alnum:]_]*|шест[её]рк[[:alnum:]_]*|сем[её]рк[[:alnum:]_]*|восьм[её]рк[[:alnum:]_]*|девятк[[:alnum:]_]*|(?:10|[AKQJT2-9X])(?:[[:space:]]*[SHDC][[:space:]]*:?)?)($|[^[:alnum:]_])'
  );
$$;

CREATE OR REPLACE FUNCTION bidding.is_video_canon_semantic_confidence_eligible(
  p_payload jsonb
) RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path=pg_catalog,public,bidding
AS $$
SELECT CASE
  WHEN jsonb_typeof(p_payload->'semantic_confidence')='number'
    THEN (p_payload->>'semantic_confidence') NOT IN ('NaN','Infinity','-Infinity')
     AND (p_payload->>'semantic_confidence')::numeric BETWEEN 0.95 AND 1
  ELSE false
END
$$;

CREATE OR REPLACE FUNCTION bidding.video_canon_semantic_identity_sha256(p_payload jsonb)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
STRICT
AS $$
SELECT encode(public.digest(convert_to(
  encode(public.digest(convert_to('video-canon-semantic-identity-v1','UTF8'),'sha256'),'hex') ||
  encode(public.digest(convert_to(COALESCE(p_payload->>'semantic_scope',''),'UTF8'),'sha256'),'hex') ||
  encode(public.digest(convert_to(COALESCE(p_payload->>'system_profile',''),'UTF8'),'sha256'),'hex') ||
  encode(public.digest(convert_to(COALESCE(p_payload->>'learner_level',''),'UTF8'),'sha256'),'hex') ||
  encode(public.digest(convert_to(COALESCE(p_payload#>>'{normalized_rule,rule_key}',''),'UTF8'),'sha256'),'hex'),
  'UTF8'),'sha256'),'hex')
$$;

CREATE OR REPLACE FUNCTION bidding.video_canon_runtime_scope_key(
  p_semantic_scope text,p_system_profile text,p_learner_level text
) RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
STRICT
SET search_path=pg_catalog,public
AS $$
SELECT 'video-canon:sha256:'||encode(public.digest(convert_to(
  encode(public.digest(convert_to(p_semantic_scope,'UTF8'),'sha256'),'hex') ||
  encode(public.digest(convert_to(p_system_profile,'UTF8'),'sha256'),'hex') ||
  encode(public.digest(convert_to(p_learner_level,'UTF8'),'sha256'),'hex'),
  'UTF8'),'sha256'),'hex')
$$;

CREATE OR REPLACE FUNCTION bidding.get_school_runtime_rule_catalog(
  p_school_id uuid,p_semantic_scope text,p_system_profile text,p_learner_level text
) RETURNS SETOF bidding.active_school_canon_rule_v
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path=pg_catalog,bidding,public
AS $$
SELECT * FROM bidding.active_school_canon_rule_v
 WHERE school_id=p_school_id
   AND scope_key=bidding.video_canon_runtime_scope_key(
     p_semantic_scope,p_system_profile,p_learner_level
   )
 ORDER BY priority DESC,specificity DESC,rule_key
$$;

CREATE OR REPLACE FUNCTION bidding.video_canon_rule_test_state_sha256(p_rule_id uuid)
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
SET TimeZone='UTC'
AS $$
WITH test_rows AS (
  SELECT t.test_key,t.rule_test_id,
         jsonb_build_object(
           'rule_test',to_jsonb(t)-ARRAY['created_at'],
           'latest_run',CASE WHEN latest.rule_test_run_id IS NULL
             THEN 'null'::jsonb
             ELSE to_jsonb(latest)-ARRAY['created_at']
           END
         ) AS row_value
    FROM bidding.rule_test t
    LEFT JOIN LATERAL (
      SELECT tr.*
        FROM bidding.rule_test_run tr
       WHERE tr.rule_test_id=t.rule_test_id
       ORDER BY tr.created_at DESC,tr.rule_test_run_id DESC
       LIMIT 1
    ) latest ON true
   WHERE t.rule_id=p_rule_id
), aggregate_state AS (
  SELECT COALESCE(jsonb_agg(row_value ORDER BY test_key,rule_test_id::text),'[]'::jsonb) AS rows
    FROM test_rows
)
SELECT encode(public.digest(convert_to(rows::text,'UTF8'),'sha256'),'hex')
  FROM aggregate_state
$$;

CREATE OR REPLACE FUNCTION bidding.video_canon_rule_restore_sha256(p_rule_id uuid)
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
AS $$
SELECT encode(public.digest(convert_to(
         (to_jsonb(r)-ARRAY['created_at','updated_at'])::text,'UTF8'),'sha256'),'hex')
  FROM bidding.rule r WHERE r.rule_id=p_rule_id
$$;

CREATE OR REPLACE FUNCTION bidding.current_school_canon_snapshot_sha256(p_school_id uuid)
RETURNS text
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
SET TimeZone='UTC'
AS $$
WITH effective_canon AS (
  SELECT ca.* FROM public.canon_activation ca
  WHERE ca.status='active' AND ca.valid_from<=clock_timestamp()
    AND (ca.valid_to IS NULL OR ca.valid_to>clock_timestamp())
), active_runtime AS (
  SELECT COALESCE(jsonb_agg(
    jsonb_build_object(
      'runtime_activation',to_jsonb(ra),
      'canon_activation',to_jsonb(ca),
      'rule',to_jsonb(r),
      'knowledge_version',to_jsonb(kv)
    ) ORDER BY ra.runtime_activation_id::text
  ),'[]'::jsonb) AS rows
  FROM bidding.runtime_activation ra
  JOIN effective_canon ca ON ca.canon_activation_id=ra.canon_activation_id
  JOIN bidding.rule r ON r.rule_id=ra.rule_id
  JOIN public.knowledge_version kv ON kv.knowledge_version_id=r.knowledge_version_id
  WHERE ra.school_id=p_school_id AND ra.authority_lane='school_canon'
    AND ra.status='active' AND ra.valid_from<=clock_timestamp()
    AND (ra.valid_to IS NULL OR ra.valid_to>clock_timestamp())
), active_canon AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'canon_activation',to_jsonb(ca),
    'knowledge_version',to_jsonb(kv),
    'knowledge_item',to_jsonb(ki)
  ) ORDER BY ca.canon_activation_id::text),'[]'::jsonb) AS rows
  FROM effective_canon ca
  JOIN public.knowledge_version kv ON kv.knowledge_version_id=ca.knowledge_version_id
  JOIN public.knowledge_item ki ON ki.knowledge_item_id=kv.knowledge_item_id
  WHERE ki.school_id=p_school_id
), active_canon_rules AS (
  SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY r.rule_id::text),'[]'::jsonb) AS rows
  FROM effective_canon ca
  JOIN public.knowledge_version kv ON kv.knowledge_version_id=ca.knowledge_version_id
  JOIN public.knowledge_item ki ON ki.knowledge_item_id=kv.knowledge_item_id
  JOIN bidding.rule r ON r.knowledge_version_id=kv.knowledge_version_id
  WHERE ki.school_id=p_school_id
), open_conflicts AS (
  SELECT COALESCE(jsonb_agg(to_jsonb(c) ORDER BY c.rule_conflict_id::text),'[]'::jsonb) AS rows
  FROM bidding.rule_conflict c
  WHERE c.school_id=p_school_id AND c.status='open'
    AND (EXISTS (
      SELECT 1 FROM active_canon_rules ar
       WHERE (ar.rows @> jsonb_build_array(jsonb_build_object('rule_id',c.left_rule_id)))
    ) OR EXISTS (
      SELECT 1 FROM active_canon_rules ar
       WHERE (ar.rows @> jsonb_build_array(jsonb_build_object('rule_id',c.right_rule_id)))
    ))
), active_rule_tests AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'rule_test',to_jsonb(t),
    'latest_run',(
      SELECT to_jsonb(tr) FROM bidding.rule_test_run tr
       WHERE tr.rule_test_id=t.rule_test_id
       ORDER BY tr.created_at DESC,tr.rule_test_run_id DESC LIMIT 1
    )
  ) ORDER BY t.rule_test_id::text),'[]'::jsonb) AS rows
  FROM bidding.rule_test t
  WHERE EXISTS (
    SELECT 1 FROM effective_canon ca
    JOIN public.knowledge_version kv ON kv.knowledge_version_id=ca.knowledge_version_id
    JOIN public.knowledge_item ki ON ki.knowledge_item_id=kv.knowledge_item_id
    JOIN bidding.rule r ON r.knowledge_version_id=kv.knowledge_version_id
     WHERE ki.school_id=p_school_id AND r.rule_id=t.rule_id
  )
), active_rule_sources AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'knowledge_version_source',to_jsonb(kvs),'source',to_jsonb(s)
  ) ORDER BY kvs.knowledge_version_id::text,kvs.source_id::text,kvs.relation_type),'[]'::jsonb) AS rows
  FROM public.knowledge_version_source kvs
  JOIN public.source s ON s.source_id=kvs.source_id
  WHERE EXISTS (
    SELECT 1 FROM effective_canon ca
    JOIN public.knowledge_version kv ON kv.knowledge_version_id=ca.knowledge_version_id
    JOIN public.knowledge_item ki ON ki.knowledge_item_id=kv.knowledge_item_id
     WHERE ki.school_id=p_school_id
       AND ca.knowledge_version_id=kvs.knowledge_version_id
  )
)
SELECT encode(public.digest(convert_to(jsonb_build_object(
  'school_id',p_school_id,'active_runtime',active_runtime.rows,
  'active_canon',active_canon.rows,'active_canon_rules',active_canon_rules.rows,
  'open_conflicts',open_conflicts.rows,
  'active_rule_tests',active_rule_tests.rows,'active_rule_sources',active_rule_sources.rows
)::text,'UTF8'),'sha256'),'hex')
FROM active_runtime,active_canon,active_canon_rules,open_conflicts,active_rule_tests,active_rule_sources;
$$;

CREATE OR REPLACE FUNCTION bidding.validate_video_correction_review_receipt()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_principal bidding.video_canon_verifier_registry%ROWTYPE;
    v_decoded jsonb;
    v_computed text;
BEGIN
    SELECT * INTO v_principal FROM bidding.video_canon_verifier_registry
     WHERE database_role=current_user AND status='active';
    IF NOT FOUND OR NOT ('CORRECTION_REVIEW'=ANY(v_principal.allowed_check_ids))
       OR v_principal.max_assurance_level NOT IN ('I1','I2','I3')
       OR NEW.recorded_by_role<>current_user
       OR NEW.recorded_by_principal<>session_user
       OR NOT EXISTS (
         SELECT 1 FROM pg_catalog.pg_roles login_role
          WHERE login_role.rolname=session_user
            AND login_role.rolcanlogin
            AND pg_has_role(login_role.oid,v_principal.database_role,'MEMBER')
       ) THEN
        RAISE EXCEPTION 'VIDEO_CORRECTION_REVIEW_PRINCIPAL_MISMATCH' USING ERRCODE='42501';
    END IF;
    BEGIN
        v_decoded := NEW.receipt_canonical_json::jsonb;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'VIDEO_CORRECTION_REVIEW_CANONICAL_JSON_INVALID' USING ERRCODE='23514';
    END;
    v_computed := encode(public.digest(convert_to(NEW.receipt_canonical_json,'UTF8'),'sha256'),'hex');
    IF v_decoded<>(NEW.receipt_payload-'receipt_sha256')
       OR v_computed<>NEW.receipt_sha256
       OR NEW.receipt_payload->>'receipt_sha256'<>NEW.receipt_sha256
       OR jsonb_object_length(NEW.receipt_payload)<>9
       OR NOT (NEW.receipt_payload ?& ARRAY[
         'correction_id','kind','reviewer_ref','source_sha256','input_ref',
         'corrected_value_sha256','evidence_refs','status','receipt_sha256'
       ])
       OR NEW.receipt_payload->>'kind' NOT IN (
         'ASR','SPEAKER','CARD','AUCTION','EXTRACTION','PEDAGOGY'
       )
       OR NEW.receipt_payload->>'status'<>'VERIFIED'
       OR NOT ((NEW.receipt_payload->>'source_sha256') ~ '^[0-9a-f]{64}$')
       OR NOT ((NEW.receipt_payload->>'corrected_value_sha256') ~ '^[0-9a-f]{64}$')
       OR jsonb_typeof(NEW.receipt_payload->'evidence_refs')<>'array'
       OR jsonb_array_length(NEW.receipt_payload->'evidence_refs')=0 THEN
        RAISE EXCEPTION 'VIDEO_CORRECTION_REVIEW_RECEIPT_INVALID' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER video_correction_review_receipt_guard
BEFORE INSERT ON bidding.video_correction_review_receipt
FOR EACH ROW EXECUTE FUNCTION bidding.validate_video_correction_review_receipt();
CREATE TRIGGER video_correction_review_receipt_append_only
BEFORE UPDATE OR DELETE ON bidding.video_correction_review_receipt
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();

CREATE OR REPLACE FUNCTION bidding.validate_video_canon_verification_bundle()
RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog,public,bidding AS $$
DECLARE
    v_candidate public.analysis_candidate%ROWTYPE;
    v_decoded jsonb;
    v_candidate_decoded jsonb;
    v_candidate_canonical_json text;
    v_computed text;
    v_valid_from timestamptz;
    v_valid_to timestamptz;
BEGIN
    SELECT * INTO v_candidate FROM public.analysis_candidate
     WHERE analysis_candidate_id=NEW.analysis_candidate_id;
    IF NOT FOUND OR v_candidate.school_id<>NEW.school_id
       OR v_candidate.payload_hash<>NEW.candidate_payload_hash
       OR v_candidate.candidate_type<>'video_school_canon_candidate'
       OR v_candidate.payload->>'authority_class' IS DISTINCT FROM 'TEACHER_VIDEO'
       OR v_candidate.status<>'active'
       OR v_candidate.quality_status<>'AI_VERIFICATION_PENDING'
       OR v_candidate.promotion_status NOT IN ('staging','review_queue') THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_CANDIDATE_MISMATCH' USING ERRCODE='23514';
    END IF;
    BEGIN
        v_decoded := NEW.bundle_canonical_json::jsonb;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_CANONICAL_JSON_INVALID' USING ERRCODE='23514';
    END;
    v_candidate_canonical_json := NEW.bundle_payload->>'candidate_canonical_json';
    BEGIN
        v_candidate_decoded := v_candidate_canonical_json::jsonb;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'VIDEO_CANON_CANDIDATE_CANONICAL_JSON_INVALID' USING ERRCODE='23514';
    END;
    v_computed := encode(public.digest(convert_to(NEW.bundle_canonical_json,'UTF8'),'sha256'),'hex');
    IF v_decoded<>NEW.bundle_payload OR v_computed<>NEW.verification_bundle_sha256
       OR NEW.bundle_payload->>'schema'<>'video-canon-ai-promotion-v1'
       OR NEW.bundle_payload->>'policy_version'<>'school-video-auto-canon-v1'
       OR (SELECT count(*) FROM jsonb_object_keys(NEW.bundle_payload))<>13
       OR NOT (NEW.bundle_payload ?& ARRAY[
         'schema','policy_version','candidate_payload_hash','candidate_payload',
         'candidate_canonical_json',
         'system_profile','learner_level','effective_period','activation_scope',
         'canon_snapshot_sha256','rule_test_state_sha256','checks','rollback'
       ])
       OR NEW.bundle_payload->>'candidate_payload_hash'
            IS DISTINCT FROM NEW.candidate_payload_hash
       OR NEW.bundle_payload->'candidate_payload' IS DISTINCT FROM v_candidate.payload
       OR jsonb_typeof(NEW.bundle_payload->'candidate_canonical_json')<>'string'
       OR v_candidate_decoded IS DISTINCT FROM v_candidate.payload
       OR encode(public.digest(convert_to(
            v_candidate_canonical_json,'UTF8'
          ),'sha256'),'hex') IS DISTINCT FROM v_candidate.payload_hash
       OR NEW.bundle_payload->>'activation_scope'
            IS DISTINCT FROM v_candidate.payload->>'semantic_scope'
       OR NEW.bundle_payload->>'system_profile'
            IS DISTINCT FROM v_candidate.payload->>'system_profile'
       OR NEW.bundle_payload->>'learner_level'
            IS DISTINCT FROM v_candidate.payload->>'learner_level'
       OR NOT ((NEW.bundle_payload->>'canon_snapshot_sha256') ~ '^[0-9a-f]{64}$')
       OR NOT ((NEW.bundle_payload->>'rule_test_state_sha256') ~ '^[0-9a-f]{64}$')
       OR jsonb_typeof(NEW.bundle_payload->'checks')<>'array'
       OR jsonb_array_length(NEW.bundle_payload->'checks')<>16
       OR EXISTS (
         SELECT 1 FROM jsonb_array_elements(NEW.bundle_payload->'checks') AS c(value)
          WHERE c.value->>'result' IS DISTINCT FROM 'PASS'
             OR c.value->>'check_id' NOT IN (
               'SOURCE_AUTHORITY','SOURCE_BINDING','SPEAKER_IDENTITY','TRANSCRIPT_BINDING',
               'SEMANTIC_PARSE','EXPLANATION_COMPLETENESS','BRIDGE_LOGIC',
               'HIDDEN_INFORMATION_FIREWALL','POSITIVE_TESTS','NEGATIVE_TESTS',
               'BOUNDARY_TESTS','INTERFERENCE_TESTS','CANON_REGRESSION','CANON_INTEGRITY',
               'CANON_CONFLICT_SCAN','ROLLBACK_RESTORE'
             )
       )
       OR (SELECT count(DISTINCT c.value->>'check_id')
             FROM jsonb_array_elements(NEW.bundle_payload->'checks') AS c(value))<>16
       OR jsonb_typeof(NEW.bundle_payload->'effective_period')<>'object'
       OR (SELECT count(*) FROM jsonb_object_keys(
            NEW.bundle_payload->'effective_period'
          ))<>2
       OR NOT (NEW.bundle_payload->'effective_period' ?& ARRAY['valid_from','valid_to'])
       OR jsonb_typeof(NEW.bundle_payload#>'{effective_period,valid_from}')<>'string'
       OR NOT ((NEW.bundle_payload#>>'{effective_period,valid_from}') ~
            '(?:[Zz]|[+-][0-9]{2}(?::?[0-9]{2})?)$')
       OR jsonb_typeof(NEW.bundle_payload#>'{effective_period,valid_to}')
            NOT IN ('string','null')
       OR (
         jsonb_typeof(NEW.bundle_payload#>'{effective_period,valid_to}')='string'
         AND NOT ((NEW.bundle_payload#>>'{effective_period,valid_to}') ~
              '(?:[Zz]|[+-][0-9]{2}(?::?[0-9]{2})?)$')
       )
       OR jsonb_typeof(NEW.bundle_payload->'rollback')<>'object'
       OR (SELECT count(*) FROM jsonb_object_keys(
            NEW.bundle_payload->'rollback'
          ))<>5
       OR NOT (NEW.bundle_payload->'rollback' ?& ARRAY[
         'strategy','target_knowledge_version_id','target_canon_activation_id',
         'restore_test_sha256','result'
       ])
       OR jsonb_typeof(NEW.bundle_payload#>'{rollback,strategy}')<>'string'
       OR btrim(NEW.bundle_payload#>>'{rollback,strategy}')=''
       OR jsonb_typeof(NEW.bundle_payload#>'{rollback,target_knowledge_version_id}')
            NOT IN ('string','null')
       OR (
         jsonb_typeof(NEW.bundle_payload#>'{rollback,target_knowledge_version_id}')='string'
         AND NOT ((NEW.bundle_payload#>>'{rollback,target_knowledge_version_id}') ~
           '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
       )
       OR jsonb_typeof(NEW.bundle_payload#>'{rollback,target_canon_activation_id}')
            NOT IN ('string','null')
       OR (
         jsonb_typeof(NEW.bundle_payload#>'{rollback,target_canon_activation_id}')='string'
         AND NOT ((NEW.bundle_payload#>>'{rollback,target_canon_activation_id}') ~
           '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
       )
       OR jsonb_typeof(NEW.bundle_payload#>'{rollback,restore_test_sha256}')<>'string'
       OR NOT ((NEW.bundle_payload#>>'{rollback,restore_test_sha256}') ~ '^[0-9a-f]{64}$')
       OR jsonb_typeof(NEW.bundle_payload#>'{rollback,result}')<>'string'
       OR NEW.bundle_payload#>>'{rollback,result}'<>'PASS' THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_CONTENT_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF (
         NEW.bundle_payload#>>'{rollback,target_knowledge_version_id}' IS NULL
       ) <> (
         NEW.bundle_payload#>>'{rollback,target_canon_activation_id}' IS NULL
       ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_ROLLBACK_TARGET_PAIR_INVALID'
          USING ERRCODE='23514';
    END IF;
    BEGIN
        v_valid_from := (NEW.bundle_payload#>>'{effective_period,valid_from}')::timestamptz;
        v_valid_to := CASE
          WHEN jsonb_typeof(NEW.bundle_payload#>'{effective_period,valid_to}')='null' THEN NULL
          ELSE (NEW.bundle_payload#>>'{effective_period,valid_to}')::timestamptz
        END;
    EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_EFFECTIVE_PERIOD_INVALID' USING ERRCODE='23514';
    END;
    IF v_valid_from>statement_timestamp()
       OR (v_valid_to IS NOT NULL AND v_valid_to<=v_valid_from)
       OR (v_valid_to IS NOT NULL AND v_valid_to<=statement_timestamp()) THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_EFFECTIVE_PERIOD_INVALID' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER video_canon_verification_bundle_guard
BEFORE INSERT ON bidding.video_canon_ai_verification_bundle
FOR EACH ROW EXECUTE FUNCTION bidding.validate_video_canon_verification_bundle();

CREATE VIEW bidding.video_canon_bound_candidate
WITH (security_barrier=true) AS
SELECT c.*,b.video_canon_ai_verification_bundle_id,
       b.candidate_payload_hash AS bound_candidate_payload_hash,
       b.verification_bundle_sha256,
       (
         SELECT jsonb_agg(check_row.value ORDER BY check_row.ordinality)
           FROM jsonb_array_elements(b.bundle_payload->'checks')
                WITH ORDINALITY AS check_row(value,ordinality)
          WHERE check_row.value->>'execution_principal'=session_user::text
       ) AS assigned_checks,
       EXISTS (
         SELECT 1 FROM bidding.video_canon_ai_promotion_receipt p
          WHERE p.analysis_candidate_id=c.analysis_candidate_id
            AND p.candidate_payload_hash=b.candidate_payload_hash
            AND p.verification_bundle_sha256=b.verification_bundle_sha256
       ) AS verification_set_closed
  FROM public.analysis_candidate c
  JOIN bidding.video_canon_ai_verification_bundle b
    ON b.analysis_candidate_id=c.analysis_candidate_id
   AND b.school_id=c.school_id AND b.candidate_payload_hash=c.payload_hash
 WHERE c.candidate_type='video_school_canon_candidate'
   AND EXISTS (
     SELECT 1 FROM jsonb_array_elements(b.bundle_payload->'checks') AS check_row(value)
      WHERE check_row.value->>'execution_principal'=session_user::text
   );

CREATE OR REPLACE FUNCTION bidding.validate_video_canon_verification()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_bound record;
    v_principal bidding.video_canon_verifier_registry%ROWTYPE;
BEGIN
    SELECT * INTO v_bound FROM bidding.video_canon_bound_candidate
     WHERE analysis_candidate_id=NEW.analysis_candidate_id
       AND video_canon_ai_verification_bundle_id=NEW.video_canon_ai_verification_bundle_id;
    IF NOT FOUND OR v_bound.school_id<>NEW.school_id
       OR v_bound.payload_hash<>NEW.candidate_payload_hash
       OR v_bound.bound_candidate_payload_hash<>NEW.candidate_payload_hash
       OR v_bound.verification_bundle_sha256<>NEW.verification_bundle_sha256 THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_BINDING_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF v_bound.verification_set_closed THEN
        RAISE EXCEPTION 'VIDEO_CANON_PROMOTED_VERIFICATION_SET_CLOSED' USING ERRCODE='55000';
    END IF;
    SELECT * INTO v_principal FROM bidding.video_canon_verifier_registry
     WHERE database_role=current_user AND status='active';
    IF NOT FOUND OR NEW.verifier_family<>v_principal.verifier_family
       OR NEW.execution_principal<>session_user
       OR NOT (NEW.check_id=ANY(v_principal.allowed_check_ids))
       OR (v_principal.max_assurance_level='I1' AND NEW.assurance_level NOT IN ('I0','I1'))
       OR (v_principal.max_assurance_level='I2' AND NEW.assurance_level NOT IN ('I0','I1','I2'))
       OR v_principal.max_assurance_level NOT IN ('I1','I2','I3') THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFIER_PRINCIPAL_MISMATCH' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (
         SELECT 1 FROM jsonb_array_elements(v_bound.assigned_checks) AS c(value)
          WHERE c.value->>'check_id'=NEW.check_id
            AND c.value->>'result'=NEW.result
            AND c.value->>'verifier_family'=NEW.verifier_family
            AND c.value->>'verifier_version'=NEW.verifier_version
            AND c.value->>'execution_principal'=NEW.execution_principal
            AND c.value->>'assurance_level'=NEW.assurance_level
            AND c.value->>'evidence_sha256'=NEW.evidence_sha256
            AND (c.value->>'canon_snapshot_sha256') IS NOT DISTINCT FROM NEW.canon_snapshot_sha256
       ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_BUNDLE_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER video_canon_verification_guard
BEFORE INSERT ON bidding.video_canon_ai_verification
FOR EACH ROW EXECUTE FUNCTION bidding.validate_video_canon_verification();

CREATE OR REPLACE FUNCTION bidding.guard_bound_video_canon_candidate()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM bidding.video_canon_ai_verification_bundle
         WHERE analysis_candidate_id=OLD.analysis_candidate_id
    ) THEN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'VIDEO_CANON_BOUND_CANDIDATE_DELETE_FORBIDDEN' USING ERRCODE='55000';
        END IF;
        IF NEW.school_id IS DISTINCT FROM OLD.school_id
           OR NEW.analysis_run_id IS DISTINCT FROM OLD.analysis_run_id
           OR NEW.source_id IS DISTINCT FROM OLD.source_id
           OR NEW.candidate_type IS DISTINCT FROM OLD.candidate_type
           OR NEW.stable_key IS DISTINCT FROM OLD.stable_key
           OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
           OR NEW.payload IS DISTINCT FROM OLD.payload
           OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
           OR NEW.evidence_refs IS DISTINCT FROM OLD.evidence_refs
           OR NEW.method_version IS DISTINCT FROM OLD.method_version
           OR NEW.supersedes_candidate_id IS DISTINCT FROM OLD.supersedes_candidate_id
           OR NEW.status IS DISTINCT FROM OLD.status
           OR (
             (NEW.promotion_status IS DISTINCT FROM OLD.promotion_status
              OR NEW.quality_status IS DISTINCT FROM OLD.quality_status)
             AND NOT (
               OLD.status='active'
               AND OLD.quality_status='AI_VERIFICATION_PENDING'
               AND OLD.promotion_status IN ('staging','review_queue')
               AND NEW.status='active'
               AND NEW.quality_status='AI_VERIFIED'
               AND NEW.promotion_status='promoted'
               AND EXISTS (
                 SELECT 1 FROM bidding.video_canon_ai_promotion_receipt p
                  WHERE p.analysis_candidate_id=OLD.analysis_candidate_id
                    AND p.candidate_payload_hash=OLD.payload_hash
               )
             )
           ) THEN
            RAISE EXCEPTION 'VIDEO_CANON_BOUND_CANDIDATE_MUTATION_FORBIDDEN' USING ERRCODE='55000';
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER bound_video_canon_candidate_guard
BEFORE UPDATE OR DELETE ON public.analysis_candidate
FOR EACH ROW EXECUTE FUNCTION bidding.guard_bound_video_canon_candidate();

CREATE OR REPLACE FUNCTION bidding.guard_promoted_video_canon_source_binding()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
AS $$
DECLARE v_old_version_id uuid; v_new_version_id uuid;
BEGIN
    IF TG_OP IN ('UPDATE','DELETE') THEN
        v_old_version_id := OLD.knowledge_version_id;
    END IF;
    IF TG_OP IN ('INSERT','UPDATE') THEN
        v_new_version_id := NEW.knowledge_version_id;
    END IF;
    IF EXISTS (
      SELECT 1
        FROM bidding.video_canon_ai_promotion_receipt p
        JOIN public.canon_activation ca
          ON ca.canon_activation_id=p.canon_activation_id
          OR ca.canon_activation_id=p.superseded_canon_activation_id
       WHERE ca.knowledge_version_id=v_old_version_id
          OR ca.knowledge_version_id=v_new_version_id
    ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_PROMOTED_SOURCE_BINDING_IMMUTABLE' USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER promoted_video_canon_source_binding_guard
BEFORE INSERT OR UPDATE OR DELETE ON public.knowledge_version_source
FOR EACH ROW EXECUTE FUNCTION bidding.guard_promoted_video_canon_source_binding();

CREATE OR REPLACE FUNCTION bidding.guard_promoted_video_canon_source_identity()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
AS $$
BEGIN
    IF (to_jsonb(NEW)-ARRAY['status','updated_at']) IS DISTINCT FROM
         (to_jsonb(OLD)-ARRAY['status','updated_at'])
       AND EXISTS (
         SELECT 1
           FROM public.knowledge_version_source kvs
           JOIN public.canon_activation ca
             ON ca.knowledge_version_id=kvs.knowledge_version_id
           JOIN bidding.video_canon_ai_promotion_receipt p
             ON p.canon_activation_id=ca.canon_activation_id
             OR p.superseded_canon_activation_id=ca.canon_activation_id
          WHERE kvs.source_id=OLD.source_id
       ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_PROMOTED_SOURCE_IDENTITY_IMMUTABLE' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER promoted_video_canon_source_identity_guard
BEFORE UPDATE ON public.source
FOR EACH ROW EXECUTE FUNCTION bidding.guard_promoted_video_canon_source_identity();

CREATE OR REPLACE FUNCTION bidding.guard_promoted_video_canon_provider_identity()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
AS $$
DECLARE v_old_source_id uuid; v_new_source_id uuid;
BEGIN
    IF TG_OP IN ('UPDATE','DELETE') THEN
        v_old_source_id := OLD.source_id;
    END IF;
    IF TG_OP='UPDATE' THEN
        v_new_source_id := NEW.source_id;
    END IF;
    IF EXISTS (
         SELECT 1
           FROM public.knowledge_version_source kvs
           JOIN public.canon_activation ca
             ON ca.knowledge_version_id=kvs.knowledge_version_id
           JOIN bidding.video_canon_ai_promotion_receipt p
             ON p.canon_activation_id=ca.canon_activation_id
             OR p.superseded_canon_activation_id=ca.canon_activation_id
          WHERE kvs.source_id=v_old_source_id OR kvs.source_id=v_new_source_id
       ) AND (
         TG_OP='DELETE' OR
         (to_jsonb(NEW)-ARRAY['display_name','last_seen_at','attributes'])
           IS DISTINCT FROM
         (to_jsonb(OLD)-ARRAY['display_name','last_seen_at','attributes']) OR
         (COALESCE(NEW.attributes,'{}'::jsonb)-'job_id') IS DISTINCT FROM
         (COALESCE(OLD.attributes,'{}'::jsonb)-'job_id')
       ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_PROMOTED_PROVIDER_IDENTITY_IMMUTABLE'
          USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER promoted_video_canon_provider_identity_guard
BEFORE UPDATE OR DELETE ON public.source_identity
FOR EACH ROW EXECUTE FUNCTION bidding.guard_promoted_video_canon_provider_identity();

CREATE OR REPLACE FUNCTION bidding.guard_promoted_video_canon_knowledge_version()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
AS $$
DECLARE v_version_id uuid;
BEGIN
    v_version_id := OLD.knowledge_version_id;
    IF EXISTS (
      SELECT 1
        FROM public.canon_activation ca
        JOIN bidding.video_canon_ai_promotion_receipt p
          ON p.canon_activation_id=ca.canon_activation_id
          OR p.superseded_canon_activation_id=ca.canon_activation_id
       WHERE ca.knowledge_version_id=v_version_id
    ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_PROMOTED_KNOWLEDGE_VERSION_IMMUTABLE'
          USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER promoted_video_canon_knowledge_version_guard
BEFORE UPDATE OR DELETE ON public.knowledge_version
FOR EACH ROW EXECUTE FUNCTION bidding.guard_promoted_video_canon_knowledge_version();

CREATE OR REPLACE FUNCTION bidding.guard_promoted_video_canon_knowledge_item()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
AS $$
DECLARE v_item_id uuid;
BEGIN
    v_item_id := OLD.knowledge_item_id;
    IF EXISTS (
      SELECT 1
        FROM public.knowledge_version kv
        JOIN public.canon_activation ca
          ON ca.knowledge_version_id=kv.knowledge_version_id
        JOIN bidding.video_canon_ai_promotion_receipt p
          ON p.canon_activation_id=ca.canon_activation_id
          OR p.superseded_canon_activation_id=ca.canon_activation_id
       WHERE kv.knowledge_item_id=v_item_id
    ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_PROMOTED_KNOWLEDGE_ITEM_IMMUTABLE'
          USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER promoted_video_canon_knowledge_item_guard
BEFORE UPDATE OR DELETE ON public.knowledge_item
FOR EACH ROW EXECUTE FUNCTION bidding.guard_promoted_video_canon_knowledge_item();

CREATE OR REPLACE FUNCTION bidding.guard_promoted_video_canon_rule()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
AS $$
DECLARE v_old_version_id uuid; v_new_version_id uuid;
BEGIN
    IF TG_OP IN ('UPDATE','DELETE') THEN
        v_old_version_id := OLD.knowledge_version_id;
    END IF;
    IF TG_OP IN ('INSERT','UPDATE') THEN
        v_new_version_id := NEW.knowledge_version_id;
    END IF;
    IF EXISTS (
      SELECT 1
        FROM public.canon_activation ca
        JOIN bidding.video_canon_ai_promotion_receipt p
          ON p.canon_activation_id=ca.canon_activation_id
          OR p.superseded_canon_activation_id=ca.canon_activation_id
       WHERE ca.knowledge_version_id=v_old_version_id
          OR ca.knowledge_version_id=v_new_version_id
    ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_PROMOTED_RULE_IMMUTABLE' USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER promoted_video_canon_rule_guard
BEFORE INSERT OR UPDATE OR DELETE ON bidding.rule
FOR EACH ROW EXECUTE FUNCTION bidding.guard_promoted_video_canon_rule();

CREATE OR REPLACE FUNCTION bidding.guard_promoted_video_canon_rule_test()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
AS $$
DECLARE v_old_rule_id uuid; v_new_rule_id uuid;
BEGIN
    IF TG_OP IN ('UPDATE','DELETE') THEN
        v_old_rule_id := OLD.rule_id;
    END IF;
    IF TG_OP IN ('INSERT','UPDATE') THEN
        v_new_rule_id := NEW.rule_id;
    END IF;
    IF EXISTS (
      SELECT 1
        FROM bidding.rule r
        JOIN public.canon_activation ca
          ON ca.knowledge_version_id=r.knowledge_version_id
        JOIN bidding.video_canon_ai_promotion_receipt p
          ON p.canon_activation_id=ca.canon_activation_id
          OR p.superseded_canon_activation_id=ca.canon_activation_id
       WHERE r.rule_id=v_old_rule_id OR r.rule_id=v_new_rule_id
    ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_PROMOTED_RULE_TEST_IMMUTABLE' USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER promoted_video_canon_rule_test_guard
BEFORE INSERT OR UPDATE OR DELETE ON bidding.rule_test
FOR EACH ROW EXECUTE FUNCTION bidding.guard_promoted_video_canon_rule_test();

CREATE OR REPLACE FUNCTION bidding.guard_superseded_video_canon_rule_test_run()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
AS $$
DECLARE v_old_test_id uuid; v_new_test_id uuid;
BEGIN
    IF TG_OP IN ('UPDATE','DELETE') THEN
        v_old_test_id := OLD.rule_test_id;
    END IF;
    IF TG_OP IN ('INSERT','UPDATE') THEN
        v_new_test_id := NEW.rule_test_id;
    END IF;
    IF EXISTS (
      SELECT 1
        FROM bidding.rule_test rt
        JOIN bidding.rule r ON r.rule_id=rt.rule_id
        JOIN public.canon_activation ca
          ON ca.knowledge_version_id=r.knowledge_version_id
        JOIN bidding.video_canon_ai_promotion_receipt p
          ON p.superseded_canon_activation_id=ca.canon_activation_id
       WHERE ca.status='superseded'
         AND (rt.rule_test_id=v_old_test_id OR rt.rule_test_id=v_new_test_id)
    ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_SUPERSEDED_RULE_TEST_RUN_IMMUTABLE'
          USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER superseded_video_canon_rule_test_run_guard
BEFORE INSERT OR UPDATE OR DELETE ON bidding.rule_test_run
FOR EACH ROW EXECUTE FUNCTION bidding.guard_superseded_video_canon_rule_test_run();

CREATE OR REPLACE FUNCTION bidding.guard_video_canon_source_policy_lifecycle()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_retired_at timestamptz;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'VIDEO_CANON_SOURCE_POLICY_DELETE_FORBIDDEN' USING ERRCODE='55000';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.source s
       WHERE s.source_id=NEW.source_id AND s.school_id=NEW.school_id
    ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_SOURCE_POLICY_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        RETURN NEW;
    END IF;
    -- A BEFORE UPDATE row trigger runs after the target row is locked. Capture
    -- the authority boundary here so lock wait time cannot backdate history.
    v_retired_at := clock_timestamp();
    IF OLD.status<>'active' OR NEW.status NOT IN ('revoked','superseded')
       OR (to_jsonb(NEW)-ARRAY['status','valid_to','retired_at'])
          <>(to_jsonb(OLD)-ARRAY['status','valid_to','retired_at']) THEN
        RAISE EXCEPTION 'VIDEO_CANON_SOURCE_POLICY_MUTATION_FORBIDDEN' USING ERRCODE='55000';
    END IF;
    NEW.retired_at := v_retired_at;
    IF OLD.valid_from<=v_retired_at THEN
        NEW.valid_to := v_retired_at;
    ELSE
        NEW.valid_to := OLD.valid_to;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER video_canon_source_policy_lifecycle_guard
BEFORE INSERT OR UPDATE OR DELETE ON bidding.video_canon_source_policy
FOR EACH ROW EXECUTE FUNCTION bidding.guard_video_canon_source_policy_lifecycle();
CREATE TRIGGER video_canon_verification_bundle_append_only
BEFORE UPDATE OR DELETE ON bidding.video_canon_ai_verification_bundle
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();
CREATE OR REPLACE FUNCTION bidding.guard_video_canon_verifier_registry_lifecycle()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' OR OLD.status<>'active' OR NEW.status<>'revoked'
       OR (to_jsonb(NEW)-'status')<>(to_jsonb(OLD)-'status') THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFIER_REGISTRY_MUTATION_FORBIDDEN' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER video_canon_verifier_registry_lifecycle_guard
BEFORE UPDATE OR DELETE ON bidding.video_canon_verifier_registry
FOR EACH ROW EXECUTE FUNCTION bidding.guard_video_canon_verifier_registry_lifecycle();
CREATE TRIGGER video_canon_verification_append_only
BEFORE UPDATE OR DELETE ON bidding.video_canon_ai_verification
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();
CREATE TRIGGER video_canon_promotion_receipt_append_only
BEFORE UPDATE OR DELETE ON bidding.video_canon_ai_promotion_receipt
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();

CREATE OR REPLACE FUNCTION bidding.video_canon_drive_source_id(p_video_file_id text)
RETURNS uuid
LANGUAGE plpgsql
IMMUTABLE
STRICT
PARALLEL SAFE
SET search_path=pg_catalog,public
AS $$
DECLARE v_hash bytea;
BEGIN
    IF btrim(p_video_file_id)='' THEN RETURN NULL; END IF;
    v_hash := public.digest(
      uuid_send('6ba7b811-9dad-11d1-80b4-00c04fd430c8'::uuid) ||
      convert_to('bridge-school:source:google-drive|'||p_video_file_id,'UTF8'),
      'sha1'
    );
    v_hash := set_byte(v_hash,6,(get_byte(v_hash,6)&15)|80);
    v_hash := set_byte(v_hash,8,(get_byte(v_hash,8)&63)|128);
    RETURN encode(substring(v_hash FROM 1 FOR 16),'hex')::uuid;
END $$;

CREATE OR REPLACE FUNCTION bidding.activate_ai_verified_video_canon(
    p_analysis_candidate_id uuid,
    p_rule_id uuid,
    p_verification_bundle_sha256 text
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
SET TimeZone='UTC'
AS $$
DECLARE
    v_candidate public.analysis_candidate%ROWTYPE;
    v_rule bidding.rule%ROWTYPE;
    v_version public.knowledge_version%ROWTYPE;
    v_policy bidding.video_canon_source_policy%ROWTYPE;
    v_bundle bidding.video_canon_ai_verification_bundle%ROWTYPE;
    v_canon_activation uuid;
    v_runtime_activation uuid;
    v_existing bidding.video_canon_ai_promotion_receipt%ROWTYPE;
    v_prior_canon public.canon_activation%ROWTYPE;
    v_prior_version public.knowledge_version%ROWTYPE;
    v_prior_item public.knowledge_item%ROWTYPE;
    v_prior_runtime_ids uuid[] := '{}'::uuid[];
    v_prior_runtime_state jsonb := '[]'::jsonb;
    v_prior_rule_state jsonb := '[]'::jsonb;
    v_prior_rule_test_state jsonb := '[]'::jsonb;
    v_prior_source_state jsonb := '[]'::jsonb;
    v_semantic_family text;
    v_bridge_family text;
    v_semantic_principal text;
    v_bridge_principal text;
    v_firewall_principal text;
    v_semantic_scope text;
    v_scope_key text;
    v_policy_version text;
    v_valid_from timestamptz;
    v_valid_to timestamptz;
    v_rule_content jsonb;
    v_rule_content_sha256 text;
    v_expected_rule_content_sha256 text;
    v_version_content_sha256 text;
    v_prior_version_content_sha256 text;
    v_prior_item_content_sha256 text;
    v_rule_test_state_sha256 text;
    v_expected_version_provenance jsonb;
    v_canon_snapshot_sha256 text;
    v_now timestamptz;
BEGIN
    IF p_verification_bundle_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'VIDEO_CANON_PROMOTION_ARGUMENT_INVALID' USING ERRCODE='23514';
    END IF;

    SELECT * INTO v_candidate FROM public.analysis_candidate
     WHERE analysis_candidate_id=p_analysis_candidate_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'VIDEO_CANON_CANDIDATE_NOT_FOUND' USING ERRCODE='23514'; END IF;

    SELECT * INTO v_bundle FROM bidding.video_canon_ai_verification_bundle
     WHERE analysis_candidate_id=p_analysis_candidate_id
       AND candidate_payload_hash=v_candidate.payload_hash
       AND verification_bundle_sha256=p_verification_bundle_sha256;
    IF NOT FOUND OR v_bundle.bundle_payload->'candidate_payload'
          IS DISTINCT FROM v_candidate.payload THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_BUNDLE_NOT_FOUND' USING ERRCODE='23514';
    END IF;
    v_semantic_scope := v_bundle.bundle_payload->>'activation_scope';
    v_scope_key := bidding.video_canon_runtime_scope_key(
      v_semantic_scope,
      v_bundle.bundle_payload->>'system_profile',
      v_bundle.bundle_payload->>'learner_level'
    );
    v_policy_version := v_bundle.bundle_payload->>'policy_version';
    BEGIN
        v_valid_from := (v_bundle.bundle_payload#>>'{effective_period,valid_from}')::timestamptz;
        v_valid_to := NULLIF(v_bundle.bundle_payload#>>'{effective_period,valid_to}','')::timestamptz;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'VIDEO_CANON_EFFECTIVE_PERIOD_INVALID' USING ERRCODE='23514';
    END;
    IF v_policy_version<>'school-video-auto-canon-v1'
       OR btrim(COALESCE(v_semantic_scope,''))=''
       OR btrim(COALESCE(v_scope_key,''))=''
       OR v_valid_from IS NULL OR v_valid_from>statement_timestamp()
       OR (v_valid_to IS NOT NULL AND v_valid_to<=v_valid_from) THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_ARGUMENT_INVALID' USING ERRCODE='23514';
    END IF;
    IF v_valid_to IS NOT NULL AND v_valid_to<=statement_timestamp() THEN
        RAISE EXCEPTION 'VIDEO_CANON_EFFECTIVE_PERIOD_EXPIRED' USING ERRCODE='23514';
    END IF;

    SELECT * INTO v_existing
      FROM bidding.video_canon_ai_promotion_receipt
     WHERE analysis_candidate_id=p_analysis_candidate_id;
    IF FOUND THEN
        IF v_existing.candidate_payload_hash<>v_candidate.payload_hash
           OR v_existing.verification_bundle_sha256<>p_verification_bundle_sha256
           OR v_existing.policy_version<>v_policy_version
           OR v_existing.semantic_scope<>v_semantic_scope
           OR v_existing.scope_key<>v_scope_key
           OR v_existing.rule_id<>p_rule_id THEN
            RAISE EXCEPTION 'VIDEO_CANON_IDEMPOTENCY_MISMATCH' USING ERRCODE='23514';
        END IF;
        -- Serialize retained-receipt replay with restoration and reject a
        -- receipt whose authoritative activation is no longer live.
        PERFORM pg_advisory_xact_lock(hashtextextended(v_candidate.school_id::text,0));
        v_now := clock_timestamp();
        IF EXISTS (
             SELECT 1 FROM bidding.video_canon_ai_restore_receipt rr
              WHERE rr.video_canon_ai_promotion_receipt_id=
                    v_existing.video_canon_ai_promotion_receipt_id
           ) OR NOT EXISTS (
             SELECT 1
               FROM public.canon_activation ca
               JOIN bidding.runtime_activation ra
                 ON ra.runtime_activation_id=v_existing.runtime_activation_id
                AND ra.canon_activation_id=ca.canon_activation_id
              WHERE ca.canon_activation_id=v_existing.canon_activation_id
                AND ra.rule_id=v_existing.rule_id
                AND ra.school_id=v_existing.school_id
                AND ca.status='active' AND ra.status='active'
                AND ca.valid_from<=v_now AND (ca.valid_to IS NULL OR ca.valid_to>v_now)
                AND ra.valid_from<=v_now AND (ra.valid_to IS NULL OR ra.valid_to>v_now)
           ) THEN
            RAISE EXCEPTION 'VIDEO_CANON_IDEMPOTENT_RECEIPT_STALE' USING ERRCODE='55000';
        END IF;
        RETURN v_existing.video_canon_ai_promotion_receipt_id;
    END IF;

    -- Serialize all Video-to-Canon writes for this school and freeze the
    -- underlying activation/conflict tables while the state digest is checked.
    PERFORM pg_advisory_xact_lock(hashtextextended(v_candidate.school_id::text,0));
    LOCK TABLE public.analysis_candidate,public.canon_activation,
      public.knowledge_item,public.knowledge_version,
      public.source,public.source_identity,public.knowledge_version_source,
      bidding.runtime_activation,bidding.rule,bidding.rule_test,bidding.rule_test_run,
      bidding.rule_conflict,bidding.video_canon_verifier_registry,
      bidding.video_canon_ai_verification_bundle,bidding.video_canon_ai_verification,
      bidding.video_canon_ai_promotion_receipt IN SHARE ROW EXCLUSIVE MODE;
    v_canon_snapshot_sha256 := bidding.current_school_canon_snapshot_sha256(v_candidate.school_id);
    IF v_bundle.bundle_payload->>'canon_snapshot_sha256'<>v_canon_snapshot_sha256 THEN
        RAISE EXCEPTION 'VIDEO_CANON_STATE_CHECKS_STALE' USING ERRCODE='23514';
    END IF;

    IF v_candidate.candidate_type<>'video_school_canon_candidate'
       OR v_candidate.status<>'active'
       OR v_candidate.quality_status<>'AI_VERIFICATION_PENDING'
       OR v_candidate.promotion_status NOT IN ('staging','review_queue')
       OR v_candidate.payload->>'schema' IS DISTINCT FROM 'video-canon-evidence-v2'
       OR v_candidate.payload->>'review_eligibility' IS DISTINCT FROM 'AI_VERIFICATION_PENDING'
       OR v_candidate.payload->>'source_class' IS DISTINCT FROM 'SCHOOL_PRIMARY_EVIDENCE'
       OR v_candidate.payload->>'authority_class' IS DISTINCT FROM 'TEACHER_VIDEO'
       OR (v_bundle.bundle_payload->>'candidate_canonical_json')::jsonb
            IS DISTINCT FROM v_candidate.payload
       OR encode(public.digest(convert_to(
            v_bundle.bundle_payload->>'candidate_canonical_json','UTF8'
          ),'sha256'),'hex') IS DISTINCT FROM v_candidate.payload_hash
       OR v_candidate.payload#>>'{source_authorization,policy_version}'
            IS DISTINCT FROM v_policy_version
       OR v_candidate.payload->>'semantic_scope' IS DISTINCT FROM v_semantic_scope
       OR v_candidate.payload->>'system_profile'
            IS DISTINCT FROM v_bundle.bundle_payload->>'system_profile'
       OR v_candidate.payload->>'learner_level'
            IS DISTINCT FROM v_bundle.bundle_payload->>'learner_level'
       OR v_candidate.payload->>'semantic_identity_sha256'
            IS DISTINCT FROM bidding.video_canon_semantic_identity_sha256(v_candidate.payload)
       OR jsonb_typeof(v_candidate.payload->'ambiguities') IS DISTINCT FROM 'array'
       OR jsonb_typeof(v_candidate.payload->'contradictions') IS DISTINCT FROM 'array'
       OR jsonb_array_length(v_candidate.payload->'ambiguities')<>0
       OR jsonb_array_length(v_candidate.payload->'contradictions')<>0
       OR NOT bidding.is_video_canon_semantic_confidence_eligible(v_candidate.payload)
       OR bidding.contains_forbidden_hidden_key(v_candidate.payload)
       OR bidding.contains_forbidden_hidden_value(v_candidate.payload) THEN
        RAISE EXCEPTION 'VIDEO_CANON_CANDIDATE_NOT_ELIGIBLE' USING ERRCODE='23514';
    END IF;

    SELECT * INTO v_rule FROM bidding.rule WHERE rule_id=p_rule_id FOR UPDATE;
    IF NOT FOUND OR v_rule.school_id<>v_candidate.school_id
       OR v_rule.lifecycle_status<>'validated'
       OR v_rule.compiled_payload->>'video_candidate_payload_hash'<>v_candidate.payload_hash THEN
        RAISE EXCEPTION 'VIDEO_CANON_RULE_BINDING_INVALID' USING ERRCODE='23514';
    END IF;
    v_rule_content := jsonb_build_object(
      'rule_key',v_rule.rule_key,'rule_kind',v_rule.rule_kind,
      'auction_pattern',v_rule.auction_pattern,'hand_constraints',v_rule.hand_constraints,
      'public_context_constraints',v_rule.public_context_constraints,'action',v_rule.action,
      'meaning',v_rule.meaning,'public_inference',v_rule.public_inference,
      'alert_semantics',v_rule.alert_semantics,'forcing_semantics',v_rule.forcing_semantics,
      'priority',v_rule.priority,'specificity',v_rule.specificity,
      'condition_schema_version',v_rule.condition_schema_version,
      'compiled_payload',v_rule.compiled_payload-ARRAY['video_candidate_payload_hash','video_rule_content_sha256'],
      'method_version',v_rule.method_version
    );
    v_rule_content_sha256 := encode(public.digest(convert_to(
      jsonb_build_object('normalized_rule',v_rule_content,'explanation',v_rule.explanation)::text,
      'UTF8'),'sha256'),'hex');
    v_expected_rule_content_sha256 := encode(public.digest(convert_to(
      jsonb_build_object('normalized_rule',v_candidate.payload->'normalized_rule',
        'explanation',v_candidate.payload->'explanation')::text,'UTF8'),'sha256'),'hex');
    IF v_rule_content<>v_candidate.payload->'normalized_rule'
       OR v_rule.explanation<>v_candidate.payload->'explanation'
       OR v_rule_content_sha256<>v_expected_rule_content_sha256 THEN
        RAISE EXCEPTION 'VIDEO_CANON_RULE_CONTENT_MISMATCH' USING ERRCODE='23514';
    END IF;
    SELECT kv.* INTO v_version
      FROM public.knowledge_version kv
      JOIN public.knowledge_item ki ON ki.knowledge_item_id=kv.knowledge_item_id
       AND ki.school_id=v_candidate.school_id AND ki.knowledge_type='bidding_rule'
       AND ki.status='active'
       AND ki.stable_key='video-canon:'||v_candidate.payload->>'semantic_identity_sha256'
     WHERE kv.knowledge_version_id=v_rule.knowledge_version_id;
    v_expected_version_provenance := jsonb_build_object(
      'promotion_mode','AI_VERIFIED_TEACHER_VIDEO',
      'analysis_candidate_id',v_candidate.analysis_candidate_id,
      'candidate_payload_hash',v_candidate.payload_hash,
      'semantic_identity_sha256',v_candidate.payload->>'semantic_identity_sha256',
      'source_id',v_candidate.source_id,
      'source_sha256',v_candidate.payload#>>'{source,source_sha256}',
      'video_file_id',v_candidate.payload#>>'{source,video_file_id}',
      'teacher_statement_sha256',v_candidate.payload#>>'{teacher_assertion,statement_sha256}',
      'transcript_locators',v_candidate.payload#>'{teacher_assertion,transcript_locators}'
    );
    IF v_version.knowledge_version_id IS NULL
       OR v_version.authority_class<>'school_canon'
       OR v_version.review_status<>'unreviewed'
       OR v_version.status<>'candidate'
       OR v_version.content<>v_candidate.payload
       OR v_version.bidding_system_key IS DISTINCT FROM
            v_bundle.bundle_payload->>'system_profile'
       OR v_version.agreement_scope<>jsonb_build_object('scope_key',v_semantic_scope)
       OR v_version.level_scope<>jsonb_build_object(
            'level_key',v_bundle.bundle_payload->>'learner_level'
          )
       OR v_version.effective_from IS DISTINCT FROM v_valid_from
       OR v_version.effective_to IS DISTINCT FROM v_valid_to
       OR v_version.method_version IS DISTINCT FROM v_candidate.method_version
       OR v_version.provenance<>v_expected_version_provenance
       OR (SELECT count(*) FROM bidding.rule r
            WHERE r.knowledge_version_id=v_version.knowledge_version_id)<>1
       OR (SELECT count(*) FROM public.knowledge_version_source kvs
            WHERE kvs.knowledge_version_id=v_version.knowledge_version_id)<>1
       OR NOT EXISTS (
            SELECT 1 FROM public.knowledge_version_source kvs
             WHERE kvs.knowledge_version_id=v_version.knowledge_version_id
               AND kvs.source_id=v_candidate.source_id
               AND kvs.relation_type='derived_from'
               AND kvs.source_locator=jsonb_build_object(
                 'transcript_locators',
                 v_candidate.payload#>'{teacher_assertion,transcript_locators}'
               )
          ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_KNOWLEDGE_VERSION_BINDING_INVALID' USING ERRCODE='23514';
    END IF;
    v_version_content_sha256 := encode(public.digest(convert_to(
      (to_jsonb(v_version)-ARRAY['review_status','status','created_at'])::text,
      'UTF8'),'sha256'),'hex');

    SELECT p.* INTO v_policy
      FROM bidding.video_canon_source_policy p
      JOIN public.source s ON s.source_id=p.source_id AND s.school_id=p.school_id AND s.status='active'
      JOIN public.source_identity si
        ON si.source_id=s.source_id
       AND si.source_native_key='google-drive:'||p.video_file_id
       AND si.attributes->>'provider'='google_drive'
       AND si.attributes->>'drive_file_id'=p.video_file_id
      JOIN public.knowledge_version_source kvs
        ON kvs.source_id=p.source_id AND kvs.knowledge_version_id=v_version.knowledge_version_id
     WHERE p.school_id=v_candidate.school_id
       AND p.source_id=v_candidate.source_id
       AND p.source_id=bidding.video_canon_drive_source_id(p.video_file_id)
       AND p.status='active'
       AND p.valid_from<=clock_timestamp()
       AND (p.valid_to IS NULL OR p.valid_to>clock_timestamp())
       AND p.valid_from<=v_valid_from AND (p.valid_to IS NULL OR p.valid_to>v_valid_from)
       AND (p.valid_to IS NULL OR (v_valid_to IS NOT NULL AND v_valid_to<=p.valid_to))
       AND p.source_sha256=v_candidate.payload#>>'{source,source_sha256}'
       AND p.video_file_id=v_candidate.payload#>>'{source,video_file_id}'
       AND (v_candidate.payload#>>'{teacher_assertion,speaker_id}')=ANY(p.teacher_ids)
       AND v_semantic_scope=ANY(p.semantic_scopes)
       AND p.policy_version=v_policy_version
       AND p.authorization_evidence_sha256=v_candidate.payload#>>'{source_authorization,authorization_evidence_sha256}'
       AND p.system_profile=v_bundle.bundle_payload->>'system_profile'
       AND p.learner_level=v_bundle.bundle_payload->>'learner_level'
       AND p.system_profile=v_version.bidding_system_key
       AND p.learner_level=v_version.level_scope->>'level_key'
     FOR SHARE OF p,s,si;
    IF NOT FOUND THEN RAISE EXCEPTION 'VIDEO_CANON_SOURCE_POLICY_NOT_FOUND' USING ERRCODE='23514'; END IF;

    IF EXISTS (
        SELECT 1 FROM bidding.video_canon_ai_verification v
         WHERE v.analysis_candidate_id=p_analysis_candidate_id
           AND v.candidate_payload_hash=v_candidate.payload_hash
           AND v.verification_bundle_sha256=p_verification_bundle_sha256
           AND v.result<>'PASS'
    ) OR EXISTS (
        SELECT req.check_id FROM (VALUES
          ('SOURCE_AUTHORITY'),('SOURCE_BINDING'),('SPEAKER_IDENTITY'),('TRANSCRIPT_BINDING'),
          ('SEMANTIC_PARSE'),('EXPLANATION_COMPLETENESS'),('BRIDGE_LOGIC'),
          ('HIDDEN_INFORMATION_FIREWALL'),('POSITIVE_TESTS'),('NEGATIVE_TESTS'),
          ('BOUNDARY_TESTS'),('INTERFERENCE_TESTS'),('CANON_REGRESSION'),('CANON_INTEGRITY'),
          ('CANON_CONFLICT_SCAN'),('ROLLBACK_RESTORE')
        ) req(check_id)
        WHERE NOT EXISTS (
          SELECT 1 FROM bidding.video_canon_ai_verification v
          JOIN bidding.video_canon_verifier_registry vr
            ON vr.verifier_family=v.verifier_family
           AND vr.status='active'
           AND v.check_id=ANY(vr.allowed_check_ids)
           AND (vr.max_assurance_level='I3'
                OR (vr.max_assurance_level='I2' AND v.assurance_level IN ('I0','I1','I2'))
                OR (vr.max_assurance_level='I1' AND v.assurance_level IN ('I0','I1')))
           WHERE v.analysis_candidate_id=p_analysis_candidate_id
             AND v.candidate_payload_hash=v_candidate.payload_hash
             AND v.verification_bundle_sha256=p_verification_bundle_sha256
             AND v.check_id=req.check_id AND v.result='PASS'
             AND (
               v.check_id NOT IN ('CANON_REGRESSION','CANON_INTEGRITY','CANON_CONFLICT_SCAN','ROLLBACK_RESTORE')
               OR v.canon_snapshot_sha256=v_canon_snapshot_sha256
             )
        )
    ) THEN RAISE EXCEPTION 'VIDEO_CANON_AI_CHECKS_INCOMPLETE' USING ERRCODE='23514'; END IF;

    SELECT v.verifier_family,v.execution_principal INTO v_semantic_family,v_semantic_principal
      FROM bidding.video_canon_ai_verification v
      JOIN bidding.video_canon_verifier_registry vr
       ON vr.verifier_family=v.verifier_family AND vr.status='active'
       AND v.check_id=ANY(vr.allowed_check_ids)
       AND vr.max_assurance_level IN ('I2','I3')
       AND (vr.max_assurance_level='I3' OR v.assurance_level='I2')
     WHERE v.analysis_candidate_id=p_analysis_candidate_id
       AND v.candidate_payload_hash=v_candidate.payload_hash
       AND v.verification_bundle_sha256=p_verification_bundle_sha256
       AND v.check_id='SEMANTIC_PARSE' AND v.result='PASS'
       AND v.assurance_level IN ('I2','I3') LIMIT 1;
    SELECT v.verifier_family,v.execution_principal INTO v_bridge_family,v_bridge_principal
      FROM bidding.video_canon_ai_verification v
      JOIN bidding.video_canon_verifier_registry vr
       ON vr.verifier_family=v.verifier_family AND vr.status='active'
       AND v.check_id=ANY(vr.allowed_check_ids)
       AND vr.max_assurance_level IN ('I2','I3')
       AND (vr.max_assurance_level='I3' OR v.assurance_level='I2')
     WHERE v.analysis_candidate_id=p_analysis_candidate_id
       AND v.candidate_payload_hash=v_candidate.payload_hash
       AND v.verification_bundle_sha256=p_verification_bundle_sha256
       AND v.check_id='BRIDGE_LOGIC' AND v.result='PASS'
       AND v.assurance_level IN ('I2','I3') LIMIT 1;
    SELECT v.execution_principal INTO v_firewall_principal
      FROM bidding.video_canon_ai_verification v
         JOIN bidding.video_canon_verifier_registry vr
           ON vr.verifier_family=v.verifier_family AND vr.status='active'
          AND v.check_id=ANY(vr.allowed_check_ids)
          AND vr.max_assurance_level IN ('I2','I3')
          AND (vr.max_assurance_level='I3' OR v.assurance_level='I2')
          WHERE v.analysis_candidate_id=p_analysis_candidate_id
            AND v.candidate_payload_hash=v_candidate.payload_hash
            AND v.verification_bundle_sha256=p_verification_bundle_sha256
            AND v.check_id='HIDDEN_INFORMATION_FIREWALL' AND v.result='PASS'
            AND v.assurance_level IN ('I2','I3')
      LIMIT 1;
    IF v_semantic_family IS NULL OR v_bridge_family IS NULL OR v_semantic_family=v_bridge_family
       OR v_semantic_principal IS NULL OR v_bridge_principal IS NULL OR v_firewall_principal IS NULL
       OR v_semantic_principal=v_bridge_principal
       OR v_semantic_principal=v_firewall_principal
       OR v_bridge_principal=v_firewall_principal THEN
        RAISE EXCEPTION 'VIDEO_CANON_I2_INDEPENDENCE_MISSING' USING ERRCODE='23514';
    END IF;

    -- The four source-derived test classes must be an exact projection of the
    -- sealed candidate. Extra or edited definitions cannot borrow old PASS runs.
    IF EXISTS (
      WITH expected_tests AS (
        SELECT test_group.test_type,test_case.ordinality,
               test_case.value AS case_payload,
               'video-canon:'||v_candidate.payload_hash||':'||
                 test_group.test_type||':'||test_case.ordinality::text AS test_key
          FROM jsonb_each(v_candidate.payload->'tests')
               AS test_group(test_type,cases)
          CROSS JOIN LATERAL jsonb_array_elements(test_group.cases)
               WITH ORDINALITY AS test_case(value,ordinality)
      )
      SELECT 1
        FROM expected_tests expected
        LEFT JOIN bidding.rule_test t
          ON t.rule_id=p_rule_id AND t.test_type::text=expected.test_type
         AND t.test_key=expected.test_key
       WHERE t.rule_test_id IS NULL OR NOT t.enabled
          OR t.fixture<>(expected.case_payload-'expect')
          OR t.expected<>jsonb_build_object('expect',expected.case_payload->'expect')
    ) OR EXISTS (
      SELECT 1
        FROM bidding.rule_test t
       WHERE t.rule_id=p_rule_id
         AND t.test_type IN ('positive','negative','boundary','interference')
         AND NOT EXISTS (
           SELECT 1
             FROM jsonb_each(v_candidate.payload->'tests')
                  AS test_group(test_type,cases)
             CROSS JOIN LATERAL jsonb_array_elements(test_group.cases)
                  WITH ORDINALITY AS test_case(value,ordinality)
            WHERE test_group.test_type=t.test_type::text
              AND t.test_key='video-canon:'||v_candidate.payload_hash||':'||
                    test_group.test_type||':'||test_case.ordinality::text
         )
    ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_RULE_TEST_BINDING_INVALID' USING ERRCODE='23514';
    END IF;

    v_rule_test_state_sha256 := bidding.video_canon_rule_test_state_sha256(p_rule_id);
    IF v_rule_test_state_sha256 IS DISTINCT FROM
         v_bundle.bundle_payload->>'rule_test_state_sha256' THEN
        RAISE EXCEPTION 'VIDEO_CANON_RULE_TEST_STATE_MISMATCH' USING ERRCODE='23514';
    END IF;

    IF EXISTS (
      SELECT req.test_type FROM (VALUES ('positive'),('negative'),('boundary'),('interference'),('hidden_information'),('regression')) req(test_type)
      WHERE NOT EXISTS (
        SELECT 1 FROM bidding.rule_test t WHERE t.rule_id=p_rule_id AND t.enabled
          AND t.test_type=req.test_type AND bidding.latest_test_result(t.rule_test_id)='pass'
      )
    ) OR EXISTS (
      SELECT 1 FROM bidding.rule_test t
       WHERE t.rule_id=p_rule_id AND t.enabled
         AND bidding.latest_test_result(t.rule_test_id) IS DISTINCT FROM 'pass'
    ) OR EXISTS (
      SELECT 1 FROM bidding.rule_conflict c WHERE c.status='open'
        AND (c.left_rule_id=p_rule_id OR c.right_rule_id=p_rule_id)
    ) THEN RAISE EXCEPTION 'VIDEO_CANON_RULE_GATES_FAILED' USING ERRCODE='23514'; END IF;

    IF v_valid_to IS NOT NULL AND v_valid_to<=clock_timestamp() THEN
        RAISE EXCEPTION 'VIDEO_CANON_EFFECTIVE_PERIOD_EXPIRED' USING ERRCODE='23514';
    END IF;
    v_canon_snapshot_sha256 := bidding.current_school_canon_snapshot_sha256(v_candidate.school_id);
    IF v_bundle.bundle_payload->>'canon_snapshot_sha256'<>v_canon_snapshot_sha256 THEN
        RAISE EXCEPTION 'VIDEO_CANON_STATE_CHECKS_STALE' USING ERRCODE='23514';
    END IF;

    SELECT ca.* INTO v_prior_canon
      FROM public.canon_activation ca
      JOIN public.knowledge_version prior_kv
        ON prior_kv.knowledge_version_id=ca.knowledge_version_id
     WHERE prior_kv.knowledge_item_id=v_version.knowledge_item_id
       AND ca.scope_key=v_scope_key AND ca.status='active'
       AND ca.valid_from<=clock_timestamp()
       AND (ca.valid_to IS NULL OR ca.valid_to>clock_timestamp())
       AND tstzrange(ca.valid_from,ca.valid_to,'[)') && tstzrange(v_valid_from,v_valid_to,'[)')
     FOR UPDATE OF ca;
    IF FOUND THEN
        IF v_prior_canon.knowledge_version_id=v_version.knowledge_version_id
           OR v_prior_canon.valid_from>=v_valid_from
           OR v_bundle.bundle_payload#>>'{rollback,target_knowledge_version_id}'
                IS DISTINCT FROM v_prior_canon.knowledge_version_id::text
           OR v_bundle.bundle_payload#>>'{rollback,target_canon_activation_id}'
                IS DISTINCT FROM v_prior_canon.canon_activation_id::text THEN
            RAISE EXCEPTION 'VIDEO_CANON_ROLLBACK_TARGET_MISMATCH' USING ERRCODE='23514';
        END IF;
        SELECT * INTO v_prior_version
          FROM public.knowledge_version
         WHERE knowledge_version_id=v_prior_canon.knowledge_version_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'VIDEO_CANON_ROLLBACK_TARGET_VERSION_MISSING' USING ERRCODE='23514';
        END IF;
        SELECT * INTO v_prior_item
          FROM public.knowledge_item
         WHERE knowledge_item_id=v_prior_version.knowledge_item_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'VIDEO_CANON_ROLLBACK_TARGET_ITEM_MISSING' USING ERRCODE='23514';
        END IF;
        v_prior_version_content_sha256 := encode(public.digest(convert_to(
          (to_jsonb(v_prior_version)-ARRAY['created_at'])::text,
          'UTF8'),'sha256'),'hex');
        v_prior_item_content_sha256 := encode(public.digest(convert_to(
          (to_jsonb(v_prior_item)-ARRAY['created_at'])::text,
          'UTF8'),'sha256'),'hex');
        PERFORM 1 FROM bidding.runtime_activation
         WHERE canon_activation_id=v_prior_canon.canon_activation_id AND status='active' FOR UPDATE;
        SELECT COALESCE(array_agg(runtime_activation_id ORDER BY runtime_activation_id),'{}'::uuid[]),
               COALESCE(jsonb_agg(jsonb_build_object(
                 'runtime_activation_id',runtime_activation_id,
                 'valid_to',valid_to
               ) ORDER BY runtime_activation_id),'[]'::jsonb)
          INTO v_prior_runtime_ids,v_prior_runtime_state FROM bidding.runtime_activation
         WHERE canon_activation_id=v_prior_canon.canon_activation_id AND status='active';
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                 'rule_id',r.rule_id,
                 'rule_content_sha256',bidding.video_canon_rule_restore_sha256(r.rule_id)
               ) ORDER BY r.rule_id::text),'[]'::jsonb)
          INTO v_prior_rule_state
          FROM bidding.rule r
         WHERE r.knowledge_version_id=v_prior_canon.knowledge_version_id;
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                 'rule_id',r.rule_id,
                 'rule_test_state_sha256',
                   bidding.video_canon_rule_test_state_sha256(r.rule_id)
               ) ORDER BY r.rule_id::text),'[]'::jsonb)
          INTO v_prior_rule_test_state
          FROM bidding.rule r
         WHERE r.knowledge_version_id=v_prior_canon.knowledge_version_id;
        SELECT COALESCE(jsonb_agg(to_jsonb(kvs)
                 ORDER BY kvs.source_id::text,kvs.relation_type,kvs.source_locator::text),
               '[]'::jsonb)
          INTO v_prior_source_state
          FROM public.knowledge_version_source kvs
         WHERE kvs.knowledge_version_id=v_prior_canon.knowledge_version_id;
        IF v_valid_to IS NOT NULL AND v_valid_to<=clock_timestamp() THEN
            RAISE EXCEPTION 'VIDEO_CANON_EFFECTIVE_PERIOD_EXPIRED' USING ERRCODE='23514';
        END IF;
        UPDATE bidding.runtime_activation SET status='superseded',valid_to=v_valid_from
         WHERE canon_activation_id=v_prior_canon.canon_activation_id AND status='active';
        UPDATE public.canon_activation SET status='superseded',valid_to=v_valid_from
         WHERE canon_activation_id=v_prior_canon.canon_activation_id;
    ELSIF v_bundle.bundle_payload#>>'{rollback,target_knowledge_version_id}' IS NOT NULL
       OR v_bundle.bundle_payload#>>'{rollback,target_canon_activation_id}' IS NOT NULL THEN
        RAISE EXCEPTION 'VIDEO_CANON_ROLLBACK_TARGET_UNEXPECTED' USING ERRCODE='23514';
    END IF;

    IF v_valid_to IS NOT NULL AND v_valid_to<=clock_timestamp() THEN
        RAISE EXCEPTION 'VIDEO_CANON_EFFECTIVE_PERIOD_EXPIRED' USING ERRCODE='23514';
    END IF;
    IF EXISTS (
      SELECT 1
        FROM bidding.video_canon_ai_verification v
        JOIN bidding.video_canon_verifier_registry vr
          ON vr.verifier_family=v.verifier_family
       WHERE v.analysis_candidate_id=p_analysis_candidate_id
         AND v.candidate_payload_hash=v_candidate.payload_hash
         AND v.verification_bundle_sha256=p_verification_bundle_sha256
         AND NOT EXISTS (
           SELECT 1
             FROM pg_catalog.pg_roles attestor
             JOIN pg_catalog.pg_roles capability
               ON capability.rolname=vr.database_role
            WHERE attestor.rolname=v.execution_principal
              AND attestor.rolcanlogin
              AND pg_has_role(attestor.oid,capability.oid,'MEMBER')
         )
    ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFIER_PRINCIPAL_REVOKED' USING ERRCODE='42501';
    END IF;
    IF v_policy.status<>'active' OR v_policy.valid_from>clock_timestamp()
       OR (v_policy.valid_to IS NOT NULL AND v_policy.valid_to<=clock_timestamp()) THEN
        RAISE EXCEPTION 'VIDEO_CANON_SOURCE_POLICY_EXPIRED' USING ERRCODE='23514';
    END IF;
    IF v_valid_to IS NOT NULL AND v_valid_to<=clock_timestamp() THEN
        RAISE EXCEPTION 'VIDEO_CANON_EFFECTIVE_PERIOD_EXPIRED' USING ERRCODE='23514';
    END IF;
    UPDATE public.knowledge_version SET review_status='approved',status='approved'
     WHERE knowledge_version_id=v_version.knowledge_version_id;
    INSERT INTO public.canon_activation(
      knowledge_version_id,scope_key,valid_from,valid_to,approved_by_person_id,approval_provenance,status
    ) VALUES (
      v_version.knowledge_version_id,v_scope_key,v_valid_from,v_valid_to,NULL,
      jsonb_build_object('promotion_mode','AI_VERIFIED_TEACHER_VIDEO','policy_version',v_policy_version,
        'candidate_id',p_analysis_candidate_id,'candidate_payload_hash',v_candidate.payload_hash,
        'semantic_identity_sha256',v_candidate.payload->>'semantic_identity_sha256',
        'verification_bundle_sha256',p_verification_bundle_sha256,
        'rule_content_sha256',v_rule_content_sha256,
        'knowledge_version_content_sha256',v_version_content_sha256,
        'rule_test_state_sha256',v_rule_test_state_sha256,
        'human_approval_required',false),'active'
    ) RETURNING canon_activation_id INTO v_canon_activation;

    INSERT INTO bidding.runtime_activation(
      school_id,rule_id,authority_lane,canon_activation_id,scope_key,valid_from,valid_to,status,
      activation_provenance,activated_by_person_id
    ) VALUES (
      v_candidate.school_id,p_rule_id,'school_canon',v_canon_activation,v_scope_key,v_valid_from,v_valid_to,'active',
      jsonb_build_object('promotion_mode','AI_VERIFIED_TEACHER_VIDEO',
        'candidate_payload_hash',v_candidate.payload_hash,
        'rule_content_sha256',v_rule_content_sha256,
        'knowledge_version_content_sha256',v_version_content_sha256,
        'rule_test_state_sha256',v_rule_test_state_sha256),NULL
    ) RETURNING runtime_activation_id INTO v_runtime_activation;

    INSERT INTO bidding.video_canon_ai_promotion_receipt(
      school_id,analysis_candidate_id,candidate_payload_hash,verification_bundle_sha256,
      policy_version,semantic_scope,scope_key,rule_content_sha256,knowledge_version_content_sha256,
      rule_test_state_sha256,rule_id,canon_activation_id,runtime_activation_id,
      superseded_canon_activation_id,superseded_canon_valid_to,
      superseded_runtime_activation_ids,superseded_runtime_state,superseded_rule_state,
      superseded_rule_test_state,superseded_source_state,
      superseded_knowledge_version_content_sha256,
      superseded_knowledge_item_content_sha256,promotion_mode,human_approval_required
    ) VALUES (
      v_candidate.school_id,p_analysis_candidate_id,v_candidate.payload_hash,p_verification_bundle_sha256,
      v_policy_version,v_semantic_scope,v_scope_key,v_rule_content_sha256,v_version_content_sha256,
      v_rule_test_state_sha256,p_rule_id,v_canon_activation,v_runtime_activation,
      v_prior_canon.canon_activation_id,v_prior_canon.valid_to,
      v_prior_runtime_ids,v_prior_runtime_state,v_prior_rule_state,
      v_prior_rule_test_state,v_prior_source_state,
      v_prior_version_content_sha256,v_prior_item_content_sha256,
      'AI_VERIFIED_TEACHER_VIDEO',false
    ) RETURNING * INTO v_existing;
    UPDATE public.analysis_candidate SET quality_status='AI_VERIFIED',promotion_status='promoted'
     WHERE analysis_candidate_id=p_analysis_candidate_id;
    RETURN v_existing.video_canon_ai_promotion_receipt_id;
END $$;

CREATE TRIGGER video_canon_restore_receipt_append_only
BEFORE UPDATE OR DELETE ON bidding.video_canon_ai_restore_receipt
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();

CREATE OR REPLACE FUNCTION bidding.restore_ai_verified_video_canon(
    p_video_canon_ai_promotion_receipt_id uuid,
    p_verification_bundle_sha256 text,
    p_restore_evidence_sha256 text
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
SET TimeZone='UTC'
AS $$
DECLARE
    v_promotion bidding.video_canon_ai_promotion_receipt%ROWTYPE;
    v_existing bidding.video_canon_ai_restore_receipt%ROWTYPE;
    v_new_canon public.canon_activation%ROWTYPE;
    v_new_runtime bidding.runtime_activation%ROWTYPE;
    v_prior_runtime bidding.runtime_activation%ROWTYPE;
    v_prior_canon public.canon_activation%ROWTYPE;
    v_prior_promotion bidding.video_canon_ai_promotion_receipt%ROWTYPE;
    v_prior_version public.knowledge_version%ROWTYPE;
    v_prior_item public.knowledge_item%ROWTYPE;
    v_prior_policy bidding.video_canon_source_policy%ROWTYPE;
    v_bundle bidding.video_canon_ai_verification_bundle%ROWTYPE;
    v_current_prior_rule_state jsonb;
    v_current_prior_rule_test_state jsonb;
    v_current_prior_source_state jsonb;
    v_prior_version_content_sha256 text;
    v_prior_item_content_sha256 text;
    v_prior_ai_version_content_sha256 text;
    v_state jsonb;
    v_original_valid_to timestamptz;
    v_revoked_at timestamptz;
    v_now timestamptz;
    v_restored_runtime_ids uuid[] := '{}'::uuid[];
BEGIN
    IF p_verification_bundle_sha256 !~ '^[0-9a-f]{64}$'
       OR p_restore_evidence_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'VIDEO_CANON_RESTORE_ARGUMENT_INVALID' USING ERRCODE='23514';
    END IF;
    SELECT * INTO v_promotion FROM bidding.video_canon_ai_promotion_receipt
     WHERE video_canon_ai_promotion_receipt_id=p_video_canon_ai_promotion_receipt_id;
    IF NOT FOUND OR v_promotion.verification_bundle_sha256<>p_verification_bundle_sha256 THEN
        RAISE EXCEPTION 'VIDEO_CANON_RESTORE_RECEIPT_MISMATCH' USING ERRCODE='23514';
    END IF;
    SELECT * INTO v_existing FROM bidding.video_canon_ai_restore_receipt
     WHERE video_canon_ai_promotion_receipt_id=p_video_canon_ai_promotion_receipt_id;
    IF FOUND THEN
        IF v_existing.verification_bundle_sha256<>p_verification_bundle_sha256
           OR v_existing.restore_evidence_sha256<>p_restore_evidence_sha256 THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_IDEMPOTENCY_MISMATCH' USING ERRCODE='23514';
        END IF;
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(v_promotion.school_id::text,0));
    LOCK TABLE public.canon_activation,public.knowledge_item,public.knowledge_version,
      public.source,public.source_identity,public.knowledge_version_source,
      bidding.runtime_activation,bidding.rule,bidding.rule_test,bidding.rule_test_run,
      bidding.rule_conflict,bidding.video_canon_source_policy,
      bidding.video_canon_verifier_registry,bidding.video_canon_ai_verification,
      bidding.video_canon_ai_restore_receipt IN SHARE ROW EXCLUSIVE MODE;
    SELECT * INTO v_existing FROM bidding.video_canon_ai_restore_receipt
     WHERE video_canon_ai_promotion_receipt_id=p_video_canon_ai_promotion_receipt_id;
    IF FOUND THEN
        IF v_existing.verification_bundle_sha256<>p_verification_bundle_sha256
           OR v_existing.restore_evidence_sha256<>p_restore_evidence_sha256 THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_IDEMPOTENCY_MISMATCH' USING ERRCODE='23514';
        END IF;
        v_now := clock_timestamp();
        IF NOT EXISTS (
             SELECT 1
               FROM public.canon_activation ca
               JOIN bidding.runtime_activation ra
                 ON ra.runtime_activation_id=v_existing.revoked_runtime_activation_id
                AND ra.canon_activation_id=ca.canon_activation_id
              WHERE ca.canon_activation_id=v_existing.revoked_canon_activation_id
                AND ca.status='revoked' AND ra.status='revoked'
                AND ca.valid_to IS NOT NULL AND ca.valid_to<=v_now
                AND ra.valid_to IS NOT NULL AND ra.valid_to<=v_now
                AND ra.school_id=v_existing.school_id
           ) OR (
             v_existing.restored_canon_activation_id IS NULL AND (
               cardinality(v_existing.restored_runtime_activation_ids)<>0
               OR EXISTS (
                 SELECT 1
                   FROM public.canon_activation ca
                   JOIN public.knowledge_version active_kv
                     ON active_kv.knowledge_version_id=ca.knowledge_version_id
                   JOIN public.canon_activation revoked_ca
                     ON revoked_ca.canon_activation_id=v_existing.revoked_canon_activation_id
                   JOIN public.knowledge_version revoked_kv
                     ON revoked_kv.knowledge_version_id=revoked_ca.knowledge_version_id
                  WHERE active_kv.knowledge_item_id=revoked_kv.knowledge_item_id
                    AND ca.status='active' AND ca.valid_from<=v_now
                    AND (ca.valid_to IS NULL OR ca.valid_to>v_now)
               )
               OR EXISTS (
                 SELECT 1
                   FROM bidding.runtime_activation ra
                   JOIN bidding.rule active_rule ON active_rule.rule_id=ra.rule_id
                   JOIN public.knowledge_version active_kv
                     ON active_kv.knowledge_version_id=active_rule.knowledge_version_id
                   JOIN bidding.runtime_activation revoked_ra
                     ON revoked_ra.runtime_activation_id=v_existing.revoked_runtime_activation_id
                   JOIN bidding.rule revoked_rule ON revoked_rule.rule_id=revoked_ra.rule_id
                   JOIN public.knowledge_version revoked_kv
                     ON revoked_kv.knowledge_version_id=revoked_rule.knowledge_version_id
                  WHERE active_kv.knowledge_item_id=revoked_kv.knowledge_item_id
                    AND ra.school_id=v_existing.school_id AND ra.status='active'
                    AND ra.valid_from<=v_now
                    AND (ra.valid_to IS NULL OR ra.valid_to>v_now)
               )
             )
           ) OR (
             v_existing.restored_canon_activation_id IS NOT NULL AND (
               NOT EXISTS (
                 SELECT 1 FROM public.canon_activation ca
                  WHERE ca.canon_activation_id=v_existing.restored_canon_activation_id
                    AND ca.scope_key=v_promotion.scope_key AND ca.status='active'
                    AND ca.valid_from<=v_now
                    AND (ca.valid_to IS NULL OR ca.valid_to>v_now)
               )
               OR (SELECT count(*) FROM bidding.runtime_activation ra
                    WHERE ra.runtime_activation_id=ANY(v_existing.restored_runtime_activation_ids)
                      AND ra.canon_activation_id=v_existing.restored_canon_activation_id
                      AND ra.school_id=v_existing.school_id
                      AND ra.scope_key=v_promotion.scope_key AND ra.status='active'
                      AND ra.valid_from<=v_now
                      AND (ra.valid_to IS NULL OR ra.valid_to>v_now))
                    <>cardinality(v_existing.restored_runtime_activation_ids)
             )
           ) THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_RECEIPT_STALE' USING ERRCODE='55000';
        END IF;
        RETURN v_existing.video_canon_ai_restore_receipt_id;
    END IF;
    SELECT * INTO v_promotion FROM bidding.video_canon_ai_promotion_receipt
     WHERE video_canon_ai_promotion_receipt_id=p_video_canon_ai_promotion_receipt_id FOR UPDATE;
    SELECT * INTO v_bundle FROM bidding.video_canon_ai_verification_bundle
     WHERE analysis_candidate_id=v_promotion.analysis_candidate_id
       AND candidate_payload_hash=v_promotion.candidate_payload_hash
       AND verification_bundle_sha256=v_promotion.verification_bundle_sha256;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'VIDEO_CANON_RESTORE_BUNDLE_NOT_FOUND' USING ERRCODE='23514';
    END IF;
    SELECT * INTO v_new_canon FROM public.canon_activation
     WHERE canon_activation_id=v_promotion.canon_activation_id FOR UPDATE;
    SELECT * INTO v_new_runtime FROM bidding.runtime_activation
     WHERE runtime_activation_id=v_promotion.runtime_activation_id FOR UPDATE;
    IF v_new_canon.canon_activation_id IS NULL OR v_new_runtime.runtime_activation_id IS NULL
       OR v_new_canon.status<>'active' OR v_new_runtime.status<>'active'
       OR (v_new_canon.valid_to IS NOT NULL
           AND v_new_canon.valid_to<=clock_timestamp())
       OR (v_new_runtime.valid_to IS NOT NULL
           AND v_new_runtime.valid_to<=clock_timestamp())
       OR v_new_runtime.canon_activation_id<>v_new_canon.canon_activation_id
       OR v_new_runtime.rule_id<>v_promotion.rule_id
       OR v_new_runtime.school_id<>v_promotion.school_id THEN
        RAISE EXCEPTION 'VIDEO_CANON_RESTORE_CURRENT_ACTIVATION_MISMATCH' USING ERRCODE='23514';
    END IF;

    -- Lock every restoration target before any final authority check or
    -- mutation. SHARE ROW EXCLUSIVE table locks prevent later DML/lock upgrades.
    IF v_promotion.superseded_canon_activation_id IS NOT NULL THEN
        PERFORM 1 FROM public.canon_activation
         WHERE canon_activation_id=v_promotion.superseded_canon_activation_id
         FOR UPDATE;
        PERFORM 1 FROM bidding.runtime_activation
         WHERE runtime_activation_id=ANY(v_promotion.superseded_runtime_activation_ids)
         ORDER BY runtime_activation_id
         FOR UPDATE;
        IF (SELECT count(*) FROM bidding.runtime_activation
             WHERE runtime_activation_id=ANY(v_promotion.superseded_runtime_activation_ids))
             <>cardinality(v_promotion.superseded_runtime_activation_ids)
           OR jsonb_array_length(v_promotion.superseded_runtime_state)
             <>cardinality(v_promotion.superseded_runtime_activation_ids) THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_RUNTIME_STATE_MISMATCH' USING ERRCODE='23514';
        END IF;
    END IF;

    IF v_promotion.superseded_canon_activation_id IS NOT NULL THEN
        IF v_promotion.superseded_canon_valid_to IS NOT NULL
           AND v_promotion.superseded_canon_valid_to<=clock_timestamp() THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_TARGET_EXPIRED' USING ERRCODE='23514';
        END IF;
        SELECT * INTO v_prior_canon FROM public.canon_activation
         WHERE canon_activation_id=v_promotion.superseded_canon_activation_id
           AND status='superseded' AND valid_to=v_new_canon.valid_from FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_TARGET_MISMATCH' USING ERRCODE='23514';
        END IF;
        IF v_prior_canon.knowledge_version_id::text IS DISTINCT FROM
             v_bundle.bundle_payload#>>'{rollback,target_knowledge_version_id}'
           OR v_prior_canon.canon_activation_id::text IS DISTINCT FROM
             v_bundle.bundle_payload#>>'{rollback,target_canon_activation_id}'
           OR v_prior_canon.scope_key IS DISTINCT FROM v_promotion.scope_key THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_TARGET_BINDING_MISMATCH' USING ERRCODE='23514';
        END IF;
        SELECT * INTO v_prior_version
          FROM public.knowledge_version
         WHERE knowledge_version_id=v_prior_canon.knowledge_version_id
         FOR UPDATE;
        v_prior_version_content_sha256 := encode(public.digest(convert_to(
          (to_jsonb(v_prior_version)-ARRAY['created_at'])::text,
          'UTF8'),'sha256'),'hex');
        IF v_prior_version.knowledge_version_id IS NULL
           OR v_prior_version_content_sha256 IS DISTINCT FROM
                v_promotion.superseded_knowledge_version_content_sha256 THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_PREDECESSOR_VERSION_MISMATCH'
              USING ERRCODE='23514';
        END IF;
        SELECT * INTO v_prior_item
          FROM public.knowledge_item
         WHERE knowledge_item_id=v_prior_version.knowledge_item_id
         FOR UPDATE;
        v_prior_item_content_sha256 := encode(public.digest(convert_to(
          (to_jsonb(v_prior_item)-ARRAY['created_at'])::text,
          'UTF8'),'sha256'),'hex');
        IF v_prior_item.knowledge_item_id IS NULL
           OR v_prior_item_content_sha256 IS DISTINCT FROM
                v_promotion.superseded_knowledge_item_content_sha256 THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_PREDECESSOR_ITEM_MISMATCH'
              USING ERRCODE='23514';
        END IF;
        SELECT COALESCE(jsonb_agg(to_jsonb(kvs)
                 ORDER BY kvs.source_id::text,kvs.relation_type,kvs.source_locator::text),
               '[]'::jsonb)
          INTO v_current_prior_source_state
          FROM public.knowledge_version_source kvs
         WHERE kvs.knowledge_version_id=v_prior_canon.knowledge_version_id;
        IF v_current_prior_source_state<>v_promotion.superseded_source_state THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_SOURCE_SET_MISMATCH' USING ERRCODE='23514';
        END IF;
        SELECT * INTO v_prior_promotion
          FROM bidding.video_canon_ai_promotion_receipt
         WHERE canon_activation_id=v_promotion.superseded_canon_activation_id;
        IF v_prior_promotion.video_canon_ai_promotion_receipt_id IS NOT NULL THEN
            -- 0323 adds independent I2/I3 delivery assurance. A predecessor
            -- promoted through that consumer may be restored only while its
            -- retained assurance principals and capabilities are still live.
            -- Keep 0322 independently usable: the optional tables are touched
            -- only when the later migration is installed.
            IF to_regclass('bidding.video_canon_promotion_delivery_receipt') IS NOT NULL THEN
                EXECUTE 'LOCK TABLE bidding.video_canon_promotion_delivery_receipt,
                  bidding.video_canon_assurance_verdict,
                  bidding.video_canon_assurance_assignment,
                  bidding.video_canon_assurance_verifier_registry IN SHARE MODE';
                IF EXISTS (
                    SELECT 1
                      FROM bidding.video_canon_promotion_delivery_receipt dr
                     WHERE dr.promotion_receipt_id=
                           v_prior_promotion.video_canon_ai_promotion_receipt_id
                ) AND NOT EXISTS (
                    SELECT 1
                      FROM bidding.video_canon_promotion_delivery_receipt dr
                      JOIN bidding.video_canon_assurance_verdict i2
                        ON i2.analysis_candidate_id=dr.analysis_candidate_id
                       AND i2.candidate_payload_hash=dr.candidate_payload_hash
                       AND i2.verification_bundle_sha256=dr.verification_bundle_sha256
                       AND i2.assurance_set_sha256=dr.assurance_set_sha256
                       AND i2.assurance_level='I2'
                      JOIN bidding.video_canon_assurance_verdict i3
                        ON i3.analysis_candidate_id=i2.analysis_candidate_id
                       AND i3.candidate_payload_hash=i2.candidate_payload_hash
                       AND i3.verification_bundle_sha256=i2.verification_bundle_sha256
                       AND i3.assurance_set_sha256=i2.assurance_set_sha256
                       AND i3.assurance_level='I3'
                      JOIN bidding.video_canon_assurance_assignment a2
                        ON a2.video_canon_assurance_assignment_id=
                           i2.video_canon_assurance_assignment_id
                       AND a2.assurance_level='I2'
                       AND a2.assigned_principal=i2.execution_principal
                       AND a2.status='active'
                      JOIN bidding.video_canon_assurance_assignment a3
                        ON a3.video_canon_assurance_assignment_id=
                           i3.video_canon_assurance_assignment_id
                       AND a3.assurance_level='I3'
                       AND a3.assigned_principal=i3.execution_principal
                       AND a3.status='active'
                      JOIN bidding.video_canon_assurance_verifier_registry r2
                        ON r2.assurance_level='I2'
                       AND r2.capability_role='bridge_school_canon_i2_verifier'
                       AND r2.verifier_family=i2.verifier_family
                       AND r2.verifier_version=i2.verifier_version
                      JOIN bidding.video_canon_assurance_verifier_registry r3
                        ON r3.assurance_level='I3'
                       AND r3.capability_role='bridge_school_canon_i3_verifier'
                       AND r3.verifier_family=i3.verifier_family
                       AND r3.verifier_version=i3.verifier_version
                     WHERE dr.promotion_receipt_id=
                           v_prior_promotion.video_canon_ai_promotion_receipt_id
                       AND i2.verdict='VERIFIED_FOR_PROMOTION'
                       AND i3.verdict='VERIFIED_FOR_PROMOTION'
                       AND i2.provenance_verified AND i3.provenance_verified
                       AND i2.hidden_information_clear AND i3.hidden_information_clear
                       AND i2.profile_unambiguous AND i3.profile_unambiguous
                       AND NOT i2.canon_conflict AND NOT i3.canon_conflict
                       AND i2.deterministic AND i3.deterministic
                       AND i2.verifier_family<>i3.verifier_family
                       AND i2.execution_principal<>i3.execution_principal
                       AND i2.canon_snapshot_sha256=i3.canon_snapshot_sha256
                       AND i2.system_profile=i3.system_profile
                       AND i2.learner_level=i3.learner_level
                       AND EXISTS (
                         SELECT 1 FROM pg_catalog.pg_roles attestor
                          WHERE attestor.rolname=i2.execution_principal
                            AND attestor.rolcanlogin
                            AND pg_has_role(attestor.oid,r2.capability_role,'MEMBER')
                       )
                       AND EXISTS (
                         SELECT 1 FROM pg_catalog.pg_roles attestor
                          WHERE attestor.rolname=i3.execution_principal
                            AND attestor.rolcanlogin
                            AND pg_has_role(attestor.oid,r3.capability_role,'MEMBER')
                       )
                ) THEN
                    RAISE EXCEPTION 'VIDEO_CANON_RESTORE_I2_I3_ASSURANCE_REVOKED'
                      USING ERRCODE='42501';
                END IF;
            END IF;
            SELECT p.* INTO v_prior_policy
              FROM public.analysis_candidate c
              JOIN bidding.video_canon_source_policy p
                ON p.school_id=c.school_id AND p.source_id=c.source_id
              JOIN public.source s ON s.source_id=p.source_id
               AND s.school_id=p.school_id AND s.status='active'
              JOIN public.source_identity si
                ON si.source_id=s.source_id
               AND si.source_native_key='google-drive:'||p.video_file_id
               AND si.attributes->>'provider'='google_drive'
               AND si.attributes->>'drive_file_id'=p.video_file_id
             WHERE c.analysis_candidate_id=v_prior_promotion.analysis_candidate_id
               AND c.payload_hash=v_prior_promotion.candidate_payload_hash
               AND c.payload->>'source_class'='SCHOOL_PRIMARY_EVIDENCE'
               AND c.payload#>>'{source_authorization,policy_version}'=p.policy_version
               AND p.status='active' AND p.policy_version=v_prior_promotion.policy_version
               AND p.source_id=bidding.video_canon_drive_source_id(p.video_file_id)
               AND p.valid_from<=clock_timestamp()
               AND (p.valid_to IS NULL OR p.valid_to>clock_timestamp())
               AND p.valid_from<=v_prior_canon.valid_from
               AND (p.valid_to IS NULL OR p.valid_to>v_prior_canon.valid_from)
               AND (p.valid_to IS NULL OR (
                 v_promotion.superseded_canon_valid_to IS NOT NULL
                 AND v_promotion.superseded_canon_valid_to<=p.valid_to
               ))
               AND p.source_sha256=c.payload#>>'{source,source_sha256}'
               AND p.video_file_id=c.payload#>>'{source,video_file_id}'
               AND (c.payload#>>'{teacher_assertion,speaker_id}')=ANY(p.teacher_ids)
               AND v_prior_promotion.semantic_scope=ANY(p.semantic_scopes)
               AND p.system_profile=c.payload->>'system_profile'
               AND p.learner_level=c.payload->>'learner_level'
               AND p.system_profile=v_prior_version.bidding_system_key
               AND p.learner_level=v_prior_version.level_scope->>'level_key'
               AND p.authorization_evidence_sha256=
                     c.payload#>>'{source_authorization,authorization_evidence_sha256}'
             FOR SHARE OF p,s,si;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'VIDEO_CANON_RESTORE_SOURCE_POLICY_INACTIVE' USING ERRCODE='23514';
            END IF;
            v_prior_ai_version_content_sha256 := encode(public.digest(convert_to(
              (to_jsonb(v_prior_version)-ARRAY['review_status','status','created_at'])::text,
              'UTF8'),'sha256'),'hex');
            IF v_prior_ai_version_content_sha256<>
                    v_prior_promotion.knowledge_version_content_sha256 THEN
                RAISE EXCEPTION 'VIDEO_CANON_RESTORE_VERSION_CONTENT_MISMATCH' USING ERRCODE='23514';
            END IF;
            IF (
                 SELECT count(*) FROM public.knowledge_version_source kvs
                  WHERE kvs.knowledge_version_id=v_prior_version.knowledge_version_id
               )<>1 OR NOT EXISTS (
                 SELECT 1
                   FROM public.analysis_candidate c
                   JOIN public.knowledge_version_source kvs
                     ON kvs.knowledge_version_id=v_prior_version.knowledge_version_id
                    AND kvs.source_id=c.source_id
                    AND kvs.relation_type='derived_from'
                    AND kvs.source_locator=jsonb_build_object(
                      'transcript_locators',
                      c.payload#>'{teacher_assertion,transcript_locators}'
                    )
                  WHERE c.analysis_candidate_id=v_prior_promotion.analysis_candidate_id
                    AND c.payload_hash=v_prior_promotion.candidate_payload_hash
               ) THEN
                RAISE EXCEPTION 'VIDEO_CANON_RESTORE_SOURCE_BINDING_MISMATCH' USING ERRCODE='23514';
            END IF;
            IF bidding.video_canon_rule_test_state_sha256(v_prior_promotion.rule_id)
                 IS DISTINCT FROM v_prior_promotion.rule_test_state_sha256 THEN
                RAISE EXCEPTION 'VIDEO_CANON_RESTORE_RULE_TEST_STATE_MISMATCH' USING ERRCODE='23514';
            END IF;
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                 'rule_id',r.rule_id,
                 'rule_test_state_sha256',
                   bidding.video_canon_rule_test_state_sha256(r.rule_id)
               ) ORDER BY r.rule_id::text),'[]'::jsonb)
          INTO v_current_prior_rule_test_state
          FROM bidding.rule r
         WHERE r.knowledge_version_id=v_prior_canon.knowledge_version_id;
        IF v_current_prior_rule_test_state<>v_promotion.superseded_rule_test_state THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_PREDECESSOR_TEST_STATE_MISMATCH'
              USING ERRCODE='23514';
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                 'rule_id',r.rule_id,
                 'rule_content_sha256',bidding.video_canon_rule_restore_sha256(r.rule_id)
               ) ORDER BY r.rule_id::text),'[]'::jsonb)
          INTO v_current_prior_rule_state
          FROM bidding.rule r
         WHERE r.knowledge_version_id=v_prior_canon.knowledge_version_id;
        IF v_current_prior_rule_state<>v_promotion.superseded_rule_state THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_RULE_CONTENT_MISMATCH' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS (
             SELECT 1 FROM bidding.rule r
              WHERE r.knowledge_version_id=v_prior_canon.knowledge_version_id
           ) OR EXISTS (
             SELECT 1 FROM bidding.rule r
              WHERE r.knowledge_version_id=v_prior_canon.knowledge_version_id
                AND NOT bidding.rule_passes_activation_gates(r.rule_id)
           ) OR NOT EXISTS (
             SELECT 1 FROM public.knowledge_version_source kvs
             JOIN public.source s ON s.source_id=kvs.source_id
                AND s.school_id=v_promotion.school_id AND s.status='active'
              WHERE kvs.knowledge_version_id=v_prior_canon.knowledge_version_id
           ) THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_CANON_VERSION_GATES_FAILED' USING ERRCODE='23514';
        END IF;
        IF v_prior_promotion.video_canon_ai_promotion_receipt_id IS NOT NULL THEN
            IF EXISTS (
              SELECT 1
                FROM bidding.video_canon_ai_verification v
                LEFT JOIN bidding.video_canon_verifier_registry vr
                  ON vr.verifier_family=v.verifier_family
                 AND v.check_id=ANY(vr.allowed_check_ids)
               WHERE v.analysis_candidate_id=v_prior_promotion.analysis_candidate_id
                 AND v.candidate_payload_hash=v_prior_promotion.candidate_payload_hash
                 AND v.verification_bundle_sha256=v_prior_promotion.verification_bundle_sha256
                 AND (
                   vr.database_role IS NULL OR vr.status<>'active' OR NOT EXISTS (
                     SELECT 1
                       FROM pg_catalog.pg_roles attestor
                       JOIN pg_catalog.pg_roles capability
                         ON capability.rolname=vr.database_role
                      WHERE attestor.rolname=v.execution_principal
                        AND attestor.rolcanlogin
                        AND pg_has_role(attestor.oid,capability.oid,'MEMBER')
                   )
                 )
            ) THEN
                RAISE EXCEPTION 'VIDEO_CANON_RESTORE_ATTESTOR_REVOKED' USING ERRCODE='42501';
            END IF;
            IF v_prior_policy.status<>'active'
               OR v_prior_policy.valid_from>clock_timestamp()
               OR (v_prior_policy.valid_to IS NOT NULL
                   AND v_prior_policy.valid_to<=clock_timestamp()) THEN
                RAISE EXCEPTION 'VIDEO_CANON_RESTORE_SOURCE_POLICY_INACTIVE' USING ERRCODE='23514';
            END IF;
        END IF;
        -- Phase 1: validate every prelocked runtime target without mutation.
        FOR v_state IN SELECT value FROM jsonb_array_elements(v_promotion.superseded_runtime_state)
        LOOP
            BEGIN
                v_original_valid_to := NULLIF(v_state->>'valid_to','')::timestamptz;
            EXCEPTION WHEN OTHERS THEN
                RAISE EXCEPTION 'VIDEO_CANON_RESTORE_RUNTIME_STATE_INVALID' USING ERRCODE='23514';
            END;
            IF v_original_valid_to IS NOT NULL AND v_original_valid_to<=clock_timestamp() THEN
                RAISE EXCEPTION 'VIDEO_CANON_RESTORE_RUNTIME_TARGET_EXPIRED' USING ERRCODE='23514';
            END IF;
            SELECT * INTO v_prior_runtime FROM bidding.runtime_activation
             WHERE runtime_activation_id=(v_state->>'runtime_activation_id')::uuid
               AND canon_activation_id=v_promotion.superseded_canon_activation_id
               AND status='superseded' AND valid_to=v_new_canon.valid_from;
            IF NOT FOUND OR NOT (v_prior_runtime.runtime_activation_id=ANY(
                 v_promotion.superseded_runtime_activation_ids
               )) THEN
                RAISE EXCEPTION 'VIDEO_CANON_RESTORE_RUNTIME_TARGET_MISMATCH' USING ERRCODE='23514';
            END IF;
            IF NOT bidding.rule_passes_activation_gates(v_prior_runtime.rule_id) THEN
                RAISE EXCEPTION 'VIDEO_CANON_RESTORE_VALIDATION_GATES_FAILED' USING ERRCODE='23514';
            END IF;
            v_restored_runtime_ids := array_append(
              v_restored_runtime_ids,v_prior_runtime.runtime_activation_id
            );
        END LOOP;

        -- Capability membership is external to these tables; re-resolve every
        -- predecessor attestor after phase-one work and just before mutation.
        IF v_prior_promotion.video_canon_ai_promotion_receipt_id IS NOT NULL
           AND EXISTS (
              SELECT 1
                FROM bidding.video_canon_ai_verification v
                LEFT JOIN bidding.video_canon_verifier_registry vr
                  ON vr.verifier_family=v.verifier_family
                 AND v.check_id=ANY(vr.allowed_check_ids)
               WHERE v.analysis_candidate_id=v_prior_promotion.analysis_candidate_id
                 AND v.candidate_payload_hash=v_prior_promotion.candidate_payload_hash
                 AND v.verification_bundle_sha256=v_prior_promotion.verification_bundle_sha256
                 AND (
                   vr.database_role IS NULL OR vr.status<>'active' OR NOT EXISTS (
                     SELECT 1
                       FROM pg_catalog.pg_roles attestor
                       JOIN pg_catalog.pg_roles capability
                         ON capability.rolname=vr.database_role
                      WHERE attestor.rolname=v.execution_principal
                        AND attestor.rolcanlogin
                        AND pg_has_role(attestor.oid,capability.oid,'MEMBER')
                   )
                 )
           ) THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_ATTESTOR_REVOKED' USING ERRCODE='42501';
        END IF;

        -- Capture the exact mutation boundary once. Every final authority
        -- check and both outgoing valid_to writes use this same instant.
        v_revoked_at := clock_timestamp();
        IF v_prior_promotion.video_canon_ai_promotion_receipt_id IS NOT NULL
           AND (v_prior_policy.status<>'active'
             OR v_prior_policy.valid_from>v_revoked_at
             OR (v_prior_policy.valid_to IS NOT NULL
                 AND v_prior_policy.valid_to<=v_revoked_at)) THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_SOURCE_POLICY_INACTIVE' USING ERRCODE='23514';
        END IF;
        IF v_promotion.superseded_canon_valid_to IS NOT NULL
           AND v_promotion.superseded_canon_valid_to<=v_revoked_at THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_TARGET_EXPIRED' USING ERRCODE='23514';
        END IF;
        IF EXISTS (
          SELECT 1 FROM jsonb_array_elements(v_promotion.superseded_runtime_state) AS state(value)
           WHERE NULLIF(state.value->>'valid_to','')::timestamptz<=v_revoked_at
        ) THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_RUNTIME_TARGET_EXPIRED' USING ERRCODE='23514';
        END IF;
        IF (v_new_canon.valid_to IS NOT NULL
            AND v_new_canon.valid_to<=v_revoked_at)
           OR (v_new_runtime.valid_to IS NOT NULL
               AND v_new_runtime.valid_to<=v_revoked_at) THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_CURRENT_ACTIVATION_EXPIRED' USING ERRCODE='23514';
        END IF;

        -- Role membership is maintained outside the locked application
        -- tables. Re-resolve the predecessor's complete independent assurance
        -- set at the final write boundary, after every potentially long rule,
        -- source and runtime validation above.
        IF v_prior_promotion.video_canon_ai_promotion_receipt_id IS NOT NULL
           AND to_regclass('bidding.video_canon_promotion_delivery_receipt') IS NOT NULL THEN
          -- Keep optional 0323 relations in an inner PL/pgSQL statement. The
          -- outer branch is prepared without resolving relations that do not
          -- exist in a standalone 0322 installation.
          IF EXISTS (
              SELECT 1
                FROM bidding.video_canon_promotion_delivery_receipt dr
               WHERE dr.promotion_receipt_id=
                     v_prior_promotion.video_canon_ai_promotion_receipt_id
           ) AND NOT EXISTS (
              SELECT 1
                FROM bidding.video_canon_promotion_delivery_receipt dr
                JOIN bidding.video_canon_assurance_verdict i2
                  ON i2.analysis_candidate_id=dr.analysis_candidate_id
                 AND i2.candidate_payload_hash=dr.candidate_payload_hash
                 AND i2.verification_bundle_sha256=dr.verification_bundle_sha256
                 AND i2.assurance_set_sha256=dr.assurance_set_sha256
                 AND i2.assurance_level='I2'
                JOIN bidding.video_canon_assurance_verdict i3
                  ON i3.analysis_candidate_id=i2.analysis_candidate_id
                 AND i3.candidate_payload_hash=i2.candidate_payload_hash
                 AND i3.verification_bundle_sha256=i2.verification_bundle_sha256
                 AND i3.assurance_set_sha256=i2.assurance_set_sha256
                 AND i3.assurance_level='I3'
                JOIN bidding.video_canon_assurance_assignment a2
                  ON a2.video_canon_assurance_assignment_id=
                     i2.video_canon_assurance_assignment_id
                 AND a2.assurance_level='I2'
                 AND a2.assigned_principal=i2.execution_principal
                 AND a2.status='active'
                JOIN bidding.video_canon_assurance_assignment a3
                  ON a3.video_canon_assurance_assignment_id=
                     i3.video_canon_assurance_assignment_id
                 AND a3.assurance_level='I3'
                 AND a3.assigned_principal=i3.execution_principal
                 AND a3.status='active'
                JOIN bidding.video_canon_assurance_verifier_registry r2
                  ON r2.assurance_level='I2'
                 AND r2.capability_role='bridge_school_canon_i2_verifier'
                 AND r2.verifier_family=i2.verifier_family
                 AND r2.verifier_version=i2.verifier_version
                JOIN bidding.video_canon_assurance_verifier_registry r3
                  ON r3.assurance_level='I3'
                 AND r3.capability_role='bridge_school_canon_i3_verifier'
                 AND r3.verifier_family=i3.verifier_family
                 AND r3.verifier_version=i3.verifier_version
               WHERE dr.promotion_receipt_id=
                     v_prior_promotion.video_canon_ai_promotion_receipt_id
                 AND i2.verdict='VERIFIED_FOR_PROMOTION'
                 AND i3.verdict='VERIFIED_FOR_PROMOTION'
                 AND i2.provenance_verified AND i3.provenance_verified
                 AND i2.hidden_information_clear AND i3.hidden_information_clear
                 AND i2.profile_unambiguous AND i3.profile_unambiguous
                 AND NOT i2.canon_conflict AND NOT i3.canon_conflict
                 AND i2.deterministic AND i3.deterministic
                 AND i2.verifier_family<>i3.verifier_family
                 AND i2.execution_principal<>i3.execution_principal
                 AND i2.canon_snapshot_sha256=i3.canon_snapshot_sha256
                 AND i2.system_profile=i3.system_profile
                 AND i2.learner_level=i3.learner_level
                 AND EXISTS (
                   SELECT 1 FROM pg_catalog.pg_roles attestor
                    WHERE attestor.rolname=i2.execution_principal
                      AND attestor.rolcanlogin
                      AND pg_has_role(attestor.oid,r2.capability_role,'MEMBER')
                 )
                 AND EXISTS (
                   SELECT 1 FROM pg_catalog.pg_roles attestor
                    WHERE attestor.rolname=i3.execution_principal
                      AND attestor.rolcanlogin
                      AND pg_has_role(attestor.oid,r3.capability_role,'MEMBER')
                 )
           ) THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_I2_I3_ASSURANCE_REVOKED'
              USING ERRCODE='42501';
          END IF;
        END IF;

        -- Phase 2: all targets are locked and validated; mutate as one bounded set.
        UPDATE bidding.runtime_activation
           SET status='revoked',valid_to=v_revoked_at
         WHERE runtime_activation_id=v_new_runtime.runtime_activation_id;
        UPDATE public.canon_activation
           SET status='revoked',valid_to=v_revoked_at
         WHERE canon_activation_id=v_new_canon.canon_activation_id;
        UPDATE public.canon_activation
           SET status='active',valid_to=v_promotion.superseded_canon_valid_to
         WHERE canon_activation_id=v_promotion.superseded_canon_activation_id;
        UPDATE bidding.runtime_activation target
           SET status='active',
               valid_to=NULLIF(state.value->>'valid_to','')::timestamptz
          FROM jsonb_array_elements(v_promotion.superseded_runtime_state) AS state(value)
         WHERE target.runtime_activation_id=(state.value->>'runtime_activation_id')::uuid;
    ELSIF cardinality(v_promotion.superseded_runtime_activation_ids)<>0
       OR jsonb_array_length(v_promotion.superseded_runtime_state)<>0 THEN
        RAISE EXCEPTION 'VIDEO_CANON_RESTORE_UNEXPECTED_RUNTIME_STATE' USING ERRCODE='23514';
    END IF;

    IF v_promotion.superseded_canon_activation_id IS NULL THEN
        v_revoked_at := clock_timestamp();
        IF (v_new_canon.valid_to IS NOT NULL
            AND v_new_canon.valid_to<=v_revoked_at)
           OR (v_new_runtime.valid_to IS NOT NULL
               AND v_new_runtime.valid_to<=v_revoked_at) THEN
            RAISE EXCEPTION 'VIDEO_CANON_RESTORE_CURRENT_ACTIVATION_EXPIRED' USING ERRCODE='23514';
        END IF;
        UPDATE bidding.runtime_activation
           SET status='revoked',valid_to=v_revoked_at
         WHERE runtime_activation_id=v_new_runtime.runtime_activation_id;
        UPDATE public.canon_activation
           SET status='revoked',valid_to=v_revoked_at
         WHERE canon_activation_id=v_new_canon.canon_activation_id;
    END IF;

    INSERT INTO bidding.video_canon_ai_restore_receipt(
      video_canon_ai_promotion_receipt_id,school_id,
      revoked_canon_activation_id,revoked_runtime_activation_id,
      restored_canon_activation_id,restored_runtime_activation_ids,
      verification_bundle_sha256,restore_evidence_sha256,restored_by_principal
    ) VALUES (
      v_promotion.video_canon_ai_promotion_receipt_id,v_promotion.school_id,
      v_new_canon.canon_activation_id,v_new_runtime.runtime_activation_id,
      v_promotion.superseded_canon_activation_id,v_restored_runtime_ids,
      p_verification_bundle_sha256,p_restore_evidence_sha256,session_user
    ) RETURNING * INTO v_existing;
    RETURN v_existing.video_canon_ai_restore_receipt_id;
END $$;

REVOKE ALL ON FUNCTION bidding.activate_ai_verified_video_canon(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bidding.activate_ai_verified_video_canon(uuid,uuid,text)
  TO bridge_school_canon_promoter;
REVOKE ALL ON FUNCTION bidding.restore_ai_verified_video_canon(uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bidding.restore_ai_verified_video_canon(uuid,text,text)
  TO bridge_school_canon_restorer;
REVOKE ALL ON FUNCTION bidding.video_canon_runtime_scope_key(text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.get_school_runtime_rule_catalog(uuid,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bidding.get_school_runtime_rule_catalog(uuid,text,text,text)
  TO bridge_school_reader,bridge_school_app,bridge_school_worker;

GRANT SELECT ON bidding.video_canon_source_policy,bidding.video_canon_ai_verification_bundle,
  bidding.video_canon_verifier_registry,
  bidding.video_canon_ai_verification,
  bidding.video_canon_ai_promotion_receipt,bidding.video_canon_ai_restore_receipt,
  bidding.video_correction_review_receipt
  TO bridge_school_reader;
REVOKE ALL ON FUNCTION bidding.is_complete_bridge_hand(text),
  bidding.is_video_canon_semantic_confidence_eligible(jsonb),
  bidding.video_canon_semantic_identity_sha256(jsonb),
  bidding.video_canon_runtime_scope_key(text,text,text),
  bidding.video_canon_rule_test_state_sha256(uuid),
  bidding.video_canon_rule_restore_sha256(uuid),
  bidding.current_school_canon_snapshot_sha256(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bidding.video_canon_rule_test_state_sha256(uuid) TO
  bridge_school_canon_verifier;
GRANT EXECUTE ON FUNCTION bidding.current_school_canon_snapshot_sha256(uuid) TO
  bridge_school_canon_verifier,bridge_school_canon_semantic_verifier,
  bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,
  bridge_school_canon_control_verifier;
GRANT SELECT ON bidding.video_correction_review_receipt TO bridge_school_worker;
GRANT INSERT ON bidding.video_correction_review_receipt TO bridge_school_canon_control_verifier;
GRANT INSERT ON bidding.video_canon_ai_verification_bundle TO bridge_school_canon_verifier;
GRANT SELECT ON bidding.video_canon_bound_candidate,
  bidding.video_canon_verifier_registry TO
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE SELECT ON public.analysis_candidate,bidding.video_canon_ai_verification_bundle
  FROM bridge_school_canon_verifier,bridge_school_canon_semantic_verifier,
  bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,
  bridge_school_canon_control_verifier;
GRANT SELECT ON bidding.video_canon_verifier_registry TO bridge_school_canon_verifier;
GRANT INSERT ON bidding.video_canon_ai_verification TO
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON bidding.video_canon_source_policy,
  bidding.video_canon_verifier_registry,bidding.video_canon_ai_promotion_receipt,
  bidding.video_canon_ai_restore_receipt,bidding.video_correction_review_receipt
  FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_canon_verifier,
  bridge_school_canon_promoter,bridge_school_canon_restorer;
REVOKE UPDATE,DELETE,TRUNCATE ON bidding.video_correction_review_receipt
  FROM bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE INSERT ON bidding.video_correction_review_receipt FROM
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_promoter;
REVOKE UPDATE,DELETE,TRUNCATE ON bidding.video_canon_ai_verification_bundle,bidding.video_canon_ai_verification
  FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_canon_verifier,
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier,
  bridge_school_canon_promoter,bridge_school_canon_restorer;
REVOKE INSERT ON bidding.video_canon_ai_verification FROM bridge_school_canon_verifier,
  bridge_school_canon_promoter,bridge_school_canon_restorer;
REVOKE INSERT ON bidding.video_canon_ai_verification_bundle FROM
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier,
  bridge_school_canon_promoter,bridge_school_canon_restorer;
REVOKE ALL ON FUNCTION bidding.contains_forbidden_hidden_value(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.validate_video_canon_verification_bundle() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.validate_video_canon_verification() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.video_canon_drive_source_id(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_bound_video_canon_candidate() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_promoted_video_canon_source_binding() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_promoted_video_canon_source_identity() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_promoted_video_canon_provider_identity() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_promoted_video_canon_knowledge_version() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_promoted_video_canon_knowledge_item() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_promoted_video_canon_rule() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_promoted_video_canon_rule_test() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_superseded_video_canon_rule_test_run() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_video_canon_source_policy_lifecycle() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_video_canon_verifier_registry_lifecycle() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.validate_video_correction_review_receipt() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION bidding.validate_video_canon_verification_bundle(),
  bidding.video_canon_drive_source_id(text),
  bidding.guard_bound_video_canon_candidate(),bidding.guard_promoted_video_canon_source_binding(),
  bidding.guard_promoted_video_canon_source_identity(),
  bidding.guard_promoted_video_canon_provider_identity(),
  bidding.guard_promoted_video_canon_knowledge_version(),
  bidding.guard_promoted_video_canon_knowledge_item(),
  bidding.guard_promoted_video_canon_rule(),
  bidding.guard_promoted_video_canon_rule_test(),
  bidding.guard_superseded_video_canon_rule_test_run(),
  bidding.guard_video_canon_source_policy_lifecycle(),
  bidding.guard_video_canon_verifier_registry_lifecycle()
  FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_canon_verifier,
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier,
  bridge_school_canon_promoter,bridge_school_canon_restorer;
REVOKE EXECUTE ON FUNCTION bidding.validate_video_correction_review_receipt()
  FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_canon_verifier,
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier,
  bridge_school_canon_promoter,bridge_school_canon_restorer;
REVOKE EXECUTE ON FUNCTION bidding.validate_video_canon_verification()
  FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_canon_verifier,
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier,
  bridge_school_canon_promoter,bridge_school_canon_restorer;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0322_workflow_video_canon_ai_promotion') ON CONFLICT DO NOTHING;
COMMIT;
