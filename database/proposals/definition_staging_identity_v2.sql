-- SOURCE PROPOSAL ONLY: existing0332 full store must be installed and disabled.
-- No candidate/KI/KV/activation data, principals, GRANT or control rows are changed.
BEGIN;
SET LOCAL statement_timeout='3000ms';
SET LOCAL lock_timeout='1000ms';
SET LOCAL search_path=pg_catalog,public,bidding,pg_temp;
-- Same first fence as existing native metadata path, drained before replacement.
LOCK TABLE bidding.video_definition_store_binding IN ACCESS EXCLUSIVE MODE;
LOCK TABLE bidding.video_definition_store_control IN SHARE ROW EXCLUSIVE MODE;
DO $identity_change$
DECLARE before_row record; after_row record; current_sha text;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM bidding.video_definition_store_control WHERE singleton=true AND enabled=false)
 THEN RAISE EXCEPTION 'DEFINITION_DISABLED_STORE_REQUIRED'; END IF;
 SELECT * INTO STRICT before_row FROM pg_catalog.pg_proc
 WHERE oid='bidding.video_definition_scope(uuid,jsonb)'::regprocedure;
 IF pg_catalog.pg_get_userbyid(before_row.proowner)<>current_user OR NOT before_row.prosecdef
   OR before_row.provolatile<>'s' OR before_row.prorettype<>'jsonb'::regtype
   OR before_row.prolang<>(SELECT oid FROM pg_catalog.pg_language WHERE lanname='plpgsql')
 THEN RAISE EXCEPTION 'DEFINITION_SCOPE_OWNER_OR_SIGNATURE_MISMATCH'; END IF;
 -- Check source-declared attributes before both replacement and no-op paths.
 -- Unexpected access/configuration is refused, never repaired by this proposal.
 IF before_row.prokind<>'f' OR before_row.proisstrict OR before_row.proleakproof
   OR before_row.proretset OR before_row.proparallel<>'u' OR before_row.procost<>100
   OR before_row.prorows<>0 OR before_row.pronargs<>2 OR before_row.pronargdefaults<>0
   OR before_row.provariadic<>0 OR before_row.prosupport<>0
   OR before_row.proargtypes<>'2950 3802'::pg_catalog.oidvector
   OR before_row.proargnames IS DISTINCT FROM ARRAY['p_candidate','p_scope']::text[]
   OR before_row.proallargtypes IS NOT NULL OR before_row.proargmodes IS NOT NULL
   OR before_row.proargdefaults IS NOT NULL OR before_row.protrftypes IS NOT NULL
   OR before_row.prosqlbody IS NOT NULL OR before_row.probin IS NOT NULL
   OR (SELECT array_agg(v ORDER BY v COLLATE "C") FROM unnest(before_row.proconfig) AS cfg(v))
       IS DISTINCT FROM ARRAY['TimeZone=UTC','search_path=pg_catalog, public, bidding, pg_temp']::text[]
   OR EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(before_row.proacl,
       pg_catalog.acldefault('f',before_row.proowner))) a
       WHERE a.grantee<>before_row.proowner OR a.grantor<>before_row.proowner
          OR a.privilege_type<>'EXECUTE')
   OR NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(before_row.proacl,
       pg_catalog.acldefault('f',before_row.proowner))) a
       WHERE a.grantee=before_row.proowner AND a.privilege_type='EXECUTE')
 THEN RAISE EXCEPTION 'DEFINITION_SCOPE_METADATA_DRIFT'; END IF;
 IF (SELECT encode(public.digest(convert_to(replace(prosrc,E'\r\n',E'\n'),'UTF8'),'sha256'),'hex') FROM pg_catalog.pg_proc WHERE oid='bidding.validate_video_canon_verification_bundle()'::regprocedure) IS DISTINCT FROM '6967c3b85e20d0149d54f790e69cd3323507f4dd49c39a8df813411620849fa6' THEN RAISE EXCEPTION 'DEFINITION_0332_DISPATCH_PREIMAGE_MISMATCH'; END IF;
 IF (SELECT encode(public.digest(convert_to(replace(prosrc,E'\r\n',E'\n'),'UTF8'),'sha256'),'hex') FROM pg_catalog.pg_proc WHERE oid='bidding.validate_video_canon_verification()'::regprocedure) IS DISTINCT FROM 'f184ccb839e0400ab3c03b735ea07ede97abac6b7172ff0fa6e1143d5f40dbc1' THEN RAISE EXCEPTION 'DEFINITION_0332_DISPATCH_PREIMAGE_MISMATCH'; END IF;
 current_sha:=encode(public.digest(convert_to(replace(before_row.prosrc,E'\r\n',E'\n'),'UTF8'),'sha256'),'hex');
 IF current_sha='4b879eb20c0c413d7e05c4cdad442c19f2d17f9a37a131d0dfff6e32be37491b' THEN RETURN; END IF; -- Exact already-applied state: no DDL.
 IF current_sha<>'d3a94d6712e0315ee286e38e68a8f31d11ca5c098b66b74bf34a851a1f8902f3' THEN RAISE EXCEPTION 'DEFINITION_SCOPE_PREIMAGE_MISMATCH'; END IF;
 EXECUTE $scope_definition$
