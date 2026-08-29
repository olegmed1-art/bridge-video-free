-- WORLD knowledge extension. Depends on 0200_bidding_knowledge_v0; no SCHOOL
-- CANON activation is created or modified by this migration.
BEGIN;

CREATE TABLE bidding.world_intake_batch (
    world_intake_batch_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    batch_key text NOT NULL CHECK (btrim(batch_key) <> ''),
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    source_count integer NOT NULL CHECK (source_count >= 0),
    author_count integer NOT NULL CHECK (author_count >= 0),
    audit_count integer NOT NULL CHECK (audit_count >= 0),
    queue_count integer NOT NULL CHECK (queue_count >= 0),
    status text NOT NULL DEFAULT 'staged' CHECK (status IN ('staged','validated','rejected')),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata)='object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (school_id, batch_key),
    CHECK (NOT bidding.contains_forbidden_hidden_key(metadata))
);

CREATE TABLE bidding.world_robot (
    world_robot_id uuid PRIMARY KEY DEFAULT uuidv7(),
    robot_key text NOT NULL UNIQUE CHECK (btrim(robot_key) <> ''),
    display_name text NOT NULL CHECK (btrim(display_name) <> ''),
    engine_version text NOT NULL CHECK (btrim(engine_version) <> ''),
    model_hash text,
    license_boundary jsonb NOT NULL CHECK (jsonb_typeof(license_boundary)='object'),
    status text NOT NULL DEFAULT 'research' CHECK (status IN ('research','available','retired')),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT bidding.contains_forbidden_hidden_key(license_boundary))
);

CREATE TABLE bidding.world_robot_configuration (
    world_robot_configuration_id uuid PRIMARY KEY DEFAULT uuidv7(),
    world_robot_id uuid NOT NULL REFERENCES bidding.world_robot(world_robot_id) ON DELETE RESTRICT,
    configuration_hash text NOT NULL CHECK (configuration_hash ~ '^[0-9a-f]{64}$'),
    convention_card jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(convention_card)='object'),
    configuration jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(configuration)='object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (world_robot_id, configuration_hash),
    CHECK (NOT bidding.contains_forbidden_hidden_key(convention_card)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(configuration))
);

CREATE TABLE bidding.world_robot_decision (
    world_robot_decision_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    world_robot_configuration_id uuid NOT NULL REFERENCES bidding.world_robot_configuration(world_robot_configuration_id) ON DELETE RESTRICT,
    decision_mode text NOT NULL CHECK (decision_mode IN ('ROBOT_RECONSTRUCTED_SURFACE','ROBOT_LIVE_DECISION')),
    acting_seat text NOT NULL CHECK (acting_seat IN ('N','E','S','W')),
    acting_hand jsonb NOT NULL CHECK (jsonb_typeof(acting_hand)='object'),
    public_auction jsonb NOT NULL CHECK (jsonb_typeof(public_auction)='object'),
    public_context jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(public_context)='object'),
    raw_response jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(raw_response) IN ('object','array','string')),
    interpretation jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(interpretation)='object'),
    confidence text NOT NULL CHECK (confidence IN ('low','medium','high','verified','reproducible')),
    decision_trace jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(decision_trace)='object'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT bidding.contains_forbidden_hidden_key(acting_hand)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(public_auction)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(public_context)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(raw_response)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(interpretation)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(decision_trace))
);

CREATE TABLE bidding.world_resolution_trace (
    world_resolution_trace_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    request_fingerprint text NOT NULL CHECK (btrim(request_fingerprint) <> ''),
    canon_outcome text NOT NULL CHECK (canon_outcome IN ('CANON_MATCH','CANON_CONFLICT','CANON_GAP')),
    world_outcome text CHECK (world_outcome IS NULL OR world_outcome IN ('WORLD_FALLBACK','WORLD_CONFLICT','UNRESOLVED_GAP')),
    canon_rule_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    world_rule_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    selected_world_rule_id uuid REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
    knowledge_gap_id uuid REFERENCES public.knowledge_gap(knowledge_gap_id) ON DELETE RESTRICT,
    trace jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(trace)='object'),
    resolver_version text NOT NULL CHECK (btrim(resolver_version) <> ''),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((canon_outcome='CANON_GAP') = (knowledge_gap_id IS NOT NULL)),
    CHECK ((canon_outcome='CANON_GAP') = (world_outcome IS NOT NULL)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(trace)),
    UNIQUE (school_id, request_fingerprint)
);

