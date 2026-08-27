-- 0200_bidding_knowledge_v0 / part 05
-- Included transactionally by ../0200_bidding_knowledge_v0.sql.

CREATE OR REPLACE VIEW bidding.active_school_canon_rule_v AS
SELECT
    r.school_id,
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
    r.condition_schema_version,
    r.compiled_payload,
    r.method_version
FROM bidding.runtime_activation AS ra
JOIN bidding.rule AS r ON r.rule_id=ra.rule_id
JOIN public.knowledge_version AS kv ON kv.knowledge_version_id=r.knowledge_version_id
JOIN public.canon_activation AS ca ON ca.canon_activation_id=ra.canon_activation_id
WHERE ra.status='active'
  AND ra.school_id=r.school_id
  AND ra.authority_lane='school_canon'
  AND ra.valid_from <= now()
  AND (ra.valid_to IS NULL OR ra.valid_to > now())
  AND r.lifecycle_status='validated'
  AND kv.authority_class='school_canon'
  AND ca.status='active'
  AND ca.knowledge_version_id=r.knowledge_version_id
  AND ca.scope_key=ra.scope_key
  AND ca.valid_from <= now()
  AND (ca.valid_to IS NULL OR ca.valid_to > now())
  AND bidding.rule_passes_activation_gates(r.rule_id);

CREATE OR REPLACE VIEW bidding.active_world_rule_v AS
SELECT
    r.school_id,
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
    r.condition_schema_version,
    r.compiled_payload,
    r.method_version
FROM bidding.runtime_activation AS ra
JOIN bidding.rule AS r ON r.rule_id=ra.rule_id
JOIN public.knowledge_version AS kv ON kv.knowledge_version_id=r.knowledge_version_id
WHERE ra.status='active'
  AND ra.school_id=r.school_id
  AND ra.authority_lane='world_external'
  AND ra.canon_activation_id IS NULL
  AND ra.valid_from <= now()
  AND (ra.valid_to IS NULL OR ra.valid_to > now())
  AND r.lifecycle_status='validated'
  AND kv.authority_class='external'
  AND bidding.rule_passes_activation_gates(r.rule_id);

CREATE OR REPLACE VIEW bidding.canon_world_link_v AS
SELECT
    kr.knowledge_relation_id,
    kr.school_id,
    CASE WHEN fkv.authority_class='school_canon' THEN kr.from_version_id ELSE kr.to_version_id END AS canon_version_id,
    CASE WHEN fkv.authority_class='external' THEN kr.from_version_id ELSE kr.to_version_id END AS world_version_id,
    CASE WHEN fkv.authority_class='school_canon' THEN fr.rule_id ELSE tr.rule_id END AS canon_rule_id,
    CASE WHEN fkv.authority_class='external' THEN fr.rule_id ELSE tr.rule_id END AS world_rule_id,
    kr.relation_type,
    kr.scope,
    kr.preconditions,
    kr.confidence_class,
    kr.evidence_ids,
    kr.method_version,
    kr.created_at
FROM public.knowledge_relation AS kr
JOIN public.knowledge_version AS fkv ON fkv.knowledge_version_id=kr.from_version_id
JOIN public.knowledge_version AS tkv ON tkv.knowledge_version_id=kr.to_version_id
LEFT JOIN bidding.rule AS fr ON fr.knowledge_version_id=kr.from_version_id
LEFT JOIN bidding.rule AS tr ON tr.knowledge_version_id=kr.to_version_id
WHERE (fkv.authority_class='school_canon' AND tkv.authority_class='external')
   OR (fkv.authority_class='external' AND tkv.authority_class='school_canon');

CREATE OR REPLACE FUNCTION bidding.get_school_runtime_rule_catalog(
    p_school_id uuid,
    p_scope_key text
)
RETURNS SETOF bidding.active_school_canon_rule_v
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path=pg_catalog,bidding,public
AS $$
SELECT *
  FROM bidding.active_school_canon_rule_v
 WHERE school_id=p_school_id AND scope_key=p_scope_key
 ORDER BY priority DESC, specificity DESC, rule_key;
$$;

CREATE OR REPLACE FUNCTION bidding.get_research_rule_catalog(
    p_school_id uuid,
    p_scope_key text,
    p_include_world boolean DEFAULT true
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
SECURITY DEFINER
SET search_path=pg_catalog,bidding,public
AS $$
SELECT 'school_canon'::text, c.rule_id, c.knowledge_version_id, c.rule_key, c.rule_kind,
       c.auction_pattern, c.hand_constraints, c.public_context_constraints,
       c.action, c.meaning, c.public_inference, c.priority, c.specificity,
       c.explanation, c.method_version
  FROM bidding.active_school_canon_rule_v AS c
 WHERE c.school_id=p_school_id AND c.scope_key=p_scope_key
UNION ALL
SELECT 'world_external'::text, w.rule_id, w.knowledge_version_id, w.rule_key, w.rule_kind,
       w.auction_pattern, w.hand_constraints, w.public_context_constraints,
       w.action, w.meaning, w.public_inference, w.priority, w.specificity,
       w.explanation, w.method_version
  FROM bidding.active_world_rule_v AS w
 WHERE p_include_world AND w.school_id=p_school_id AND w.scope_key=p_scope_key
ORDER BY priority DESC, specificity DESC, rule_key;
$$;

GRANT SELECT ON bidding.active_school_canon_rule_v TO bridge_school_reader;

GRANT EXECUTE ON FUNCTION bidding.get_school_runtime_rule_catalog(uuid,text)
    TO bridge_school_reader, bridge_school_app, bridge_school_worker;

GRANT SELECT ON ALL TABLES IN SCHEMA bidding TO bridge_school_worker;

GRANT EXECUTE ON FUNCTION bidding.get_research_rule_catalog(uuid,text,boolean)
    TO bridge_school_worker;

GRANT INSERT, UPDATE ON
    bidding.rule,
    bidding.rule_relation,
    bidding.rule_test
TO bridge_school_worker;

GRANT INSERT (school_id,left_rule_id,right_rule_id,conflict_type,context_scope,details,evidence_ids)
    ON bidding.rule_conflict TO bridge_school_worker;

GRANT INSERT (school_id,rule_test_id,result,result_details,evidence_id,method_version)
    ON bidding.rule_test_run TO bridge_school_worker;

GRANT INSERT (school_id,source_id,source_manifest_key,source_sha256,repository_ref,metadata,created_by_person_id)
    ON bidding.ingestion_run TO bridge_school_worker;

GRANT UPDATE (status,finished_at) ON bidding.ingestion_run TO bridge_school_worker;

GRANT INSERT (ingestion_run_id,event_no,role_key,action_key,target_type,target_id,details,evidence_ids)
    ON bidding.ingestion_event TO bridge_school_worker;
