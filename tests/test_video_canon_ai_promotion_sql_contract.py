from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION = (ROOT / "database/migrations/0322_workflow_video_canon_ai_promotion.sql").read_text()
ROLLBACK = (ROOT / "database/rollbacks/0322_workflow_video_canon_ai_promotion.sql").read_text()
DATABASE_TEST = (ROOT / "database/tests/322_workflow_video_canon_ai_promotion.sql").read_text()


def test_database_bundle_gate_validates_the_effective_period():
    assert "NEW.bundle_payload->'effective_period'\n          ))<>2" in MIGRATION
    assert "VIDEO_CANON_BUNDLE_EFFECTIVE_PERIOD_INVALID" in MIGRATION
    assert "v_valid_to<=v_valid_from" in MIGRATION
    assert "v_valid_to<=statement_timestamp()" in MIGRATION
    assert "VIDEO_CANON_INVALID_BUNDLE_PERIOD_NOT_BLOCKED" in DATABASE_TEST
    assert "VIDEO_CANON_EXPIRED_BUNDLE_PERIOD_NOT_BLOCKED" in DATABASE_TEST


def test_database_bundle_gate_binds_profile_and_level_to_the_candidate():
    assert "candidate_canonical_json" in MIGRATION
    assert "v_candidate_decoded IS DISTINCT FROM v_candidate.payload" in MIGRATION
    assert "VIDEO_CANON_CANDIDATE_CANONICAL_JSON_INVALID" in MIGRATION
    assert (
        "v_candidate.payload->>'authority_class' IS DISTINCT FROM 'TEACHER_VIDEO'"
    ) in MIGRATION
    assert "VIDEO_CANON_BUNDLE_AUTHORITY_CLASS_NOT_BLOCKED" in DATABASE_TEST
    assert "VIDEO_CANON_CANDIDATE_PAYLOAD_HASH_NOT_RECOMPUTED" in DATABASE_TEST
    assert (
        "NEW.bundle_payload->>'system_profile'\n"
        "            IS DISTINCT FROM v_candidate.payload->>'system_profile'"
    ) in MIGRATION
    assert (
        "NEW.bundle_payload->>'learner_level'\n"
        "            IS DISTINCT FROM v_candidate.payload->>'learner_level'"
    ) in MIGRATION
    assert "VIDEO_CANON_BUNDLE_PROFILE_MISMATCH_NOT_BLOCKED_" in DATABASE_TEST


def test_provider_identity_allows_only_operational_job_refresh_metadata():
    assert "ARRAY['display_name','last_seen_at','attributes']" in MIGRATION
    assert "COALESCE(NEW.attributes,'{}'::jsonb)-'job_id'" in MIGRATION
    assert "COALESCE(OLD.attributes,'{}'::jsonb)-'job_id'" in MIGRATION
    assert "VIDEO_CANON_PROVIDER_OPERATIONAL_REFRESH_BLOCKED" in DATABASE_TEST
    assert "VIDEO_CANON_PROMOTED_PROVIDER_ATTRIBUTES_MUTATION_NOT_BLOCKED" in DATABASE_TEST


def test_correction_receipt_seals_the_correction_kind():
    assert "jsonb_object_length(NEW.receipt_payload)<>9" in MIGRATION
    assert "'correction_id','kind','reviewer_ref'" in MIGRATION
    assert "NEW.receipt_payload->>'kind' NOT IN" in MIGRATION


def test_database_bundle_gate_validates_the_complete_rollback_contract():
    assert "NEW.bundle_payload->'rollback' ?& ARRAY[" in MIGRATION
    assert "NEW.bundle_payload->'rollback'\n          ))<>5" in MIGRATION
    assert "'{rollback,restore_test_sha256}'" in MIGRATION
    assert "NEW.bundle_payload#>>'{rollback,result}'<>'PASS'" in MIGRATION
    assert "VIDEO_CANON_BUNDLE_ROLLBACK_TARGET_PAIR_INVALID" in MIGRATION
    assert "VIDEO_CANON_ROLLBACK_SHAPE_NOT_BLOCKED" in DATABASE_TEST


