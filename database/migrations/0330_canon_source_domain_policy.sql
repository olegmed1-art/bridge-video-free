\set ON_ERROR_STOP on
BEGIN;

CREATE TABLE public.canon_source_domain_policy (
    policy_version text NOT NULL,
    knowledge_domain text NOT NULL
      CHECK (knowledge_domain IN ('BIDDING','CARD_PLAY','DEFENSE')),
    evidence_lane text NOT NULL
      CHECK (evidence_lane IN ('SCHOOL_PRIMARY_EVIDENCE','WORLD_EXTERNAL')),
    auto_canon_allowed boolean NOT NULL,
    min_confidence numeric NOT NULL
      CHECK (min_confidence>=0 AND min_confidence<=1 AND min_confidence<>'NaN'::numeric),
    required_checks jsonb NOT NULL
      CHECK (
        jsonb_typeof(required_checks)='array'
        AND jsonb_array_length(required_checks)>0
      ),
    policy_reason text NOT NULL CHECK (btrim(policy_reason)<>''),
    active boolean NOT NULL DEFAULT true,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (policy_version,knowledge_domain,evidence_lane)
);

INSERT INTO public.canon_source_domain_policy(
  policy_version,knowledge_domain,evidence_lane,auto_canon_allowed,
  min_confidence,required_checks,policy_reason
) VALUES
('canon-source-domain-v1','BIDDING','SCHOOL_PRIMARY_EVIDENCE',true,0.95,
 '["asr_verified","slip_checked","interpretation_verified","context_verified","canon_conflict_free","provenance_bound"]',
 'Bidding is primarily learned from authorized School teacher video.'),
('canon-source-domain-v1','BIDDING','WORLD_EXTERNAL',false,0.95,
 '["source_authoritative","semantic_verified","interpretation_verified","context_verified","canon_conflict_free","provenance_bound","school_method_conflict_free"]',
 'External material may corroborate bidding but cannot auto-activate School bidding Canon.'),
('canon-source-domain-v1','CARD_PLAY','SCHOOL_PRIMARY_EVIDENCE',true,0.95,
 '["asr_verified","slip_checked","interpretation_verified","context_verified","canon_conflict_free","provenance_bound"]',
 'Authorized School video may populate card-play Canon after full verification.'),
('canon-source-domain-v1','CARD_PLAY','WORLD_EXTERNAL',true,0.95,
 '["source_authoritative","semantic_verified","interpretation_verified","context_verified","canon_conflict_free","provenance_bound","school_method_conflict_free"]',
 'Authoritative external card-play knowledge may enter Canon at high confidence.'),
('canon-source-domain-v1','DEFENSE','SCHOOL_PRIMARY_EVIDENCE',true,0.95,
 '["asr_verified","slip_checked","interpretation_verified","context_verified","canon_conflict_free","provenance_bound"]',
 'Authorized School video may populate defense Canon after full verification.'),
('canon-source-domain-v1','DEFENSE','WORLD_EXTERNAL',true,0.95,
 '["source_authoritative","semantic_verified","interpretation_verified","context_verified","canon_conflict_free","provenance_bound","school_method_conflict_free"]',
 'Authoritative external defense knowledge may enter Canon at high confidence.');

CREATE FUNCTION public.evaluate_canon_source_domain_policy(
    p_knowledge_domain text,
    p_evidence_lane text,
    p_confidence numeric,
    p_checks jsonb,
    p_policy_version text DEFAULT 'canon-source-domain-v1'
) RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path=pg_catalog,public
AS $$
  SELECT COALESCE((
    SELECT policy.auto_canon_allowed
       AND p_confidence IS NOT NULL
       AND p_confidence<>'NaN'::numeric
       AND p_confidence BETWEEN policy.min_confidence AND 1
       AND jsonb_typeof(p_checks)='object'
       AND NOT EXISTS (
         SELECT 1
           FROM jsonb_array_elements_text(policy.required_checks) required(check_name)
          WHERE COALESCE(p_checks->required.check_name,'false'::jsonb)<>'true'::jsonb
       )
      FROM public.canon_source_domain_policy policy
     WHERE policy.policy_version=p_policy_version
       AND policy.knowledge_domain=upper(btrim(p_knowledge_domain))
       AND policy.evidence_lane=upper(btrim(p_evidence_lane))
       AND policy.active
  ),false);
$$;

COMMENT ON FUNCTION public.evaluate_canon_source_domain_policy(text,text,numeric,jsonb,text) IS
  'Read-only fail-closed admission gate. A true result never writes or activates Canon.';

CREATE FUNCTION public.guard_canon_source_domain_policy_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'CANON_SOURCE_DOMAIN_POLICY_IMMUTABLE' USING ERRCODE='55000';
END;
$$;

CREATE TRIGGER canon_source_domain_policy_immutable
BEFORE UPDATE OR DELETE ON public.canon_source_domain_policy
FOR EACH ROW EXECUTE FUNCTION public.guard_canon_source_domain_policy_immutable();

REVOKE ALL ON public.canon_source_domain_policy FROM PUBLIC;
REVOKE ALL ON FUNCTION public.guard_canon_source_domain_policy_immutable() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.evaluate_canon_source_domain_policy(text,text,numeric,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.evaluate_canon_source_domain_policy(text,text,numeric,jsonb,text)
TO bridge_school_worker,bridge_school_reader;

COMMIT;
