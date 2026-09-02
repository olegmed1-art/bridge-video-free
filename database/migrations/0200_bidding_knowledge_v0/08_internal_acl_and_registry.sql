-- 0200_bidding_knowledge_v0 / part 08
-- Included transactionally by ../0200_bidding_knowledge_v0.sql.

REVOKE EXECUTE ON FUNCTION bidding.validate_rule_test_run_school_scope()
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.validate_rule_conflict_school_scope()
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.prevent_runtime_activation_overlap()
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.latest_test_result(uuid)
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.rule_passes_activation_gates(uuid)
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.enforce_runtime_activation()
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.rule_is_currently_active(uuid)
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.reject_active_rule_mutation()
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.reject_active_rule_test_mutation()
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.reject_active_rule_relation_mutation()
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.validate_decision_trace_school_scope()
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.enforce_ingestion_run_integrity()
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.validate_ingestion_event()
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE EXECUTE ON FUNCTION bidding.reject_append_only_mutation()
    FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0200_bidding_knowledge_v0')
ON CONFLICT DO NOTHING;
