-- 0200_bidding_knowledge_v0 / part 07
-- Included transactionally by ../0200_bidding_knowledge_v0.sql.

REVOKE ALL ON FUNCTION bidding.enforce_runtime_activation() FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.rule_is_currently_active(uuid) FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.reject_active_rule_mutation() FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.reject_active_rule_test_mutation() FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.reject_active_rule_relation_mutation() FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.validate_decision_trace_school_scope() FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.enforce_ingestion_run_integrity() FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.validate_ingestion_event() FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.reject_append_only_mutation() FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.get_school_runtime_rule_catalog(uuid,text) FROM PUBLIC;

REVOKE ALL ON FUNCTION bidding.get_research_rule_catalog(uuid,text,boolean) FROM PUBLIC;

REVOKE EXECUTE ON FUNCTION bidding.contains_forbidden_hidden_key(jsonb)
    FROM bridge_school_reader, bridge_school_app;

REVOKE EXECUTE ON FUNCTION bidding.validate_rule_school_scope()
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.validate_rule_relation_school_scope()
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.validate_rule_test_school_scope()
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
