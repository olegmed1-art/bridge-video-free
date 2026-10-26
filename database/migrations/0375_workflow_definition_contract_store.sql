-- SOURCE ONLY: not applied, no grants, roles, memberships or activation.
-- Ancillary typed validation state. KI/KV/source remain authoritative in public.
BEGIN;
CREATE TABLE bidding.video_canon_definition_state (
 school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
 candidate_payload_hash text NOT NULL CHECK(candidate_payload_hash ~ '^[0-9a-f]{64}$'),
 scope_sha256 text NOT NULL CHECK(scope_sha256 ~ '^[0-9a-f]{64}$'),
 scope_canonical_json text NOT NULL,
 analysis_candidate_id uuid NOT NULL REFERENCES public.analysis_candidate(analysis_candidate_id) ON DELETE RESTRICT,
 revision bigint NOT NULL CHECK(revision>0),
 canonical_json text NOT NULL,
 document_sha256 text NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(school_id,candidate_payload_hash,scope_sha256)
);
CREATE TABLE bidding.video_canon_definition_audit (
 school_id uuid NOT NULL,
 candidate_payload_hash text NOT NULL,
 scope_sha256 text NOT NULL,
 request_key text NOT NULL CHECK(btrim(request_key)<>''),
 event_canonical_json text NOT NULL,
 event_sha256 text NOT NULL,
 recorded_by_principal text NOT NULL DEFAULT session_user,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(school_id,candidate_payload_hash,scope_sha256,request_key),
 FOREIGN KEY(school_id,candidate_payload_hash,scope_sha256)
 REFERENCES bidding.video_canon_definition_state(school_id,candidate_payload_hash,scope_sha256) ON DELETE RESTRICT
);

CREATE FUNCTION bidding.guard_definition_contract_state()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,bidding AS $$
DECLARE d jsonb; o jsonb; s jsonb; c public.analysis_candidate%ROWTYPE; part text;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'DEFINITION_HISTORY_DELETE_FORBIDDEN'; END IF;
 d:=NEW.canonical_json::jsonb; s:=NEW.scope_canonical_json::jsonb;
 IF jsonb_typeof(d)<>'object' OR jsonb_typeof(s)<>'object'
    OR d->>'schema' IS DISTINCT FROM 'definition-contract-store-v0.1'
    OR d->>'evidence_mode' IS DISTINCT FROM 'OBSERVED'
    OR d->>'candidate_hash' IS DISTINCT FROM NEW.candidate_payload_hash
    OR d->'scope' IS DISTINCT FROM s OR s->>'school' IS DISTINCT FROM NEW.school_id::text
    OR d->>'revision' IS DISTINCT FROM NEW.revision::text
    OR jsonb_typeof(d->'sets') IS DISTINCT FROM 'object'
    OR jsonb_typeof(d->'commands') IS DISTINCT FROM 'object'
    OR jsonb_typeof(d->'attempts') IS DISTINCT FROM 'array'
    OR jsonb_typeof(d->'outcomes') IS DISTINCT FROM 'array'
    OR octet_length(NEW.canonical_json)>524288
    OR NEW.scope_sha256 IS DISTINCT FROM encode(public.digest(
       convert_to('DEFINITION_SCOPE_0_1','UTF8')||decode('00','hex')||convert_to(NEW.scope_canonical_json,'UTF8'),'sha256'),'hex') THEN
   RAISE EXCEPTION 'DEFINITION_DOCUMENT_BINDING';
 END IF;
 SELECT * INTO c FROM public.analysis_candidate WHERE analysis_candidate_id=NEW.analysis_candidate_id FOR SHARE;
 IF NOT FOUND OR c.school_id<>NEW.school_id OR c.payload_hash<>NEW.candidate_payload_hash
    OR c.payload#>>'{knowledge_item,knowledge_type}' IS DISTINCT FROM 'skill'
    OR c.payload#>>'{knowledge_version,content,definition_evidence,knowledge_domain}' IS DISTINCT FROM 'CARD_PLAY'
    OR c.payload#>>'{knowledge_version,content,definition_evidence,knowledge_kind}' IS DISTINCT FROM 'DEFINITION' THEN
   RAISE EXCEPTION 'DEFINITION_PERSISTED_SOURCE_CANDIDATE_REQUIRED';
 END IF;
 IF TG_OP='INSERT' THEN
   IF NEW.revision<>1 THEN RAISE EXCEPTION 'DEFINITION_INITIAL_REVISION'; END IF;
 ELSE
   o:=OLD.canonical_json::jsonb;
   IF (NEW.school_id,NEW.candidate_payload_hash,NEW.scope_sha256,NEW.scope_canonical_json,NEW.analysis_candidate_id)
      IS DISTINCT FROM (OLD.school_id,OLD.candidate_payload_hash,OLD.scope_sha256,OLD.scope_canonical_json,OLD.analysis_candidate_id)
      OR NEW.revision<>OLD.revision+1 THEN RAISE EXCEPTION 'DEFINITION_IMMUTABLE_IDENTITY_OR_REVISION'; END IF;
   FOREACH part IN ARRAY ARRAY['sets','commands'] LOOP
     IF EXISTS(SELECT 1 FROM jsonb_each(o->part) x WHERE d->part->x.key IS DISTINCT FROM x.value) THEN
       RAISE EXCEPTION 'DEFINITION_IMMUTABLE_PRIOR_RECORD';
     END IF;
   END LOOP;
   FOREACH part IN ARRAY ARRAY['attempts','outcomes'] LOOP
     IF jsonb_array_length(d->part)<jsonb_array_length(o->part)
        OR COALESCE((SELECT jsonb_agg(x.value ORDER BY x.ordinality)
          FROM jsonb_array_elements(d->part) WITH ORDINALITY x(value,ordinality)
          WHERE x.ordinality<=jsonb_array_length(o->part)),'[]'::jsonb) IS DISTINCT FROM o->part THEN
       RAISE EXCEPTION 'DEFINITION_APPEND_ONLY_HISTORY';
     END IF;
   END LOOP;
 END IF;
 NEW.document_sha256:=encode(public.digest(convert_to(NEW.canonical_json,'UTF8'),'sha256'),'hex');
 RETURN NEW;
