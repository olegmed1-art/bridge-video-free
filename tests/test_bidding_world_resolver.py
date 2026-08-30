from datetime import datetime, timezone

import pytest

from bridge_school_api.bidding_world_resolver import (
    CANON_CONFLICT, UNRESOLVED_GAP, WORLD_CONFLICT, WORLD_FALLBACK,
    CanonGapReceipt, KnowledgeRule, ResolutionProfile, learner_response, resolve_two_lane,
)

NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)
PROFILE = ResolutionProfile("natural", "v1", "L1", "auction-1", NOW)


def rule(key, lane, action, *, profile=PROFILE, priority=1, specificity=1, confidence="high"):
    return KnowledgeRule(key, lane, action, profile.system_profile, profile.system_version,
                         profile.learner_level, profile.auction_context_id,
                         priority=priority, specificity=specificity, confidence=confidence)


def gap(school_id, fingerprint, _profile):
    return CanonGapReceipt("gap-1", school_id, fingerprint, True)


def resolve(canon, world):
    return resolve_two_lane(school_id="school-1", request_fingerprint="request-1", profile=PROFILE,
                            canon_rules=canon, persist_canon_gap=gap,
                            world_supplier=lambda _receipt, _profile: world)


def test_canon_match_does_not_persist_gap_or_query_world():
    def forbidden(*_args):
        raise AssertionError("unexpected call")
    result = resolve_two_lane(school_id="school-1", request_fingerprint="request-1", profile=PROFILE,
                              canon_rules=[rule("c", "school_canon", "1H")],
                              persist_canon_gap=forbidden, world_supplier=forbidden)
    assert result.outcome == "CANON_MATCH" and result.trace["world_searched"] is False


def test_canon_conflict_stops_before_gap_and_world():
    def forbidden(*_args):
        raise AssertionError("unexpected call")
    result = resolve_two_lane(school_id="school-1", request_fingerprint="request-1", profile=PROFILE,
                              canon_rules=[rule("c1", "school_canon", "1H"), rule("c2", "school_canon", "1S")],
                              persist_canon_gap=forbidden, world_supplier=forbidden)
    assert result.outcome == CANON_CONFLICT and learner_response(result)["action"] is None


def test_gap_is_committed_before_world_supplier_runs():
    events = []
    def persisted(school_id, fingerprint, _profile):
        events.append("gap_committed")
        return CanonGapReceipt("gap-1", school_id, fingerprint, True)
    def supplied(_receipt, _profile):
        events.append("world_queried")
        return [rule("w", "external", "1S", confidence="reproducible")]
    result = resolve_two_lane(school_id="school-1", request_fingerprint="request-1", profile=PROFILE,
                              canon_rules=[], persist_canon_gap=persisted, world_supplier=supplied)
    assert events == ["gap_committed", "world_queried"] and result.outcome == WORLD_FALLBACK


def test_uncommitted_or_wrong_scope_gap_blocks_world():
    called = False
    def supplied(*_args):
        nonlocal called
        called = True
        return []
    with pytest.raises(RuntimeError):
        resolve_two_lane(school_id="school-1", request_fingerprint="request-1", profile=PROFILE,
                         canon_rules=[], persist_canon_gap=lambda *_: CanonGapReceipt("g", "other", "request-1", True),
                         world_supplier=supplied)
    assert called is False


def test_incompatible_profile_candidates_are_not_ranked_together():
    sayc = ResolutionProfile("sayc", "v1", "L1", "auction-1", NOW)
    result = resolve([], [rule("natural", "external", "1S"),
                          rule("sayc", "external", "1H", profile=sayc, priority=999)])
    assert result.outcome == WORLD_FALLBACK and result.selected.rule_id == "natural"


def test_world_disagreement_and_low_confidence_remain_unselected():
    conflict = resolve([], [rule("w1", "external", "1H"), rule("w2", "external", "1S")])
    unresolved = resolve([], [rule("w", "external", "1S", confidence="speculative")])
    assert conflict.outcome == WORLD_CONFLICT and conflict.selected is None
    assert unresolved.outcome == UNRESOLVED_GAP and unresolved.selected is None