def test_ai_promotion_is_narrow_guarded_and_not_granted_to_general_workers():
    assert "bridge_school_canon_verifier','bridge_school_canon_semantic_verifier" in MIGRATION
    assert "bridge_school_canon_bridge_verifier','bridge_school_canon_firewall_verifier" in MIGRATION
    assert "bridge_school_canon_control_verifier','bridge_school_canon_promoter" in MIGRATION
    assert "SECURITY DEFINER" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION bidding.activate_ai_verified_video_canon" in MIGRATION
    assert "TO bridge_school_canon_promoter" in MIGRATION
    assert "GRANT INSERT ON bidding.video_canon_ai_verification_bundle TO bridge_school_canon_verifier" in MIGRATION
    assert "GRANT INSERT ON bidding.video_canon_ai_verification TO" in MIGRATION
    assert "FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_canon_verifier" in MIGRATION
    assert "promotion_mode','AI_VERIFIED_TEACHER_VIDEO'" in MIGRATION
    assert "'human_approval_required',false" in MIGRATION
    assert "CREATE TABLE bidding.video_correction_review_receipt" in MIGRATION
    assert "'CORRECTION_REVIEW'=ANY(v_principal.allowed_check_ids)" in MIGRATION
    assert "GRANT INSERT ON bidding.video_correction_review_receipt TO bridge_school_canon_control_verifier" in MIGRATION
    assert "GRANT SELECT ON bidding.video_correction_review_receipt TO bridge_school_worker" in MIGRATION
    assert "VIDEO_CANON_ROLE_COLLISION" in MIGRATION
    assert "CREATE VIEW bidding.video_canon_bound_candidate" in MIGRATION
    assert "check_row.value->>'execution_principal'=session_user::text" in MIGRATION
    assert "REVOKE SELECT ON public.analysis_candidate,bidding.video_canon_ai_verification_bundle" in MIGRATION
    assert "bridge_school_canon_restorer" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION bidding.restore_ai_verified_video_canon" in MIGRATION
    assert "TO bridge_school_canon_restorer" in MIGRATION


