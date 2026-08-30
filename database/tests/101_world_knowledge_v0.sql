\set ON_ERROR_STOP on
BEGIN;
DO $$
DECLARE
 s uuid; oi uuid; ov uuid; wr uuid; gap uuid; robot uuid; config uuid; decision uuid;
 failed boolean; raw jsonb := '{"bid":"1S"}'::jsonb; trace jsonb;
BEGIN
 SELECT school_id INTO s FROM public.school WHERE stable_name='Школа спортивного бриджа';
 INSERT INTO public.knowledge_gap(school_id,question,context_scope,status)
 VALUES(s,'CI canon gap','{"request":"world-ci"}','open') RETURNING knowledge_gap_id INTO gap;
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
   effective_at,auction_context_id,canon_outcome,world_outcome,world_rule_ids,knowledge_gap_id,trace,resolver_version)
  VALUES(s,'fallback-without-selection','natural','v1','L1',now(),'auction-1','CANON_GAP','WORLD_FALLBACK',ARRAY[wr],gap,'{}','world-v0');
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_FALLBACK_WITHOUT_SELECTION_ACCEPTED'; END IF;

 failed:=false; BEGIN
  INSERT INTO bidding.world_resolution_trace(school_id,request_fingerprint,system_profile_key,system_version,learner_level,
   effective_at,auction_context_id,canon_outcome,world_outcome,world_rule_ids,selected_world_rule_id,knowledge_gap_id,trace,resolver_version)
  VALUES(s,'conflict-with-selection','natural','v1','L1',now(),'auction-1','CANON_GAP','WORLD_CONFLICT',ARRAY[wr],wr,gap,'{}','world-v0');
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_CONFLICT_WITH_SELECTION_ACCEPTED'; END IF;

 failed:=false; BEGIN
  INSERT INTO bidding.world_resolution_trace(school_id,request_fingerprint,system_profile_key,system_version,learner_level,
   effective_at,auction_context_id,canon_outcome,world_outcome,world_rule_ids,selected_world_rule_id,knowledge_gap_id,trace,resolver_version)
  VALUES(s,'profile-mix','sayc','v1','L1',now(),'auction-1','CANON_GAP','WORLD_FALLBACK',ARRAY[wr],wr,gap,'{}','world-v0');
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_PROFILE_MISMATCH_ACCEPTED'; END IF;

 failed:=false; BEGIN
  INSERT INTO bidding.world_robot(robot_key,display_name,engine_version,model_hash,license_boundary)
  VALUES('bad-robot','Bad','v1',repeat('a',64),'{}');
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_EMPTY_LICENSE_ACCEPTED'; END IF;

 INSERT INTO bidding.world_robot(robot_key,display_name,engine_version,model_hash,license_boundary)
 VALUES('ben-ci','BEN CI','commit-1',repeat('b',64),
  '{"license_name":"MIT","license_version":"2026-01","usage_scope":"research","api_boundary":"local-only"}')
 RETURNING world_robot_id INTO robot;
 INSERT INTO bidding.world_robot_configuration(world_robot_id,configuration_hash,convention_card,configuration)
 VALUES(robot,repeat('c',64),'{"system":"natural"}','{"temperature":0}') RETURNING world_robot_configuration_id INTO config;
 trace:=jsonb_build_object('mode','ROBOT_LIVE_DECISION','request_id','req-1','engine_key','ben-ci',
  'engine_version','commit-1','model_hash',repeat('b',64),'configuration_hash',repeat('c',64),
  'input_fingerprint','input-1','started_at','2026-08-30T00:00:00Z','completed_at','2026-08-30T00:00:01Z',
  'steps',jsonb_build_array('request','response'),'raw_response_sha256',encode(digest(raw::text,'sha256'),'hex'));
 INSERT INTO bidding.world_robot_decision(school_id,world_robot_configuration_id,decision_mode,acting_seat,acting_hand,
  public_auction,public_context,raw_response,interpretation,confidence,decision_trace)
 VALUES(s,config,'ROBOT_LIVE_DECISION','N',
  '{"cards":["AC","KC","QC","JC","TC","9C","8C","7C","6C","5C","4C","3C","2C"],"hcp":10,"shape":[13,0,0,0]}',
  '{"calls":[],"dealer":"N"}','{"scoring":"IMP","acting_seat":"N"}',raw,'{"bid":"1S"}','high',trace)
 RETURNING world_robot_decision_id INTO decision;

 failed:=false; BEGIN
  INSERT INTO bidding.world_robot_decision(school_id,world_robot_configuration_id,decision_mode,acting_seat,acting_hand,
   public_auction,public_context,raw_response,interpretation,confidence,decision_trace)
  VALUES(s,config,'ROBOT_LIVE_DECISION','N',
   '{"cards":["AC","KC","QC","JC","TC","9C","8C","7C","6C","5C","4C","3C","2C"]}',
   '{"calls":[]}','{}','{"deal":{"N":[]}}','{"bid":"1S"}','high',trace);
 EXCEPTION WHEN check_violation THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_HIDDEN_DEAL_ACCEPTED'; END IF;

 failed:=false; BEGIN
  UPDATE bidding.world_robot_decision SET confidence='low' WHERE world_robot_decision_id=decision;
 EXCEPTION WHEN object_not_in_prerequisite_state THEN failed:=true; END;
 IF NOT failed THEN RAISE EXCEPTION 'WORLD_SMOKE_DECISION_MUTATION_ACCEPTED'; END IF;
END $$;
ROLLBACK;