CREATE OR REPLACE FUNCTION bidding.validate_world_resolution_trace()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.knowledge_gap_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.knowledge_gap g WHERE g.knowledge_gap_id=NEW.knowledge_gap_id AND g.school_id=NEW.school_id) THEN
    RAISE EXCEPTION 'BID_WORLD_TRACE_GAP_SCHOOL_MISMATCH' USING ERRCODE='23514';
  END IF;
  IF NEW.selected_world_rule_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM bidding.rule r JOIN public.knowledge_version kv ON kv.knowledge_version_id=r.knowledge_version_id
     WHERE r.rule_id=NEW.selected_world_rule_id AND r.school_id=NEW.school_id AND kv.authority_class='external'
  ) THEN RAISE EXCEPTION 'BID_WORLD_TRACE_SELECTED_RULE_NOT_EXTERNAL' USING ERRCODE='23514'; END IF;
  IF EXISTS (
    SELECT 1 FROM unnest(NEW.canon_rule_ids) AS x(rule_id)
    LEFT JOIN bidding.rule r ON r.rule_id=x.rule_id
    LEFT JOIN public.knowledge_version kv ON kv.knowledge_version_id=r.knowledge_version_id
    WHERE r.school_id IS NULL OR r.school_id <> NEW.school_id OR kv.authority_class <> 'school_canon'
  ) THEN RAISE EXCEPTION 'BID_WORLD_TRACE_CANON_CANDIDATE_NOT_CANON' USING ERRCODE='23514'; END IF;
  IF EXISTS (
    SELECT 1 FROM unnest(NEW.world_rule_ids) AS x(rule_id)
    LEFT JOIN bidding.rule r ON r.rule_id=x.rule_id
    LEFT JOIN public.knowledge_version kv ON kv.knowledge_version_id=r.knowledge_version_id
    WHERE r.school_id IS NULL OR r.school_id <> NEW.school_id OR kv.authority_class <> 'external'
  ) THEN RAISE EXCEPTION 'BID_WORLD_TRACE_WORLD_CANDIDATE_NOT_EXTERNAL' USING ERRCODE='23514'; END IF;
  IF NEW.canon_outcome <> 'CANON_GAP' AND (NEW.selected_world_rule_id IS NOT NULL OR cardinality(NEW.world_rule_ids) <> 0) THEN
    RAISE EXCEPTION 'BID_WORLD_TRACE_WORLD_BEFORE_CANON_GAP' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER world_resolution_trace_guard BEFORE INSERT ON bidding.world_resolution_trace FOR EACH ROW EXECUTE FUNCTION bidding.validate_world_resolution_trace();
CREATE TRIGGER world_resolution_trace_append_only BEFORE UPDATE OR DELETE ON bidding.world_resolution_trace FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();

GRANT INSERT (school_id,batch_key,manifest_sha256,source_count,author_count,audit_count,queue_count,metadata) ON bidding.world_intake_batch TO bridge_school_worker;
GRANT INSERT ON bidding.world_robot_decision TO bridge_school_worker;
GRANT INSERT ON bidding.world_resolution_trace TO bridge_school_app, bridge_school_worker;
REVOKE ALL ON FUNCTION bidding.validate_world_resolution_trace() FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker;
INSERT INTO public.schema_migration(migration_key) VALUES ('0201_world_knowledge_v0') ON CONFLICT DO NOTHING;
COMMIT;
