from bridge_school_api.bidding_world_resolver import (
    CANON_CONFLICT, UNRESOLVED_GAP, WORLD_CONFLICT, WORLD_FALLBACK,
    KnowledgeRule, learner_response, resolve_two_lane,
)


def rule(key, lane, action, priority=1, specificity=1, confidence="high"):
    return KnowledgeRule(key, lane, action, priority, specificity, confidence)


def test_canon_match_does_not_query_or_override_with_world():
    def forbidden_world_query():
        raise AssertionError("WORLD was evaluated before CANON_GAP")
        yield

    result = resolve_two_lane([rule("c", "school_canon", "1H")], forbidden_world_query())
    assert result.outcome == "CANON_MATCH"
    assert result.selected.action == "1H"
    assert result.trace["world_searched"] is False


def test_canon_conflict_stops_before_world():
    def forbidden_world_query():
        raise AssertionError("WORLD was evaluated during CANON_CONFLICT")
        yield

    result = resolve_two_lane(
        [rule("c1", "school_canon", "1H"), rule("c2", "school_canon", "1S")],
        forbidden_world_query(),
    )
    assert result.outcome == CANON_CONFLICT
    assert result.trace["world_searched"] is False
    learner = learner_response(result)
    assert learner["status"] == "PENDING_CANON_CLARIFICATION"
    assert learner["action"] is None
    assert learner["authority"] == "SCHOOL_CANON"
    assert learner["conflicting_rule_ids"] == ["c1", "c2"]


def test_gap_allows_only_reliable_world_fallback():
    result = resolve_two_lane([], [rule("w", "external", "1S", confidence="reproducible")])
    assert result.outcome == WORLD_FALLBACK
    assert result.selected.rule_id == "w"
    assert result.trace["canon_stage"] == "CANON_GAP"


def test_world_disagreement_is_preserved_not_averaged():
    result = resolve_two_lane([], [rule("w1", "external", "1H"), rule("w2", "external", "1S")])
    assert result.outcome == WORLD_CONFLICT
    assert result.selected is None


def test_low_confidence_world_answer_is_unresolved():
    result = resolve_two_lane([], [rule("w", "external", "1S", confidence="speculative")])
    assert result.outcome == UNRESOLVED_GAP