def test_gate_requires_source_binding_all_ai_checks_independence_and_rule_tests():
    for marker in (
        "source_sha256=v_candidate.payload#>>'{source,source_sha256}'",
        "video_file_id=v_candidate.payload#>>'{source,video_file_id}'",
        "SEMANTIC_PARSE",
        "BRIDGE_LOGIC",
        "HIDDEN_INFORMATION_FIREWALL",
        "v_semantic_family=v_bridge_family",
        "('positive'),('negative'),('boundary'),('interference'),('hidden_information'),('regression')",
        "c.status='open'",
        "ROLLBACK_RESTORE",
        "public.source s ON s.source_id=p.source_id AND s.school_id=p.school_id",
        "VIDEO_CANON_SOURCE_POLICY_SCHOOL_MISMATCH",
        "guard_video_canon_source_policy_lifecycle",
        "video_canon_source_policy_active_identity_idx",
        ") WHERE status='active'",
        "guard_promoted_video_canon_source_binding",
        "promoted_video_canon_source_binding_guard",
        "VIDEO_CANON_PROMOTED_SOURCE_BINDING_IMMUTABLE",
        "guard_promoted_video_canon_source_identity",
        "promoted_video_canon_source_identity_guard",
        "VIDEO_CANON_PROMOTED_SOURCE_IDENTITY_IMMUTABLE",
        "guard_promoted_video_canon_provider_identity",
        "promoted_video_canon_provider_identity_guard",
        "VIDEO_CANON_PROMOTED_PROVIDER_IDENTITY_IMMUTABLE",
        "public.source,public.source_identity,public.knowledge_version_source",
        "video_canon_drive_source_id",
        "source_native_key='google-drive:'||p.video_file_id",
        "si.attributes->>'provider'='google_drive'",
        "si.attributes->>'drive_file_id'=p.video_file_id",
        "p.source_id=bidding.video_canon_drive_source_id(p.video_file_id)",
        "FOR SHARE OF p,s,si",
        "VIDEO_CANON_BUNDLE_ROLLBACK_TARGET_PAIR_INVALID",
        "guard_promoted_video_canon_knowledge_version",
        "promoted_video_canon_knowledge_version_guard",
        "VIDEO_CANON_PROMOTED_KNOWLEDGE_VERSION_IMMUTABLE",
        "guard_promoted_video_canon_knowledge_item",
        "promoted_video_canon_knowledge_item_guard",
        "VIDEO_CANON_PROMOTED_KNOWLEDGE_ITEM_IMMUTABLE",
        "guard_promoted_video_canon_rule",
        "promoted_video_canon_rule_guard",
        "VIDEO_CANON_PROMOTED_RULE_IMMUTABLE",
        "guard_promoted_video_canon_rule_test",
        "promoted_video_canon_rule_test_guard",
        "VIDEO_CANON_PROMOTED_RULE_TEST_IMMUTABLE",
        "guard_superseded_video_canon_rule_test_run",
        "superseded_video_canon_rule_test_run_guard",
        "VIDEO_CANON_SUPERSEDED_RULE_TEST_RUN_IMMUTABLE",
        "p.superseded_canon_activation_id=ca.canon_activation_id",
        "WHERE ca.status='superseded'",
        "|(?:[[:space:]]*[:,;=\\\\-][[:space:]]*))([^;]*)",
        "JOIN public.canon_activation ca",
        "ca.canon_activation_id=p.canon_activation_id",
        "status='superseded',valid_to=v_valid_from",
        "p.valid_from<=clock_timestamp()",
        "v_valid_from>statement_timestamp()",
        "p.system_profile=v_bundle.bundle_payload->>'system_profile'",
        "p.learner_level=v_bundle.bundle_payload->>'learner_level'",
        "v_candidate.payload->>'system_profile'\n            IS DISTINCT FROM v_bundle.bundle_payload->>'system_profile'",
        "v_candidate.payload->>'learner_level'\n            IS DISTINCT FROM v_bundle.bundle_payload->>'learner_level'",
        "VIDEO_CANON_VERIFIER_PRINCIPAL_MISMATCH",
        "database_role=current_user",
        "JOIN bidding.video_canon_verifier_registry vr",
        "vr.status='active'",
        "v.check_id=ANY(vr.allowed_check_ids)",
        "NEW.execution_principal<>session_user",
        "v_semantic_principal=v_bridge_principal",
        "current_school_canon_snapshot_sha256",
        "VIDEO_CANON_STATE_CHECKS_STALE",
        "v.canon_snapshot_sha256=v_canon_snapshot_sha256",
        "is_video_canon_semantic_confidence_eligible",
        "NOT bidding.is_video_canon_semantic_confidence_eligible(v_candidate.payload)",
        "jsonb_typeof(p_payload->'semantic_confidence')='number'",
        "IN ('NaN','Infinity','-Infinity')",
        "BETWEEN 0.95 AND 1",
        "contains_forbidden_hidden_value(v_candidate.payload)",
        "WITH RECURSIVE walk(value,actor_context)",
        "w.actor_context OR regexp_replace(",
        "партн[её]р|соперник|оппонент",
        "партн[её]ра|соперника|оппонента",
        "'active_rule_tests',active_rule_tests.rows",
        "'active_rule_sources',active_rule_sources.rows",
        "'active_canon_rules',active_canon_rules.rows",
        "SELECT ca.* FROM public.canon_activation ca",
        "bidding.rule_conflict,bidding.video_canon_verifier_registry,",
        "LOCK TABLE public.analysis_candidate,public.canon_activation",
        "bidding.runtime_activation,bidding.rule,bidding.rule_test,bidding.rule_test_run,",
        "bidding.video_canon_ai_promotion_receipt IN SHARE ROW EXCLUSIVE MODE",
        "bidding.video_canon_ai_restore_receipt IN SHARE ROW EXCLUSIVE MODE",
        "ORDER BY runtime_activation_id",
        "Lock every restoration target before any final authority check",
        "Phase 1: validate every prelocked runtime target without mutation",
        "Capture the exact mutation boundary once",
        "Phase 2: all targets are locked and validated",
        "Capability membership is external to these tables",
        "VIDEO_CANON_RESTORE_CURRENT_ACTIVATION_EXPIRED",
        "v_new_canon.valid_to<=clock_timestamp()",
        "v_new_runtime.valid_to<=clock_timestamp()",
        "UPDATE bidding.runtime_activation target",
        "v_retired_at := clock_timestamp()",
        "NEW.valid_to := v_retired_at",
        "VIDEO_CANON_PROMOTED_VERIFICATION_SET_CLOSED",
        "jsonb_array_length(NEW.bundle_payload->'checks')<>16",
        "VIDEO_CANON_RESTORE_VALIDATION_GATES_FAILED",
        "VIDEO_CANON_RESTORE_SOURCE_POLICY_INACTIVE",
        "to_regclass('bidding.video_canon_promotion_delivery_receipt') IS NOT NULL",
        "bidding.video_canon_assurance_verdict i2",
        "bidding.video_canon_assurance_verdict i3",
        "dr.assurance_set_sha256",
        "a2.status='active'",
        "a3.status='active'",
        "r2.capability_role='bridge_school_canon_i2_verifier'",
        "r3.capability_role='bridge_school_canon_i3_verifier'",
        "pg_has_role(attestor.oid,r2.capability_role,'MEMBER')",
        "pg_has_role(attestor.oid,r3.capability_role,'MEMBER')",
        "VIDEO_CANON_RESTORE_I2_I3_ASSURANCE_REVOKED",
        "VIDEO_CANON_RESTORE_CANON_VERSION_GATES_FAILED",
        "VIDEO_CANON_RESTORE_TARGET_BINDING_MISMATCH",
        "VIDEO_CANON_RESTORE_BUNDLE_NOT_FOUND",
        "VIDEO_CANON_RESTORE_RULE_CONTENT_MISMATCH",
        "superseded_rule_state",
        "superseded_rule_test_state",
        "v_prior_rule_test_state",
        "v_current_prior_rule_test_state",
        "VIDEO_CANON_RESTORE_PREDECESSOR_TEST_STATE_MISMATCH",
        "superseded_source_state",
        "superseded_knowledge_version_content_sha256",
        "superseded_knowledge_item_content_sha256",
        "v_prior_version_content_sha256",
        "v_prior_item_content_sha256",
        "to_jsonb(v_prior_version)-ARRAY['created_at']",
        "VIDEO_CANON_RESTORE_PREDECESSOR_VERSION_MISMATCH",
        "VIDEO_CANON_RESTORE_PREDECESSOR_ITEM_MISMATCH",
        "v_prior_source_state",
        "v_current_prior_source_state",
        "jsonb_agg(to_jsonb(kvs)",
        "ca.canon_activation_id=p.superseded_canon_activation_id",
        "VIDEO_CANON_RESTORE_SOURCE_SET_MISMATCH",
        "video_canon_rule_restore_sha256",
        "to_jsonb(r)-ARRAY['created_at','updated_at']",
        "v_valid_to<=statement_timestamp()",
        "SET TimeZone='UTC'",
        "WITH effective_canon AS",
        "LANGUAGE sql\nVOLATILE\nSECURITY DEFINER",
        "ca.valid_from<=clock_timestamp()",
        "v_valid_to<=clock_timestamp()",
        "v_new_canon.valid_to<=v_revoked_at",
        "is_complete_bridge_hand",
        "matched.parts[4]",
        "recorded_by_principal text NOT NULL DEFAULT session_user",
        "NEW.recorded_by_principal<>session_user",
        "pg_has_role(login_role.oid,v_principal.database_role,'MEMBER')",
        "VIDEO_CANON_VERIFIER_PRINCIPAL_REVOKED",
        "attestor.rolname=v.execution_principal",
        "VIDEO_CANON_KNOWLEDGE_VERSION_BINDING_INVALID",
        "video_canon_semantic_identity_sha256",
        "semantic_identity_sha256",
        "v_version.content<>v_candidate.payload",
        "v_version.provenance<>v_expected_version_provenance",
        "knowledge_version_content_sha256",
        "rule_test_state_sha256",
        "video_canon_rule_test_state_sha256",
        "VIDEO_CANON_RULE_TEST_BINDING_INVALID",
        "VIDEO_CANON_RULE_TEST_STATE_MISMATCH",
        "WHERE t.rule_id=p_rule_id AND t.enabled\n         AND bidding.latest_test_result(t.rule_test_id) IS DISTINCT FROM 'pass'",
        "VIDEO_CANON_RESTORE_RULE_TEST_STATE_MISMATCH",
        "'video-canon:'||v_candidate.payload_hash||':'",
        "expected.case_payload-'expect'",
        "VIDEO_CANON_RESTORE_SOURCE_BINDING_MISMATCH",
        "kvs.source_locator=jsonb_build_object(",
        "ki.stable_key='video-canon:'||v_candidate.payload->>'semantic_identity_sha256'",
        "VIDEO_CANON_SOURCE_POLICY_EXPIRED",
        "VIDEO_CANON_RESTORE_ATTESTOR_REVOKED",
        "VIDEO_CANON_RESTORE_VERSION_CONTENT_MISMATCH",
        "v_prior_version_content_sha256",
        "v_revoked_at := clock_timestamp()",
        "SET status='revoked',valid_to=v_revoked_at",
        "replace(upper(COALESCE(p_hand,'')),'10','T')",
        "(?:10)",
        "|(?:^|[^[:alnum:]])[NESW][[:space:]]*:)[^;]*?S",
        "{1,13})[[:space:]/,]+",
        "'♠','S:'",
        "'♥','H:'",
        "'♦','D:'",
        "'♣','C:'",
        "([^;]*)",
        "matched.parts[1] ~*",
        "[SHDC][[:space:]]*:",
        "[SHDC][[:space:]]*:?[[:space:]]*",
        "{1,3}($|[^[:alnum:]])",
        "{2,13})($|[^[:alnum:]])",
        "(?:10|[AKQJTX]|",
        "(?:^|[^[:alnum:]_])(?:partner|opponent|north|east|south|west|[lr]",
        "(?:^|[^[:alnum:]_])(?:рука|карты)",
        "?[2-9]($|[[:space:],./;])",
        "hearts?|spades?|diamonds?|clubs?",
        "trumps?|losers?|points?|hcp|controls?",
        "карт[[:alnum:]_]*|черв[[:alnum:]_]*|пик[[:alnum:]_]*",
        "(?:held|holds?|has|had|owns?|possesses?|retains?|carries?)",
        "(?:is|was)[[:space:]]+(?:(?:currently|still|now|already",
        "[''’]s(?:[[:space:]]+(?:(?:currently|still|now|already",
        "(?:[-–—]|to)[[:space:]]*[0-9]{1,2}|[+]",
        "does[[:space:]]+not|doesn[''’]t",
        "(?:no|neither)",
        "none[[:space:]]+of",
        "|lacks?",
        "jsonb_typeof(v_candidate.payload->'ambiguities') IS DISTINCT FROM 'array'",
        "jsonb_typeof(v_candidate.payload->'contradictions') IS DISTINCT FROM 'array'",
        "v_candidate.payload->>'schema' IS DISTINCT FROM 'video-canon-evidence-v2'",
        "v_candidate.payload->>'review_eligibility' IS DISTINCT FROM 'AI_VERIFICATION_PENDING'",
        "IS DISTINCT FROM v_candidate.payload->>'semantic_scope'",
        "p.valid_from<=v_prior_canon.valid_from",
        "p.valid_to>v_prior_canon.valid_from",
        "p.system_profile=c.payload->>'system_profile'",
        "p.learner_level=c.payload->>'learner_level'",
        "p.system_profile=v_prior_version.bidding_system_key",
        "p.learner_level=v_prior_version.level_scope->>'level_key'",
    ):
        assert marker in MIGRATION

    assert "У партнёра туз пик" in DATABASE_TEST
    assert "Partner has AKQx.Txx.xxx.xxx" in DATABASE_TEST
    assert "У партнёра есть туз" in DATABASE_TEST
    assert "У партнёра A♠" in DATABASE_TEST
    assert '"partnerDeal":"AKQ"' in DATABASE_TEST
    assert '"north/deal":"AKQ"' in DATABASE_TEST
    assert '"рука_партнера":"AKQ"' in DATABASE_TEST
    assert '"карты соперника":"AS"' in DATABASE_TEST
    assert '"противник":{"cards":"AS"}' in DATABASE_TEST
    assert "партн[её]ра|соперника|оппонента|противника" in MIGRATION
    assert '"lho":{"cards":"AS"}' in DATABASE_TEST
    assert '"left_hand_opponent":{"cards":"AS"}' in DATABASE_TEST
    assert '"rightHandOpponent":{"holding":"QH"}' in DATABASE_TEST
    assert '"left_opponent":{"cards":"AS"}' in DATABASE_TEST
    assert '"rightOpponent":{"holding":"QH"}' in DATABASE_TEST
    assert '"leftOpponentCards":"AS"' in DATABASE_TEST
    assert "leftopponent|rightopponent" in MIGRATION
    assert "lefthandopponent|righthandopponent" in MIGRATION
    assert "RHO has the queen of hearts" in DATABASE_TEST
    assert "L.H.O has the ace of spades" in DATABASE_TEST
    assert "R.H.O. has the queen of hearts" in DATABASE_TEST
    assert "VIDEO_CANON_SOURCE_POLICY_SUCCESSOR_HISTORY_NOT_PRESERVED" in DATABASE_TEST
    assert "VIDEO_CANON_BUNDLE_SCOPE_MISMATCH_NOT_BLOCKED" in DATABASE_TEST
    assert "VIDEO_CANON_BUNDLE_PROFILE_MISMATCH_NOT_BLOCKED_" in DATABASE_TEST
    assert "VIDEO_CANON_FUTURE_BUNDLE_PERIOD_NOT_BLOCKED" in DATABASE_TEST
    assert "VIDEO_CANON_ROLLBACK_TARGET_PAIR_NOT_BLOCKED" in DATABASE_TEST
    assert "VIDEO_CANON_DRIVE_SOURCE_ID_NOT_CANONICAL" in DATABASE_TEST
    assert "VIDEO_CANON_PROMOTED_PROVIDER_IDENTITY_MUTATION_NOT_BLOCKED" in DATABASE_TEST
    assert "VIDEO_CANON_PROVIDER_OPERATIONAL_REFRESH_BLOCKED" in DATABASE_TEST
    assert "VIDEO_CANON_PROMOTED_PROVIDER_ATTRIBUTES_MUTATION_NOT_BLOCKED" in DATABASE_TEST
    assert "VIDEO_CANON_PROMOTED_PROVIDER_IDENTITY_DELETE_NOT_BLOCKED" in DATABASE_TEST
    assert MIGRATION.count("VIDEO_CANON_RESTORE_I2_I3_ASSURANCE_REVOKED") == 2
    final_boundary = MIGRATION.split(
        "Role membership is maintained outside the locked application", 1
    )[1].split("Phase 2: all targets are locked and validated", 1)[0]
    assert "outer branch is prepared without resolving relations" in final_boundary
    assert "to_regclass('bidding.video_canon_promotion_delivery_receipt') IS NOT NULL THEN" in final_boundary
    assert "video_canon_assurance_verdict i2" in final_boundary
    assert "video_canon_assurance_verdict i3" in final_boundary
    assert "pg_has_role(attestor.oid,r2.capability_role,'MEMBER')" in final_boundary
    assert "pg_has_role(attestor.oid,r3.capability_role,'MEMBER')" in final_boundary

    assert "'hidden_cards','hidden_hand','hidden_hands'" in MIGRATION
    assert "'concealed_hand','concealed_hands'" in MIGRATION
    assert "'hidden_deal','hidden_deals','concealed_deal','concealed_deals'" in MIGRATION
    assert "'^(hidden|concealed)(hand|holding|cards?|deals?)+(s)?$'" in MIGRATION
    assert "(hand|holding|cards?|deals?)+(s)?$" in MIGRATION


