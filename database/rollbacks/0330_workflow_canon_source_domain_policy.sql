\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE v_exact integer;
BEGIN
  IF to_regclass('public.canon_source_domain_policy') IS NOT NULL THEN
    SELECT count(*) INTO v_exact
      FROM public.canon_source_domain_policy
     WHERE policy_version='canon-source-domain-v1'
       AND (knowledge_domain,evidence_lane,auto_canon_allowed) IN (
         ('BIDDING','SCHOOL_PRIMARY_EVIDENCE',true),
         ('BIDDING','WORLD_EXTERNAL',false),
         ('CARD_PLAY','SCHOOL_PRIMARY_EVIDENCE',true),
         ('CARD_PLAY','WORLD_EXTERNAL',true),
         ('DEFENSE','SCHOOL_PRIMARY_EVIDENCE',true),
         ('DEFENSE','WORLD_EXTERNAL',true)
       );
    IF (SELECT count(*) FROM public.canon_source_domain_policy)<>6 OR v_exact<>6 THEN
      RAISE EXCEPTION 'rollback refused: Canon source/domain policy state diverged';
    END IF;
  END IF;
END $$;

DROP TRIGGER IF EXISTS canon_source_domain_policy_immutable
  ON public.canon_source_domain_policy;
DROP FUNCTION IF EXISTS public.guard_canon_source_domain_policy_immutable();
DROP FUNCTION IF EXISTS public.evaluate_canon_source_domain_policy(text,text,numeric,jsonb,text);
DROP TABLE IF EXISTS public.canon_source_domain_policy;

COMMIT;
