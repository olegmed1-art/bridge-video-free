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

CREATE OR REPLACE FUNCTION bidding.contains_card_token(payload jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
WITH RECURSIVE walk(value,key_name) AS (
  SELECT payload,NULL::text
  UNION ALL
  SELECT child.value,child.key_name FROM walk w CROSS JOIN LATERAL (
    SELECT value,w.key_name FROM jsonb_array_elements(CASE WHEN jsonb_typeof(w.value)='array' THEN w.value ELSE '[]'::jsonb END)
    UNION ALL
    SELECT value,lower(regexp_replace(key,'[^a-z0-9]','','g'))
      FROM jsonb_each(CASE WHEN jsonb_typeof(w.value)='object' THEN w.value ELSE '{}'::jsonb END)
  ) child
)
SELECT EXISTS (SELECT 1 FROM walk WHERE jsonb_typeof(value)='string'
 AND NOT (
   (key_name IN ('bid','action','calls')
    AND value #>> '{}' ~* '^(pass|p|x|xx|[1-7](c|d|h|s|nt|n))$')
   OR (key_name IN ('inputfingerprint','rawresponsesha256','inputhash','outputhash','modelhash','configurationhash')
       AND value #>> '{}' ~ '^[0-9a-f]{64}$')
 )
 AND (
   value #>> '{}' ~* '(^|[^a-z0-9])(10|[2-9tjqka])([cdhs]|♣|♦|♥|♠)([^a-z0-9]|$)'
   OR value #>> '{}' ~* '([♣♦♥♠](10|[2-9tjqka]){1,13}|(10|[2-9tjqka]){1,13}[♣♦♥♠])'
   OR value #>> '{}' ~* '(^|[^a-z0-9])([cdhs](10|[2-9tjqka]){2,13}|(10|[2-9tjqka]){2,13}[cdhs])([^a-z0-9]|$)'
   OR value #>> '{}' ~* '(^|[^a-z0-9])(([cdhs]|♣|♦|♥|♠)[[:space:]:-]*(10|[2-9tjqka]){2,13}|(10|[2-9tjqka]){2,13}[[:space:]:-]*([cdhs]|♣|♦|♥|♠))([^a-z0-9]|$)'
   OR regexp_replace(
        regexp_replace(translate(value #>> '{}','♣♦♥♠','CDHS'),'10','T','gi'),
        '[^a-z0-9]','','gi')
      ~* '^([2-9TJQKA][CDHS])+$'
   OR value #>> '{}' ~* '(^|[^a-z0-9])([2-9tjqka]{0,13}\.){3}[2-9tjqka]{0,13}([^a-z0-9]|$)'
));
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
 AND (payload->'shape' IS NULL OR (
   jsonb_typeof(payload->'shape')='array'
   AND jsonb_array_length(payload->'shape')=4
   AND (SELECT count(*)=4
          AND bool_and(jsonb_typeof(value)='number' AND value #>> '{}' ~ '^(0|[1-9]|1[0-3])$')
          AND sum(CASE WHEN jsonb_typeof(value)='number' AND value #>> '{}' ~ '^(0|[1-9]|1[0-3])$'
                       THEN (value #>> '{}')::integer ELSE 100 END)=13
        FROM jsonb_array_elements(payload->'shape'))
   AND NOT EXISTS (
     SELECT 1
       FROM jsonb_array_elements(payload->'shape') WITH ORDINALITY shape(value,ord)
      WHERE (value #>> '{}')::integer IS DISTINCT FROM
        (SELECT count(*) FROM jsonb_array_elements_text(payload->'cards') card(value)
          WHERE right(card.value,1)=((ARRAY['C','D','H','S'])[shape.ord]))
   )
 ));
$$;

CREATE OR REPLACE FUNCTION bidding.valid_public_robot_payload(kind text, payload jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE allowed text[];
BEGIN
  IF jsonb_typeof(payload) <> 'object' OR bidding.contains_forbidden_hidden_key(payload)
     OR bidding.contains_nonpublic_card_material(payload) OR bidding.contains_card_token(payload) THEN RETURN false; END IF;
  allowed := CASE kind
    WHEN 'auction' THEN ARRAY['calls','dealer','vulnerability','alerts','explanations']
    WHEN 'context' THEN ARRAY['board_number','dealer','vulnerability','scoring','acting_seat']
    WHEN 'response' THEN ARRAY['bid','action','meaning','forcing','alert','confidence','explanation','status']
    WHEN 'interpretation' THEN ARRAY['bid','action','meaning','forcing','alert','confidence','explanation','status']
    WHEN 'trace' THEN ARRAY['mode','request_id','engine_key','engine_version','model_hash','configuration_hash','input_fingerprint','started_at','completed_at','steps','raw_response_sha256']
    ELSE ARRAY[]::text[] END;
  IF EXISTS (SELECT 1 FROM jsonb_object_keys(payload) k WHERE NOT (k = ANY(allowed))) THEN RETURN false; END IF;
  IF kind='auction' THEN
    RETURN jsonb_typeof(payload->'calls')='array'
      AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'calls') v
                       WHERE jsonb_typeof(v)<>'string'
                          OR v #>> '{}' !~* '^(pass|p|x|xx|[1-7](c|d|h|s|nt|n))$')
      AND NOT EXISTS (SELECT 1 FROM jsonb_each(payload) e
                       WHERE e.key<>'calls'
                         AND jsonb_typeof(e.value) NOT IN ('string','boolean','null'));
  ELSIF kind IN ('context','response','interpretation') THEN
    RETURN NOT EXISTS (SELECT 1 FROM jsonb_each(payload) e WHERE jsonb_typeof(e.value) NOT IN ('string','number','boolean','null'));
  END IF;
  RETURN true;
END $$;

CREATE TABLE bidding.world_intake_batch (
 world_intake_batch_id uuid PRIMARY KEY DEFAULT uuidv7(), school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
 batch_key text NOT NULL CHECK (btrim(batch_key)<>''), manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
 source_count integer NOT NULL CHECK(source_count>=0), author_count integer NOT NULL CHECK(author_count>=0),
 audit_count integer NOT NULL CHECK(audit_count>=0), queue_count integer NOT NULL CHECK(queue_count>=0),
 status text NOT NULL DEFAULT 'staged' CHECK(status IN ('staged','validated','rejected')),
 metadata jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(metadata)='object' AND NOT bidding.contains_forbidden_hidden_key(metadata)),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(school_id,batch_key));

CREATE TABLE bidding.world_canon_gap_binding (
 knowledge_gap_id uuid PRIMARY KEY REFERENCES public.knowledge_gap ON DELETE RESTRICT,
 school_id uuid NOT NULL REFERENCES public.school ON DELETE RESTRICT,
 request_fingerprint text NOT NULL CHECK(btrim(request_fingerprint)<>''),
 system_profile_key text NOT NULL CHECK(btrim(system_profile_key)<>''), system_version text NOT NULL CHECK(btrim(system_version)<>''),
 learner_level text NOT NULL CHECK(btrim(learner_level)<>''), auction_context_id text NOT NULL CHECK(btrim(auction_context_id)<>''),
 effective_at timestamptz NOT NULL, profile_fingerprint text NOT NULL CHECK(profile_fingerprint ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(school_id,request_fingerprint));
CREATE OR REPLACE FUNCTION bidding.validate_world_canon_gap_binding()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.knowledge_gap g WHERE g.knowledge_gap_id=NEW.knowledge_gap_id AND g.school_id=NEW.school_id)
 THEN RAISE EXCEPTION 'BID_WORLD_GAP_BINDING_SCHOOL_MISMATCH' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER world_canon_gap_binding_guard BEFORE INSERT ON bidding.world_canon_gap_binding
FOR EACH ROW EXECUTE FUNCTION bidding.validate_world_canon_gap_binding();
CREATE TRIGGER world_canon_gap_binding_append_only BEFORE UPDATE OR DELETE ON bidding.world_canon_gap_binding
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();
CREATE OR REPLACE FUNCTION bidding.preserve_bound_knowledge_gap_identity()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM bidding.world_canon_gap_binding b WHERE b.knowledge_gap_id=OLD.knowledge_gap_id)
    AND (NEW.knowledge_gap_id IS DISTINCT FROM OLD.knowledge_gap_id OR NEW.school_id IS DISTINCT FROM OLD.school_id)
 THEN RAISE EXCEPTION 'BID_WORLD_BOUND_GAP_IDENTITY_IMMUTABLE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER world_bound_knowledge_gap_identity_guard
BEFORE UPDATE OF knowledge_gap_id,school_id ON public.knowledge_gap
FOR EACH ROW EXECUTE FUNCTION bidding.preserve_bound_knowledge_gap_identity();

CREATE TABLE bidding.world_robot (
 world_robot_id uuid PRIMARY KEY DEFAULT uuidv7(), robot_key text NOT NULL UNIQUE CHECK(btrim(robot_key)<>''),
 display_name text NOT NULL CHECK(btrim(display_name)<>''), engine_version text NOT NULL CHECK(btrim(engine_version)<>''),
 model_hash text NOT NULL CHECK(model_hash ~ '^[0-9a-f]{64}$'),
 license_boundary jsonb NOT NULL CHECK(jsonb_typeof(license_boundary)='object' AND license_boundary <> '{}'::jsonb
   AND license_boundary ?& ARRAY['license_name','license_version','usage_scope','api_boundary']
   AND jsonb_typeof(license_boundary->'license_name')='string' AND jsonb_typeof(license_boundary->'license_version')='string'
   AND jsonb_typeof(license_boundary->'usage_scope')='string' AND jsonb_typeof(license_boundary->'api_boundary')='string'
   AND COALESCE(btrim(license_boundary->>'license_name'),'')<>'' AND COALESCE(btrim(license_boundary->>'license_version'),'')<>''
   AND COALESCE(btrim(license_boundary->>'usage_scope'),'')<>'' AND COALESCE(btrim(license_boundary->>'api_boundary'),'')<>''
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
 public_auction jsonb NOT NULL CONSTRAINT world_robot_decision_public_auction CHECK(bidding.valid_public_robot_payload('auction',public_auction)),
 public_context jsonb NOT NULL CHECK(bidding.valid_public_robot_payload('context',public_context)),
 raw_response jsonb NOT NULL CONSTRAINT world_robot_decision_public_raw_response CHECK(bidding.valid_public_robot_payload('response',raw_response)),
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
    OR EXISTS (SELECT 1 FROM unnest(ARRAY['mode','request_id','engine_key','engine_version','model_hash','configuration_hash','input_fingerprint','started_at','completed_at','raw_response_sha256']) k
               WHERE jsonb_typeof(NEW.decision_trace->k) IS DISTINCT FROM 'string')
    OR NEW.decision_trace->>'mode' IS DISTINCT FROM NEW.decision_mode OR NEW.decision_trace->>'engine_key' IS DISTINCT FROM r.robot_key
    OR NEW.decision_trace->>'engine_version' IS DISTINCT FROM r.engine_version OR NEW.decision_trace->>'model_hash' IS DISTINCT FROM r.model_hash
    OR NEW.decision_trace->>'configuration_hash' IS DISTINCT FROM c.configuration_hash
    OR NEW.decision_trace->>'input_fingerprint' IS DISTINCT FROM encode(digest(jsonb_build_object(
         'acting_seat',NEW.acting_seat,'acting_hand',NEW.acting_hand,
         'public_auction',NEW.public_auction,'public_context',NEW.public_context)::text,'sha256'),'hex')
    OR NEW.decision_trace->>'raw_response_sha256' IS DISTINCT FROM encode(digest(NEW.raw_response::text,'sha256'),'hex')
    OR jsonb_typeof(NEW.decision_trace->'steps')<>'array' OR jsonb_array_length(NEW.decision_trace->'steps')=0
    OR COALESCE(btrim(NEW.decision_trace->>'request_id'),'')='' OR COALESCE(btrim(NEW.decision_trace->>'input_fingerprint'),'')=''
    OR (NEW.decision_trace->>'started_at')::timestamptz >= (NEW.decision_trace->>'completed_at')::timestamptz
    OR EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.decision_trace->'steps') step
       WHERE jsonb_typeof(step)<>'object'
          OR NOT (step ?& ARRAY['seq','event','at','status','input_hash','output_hash'])
          OR EXISTS (SELECT 1 FROM jsonb_object_keys(step) k WHERE k NOT IN ('seq','event','at','status','input_hash','output_hash'))
          OR jsonb_typeof(step->'seq')<>'number' OR jsonb_typeof(step->'event')<>'string'
          OR jsonb_typeof(step->'at')<>'string' OR jsonb_typeof(step->'status')<>'string'
          OR jsonb_typeof(step->'input_hash')<>'string' OR jsonb_typeof(step->'output_hash')<>'string'
          OR COALESCE(btrim(step->>'event'),'')='' OR COALESCE(btrim(step->>'status'),'')=''
          OR step->>'seq' !~ '^[1-9][0-9]*$'
          OR step->>'input_hash' !~ '^[0-9a-f]{64}$'
          OR step->>'output_hash' !~ '^[0-9a-f]{64}$'
          OR (step->>'at')::timestamptz < (NEW.decision_trace->>'started_at')::timestamptz
          OR (step->>'at')::timestamptz > (NEW.decision_trace->>'completed_at')::timestamptz)
    OR EXISTS (
       SELECT 1
         FROM (
           SELECT step,ord,
                  lag((step->>'at')::timestamptz) OVER (ORDER BY ord) AS previous_at
             FROM jsonb_array_elements(NEW.decision_trace->'steps') WITH ORDINALITY AS s(step,ord)
         ) ordered_step
        WHERE (step->>'seq')::numeric <> ord
           OR (previous_at IS NOT NULL AND (step->>'at')::timestamptz < previous_at)
    )
    OR NEW.decision_trace->'steps'->0->>'input_hash' IS DISTINCT FROM NEW.decision_trace->>'input_fingerprint'
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
 IF NEW.knowledge_gap_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM bidding.world_canon_gap_binding g
   WHERE g.knowledge_gap_id=NEW.knowledge_gap_id AND g.school_id=NEW.school_id
     AND g.request_fingerprint=NEW.request_fingerprint AND g.system_profile_key=NEW.system_profile_key
     AND g.system_version=NEW.system_version AND g.learner_level=NEW.learner_level
     AND g.auction_context_id=NEW.auction_context_id AND g.effective_at=NEW.effective_at)
 THEN RAISE EXCEPTION 'BID_WORLD_TRACE_GAP_SCHOOL_MISMATCH' USING ERRCODE='23514'; END IF;
 IF EXISTS (
   SELECT 1 FROM unnest(NEW.canon_rule_ids||NEW.world_rule_ids) x(rule_id)
   JOIN bidding.rule br ON br.rule_id=x.rule_id JOIN public.knowledge_version kv ON kv.knowledge_version_id=br.knowledge_version_id
   WHERE br.school_id IS DISTINCT FROM NEW.school_id OR kv.bidding_system_key IS DISTINCT FROM NEW.system_profile_key
      OR kv.method_version IS DISTINCT FROM NEW.system_version OR kv.level_scope->>'level' IS DISTINCT FROM NEW.learner_level
      OR NEW.effective_at<COALESCE(kv.effective_from,'-infinity') OR NEW.effective_at>=COALESCE(kv.effective_to,'infinity')
      OR br.auction_pattern->>'context_id' IS DISTINCT FROM NEW.auction_context_id
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
GRANT INSERT(knowledge_gap_id,school_id,request_fingerprint,system_profile_key,system_version,learner_level,auction_context_id,effective_at,profile_fingerprint)
 ON bidding.world_canon_gap_binding TO bridge_school_app,bridge_school_worker;
GRANT SELECT ON bidding.world_canon_gap_binding TO bridge_school_app,bridge_school_worker;
GRANT INSERT ON bidding.world_robot_decision TO bridge_school_worker;
GRANT INSERT ON bidding.world_resolution_trace TO bridge_school_app,bridge_school_worker;
GRANT SELECT ON bidding.world_robot,bidding.world_robot_configuration TO bridge_school_worker;
GRANT SELECT ON bidding.rule TO bridge_school_app,bridge_school_worker;
GRANT SELECT ON public.knowledge_version,public.knowledge_gap TO bridge_school_app,bridge_school_worker;
GRANT INSERT(school_id,question,context_scope,status) ON public.knowledge_gap TO bridge_school_app,bridge_school_worker;
GRANT EXECUTE ON FUNCTION bidding.contains_forbidden_hidden_key(jsonb) TO bridge_school_app,bridge_school_worker;
GRANT EXECUTE ON FUNCTION bidding.contains_nonpublic_card_material(jsonb),bidding.contains_card_token(jsonb),
 bidding.valid_acting_hand(jsonb),bidding.valid_public_robot_payload(text,jsonb) TO bridge_school_worker;
REVOKE ALL ON FUNCTION bidding.validate_world_canon_gap_binding() FROM PUBLIC,bridge_school_reader,bridge_school_app,bridge_school_worker;
REVOKE ALL ON FUNCTION bidding.preserve_bound_knowledge_gap_identity() FROM PUBLIC,bridge_school_reader,bridge_school_app,bridge_school_worker;
REVOKE ALL ON FUNCTION bidding.validate_world_resolution_trace() FROM PUBLIC,bridge_school_reader,bridge_school_app,bridge_school_worker;
REVOKE ALL ON FUNCTION bidding.validate_world_robot_decision() FROM PUBLIC,bridge_school_reader,bridge_school_app,bridge_school_worker;
INSERT INTO public.schema_migration(migration_key) VALUES('0201_world_knowledge_v0') ON CONFLICT DO NOTHING;
COMMIT;