END $$;
CREATE TRIGGER definition_contract_state_guard BEFORE INSERT OR UPDATE OR DELETE
 ON bidding.video_canon_definition_state FOR EACH ROW EXECUTE FUNCTION bidding.guard_definition_contract_state();

CREATE FUNCTION bidding.guard_definition_contract_audit()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,bidding AS $$
DECLARE e jsonb; d jsonb; cap text;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'DEFINITION_AUDIT_APPEND_ONLY'; END IF;
 e:=NEW.event_canonical_json::jsonb;
 SELECT canonical_json::jsonb INTO d FROM bidding.video_canon_definition_state
 WHERE school_id=NEW.school_id AND candidate_payload_hash=NEW.candidate_payload_hash AND scope_sha256=NEW.scope_sha256;
 cap:=CASE e->>'operation'
   WHEN 'REGISTER_SET' THEN 'bridge_school_canon_verifier'
   WHEN 'RESERVE_ATTEMPT' THEN 'bridge_school_canon_control_verifier'
   WHEN 'RECORD_OUTCOME' THEN 'bridge_school_canon_control_verifier'
   WHEN 'RESTORE_KV' THEN 'bridge_school_canon_restorer' END;
 IF cap IS NULL OR NOT pg_has_role(session_user,cap,'MEMBER')
    OR e->>'evidence_mode' IS DISTINCT FROM 'OBSERVED'
    OR e->>'actor' IS DISTINCT FROM session_user::text
    OR NEW.recorded_by_principal IS DISTINCT FROM session_user::text
    OR d->'commands'->NEW.request_key IS DISTINCT FROM (e-'readset_sha256'-'writeset_sha256'-'evidence_mode') THEN
   RAISE EXCEPTION 'DEFINITION_AUDIT_AUTHORITY_OR_COMMAND';
 END IF;
 NEW.event_sha256:=encode(public.digest(convert_to(NEW.event_canonical_json,'UTF8'),'sha256'),'hex');
 RETURN NEW;
END $$;
CREATE TRIGGER definition_contract_audit_guard BEFORE INSERT OR UPDATE OR DELETE
 ON bidding.video_canon_definition_audit FOR EACH ROW EXECUTE FUNCTION bidding.guard_definition_contract_audit();
CREATE FUNCTION bidding.reject_definition_contract_truncate()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'DEFINITION_HISTORY_TRUNCATE_FORBIDDEN'; END $$;
CREATE TRIGGER definition_contract_state_no_truncate BEFORE TRUNCATE
 ON bidding.video_canon_definition_state FOR EACH STATEMENT EXECUTE FUNCTION bidding.reject_definition_contract_truncate();
CREATE TRIGGER definition_contract_audit_no_truncate BEFORE TRUNCATE
 ON bidding.video_canon_definition_audit FOR EACH STATEMENT EXECUTE FUNCTION bidding.reject_definition_contract_truncate();
REVOKE ALL ON bidding.video_canon_definition_state,bidding.video_canon_definition_audit FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_definition_contract_state(),bidding.guard_definition_contract_audit() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.reject_definition_contract_truncate() FROM PUBLIC;
-- No GRANT, EXECUTE capability expansion, CREATE ROLE or active route here.
INSERT INTO public.schema_migration(migration_key)
VALUES ('0375_workflow_definition_contract_store') ON CONFLICT DO NOTHING;
COMMIT;
