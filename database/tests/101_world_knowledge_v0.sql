\set ON_ERROR_STOP on
BEGIN;
DO $$
DECLARE
 s uuid; oi uuid; ov uuid; wr uuid; oi2 uuid; ov2 uuid; wr2 uuid; gap uuid; old_gap uuid; role_gap uuid; robot uuid; config uuid; decision uuid;
 failed boolean; raw jsonb := '{"bid":"1S"}'::jsonb; bad_raw jsonb; trace jsonb; bad_trace jsonb; v_constraint text;
BEGIN
 SELECT school_id INTO s FROM public.school WHERE stable_name='Школа спортивного бриджа';
 INSERT INTO public.knowledge_gap(school_id,question,context_scope,status)
 VALUES(s,'CI canon gap',jsonb_build_object('request_fingerprint','valid-fallback','system_profile_key','natural',
  'system_version','v1','learner_level','L1','auction_context_id','auction-1','effective_at',now()),'open')
 RETURNING knowledge_gap_id INTO gap;
 INSERT INTO bidding.world_canon_gap_binding(knowledge_gap_id,school_id,request_fingerprint,system_profile_key,
  system_version,learner_level,auction_context_id,effective_at,profile_fingerprint)
 VALUES(gap,s,'valid-fallback','natural','v1','L1','auction-1',now(),repeat('d',64));
 INSERT INTO public.knowledge_gap(school_id,question,status) VALUES(s,'role-app-gap','open') RETURNING knowledge_gap_id INTO role_gap;
 INSERT INTO bidding.world_canon_gap_binding(knowledge_gap_id,school_id,request_fingerprint,system_profile_key,
  system_version,learner_level,auction_context_id,effective_at,profile_fingerprint)
 VALUES(role_gap,s,'role-app-fallback','natural','v1','L1','auction-1',now(),repeat('9',64));
 INSERT INTO public.knowledge_gap(school_id,question,context_scope,status)
 VALUES(s,'Old unrelated gap',jsonb_build_object('request_fingerprint','old-request','system_profile_key','natural',
  'system_version','v1','learner_level','L1','auction_context_id','auction-1','effective_at',now()),'open')
 RETURNING knowledge_gap_id INTO old_gap;
 INSERT INTO bidding.world_canon_gap_binding(knowledge_gap_id,school_id,request_fingerprint,system_profile_key,
  system_version,learner_level,auction_context_id,effective_at,profile_fingerprint)
 VALUES(old_gap,s,'old-request','natural','v1','L1','auction-1',now(),repeat('e',64));
 INSERT INTO public.knowledge_item(school_id,stable_key,knowledge_type,title,status)
 VALUES(s,'ci-world-0201','bidding_rule','CI WORLD 0201','active') RETURNING knowledge_item_id INTO oi;
 INSERT INTO public.knowledge_version(knowledge_item_id,version_no,content,authority_class,review_status,
   bidding_system_key,level_scope,effective_from,method_version,provenance,status)
 VALUES(oi,1,'{}','external','reviewed','natural','{"level":"L1"}',now()-interval '1 day','v1','{"class":"DIRECT"}','candidate')
 RETURNING knowledge_version_id INTO ov;
 INSERT INTO bidding.rule(school_id,knowledge_version_id,rule_key,rule_kind,auction_pattern,action,lifecycle_status,method_version)
 VALUES(s,ov,'ci.world.0201','bid','{"context_id":"auction-1"}','{"call":"1S"}','validated','v1') RETURNING rule_id INTO wr;

 INSERT INTO bidding.world_resolution_trace(school_id,request_fingerprint,system_profile_key,system_version,learner_level,
   effective_at,auction_context_id,canon_outcome,world_outcome,world_rule_ids,selected_world_rule_id,knowledge_gap_id,trace,resolver_version)
 VALUES(s,'valid-fallback','natural','v1','L1',now(),'auction-1','CANON_GAP','WORLD_FALLBACK',ARRAY[wr],wr,gap,'{}','world-v0');

 failed:=false; BEGIN
  INSERT INTO bidding.world_resolution_trace(school_id,request_fingerprint,system_profile_key,system_version,learner_level,
   effective_at,auction_context_id,canon_outcome,world_outcome,world_rule_ids,selected_world_rule_id,knowledge_gap_id,trace,resolver_version)
  VALUES(s,'new-request','natural','v1','L1',now(),'auction-1','CANON_GAP','WORLD_FALLBACK',ARRAY[wr],wr,old_gap,'{}','world-v0');
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_UNRELATED_GAP_ACCEPTED'; END IF;

 INSERT INTO public.knowledge_gap(school_id,question,status) VALUES(s,'fallback-without-selection','open') RETURNING knowledge_gap_id INTO gap;
 INSERT INTO bidding.world_canon_gap_binding(knowledge_gap_id,school_id,request_fingerprint,system_profile_key,system_version,learner_level,auction_context_id,effective_at,profile_fingerprint)
 VALUES(gap,s,'fallback-without-selection','natural','v1','L1','auction-1',now(),repeat('f',64));
 failed:=false; BEGIN
  INSERT INTO bidding.world_resolution_trace(school_id,request_fingerprint,system_profile_key,system_version,learner_level,
   effective_at,auction_context_id,canon_outcome,world_outcome,world_rule_ids,knowledge_gap_id,trace,resolver_version)
  VALUES(s,'fallback-without-selection','natural','v1','L1',now(),'auction-1','CANON_GAP','WORLD_FALLBACK',ARRAY[wr],gap,'{}','world-v0');
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_FALLBACK_WITHOUT_SELECTION_ACCEPTED'; END IF;

 INSERT INTO public.knowledge_gap(school_id,question,status) VALUES(s,'conflict-with-selection','open') RETURNING knowledge_gap_id INTO gap;
 INSERT INTO bidding.world_canon_gap_binding(knowledge_gap_id,school_id,request_fingerprint,system_profile_key,system_version,learner_level,auction_context_id,effective_at,profile_fingerprint)
 VALUES(gap,s,'conflict-with-selection','natural','v1','L1','auction-1',now(),repeat('a',64));
 failed:=false; BEGIN
  INSERT INTO bidding.world_resolution_trace(school_id,request_fingerprint,system_profile_key,system_version,learner_level,
   effective_at,auction_context_id,canon_outcome,world_outcome,world_rule_ids,selected_world_rule_id,knowledge_gap_id,trace,resolver_version)
  VALUES(s,'conflict-with-selection','natural','v1','L1',now(),'auction-1','CANON_GAP','WORLD_CONFLICT',ARRAY[wr],wr,gap,'{}','world-v0');
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_CONFLICT_WITH_SELECTION_ACCEPTED'; END IF;

 INSERT INTO public.knowledge_gap(school_id,question,status) VALUES(s,'profile-mix','open') RETURNING knowledge_gap_id INTO gap;
 INSERT INTO bidding.world_canon_gap_binding(knowledge_gap_id,school_id,request_fingerprint,system_profile_key,system_version,learner_level,auction_context_id,effective_at,profile_fingerprint)
 VALUES(gap,s,'profile-mix','sayc','v1','L1','auction-1',now(),repeat('b',64));
 failed:=false; BEGIN
  INSERT INTO bidding.world_resolution_trace(school_id,request_fingerprint,system_profile_key,system_version,learner_level,
   effective_at,auction_context_id,canon_outcome,world_outcome,world_rule_ids,selected_world_rule_id,knowledge_gap_id,trace,resolver_version)
  VALUES(s,'profile-mix','sayc','v1','L1',now(),'auction-1','CANON_GAP','WORLD_FALLBACK',ARRAY[wr],wr,gap,'{}','world-v0');
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_PROFILE_MISMATCH_ACCEPTED'; END IF;

 INSERT INTO public.knowledge_item(school_id,stable_key,knowledge_type,title,status)
 VALUES(s,'ci-world-null-profile','bidding_rule','CI null profile','active') RETURNING knowledge_item_id INTO oi2;
 INSERT INTO public.knowledge_version(knowledge_item_id,version_no,content,authority_class,review_status,level_scope,status)
 VALUES(oi2,1,'{}','external','reviewed','{"level":"L1"}','candidate') RETURNING knowledge_version_id INTO ov2;
 INSERT INTO bidding.rule(school_id,knowledge_version_id,rule_key,rule_kind,auction_pattern,action,lifecycle_status)
 VALUES(s,ov2,'ci.world.null-profile','bid','{"context_id":"auction-1"}','{"call":"1H"}','validated') RETURNING rule_id INTO wr2;
 INSERT INTO public.knowledge_gap(school_id,question,status) VALUES(s,'null-profile','open') RETURNING knowledge_gap_id INTO gap;
 INSERT INTO bidding.world_canon_gap_binding(knowledge_gap_id,school_id,request_fingerprint,system_profile_key,system_version,learner_level,auction_context_id,effective_at,profile_fingerprint)
 VALUES(gap,s,'null-profile','natural','v1','L1','auction-1',now(),repeat('c',64));
 failed:=false; BEGIN
  INSERT INTO bidding.world_resolution_trace(school_id,request_fingerprint,system_profile_key,system_version,learner_level,
   effective_at,auction_context_id,canon_outcome,world_outcome,world_rule_ids,selected_world_rule_id,knowledge_gap_id,trace,resolver_version)
  VALUES(s,'null-profile','natural','v1','L1',now(),'auction-1','CANON_GAP','WORLD_FALLBACK',ARRAY[wr2],wr2,gap,'{}','world-v0');
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_NULL_PROFILE_ACCEPTED'; END IF;

 failed:=false; BEGIN
  INSERT INTO bidding.world_robot(robot_key,display_name,engine_version,model_hash,license_boundary)
  VALUES('bad-robot','Bad','v1',repeat('a',64),'{}');
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_EMPTY_LICENSE_ACCEPTED'; END IF;
 failed:=false; BEGIN
  INSERT INTO bidding.world_robot(robot_key,display_name,engine_version,model_hash,license_boundary)
  VALUES('null-license','Bad','v1',repeat('a',64),
   '{"license_name":null,"license_version":null,"usage_scope":null,"api_boundary":null}');
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_NULL_LICENSE_ACCEPTED'; END IF;

 INSERT INTO bidding.world_robot(robot_key,display_name,engine_version,model_hash,license_boundary)
 VALUES('ben-ci','BEN CI','commit-1',repeat('b',64),
  '{"license_name":"MIT","license_version":"2026-01","usage_scope":"research","api_boundary":"local-only"}')
 RETURNING world_robot_id INTO robot;
 INSERT INTO bidding.world_robot_configuration(world_robot_id,configuration_hash,convention_card,configuration)
 VALUES(robot,repeat('c',64),'{"system":"natural"}','{"temperature":0}') RETURNING world_robot_configuration_id INTO config;
 trace:=jsonb_build_object('mode','ROBOT_LIVE_DECISION','request_id','req-1','engine_key','ben-ci',
  'engine_version','commit-1','model_hash',repeat('b',64),'configuration_hash',repeat('c',64),
  'input_fingerprint','input-1','started_at','2026-08-30T00:00:00Z','completed_at','2026-08-30T00:00:01Z',
  'steps',jsonb_build_array(
    jsonb_build_object('seq',1,'event','request','at','2026-08-30T00:00:00Z','status','ok','input_hash',repeat('1',64),'output_hash',repeat('2',64)),
    jsonb_build_object('seq',2,'event','response','at','2026-08-30T00:00:01Z','status','ok','input_hash',repeat('2',64),'output_hash',repeat('3',64))),
  'raw_response_sha256',encode(digest(raw::text,'sha256'),'hex'));
 INSERT INTO bidding.world_robot_decision(school_id,world_robot_configuration_id,decision_mode,acting_seat,acting_hand,
  public_auction,public_context,raw_response,interpretation,confidence,decision_trace)
 VALUES(s,config,'ROBOT_LIVE_DECISION','N',
  '{"cards":["AC","KC","QC","JC","TC","9C","8C","7C","6C","5C","4C","3C","2C"],"hcp":10,"shape":[13,0,0,0]}',
  '{"calls":[],"dealer":"N"}','{"scoring":"IMP","acting_seat":"N"}',raw,'{"bid":"1S"}','high',trace)
 RETURNING world_robot_decision_id INTO decision;

 -- Legal bid fields are public calls, not hidden card tokens.
 INSERT INTO bidding.world_robot_decision(school_id,world_robot_configuration_id,decision_mode,acting_seat,acting_hand,
  public_auction,public_context,raw_response,interpretation,confidence,decision_trace)
 VALUES(s,config,'ROBOT_LIVE_DECISION','N',
  '{"cards":["AC","KC","QC","JC","TC","9C","8C","7C","6C","5C","4C","3C","2C"]}',
  '{"calls":["1S","PASS"],"dealer":"N"}','{}','{"bid":"2H"}','{"bid":"2H"}','high',
  jsonb_set(trace,'{raw_response_sha256}',to_jsonb(encode(digest('{"bid":"2H"}'::jsonb::text,'sha256'),'hex'))));

 bad_raw:='{"deal":{"N":[]}}';
 bad_trace:=jsonb_set(trace,'{raw_response_sha256}',to_jsonb(encode(digest(bad_raw::text,'sha256'),'hex')));
 failed:=false; v_constraint:=NULL; BEGIN
  INSERT INTO bidding.world_robot_decision(school_id,world_robot_configuration_id,decision_mode,acting_seat,acting_hand,
   public_auction,public_context,raw_response,interpretation,confidence,decision_trace)
  VALUES(s,config,'ROBOT_LIVE_DECISION','N',
   '{"cards":["AC","KC","QC","JC","TC","9C","8C","7C","6C","5C","4C","3C","2C"]}',
   '{"calls":[]}','{}',bad_raw,'{"bid":"1S"}','high',bad_trace);
 EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_constraint=CONSTRAINT_NAME; failed:=(v_constraint='world_robot_decision_public_raw_response'); END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_HIDDEN_DEAL_ACCEPTED'; END IF;

 bad_raw:='{"explanation":"as ks"}';
 bad_trace:=jsonb_set(trace,'{raw_response_sha256}',to_jsonb(encode(digest(bad_raw::text,'sha256'),'hex')));
 failed:=false; v_constraint:=NULL; BEGIN
  INSERT INTO bidding.world_robot_decision(school_id,world_robot_configuration_id,decision_mode,acting_seat,acting_hand,
   public_auction,public_context,raw_response,interpretation,confidence,decision_trace)
  VALUES(s,config,'ROBOT_LIVE_DECISION','N',
   '{"cards":["AC","KC","QC","JC","TC","9C","8C","7C","6C","5C","4C","3C","2C"]}',
   '{"calls":[]}','{}',bad_raw,'{"bid":"1S"}','high',bad_trace);
 EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_constraint=CONSTRAINT_NAME; failed:=(v_constraint='world_robot_decision_public_raw_response'); END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_NESTED_CARD_TOKENS_ACCEPTED'; END IF;

 bad_raw:='{"explanation":"ASKS"}';
 bad_trace:=jsonb_set(trace,'{raw_response_sha256}',to_jsonb(encode(digest(bad_raw::text,'sha256'),'hex')));
 failed:=false; v_constraint:=NULL; BEGIN
  INSERT INTO bidding.world_robot_decision(school_id,world_robot_configuration_id,decision_mode,acting_seat,acting_hand,
   public_auction,public_context,raw_response,interpretation,confidence,decision_trace)
  VALUES(s,config,'ROBOT_LIVE_DECISION','N',
   '{"cards":["AC","KC","QC","JC","TC","9C","8C","7C","6C","5C","4C","3C","2C"]}',
   '{"calls":[]}','{}',bad_raw,'{"bid":"1S"}','high',bad_trace);
 EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_constraint=CONSTRAINT_NAME; failed:=(v_constraint='world_robot_decision_public_raw_response'); END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_PACKED_CARD_TOKENS_ACCEPTED'; END IF;

 failed:=false; v_constraint:=NULL; BEGIN
  INSERT INTO bidding.world_robot_decision(school_id,world_robot_configuration_id,decision_mode,acting_seat,acting_hand,
   public_auction,public_context,raw_response,interpretation,confidence,decision_trace)
  VALUES(s,config,'ROBOT_LIVE_DECISION','N',
   '{"cards":["AC","KC","QC","JC","TC","9C","8C","7C","6C","5C","4C","3C","2C"]}',
   '{"calls":[],"alerts":{"private_material":[51,50,49]}}','{}',raw,'{"bid":"1S"}','high',trace);
 EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_constraint=CONSTRAINT_NAME; failed:=(v_constraint='world_robot_decision_public_auction'); END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_NESTED_AUCTION_MATERIAL_ACCEPTED'; END IF;

 failed:=false; BEGIN
  INSERT INTO bidding.world_robot(robot_key,display_name,engine_version,model_hash,license_boundary)
  VALUES('typed-license','Bad','v1',repeat('a',64),
   '{"license_name":[],"license_version":[],"usage_scope":[],"api_boundary":[]}');
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_NONTEXT_LICENSE_ACCEPTED'; END IF;

 failed:=false; BEGIN
  INSERT INTO bidding.world_robot_decision(school_id,world_robot_configuration_id,decision_mode,acting_seat,acting_hand,
   public_auction,public_context,raw_response,interpretation,confidence,decision_trace)
  VALUES(s,config,'ROBOT_LIVE_DECISION','N',
   '{"cards":["AC","KC","QC","JC","TC","9C","8C","7C","6C","5C","4C","3C","2C"]}',
   '{"calls":[]}','{}',raw,'{"bid":"1S"}','high',
   jsonb_set(jsonb_set(jsonb_set(trace,'{request_id}','""'),'{started_at}','"2026-08-30T00:00:02Z"'),'{steps}','[null]'));
 EXCEPTION WHEN check_violation OR invalid_datetime_format THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_MEANINGLESS_TRACE_ACCEPTED'; END IF;

 failed:=false; BEGIN
  INSERT INTO bidding.world_robot_decision(school_id,world_robot_configuration_id,decision_mode,acting_seat,acting_hand,
   public_auction,public_context,raw_response,interpretation,confidence,decision_trace)
  VALUES(s,config,'ROBOT_LIVE_DECISION','N',
   '{"cards":["AC","KC","QC","JC","TC","9C","8C","7C","6C","5C","4C","3C","2C"]}',
   '{"calls":[]}','{}',raw,'{"bid":"1S"}','high',
   jsonb_set(trace,'{steps}',jsonb_build_array(
     jsonb_build_object('seq',2.5,'event','response','at','2026-08-30T00:00:01Z','status','ok','input_hash','','output_hash','bad'),
     jsonb_build_object('seq',1,'event','request','at','2026-08-30T00:00:00Z','status','ok','input_hash',repeat('1',64),'output_hash',repeat('2',64)))));
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_UNPINNED_REVERSED_TRACE_ACCEPTED'; END IF;

 failed:=false; BEGIN
  INSERT INTO bidding.world_robot_decision(school_id,world_robot_configuration_id,decision_mode,acting_seat,acting_hand,
   public_auction,public_context,raw_response,interpretation,confidence,decision_trace)
  VALUES(s,config,'ROBOT_LIVE_DECISION','N',
   '{"cards":["AC","KC","QC","JC","TC","9C","8C","7C","6C","5C","4C","3C","2C"]}',
   '{"calls":[]}','{}',raw,'{"bid":"1S"}','high',jsonb_set(trace,'{engine_key}','null'::jsonb));
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_NULL_TRACE_PIN_ACCEPTED'; END IF;

 failed:=false; BEGIN
 UPDATE bidding.world_robot_decision SET confidence='low' WHERE world_robot_decision_id=decision;
 EXCEPTION WHEN object_not_in_prerequisite_state THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_DECISION_MUTATION_ACCEPTED'; END IF;

 IF NOT has_table_privilege('bridge_school_app','bidding.world_resolution_trace','INSERT')
    OR NOT has_table_privilege('bridge_school_app','bidding.world_canon_gap_binding','SELECT')
    OR NOT has_table_privilege('bridge_school_app','bidding.rule','SELECT')
    OR NOT has_table_privilege('bridge_school_app','public.knowledge_version','SELECT')
    OR NOT has_column_privilege('bridge_school_app','public.knowledge_gap','school_id','INSERT')
 THEN RAISE EXCEPTION 'WORLD_SMOKE_APP_RUNTIME_ACL_INCOMPLETE'; END IF;
 IF NOT has_table_privilege('bridge_school_worker','bidding.world_robot_decision','INSERT')
    OR NOT has_table_privilege('bridge_school_worker','bidding.world_robot','SELECT')
    OR NOT has_table_privilege('bridge_school_worker','bidding.world_robot_configuration','SELECT')
    OR NOT has_table_privilege('bridge_school_worker','public.knowledge_gap','SELECT')
 THEN RAISE EXCEPTION 'WORLD_SMOKE_WORKER_RUNTIME_ACL_INCOMPLETE'; END IF;
END $$;
ROLLBACK;