def test_promotion_is_content_bound_idempotent_and_has_fail_closed_rollback():
    assert "DROP CONSTRAINT rule_school_id_rule_key_key" in MIGRATION
    assert "CREATE TABLE bidding.rule_key_identity" in MIGRATION
    assert "PRIMARY KEY (school_id,rule_key)" in MIGRATION
    assert "CREATE OR REPLACE FUNCTION bidding.bind_rule_key_identity" in MIGRATION
    assert "ON CONFLICT (school_id,rule_key) DO UPDATE" in MIGRATION
    assert "BIDDING_RULE_KEY_IDENTITY_MISMATCH" in MIGRATION
    assert "BEFORE INSERT OR UPDATE OF school_id,knowledge_version_id,rule_key" in MIGRATION
    assert "video_candidate_payload_hash" in MIGRATION
    assert "VIDEO_CANON_IDEMPOTENT_RECEIPT_STALE" in MIGRATION
    assert "VIDEO_CANON_ACTIVE_IDEMPOTENT_REPLAY_FAILED" in DATABASE_TEST
    assert "VIDEO_CANON_STALE_IDEMPOTENT_REPLAY_NOT_BLOCKED" in DATABASE_TEST
    assert "VIDEO_CANON_RESTORE_RECEIPT_STALE" in MIGRATION
    assert "VIDEO_CANON_STALE_RESTORE_RECEIPT_REPLAY_NOT_BLOCKED" in DATABASE_TEST
    assert MIGRATION.count(
        "active_kv.knowledge_item_id=revoked_kv.knowledge_item_id"
    ) == 2
    restore_replay = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION bidding.restore_ai_verified_video_canon", 1
    )[1]
    first_receipt = restore_replay.index(
        "SELECT * INTO v_existing FROM bidding.video_canon_ai_restore_receipt"
    )
    advisory_lock = restore_replay.index("pg_advisory_xact_lock", first_receipt)
    first_return = restore_replay.index(
        "RETURN v_existing.video_canon_ai_restore_receipt_id", first_receipt
    )
    assert advisory_lock < first_return
    assert "candidate_payload_hash=v_candidate.payload_hash" in MIGRATION
    assert "verification_bundle_sha256" in MIGRATION
    assert "bundle_canonical_json" in MIGRATION
    assert "count(*) FROM jsonb_object_keys(NEW.bundle_payload))<>13" in MIGRATION
    assert "NEW.bundle_payload->'candidate_payload' IS DISTINCT FROM v_candidate.payload" in MIGRATION
    assert "v_candidate.quality_status<>'AI_VERIFICATION_PENDING'" in MIGRATION
    assert "p.candidate_payload_hash=OLD.payload_hash" in MIGRATION
    assert "video_canon_runtime_scope_key" in MIGRATION
    assert "v_existing.semantic_scope<>v_semantic_scope" in MIGRATION
    assert "v_prior_promotion.semantic_scope=ANY(p.semantic_scopes)" in MIGRATION
    assert "get_school_runtime_rule_catalog(uuid,text,text,text)" in MIGRATION
    assert "VIDEO_CANON_BOUND_CANDIDATE_MUTATION_FORBIDDEN" in MIGRATION
    assert "v.verification_bundle_sha256=p_verification_bundle_sha256" in MIGRATION
    assert "VIDEO_CANON_IDEMPOTENCY_MISMATCH" in MIGRATION
    assert "v_existing.scope_key<>v_scope_key" in MIGRATION
    assert "v_existing.rule_id<>p_rule_id" in MIGRATION
    assert "WHERE rule_id=p_rule_id FOR UPDATE" in MIGRATION
    assert "v_rule_content_sha256<>v_expected_rule_content_sha256" in MIGRATION
    assert "superseded_canon_activation_id" in MIGRATION
    assert "WHERE analysis_candidate_id=p_analysis_candidate_id FOR UPDATE" in MIGRATION
    assert "RETURN v_existing.video_canon_ai_promotion_receipt_id" in MIGRATION
    assert "rollback refused: Video-to-Canon state exists" in ROLLBACK
    assert "GROUP BY school_id,rule_key HAVING count(*)>1" in ROLLBACK
    assert "EXISTS (SELECT 1 FROM bidding.video_correction_review_receipt)" in ROLLBACK
    assert "CREATE TABLE bidding.video_canon_ai_restore_receipt" in MIGRATION
    assert "superseded_runtime_state" in MIGRATION
    assert "VIDEO_CANON_RESTORE_CURRENT_ACTIVATION_MISMATCH" in MIGRATION
    assert "DROP FUNCTION bidding.restore_ai_verified_video_canon" in ROLLBACK
    assert "DROP VIEW bidding.video_canon_bound_candidate" in ROLLBACK
    assert "DROP TRIGGER promoted_video_canon_source_binding_guard" in ROLLBACK
    assert "DROP TRIGGER promoted_video_canon_source_identity_guard" in ROLLBACK
    assert "DROP TRIGGER promoted_video_canon_provider_identity_guard" in ROLLBACK
    assert "DROP TRIGGER promoted_video_canon_knowledge_version_guard" in ROLLBACK
    assert "DROP TRIGGER promoted_video_canon_knowledge_item_guard" in ROLLBACK
    assert "DROP TRIGGER promoted_video_canon_rule_guard" in ROLLBACK
    assert "DROP TRIGGER promoted_video_canon_rule_test_guard" in ROLLBACK
    assert "DROP TRIGGER superseded_video_canon_rule_test_run_guard" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_promoted_video_canon_source_binding" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_promoted_video_canon_source_identity" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_promoted_video_canon_provider_identity" in ROLLBACK
    assert "DROP FUNCTION bidding.video_canon_drive_source_id" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_promoted_video_canon_knowledge_version" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_promoted_video_canon_knowledge_item" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_promoted_video_canon_rule" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_promoted_video_canon_rule_test" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_superseded_video_canon_rule_test_run" in ROLLBACK
    assert "DROP FUNCTION bidding.video_canon_rule_test_state_sha256" in ROLLBACK
    assert "DROP FUNCTION bidding.video_canon_rule_restore_sha256" in ROLLBACK
    assert "DROP FUNCTION bidding.video_canon_semantic_identity_sha256" in ROLLBACK
    assert "DROP FUNCTION bidding.video_canon_runtime_scope_key" in ROLLBACK
    assert "DROP FUNCTION bidding.get_school_runtime_rule_catalog(uuid,text,text,text)" in ROLLBACK
    assert "DROP FUNCTION bidding.bind_rule_key_identity" in ROLLBACK
    assert "DROP TABLE bidding.rule_key_identity" in ROLLBACK
    assert "ADD CONSTRAINT rule_school_id_rule_key_key" in ROLLBACK
    assert "DROP FUNCTION bidding.is_complete_bridge_hand" in ROLLBACK
    assert "DROP FUNCTION bidding.is_video_canon_semantic_confidence_eligible" in ROLLBACK
    assert "REVOKE ALL PRIVILEGES ON SCHEMA public,bidding" in ROLLBACK
    assert "DROP ROLE bridge_school_canon_verifier" in ROLLBACK
    assert "CREATE OR REPLACE FUNCTION bidding.contains_forbidden_hidden_key(payload jsonb)" in ROLLBACK
    assert "'hidden_hand'" not in ROLLBACK
    assert "'concealed_hand'" not in ROLLBACK
    assert "'hidden_deal'" not in ROLLBACK
    assert "'concealed_deal'" not in ROLLBACK
    assert MIGRATION.count(
        "IF v_valid_to IS NOT NULL AND v_valid_to<=clock_timestamp() THEN"
    ) >= 3
    assert MIGRATION.count(
        "v_promotion.superseded_canon_valid_to<=clock_timestamp()"
    ) >= 1
    assert MIGRATION.count(
        "v_promotion.superseded_canon_valid_to<=v_revoked_at"
    ) >= 1
    assert MIGRATION.count(
        "v_new_canon.valid_to<=v_revoked_at"
    ) >= 2
    assert MIGRATION.count(
        "v_new_runtime.valid_to<=v_revoked_at"
    ) >= 2
    assert MIGRATION.count(
        "VIDEO_CANON_RESTORE_CURRENT_ACTIVATION_EXPIRED"
    ) >= 2


def test_activation_binds_candidate_profile_and_requires_every_enabled_test_to_pass():
    assert (
        "v_candidate.payload->>'system_profile'\n"
        "            IS DISTINCT FROM v_bundle.bundle_payload->>'system_profile'"
    ) in MIGRATION
    assert (
        "v_candidate.payload->>'learner_level'\n"
        "            IS DISTINCT FROM v_bundle.bundle_payload->>'learner_level'"
    ) in MIGRATION
    assert "v_system_profile" not in MIGRATION
    assert "v_learner_level" not in MIGRATION

    all_enabled_gate = """SELECT 1 FROM bidding.rule_test t
       WHERE t.rule_id=p_rule_id AND t.enabled
         AND bidding.latest_test_result(t.rule_test_id) IS DISTINCT FROM 'pass'"""
    assert all_enabled_gate in MIGRATION
    assert "'hidden_information','regression'\n         )\n         AND bidding.latest_test_result" not in MIGRATION
