\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
  v_video jsonb := '{
    "asr_verified":true,
    "slip_checked":true,
    "interpretation_verified":true,
    "context_verified":true,
    "canon_conflict_free":true,
    "provenance_bound":true
  }';
  v_external jsonb := '{
    "source_authoritative":true,
    "semantic_verified":true,
    "interpretation_verified":true,
    "context_verified":true,
    "canon_conflict_free":true,
    "provenance_bound":true,
    "school_method_conflict_free":true
  }';
  v_canon_before bigint;
  v_versions_before bigint;
BEGIN
  SELECT count(*) INTO v_canon_before FROM public.canon_activation;
  SELECT count(*) INTO v_versions_before FROM public.knowledge_version;

  IF NOT public.evaluate_canon_source_domain_policy(
    'BIDDING','SCHOOL_PRIMARY_EVIDENCE',0.95,v_video
  ) THEN RAISE EXCEPTION 'POLICY_BIDDING_VIDEO_SHOULD_PASS'; END IF;

  IF public.evaluate_canon_source_domain_policy(
    'BIDDING','WORLD_EXTERNAL',1.0,v_external
  ) THEN RAISE EXCEPTION 'POLICY_BIDDING_EXTERNAL_SHOULD_NOT_AUTO_CANON'; END IF;

  IF NOT public.evaluate_canon_source_domain_policy(
    'CARD_PLAY','WORLD_EXTERNAL',0.99,v_external
  ) THEN RAISE EXCEPTION 'POLICY_CARD_PLAY_EXTERNAL_SHOULD_PASS'; END IF;

  IF NOT public.evaluate_canon_source_domain_policy(
    'DEFENSE','WORLD_EXTERNAL',0.99,v_external
  ) THEN RAISE EXCEPTION 'POLICY_DEFENSE_EXTERNAL_SHOULD_PASS'; END IF;

  IF public.evaluate_canon_source_domain_policy(
    'CARD_PLAY','WORLD_EXTERNAL',0.949999,v_external
  ) THEN RAISE EXCEPTION 'POLICY_LOW_CONFIDENCE_SHOULD_FAIL'; END IF;

  IF public.evaluate_canon_source_domain_policy(
    'CARD_PLAY','WORLD_EXTERNAL','NaN'::numeric,v_external
  ) THEN RAISE EXCEPTION 'POLICY_NAN_SHOULD_FAIL'; END IF;

  IF public.evaluate_canon_source_domain_policy(
    'DEFENSE','WORLD_EXTERNAL',1.0,v_external||'{"interpretation_verified":"true"}'
  ) THEN RAISE EXCEPTION 'POLICY_NON_BOOLEAN_CHECK_SHOULD_FAIL'; END IF;

  IF public.evaluate_canon_source_domain_policy(
    'DEFENSE','WORLD_EXTERNAL',1.0,v_external||'{"school_method_conflict_free":false}'
  ) THEN RAISE EXCEPTION 'POLICY_SCHOOL_CONFLICT_SHOULD_FAIL'; END IF;

  IF (SELECT count(*) FROM public.canon_activation)<>v_canon_before
     OR (SELECT count(*) FROM public.knowledge_version)<>v_versions_before THEN
    RAISE EXCEPTION 'POLICY_EVALUATION_MUTATED_CANON';
  END IF;
END $$;

ROLLBACK;
