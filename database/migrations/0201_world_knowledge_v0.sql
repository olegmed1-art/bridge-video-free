-- WORLD knowledge extension. Depends on 0200; creates no SCHOOL CANON activation.
BEGIN;

CREATE OR REPLACE FUNCTION bidding.contains_nonpublic_card_material(payload jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
WITH RECURSIVE walk(value) AS (
  SELECT payload
  UNION ALL
  SELECT child.value FROM walk w CROSS JOIN LATERAL (
    SELECT value FROM jsonb_array_elements(CASE WHEN jsonb_typeof(w.value)='array' THEN w.value ELSE '[]'::jsonb END)
    UNION ALL
    SELECT value FROM jsonb_each(CASE WHEN jsonb_typeof(w.value)='object' THEN w.value ELSE '{}'::jsonb END)
  ) child
), keys AS (
  SELECT lower(regexp_replace(k.key,'[^a-z0-9]','','g')) AS key
  FROM walk w CROSS JOIN LATERAL jsonb_object_keys(CASE WHEN jsonb_typeof(w.value)='object' THEN w.value ELSE '{}'::jsonb END) k(key)
)
SELECT EXISTS (SELECT 1 FROM keys WHERE key IN
 ('deal','fulldeal','hand','hands','cards','card','holding','holdings','partnerhand','opponenthand','hiddenhand','hiddencards'));
$$;

CREATE OR REPLACE FUNCTION bidding.valid_acting_hand(payload jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
SELECT jsonb_typeof(payload)='object'
 AND NOT EXISTS (SELECT 1 FROM jsonb_object_keys(payload) k WHERE k NOT IN ('cards','hcp','shape'))
 AND jsonb_typeof(payload->'cards')='array'
 AND jsonb_array_length(payload->'cards')=13
 AND (SELECT count(*)=13 AND count(DISTINCT value)=13
      FROM jsonb_array_elements_text(payload->'cards') c(value)
      WHERE value ~ '^[2-9TJQKA][CDHS]$')
 AND (payload->'hcp' IS NULL OR jsonb_typeof(payload->'hcp')='number')
 AND (payload->'shape' IS NULL OR jsonb_typeof(payload->'shape')='array');
$$;

CREATE OR REPLACE FUNCTION bidding.valid_public_robot_payload(kind text, payload jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE allowed text[];
BEGIN
  IF jsonb_typeof(payload) <> 'object' OR bidding.contains_forbidden_hidden_key(payload)
     OR bidding.contains_nonpublic_card_material(payload) THEN RETURN false; END IF;
  allowed := CASE kind
    WHEN 'auction' THEN ARRAY['calls','dealer','vulnerability','alerts','explanations']
    WHEN 'context' THEN ARRAY['board_number','dealer','vulnerability','scoring','acting_seat','public_inferences']
    WHEN 'response' THEN ARRAY['bid','action','meaning','forcing','alert','confidence','alternatives','explanation','status','engine_output']
    WHEN 'interpretation' THEN ARRAY['bid','action','meaning','forcing','alert','confidence','alternatives','explanation','status','public_inferences']
    WHEN 'trace' THEN ARRAY['mode','request_id','engine_key','engine_version','model_hash','configuration_hash','input_fingerprint','started_at','completed_at','steps','raw_response_sha256']
    ELSE ARRAY[]::text[] END;
  RETURN NOT EXISTS (SELECT 1 FROM jsonb_object_keys(payload) k WHERE NOT (k = ANY(allowed)));
END $$;

CREATE TABLE bidding.world_intake_batch (
 world_intake_batch_id uuid PRIMARY KEY DEFAULT uuidv7(), school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
 batch_key text NOT NULL CHECK (btrim(batch_key)<>''), manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
 source_count integer NOT NULL CHECK(source_count>=0), author_count integer NOT NULL CHECK(author_count>=0),
 audit_count integer NOT NULL CHECK(audit_count>=0), queue_count integer NOT NULL CHECK(queue_count>=0),
 status text NOT NULL DEFAULT 'staged' CHECK(status IN ('staged','validated','rejected')),
 metadata jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(metadata)='object' AND NOT bidding.contains_forbidden_hidden_key(metadata)),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(school_id,batch_key));

CREATE TABLE bidding.world_robot (
 world_robot_id uuid PRIMARY KEY DEFAULT uuidv7(), robot_key text NOT NULL UNIQUE CHECK(btrim(robot_key)<>''),
 display_name text NOT NULL CHECK(btrim(display_name)<>''), engine_version text NOT NULL CHECK(btrim(engine_version)<>''),
 model_hash text NOT NULL CHECK(model_hash ~ '^[0-9a-f]{64}$'),
 license_boundary jsonb NOT NULL CHECK(jsonb_typeof(license_boundary)='object' AND license_boundary <> '{}'::jsonb
   AND license_boundary ?& ARRAY['license_name','license_version','usage_scope','api_boundary']
   AND btrim(license_boundary->>'license_name')<>'' AND btrim(license_boundary->>'license_version')<>''
   AND btrim(license_boundary->>'usage_scope')<>'' AND btrim(license_boundary->>'api_boundary')<>''
   AND NOT bidding.contains_forbidden_hidden_key(license_boundary)),
 status text NOT NULL DEFAULT 'research' CHECK(status IN ('research','available','retired')), created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE bidding.world_robot_configuration (
 world_robot_configuration_id uuid PRIMARY KEY DEFAULT uuidv7(), world_robot_id uuid NOT NULL REFERENCES bidding.world_robot ON DELETE RESTRICT,
 configuration_hash text NOT NULL CHECK(configuration_hash ~ '^[0-9a-f]{64}$'),
 convention_card jsonb NOT NULL CHECK(jsonb_typeof(convention_card)='object' AND convention_card<>'{}'::jsonb AND NOT bidding.contains_forbidden_hidden_key(convention_card)),
 configuration jsonb NOT NULL CHECK(jsonb_typeof(configuration)='object' AND configuration<>'{}'::jsonb AND NOT bidding.contains_forbidden_hidden_key(configuration)),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(world_robot_id,configuration_hash));

CREATE TABLE bidding.world_robot_decision (
 world_robot_decision_id uuid PRIMARY KEY DEFAULT uuidv7(), school_id uuid NOT NULL REFERENCES public.school ON DELETE RESTRICT,
 world_robot_configuration_id uuid NOT NULL REFERENCES bidding.world_robot_configuration ON DELETE RESTRICT,
 decision_mode text NOT NULL CHECK(decision_mode IN ('ROBOT_RECONSTRUCTED_SURFACE','ROBOT_LIVE_DECISION')),
 acting_seat text NOT NULL CHECK(acting_seat IN ('N','E','S','W')), acting_hand jsonb NOT NULL CHECK(bidding.valid_acting_hand(acting_hand)),
 public_auction jsonb NOT NULL CHECK(bidding.valid_public_robot_payload('auction',public_auction)),
 public_context jsonb NOT NULL CHECK(bidding.valid_public_robot_payload('context',public_context)),
 raw_response jsonb NOT NULL CHECK(bidding.valid_public_robot_payload('response',raw_response)),
 interpretation jsonb NOT NULL CHECK(bidding.valid_public_robot_payload('interpretation',interpretation)),
 confidence text NOT NULL CHECK(confidence IN ('low','medium','high','verified','reproducible')),
 decision_trace jsonb NOT NULL CHECK(bidding.valid_public_robot_payload('trace',decision_trace)), recorded_at timestamptz NOT NULL DEFAULT now());

CREATE OR REPLACE FUNCTION bidding.validate_world_robot_decision()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r bidding.world_robot%ROWTYPE; c bidding.world_robot_configuration%ROWTYPE;
BEGIN
 SELECT * INTO c FROM bidding.world_robot_configuration WHERE world_robot_configuration_id=NEW.world_robot_configuration_id;
 SELECT * INTO r FROM bidding.world_robot WHERE world_robot_id=c.world_robot_id;
 IF NOT (NEW.decision_trace ?& ARRAY['mode','request_id','engine_key','engine_version','model_hash','configuration_hash','input_fingerprint','started_at','completed_at','steps','raw_response_sha256'])
    OR NEW.decision_trace->>'mode'<>NEW.decision_mode OR NEW.decision_trace->>'engine_key'<>r.robot_key
    OR NEW.decision_trace->>'engine_version'<>r.engine_version OR NEW.decision_trace->>'model_hash'<>r.model_hash
    OR NEW.decision_trace->>'configuration_hash'<>c.configuration_hash
    OR NEW.decision_trace->>'raw_response_sha256'<>encode(digest(NEW.raw_response::text,'sha256'),'hex')
    OR jsonb_typeof(NEW.decision_trace->'steps')<>'array' OR jsonb_array_length(NEW.decision_trace->'steps')=0
 THEN RAISE EXCEPTION 'BID_WORLD_ROBOT_TRACE_INCOMPLETE_OR_UNPINNED' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER world_robot_decision_guard BEFORE INSERT ON bidding.world_robot_decision FOR EACH ROW EXECUTE FUNCTION bidding.validate_world_robot_decision();
CREATE TRIGGER world_robot_decision_append_only BEFORE UPDATE OR DELETE ON bidding.world_robot_decision FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();

CREATE TABLE bidding.world_resolution_trace (
 world_resolution_trace_id uuid PRIMARY KEY DEFAULT uuidv7(), school_id uuid NOT NULL REFERENCES public.school ON DELETE RESTRICT,
 request_fingerprint text NOT NULL CHECK(btrim(request_fingerprint)<>''), system_profile_key text NOT NULL CHECK(btrim(system_profile_key)<>''),
 system_version text NOT NULL CHECK(btrim(system_version)<>''), learner_level text NOT NULL CHECK(btrim(learner_level)<>''),
 effective_at timestamptz NOT NULL, auction_context_id text NOT NULL CHECK(btrim(auction_context_id)<>''),
 canon_outcome text NOT NULL CHECK(canon_outcome IN ('CANON_MATCH','CANON_CONFLICT','CANON_GAP')),
 world_outcome text CHECK(world_outcome IS NULL OR world_outcome IN ('WORLD_FALLBACK','WORLD_CONFLICT','UNRESOLVED_GAP')),
 canon_rule_ids uuid[] NOT NULL DEFAULT '{}', world_rule_ids uuid[] NOT NULL DEFAULT '{}',
 selected_world_rule_id uuid REFERENCES bidding.rule ON DELETE RESTRICT, knowledge_gap_id uuid REFERENCES public.knowledge_gap ON DELETE RESTRICT,
 trace jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(trace)='object' AND NOT bidding.contains_forbidden_hidden_key(trace)),
 resolver_version text NOT NULL CHECK(btrim(resolver_version)<>''), recorded_at timestamptz NOT NULL DEFAULT now(),
 CHECK((canon_outcome='CANON_GAP')=(knowledge_gap_id IS NOT NULL)), CHECK((canon_outcome='CANON_GAP')=(world_outcome IS NOT NULL)),
 CHECK((world_outcome='WORLD_FALLBACK')=(selected_world_rule_id IS NOT NULL)),
 CHECK(selected_world_rule_id IS NULL OR selected_world_rule_id=ANY(world_rule_ids)), UNIQUE(school_id,request_fingerprint));

CREATE OR REPLACE FUNCTION bidding.validate_world_resolution_trace()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.knowledge_gap_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.knowledge_gap g WHERE g.knowledge_gap_id=NEW.knowledge_gap_id AND g.school_id=NEW.school_id)
 THEN RAISE EXCEPTION 'BID_WORLD_TRACE_GAP_SCHOOL_MISMATCH' USING ERRCODE='23514'; END IF;
 IF EXISTS (
   SELECT 1 FROM unnest(NEW.canon_rule_ids||NEW.world_rule_ids) x(rule_id)
   JOIN bidding.rule br ON br.rule_id=x.rule_id JOIN public.knowledge_version kv ON kv.knowledge_version_id=br.knowledge_version_id
   WHERE br.school_id<>NEW.school_id OR kv.bidding_system_key<>NEW.system_profile_key OR kv.method_version<>NEW.system_version
      OR COALESCE(kv.level_scope->>'level','')<>NEW.learner_level
      OR NEW.effective_at<COALESCE(kv.effective_from,'-infinity') OR NEW.effective_at>=COALESCE(kv.effective_to,'infinity')
      OR COALESCE(br.auction_pattern->>'context_id','')<>NEW.auction_context_id
 ) THEN RAISE EXCEPTION 'BID_WORLD_TRACE_PROFILE_MISMATCH' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM unnest(NEW.canon_rule_ids) x(rule_id) LEFT JOIN bidding.rule br ON br.rule_id=x.rule_id
   LEFT JOIN public.knowledge_version kv ON kv.knowledge_version_id=br.knowledge_version_id
   WHERE br.school_id IS NULL OR kv.authority_class<>'school_canon')
 THEN RAISE EXCEPTION 'BID_WORLD_TRACE_CANON_CANDIDATE_NOT_CANON' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM unnest(NEW.world_rule_ids) x(rule_id) LEFT JOIN bidding.rule br ON br.rule_id=x.rule_id
   LEFT JOIN public.knowledge_version kv ON kv.knowledge_version_id=br.knowledge_version_id
   WHERE br.school_id IS NULL OR kv.authority_class<>'external')
 THEN RAISE EXCEPTION 'BID_WORLD_TRACE_WORLD_CANDIDATE_NOT_EXTERNAL' USING ERRCODE='23514'; END IF;
 IF NEW.canon_outcome<>'CANON_GAP' AND (NEW.selected_world_rule_id IS NOT NULL OR cardinality(NEW.world_rule_ids)<>0)
 THEN RAISE EXCEPTION 'BID_WORLD_TRACE_WORLD_BEFORE_CANON_GAP' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER world_resolution_trace_guard BEFORE INSERT ON bidding.world_resolution_trace FOR EACH ROW EXECUTE FUNCTION bidding.validate_world_resolution_trace();
CREATE TRIGGER world_resolution_trace_append_only BEFORE UPDATE OR DELETE ON bidding.world_resolution_trace FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();

GRANT INSERT(school_id,batch_key,manifest_sha256,source_count,author_count,audit_count,queue_count,metadata) ON bidding.world_intake_batch TO bridge_school_worker;
GRANT INSERT ON bidding.world_robot_decision TO bridge_school_worker;
GRANT INSERT ON bidding.world_resolution_trace TO bridge_school_app,bridge_school_worker;
REVOKE ALL ON FUNCTION bidding.validate_world_resolution_trace() FROM PUBLIC,bridge_school_reader,bridge_school_app,bridge_school_worker;
REVOKE ALL ON FUNCTION bidding.validate_world_robot_decision() FROM PUBLIC,bridge_school_reader,bridge_school_app,bridge_school_worker;
INSERT INTO public.schema_migration(migration_key) VALUES('0201_world_knowledge_v0') ON CONFLICT DO NOTHING;
COMMIT;
