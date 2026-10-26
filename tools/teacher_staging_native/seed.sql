-- NOT_RUN. Fresh disposable teacher0332 database only. No project identities.
BEGIN;
SET LOCAL statement_timeout='3000ms';
SET LOCAL lock_timeout='1000ms';
SET LOCAL search_path=pg_catalog,public,bidding,pg_temp;
DO $source_only$
DECLARE target_school uuid;
BEGIN
 IF current_database()<>'teacher0332' OR current_setting('server_version_num')::integer NOT BETWEEN 180000 AND 189999
 THEN RAISE EXCEPTION 'SOURCE_ONLY_TARGET_REQUIRED'; END IF;
 IF EXISTS(SELECT 1 FROM public.source) OR EXISTS(SELECT 1 FROM public.analysis_candidate)
    OR EXISTS(SELECT 1 FROM public.knowledge_item) OR EXISTS(SELECT 1 FROM public.knowledge_version)
    OR EXISTS(SELECT 1 FROM public.canon_activation) OR EXISTS(SELECT 1 FROM public.algorithm)
    OR EXISTS(SELECT 1 FROM public.algorithm_version)
 THEN RAISE EXCEPTION 'FRESH_EMPTY_SOURCE_ONLY_BASELINE_REQUIRED'; END IF;
 SELECT school_id INTO STRICT target_school FROM public.school WHERE stable_name='Школа спортивного бриджа' AND status='active';
 INSERT INTO public.algorithm(algorithm_id,school_id,stable_key,name,purpose,status)
 VALUES('00000000-0000-4000-8000-000000000333',target_school,'bridge-video-master-analysis',
   'SOURCE_ONLY native staging fixture','TEST DATA ONLY; not an accepted algorithm or authority','active');
 INSERT INTO public.algorithm_version(algorithm_version_id,algorithm_id,version_no,version_label,configuration,status)
 VALUES('00000000-0000-4000-8000-000000000334','00000000-0000-4000-8000-000000000333',1,
   'SOURCE_ONLY_NATIVE_STAGING_R4','{"SOURCE_ONLY":true,"genuine_evidence":false}'::jsonb,'candidate');
END $source_only$;
COMMIT;