CREATE OR REPLACE FUNCTION bidding.video_definition_scope(p_candidate uuid,p_scope jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp SET TimeZone='UTC' AS $$
DECLARE c record; d jsonb; span jsonb;
BEGIN
 PERFORM bidding.video_definition_need(bidding.video_definition_keys(p_scope,ARRAY['school_key','system_profile','learner_level','activation_scope'])
   AND NOT EXISTS(SELECT 1 FROM jsonb_each(p_scope) x WHERE jsonb_typeof(x.value)<>'string' OR length(x.value#>>'{}') NOT BETWEEN 1 AND 512),'DEFINITION_SCOPE_REQUIRED');
 SELECT a.*,s.status source_status,s.school_id source_school,school.stable_name INTO c
 FROM public.analysis_candidate a JOIN public.source s ON s.source_id=a.source_id JOIN public.school school ON school.school_id=a.school_id
 WHERE a.analysis_candidate_id=p_candidate;
 PERFORM bidding.video_definition_need(FOUND AND c.school_id=c.source_school AND c.stable_name=p_scope->>'school_key'
   AND c.candidate_type='video_school_canon_candidate' AND c.status='active' AND c.promotion_status IN ('staging','review_queue')
   AND c.source_status='active' AND c.payload_hash ~ '^[0-9a-f]{64}$'
   AND bidding.video_definition_keys(c.payload,ARRAY['schema','status','knowledge_item','knowledge_version','knowledge_version_source',
       'canon_activation','accepted_readback','db_write_performed'])
   AND c.payload->>'schema'='knowledge-storage-skill-proposal-only-v1'
   AND c.payload->>'status'='PROPOSAL_NOT_INSERTABLE_WITHOUT_REAL_FOREIGN_KEYS'
   AND c.payload->'db_write_performed'='false'::jsonb AND c.payload->'canon_activation'='null'::jsonb
   AND c.payload->'accepted_readback'='null'::jsonb
   AND bidding.video_definition_keys(c.payload->'knowledge_item',ARRAY['knowledge_item_id','school_id','stable_key','knowledge_type','title','status'])
   AND bidding.video_definition_keys(c.payload->'knowledge_version',ARRAY['knowledge_version_id','knowledge_item_id','version_no','content','authority_class',
       'review_status','status','bidding_system_key','agreement_scope','level_scope','effective_from','effective_to','method_version','provenance'])
   AND bidding.video_definition_keys(c.payload->'knowledge_version_source',ARRAY['knowledge_version_id','source_id','relation_type','source_locator'])
   AND c.payload#>>'{knowledge_item,knowledge_type}'='skill' AND c.payload#>>'{knowledge_item,status}'='candidate'
   AND jsonb_typeof(c.payload#>'{knowledge_item,stable_key}')='string'
   AND jsonb_typeof(c.payload#>'{knowledge_item,title}')='string' AND length(c.payload#>>'{knowledge_item,title}')>0
   AND c.payload#>'{knowledge_item,knowledge_item_id}'='null'::jsonb AND c.payload#>'{knowledge_item,school_id}'='null'::jsonb
   AND c.stable_key IN (
     c.payload#>>'{knowledge_item,stable_key}',
     CASE WHEN right(c.payload#>>'{knowledge_item,stable_key}',72)=':sha256:'||c.payload_hash
       THEN c.payload#>>'{knowledge_item,stable_key}'
       ELSE (c.payload#>>'{knowledge_item,stable_key}')||':sha256:'||c.payload_hash END)
   AND c.payload#>>'{knowledge_version,authority_class}'='research_candidate'
   AND c.payload#>>'{knowledge_version,review_status}'='unreviewed' AND c.payload#>>'{knowledge_version,status}'='candidate'
   AND c.payload#>'{knowledge_version,knowledge_version_id}'='null'::jsonb
   AND c.payload#>'{knowledge_version,knowledge_item_id}'='null'::jsonb
   AND c.payload#>'{knowledge_version,version_no}'='1'::jsonb
   AND c.payload#>'{knowledge_version,agreement_scope}'='{}'::jsonb AND c.payload#>'{knowledge_version,level_scope}'='{}'::jsonb
   AND c.payload#>'{knowledge_version,effective_from}'='null'::jsonb AND c.payload#>'{knowledge_version,effective_to}'='null'::jsonb
   AND (c.payload#>'{knowledge_version,bidding_system_key}'='null'::jsonb OR
        c.payload#>>'{knowledge_version,bidding_system_key}'=p_scope->>'system_profile')
   AND jsonb_typeof(c.payload#>'{knowledge_version,provenance}')='object'
   AND jsonb_typeof(c.payload#>'{knowledge_version,method_version}')='string' AND length(c.payload#>>'{knowledge_version,method_version}')>0
   AND c.payload#>'{knowledge_version_source,knowledge_version_id}'='null'::jsonb
   AND c.payload#>'{knowledge_version_source,source_id}'='null'::jsonb
   AND c.payload#>>'{knowledge_version_source,relation_type}'='derived_from'
   AND jsonb_typeof(c.payload#>'{knowledge_version_source,source_locator}')='object',
   'DEFINITION_NATIVE_CANDIDATE_SCOPE');
 PERFORM bidding.video_definition_need(bidding.video_definition_int(c.payload#>'{knowledge_version,version_no}',1)=1,'DEFINITION_INITIAL_VERSION_REQUIRED');
 d:=c.payload#>'{knowledge_version,content,definition_evidence}';
 PERFORM bidding.video_definition_need(bidding.video_definition_keys(d,ARRAY['schema','knowledge_domain','knowledge_kind','statement_text',
   'statement_sha256','source_binding','owner_confirmation','scope','requires_52_cards','semantic_confidence','source_authorization','admission'])
   AND d->>'schema'='card-play-definition-proposal-v1' AND d->>'knowledge_domain'='CARD_PLAY'
   AND d->>'knowledge_kind'='DEFINITION' AND d->>'scope'='DEFINITION_RECOGNITION_ONLY' AND d->'requires_52_cards'='false'::jsonb
   AND d->'semantic_confidence'='null'::jsonb AND d->'source_authorization'='null'::jsonb
   AND bidding.video_definition_keys(d->'source_binding',ARRAY['source_candidate_id','source_drive_id','source_sha256','source_segment_id',
       'interval_seconds','raw_text','raw_text_sha256'])
   AND NOT EXISTS(SELECT 1 FROM unnest(ARRAY['source_candidate_id','source_drive_id','source_segment_id','raw_text']) AS h(k)
       WHERE jsonb_typeof(d->'source_binding'->k)<>'string' OR length(d->'source_binding'->>k)=0)
   AND d#>>'{source_binding,source_sha256}' ~ '^[0-9a-f]{64}$'
   AND jsonb_typeof(d#>'{source_binding,source_sha256}')='string' AND jsonb_typeof(d#>'{source_binding,raw_text_sha256}')='string'
   AND jsonb_typeof(d->'statement_sha256')='string'
   AND jsonb_typeof(d->'statement_text')='string' AND length(d->>'statement_text')>0
   AND bidding.video_definition_keys(d->'owner_confirmation',ARRAY['status','message_ref','record_sha256','confirmed_role','named_person','independent_audio_review'])
   AND d#>>'{owner_confirmation,status}'='OWNER_CONFIRMED' AND d#>>'{owner_confirmation,confirmed_role}'='teacher'
   AND d#>'{owner_confirmation,named_person}'='null'::jsonb AND d#>'{owner_confirmation,independent_audio_review}'='false'::jsonb
   AND jsonb_typeof(d#>'{owner_confirmation,message_ref}')='string' AND length(d#>>'{owner_confirmation,message_ref}')>0
   AND d#>>'{owner_confirmation,record_sha256}' ~ '^[0-9a-f]{64}$'
   AND jsonb_typeof(d#>'{owner_confirmation,record_sha256}')='string'
   AND bidding.video_definition_keys(d->'admission',ARRAY['status','required_checks','verification_receipts'])
   AND d#>>'{admission,status}'='BLOCKED' AND d#>'{admission,verification_receipts}'='[]'::jsonb
   AND d#>'{admission,required_checks}'='["BOUNDARY_TESTS","BRIDGE_LOGIC","CANON_CONFLICT_SCAN","CANON_INTEGRITY","CANON_REGRESSION","EXPLANATION_COMPLETENESS","HIDDEN_INFORMATION_FIREWALL","INTERFERENCE_TESTS","NEGATIVE_TESTS","POSITIVE_TESTS","ROLLBACK_RESTORE","SEMANTIC_PARSE","SOURCE_AUTHORITY","SOURCE_BINDING","SPEAKER_IDENTITY","TRANSCRIPT_BINDING"]'::jsonb
   AND encode(public.digest(convert_to(d->>'statement_text','UTF8'),'sha256'),'hex')=d->>'statement_sha256'
   AND encode(public.digest(convert_to(d#>>'{source_binding,raw_text}','UTF8'),'sha256'),'hex')=d#>>'{source_binding,raw_text_sha256}',
   'DEFINITION_TYPED_SOURCE_REQUIRED');
 span:=d#>'{source_binding,interval_seconds}';
 PERFORM bidding.video_definition_need(jsonb_typeof(span)='array' AND jsonb_array_length(span)=2
   AND jsonb_typeof(span->0)='number' AND jsonb_typeof(span->1)='number'
   AND (span->>0)::numeric>=0 AND (span->>0)::numeric<(span->>1)::numeric,'DEFINITION_SOURCE_INTERVAL');
 RETURN jsonb_build_object('school',c.school_id::text,'profile',p_scope->>'system_profile',
   'level',p_scope->>'learner_level','scope',p_scope->>'activation_scope');
END $$;
$scope_definition$;
 SELECT * INTO STRICT after_row FROM pg_catalog.pg_proc WHERE oid=before_row.oid;
 IF (to_jsonb(after_row)-'prosrc') IS DISTINCT FROM (to_jsonb(before_row)-'prosrc')
   OR encode(public.digest(convert_to(replace(after_row.prosrc,E'\r\n',E'\n'),'UTF8'),'sha256'),'hex')<>'4b879eb20c0c413d7e05c4cdad442c19f2d17f9a37a131d0dfff6e32be37491b'
 THEN RAISE EXCEPTION 'DEFINITION_SCOPE_REPLACEMENT_MISMATCH'; END IF;
END $identity_change$;
COMMIT;
