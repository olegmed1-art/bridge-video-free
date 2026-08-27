-- 0200_bidding_knowledge_v0 / part 06
-- Included transactionally by ../0200_bidding_knowledge_v0.sql.

GRANT INSERT (
    school_id,decision_key,request_fingerprint,acting_seat,acting_hand,public_auction,
    public_context,scope_key,knowledge_version_ids,candidate_rule_ids,rejected_candidates,
    selected_rule_id,selected_call,outcome,knowledge_gap_id,explanation,resolver_version
) ON bidding.decision_trace TO bridge_school_app, bridge_school_worker;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON bidding.runtime_activation
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE UPDATE, DELETE, TRUNCATE ON bidding.rule_test_run
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE UPDATE, DELETE, TRUNCATE ON bidding.decision_trace
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE UPDATE, DELETE, TRUNCATE ON bidding.ingestion_event
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE DELETE, TRUNCATE ON ALL TABLES IN SCHEMA bidding
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE ALL ON FUNCTION bidding.contains_forbidden_hidden_key(jsonb) FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.validate_rule_school_scope() FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.validate_rule_relation_school_scope() FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.validate_rule_test_school_scope() FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.validate_rule_test_run_school_scope() FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.validate_rule_conflict_school_scope() FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.prevent_runtime_activation_overlap() FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.latest_test_result(uuid) FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.rule_passes_activation_gates(uuid) FROM PUBLIC;
