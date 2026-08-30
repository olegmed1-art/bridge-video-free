from datetime import datetime, timezone

import pytest
from unittest.mock import patch

from bridge_school_api.bidding_world_resolver import (
    CANON_CONFLICT, UNRESOLVED_GAP, WORLD_CONFLICT, WORLD_FALLBACK,
    CanonGapReceipt, KnowledgeRule, PostgresCanonGapStore, ResolutionProfile, learner_response, resolve_two_lane,
)
from bridge_school_api.bidding_world_resolver import _profile_fingerprint

NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)
PROFILE = ResolutionProfile("natural", "v1", "L1", "auction-1", NOW)


def rule(key, lane, action, *, profile=PROFILE, priority=1, specificity=1, confidence="high"):
    return KnowledgeRule(key, lane, action, profile.system_profile, profile.system_version,
                         profile.learner_level, profile.auction_context_id,
                         priority=priority, specificity=specificity, confidence=confidence)


def verified(gap_id, school_id, fingerprint, profile):
    profile_key = _profile_fingerprint(profile)
    return CanonGapReceipt(gap_id, school_id, fingerprint, profile_key, NOW)


STORE = PostgresCanonGapStore(lambda: None)


def resolve(canon, world):
    with patch.object(PostgresCanonGapStore, "persist_and_verify",
                      return_value=verified("gap-1", "school-1", "request-1", PROFILE)):
        return resolve_two_lane(school_id="school-1", request_fingerprint="request-1", profile=PROFILE,
                                canon_rules=canon, gap_store=STORE,
                                world_supplier=lambda _receipt, _profile: world)


def test_canon_match_does_not_persist_gap_or_query_world():
    def forbidden(*_args):
        raise AssertionError("unexpected call")
    result = resolve_two_lane(school_id="school-1", request_fingerprint="request-1", profile=PROFILE,
                              canon_rules=[rule("c", "school_canon", "1H")],
                              gap_store=STORE, world_supplier=forbidden)
    assert result.outcome == "CANON_MATCH" and result.trace["world_searched"] is False


def test_canon_conflict_stops_before_gap_and_world():
    def forbidden(*_args):
        raise AssertionError("unexpected call")
    result = resolve_two_lane(school_id="school-1", request_fingerprint="request-1", profile=PROFILE,
                              canon_rules=[rule("c1", "school_canon", "1H"), rule("c2", "school_canon", "1S")],
                              gap_store=STORE, world_supplier=forbidden)
    assert result.outcome == CANON_CONFLICT and learner_response(result)["action"] is None


def test_gap_is_committed_before_world_supplier_runs():
    events = []
    def persisted(school_id, fingerprint, profile):
        events.extend(["gap_committed", "gap_verified_post_commit"])
        return verified("gap-1", school_id, fingerprint, profile)
    def supplied(_receipt, _profile):
        events.append("world_queried")
        return [rule("w", "external", "1S", confidence="reproducible")]
    with patch.object(PostgresCanonGapStore, "persist_and_verify", side_effect=persisted):
        result = resolve_two_lane(school_id="school-1", request_fingerprint="request-1", profile=PROFILE,
                                  canon_rules=[], gap_store=STORE, world_supplier=supplied)
    assert events == ["gap_committed", "gap_verified_post_commit", "world_queried"] and result.outcome == WORLD_FALLBACK


def test_uncommitted_or_wrong_scope_gap_blocks_world():
    called = False
    def supplied(*_args):
        nonlocal called
        called = True
        return []
    with patch.object(PostgresCanonGapStore, "persist_and_verify", side_effect=RuntimeError("not visible")):
        with pytest.raises(RuntimeError):
            resolve_two_lane(school_id="school-1", request_fingerprint="request-1", profile=PROFILE,
                             canon_rules=[], gap_store=STORE, world_supplier=supplied)
    assert called is False


def test_postgres_gap_store_commits_then_verifies_on_fresh_connection():
    events = []

    class Cursor:
        def __init__(self, rows):
            self.rows = iter(rows)
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def execute(self, sql, _params):
            events.append("reader_select" if "created_at" in sql else "writer_sql")
        def fetchone(self):
            return next(self.rows)

    class Connection:
        def __init__(self, name, rows):
            self.name, self.rows = name, rows
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def cursor(self):
            return Cursor(self.rows)
        def commit(self):
            events.append("writer_commit")

    profile_hash = verified("gap-1", "school-1", "request-1", PROFILE).profile_fingerprint
    connections = iter((
        Connection("writer", [None, ("gap-1",)]),
        Connection("reader", [("gap-1", "school-1", "request-1", profile_hash, NOW)]),
    ))
    receipt = PostgresCanonGapStore(lambda: next(connections)).persist_and_verify(
        "school-1", "request-1", PROFILE)
    assert receipt.gap_id == "gap-1"
    assert events[-2:] == ["writer_commit", "reader_select"]


def test_incompatible_profile_candidates_are_not_ranked_together():
    sayc = ResolutionProfile("sayc", "v1", "L1", "auction-1", NOW)
    result = resolve([], [rule("natural", "external", "1S"),
                          rule("sayc", "external", "1H", profile=sayc, priority=999)])
    assert result.outcome == WORLD_FALLBACK and result.selected.rule_id == "natural"


def test_profile_fingerprint_is_not_ambiguous_when_fields_contain_delimiters():
    left = ResolutionProfile("natural|v1", "L1", "beginner", "auction-1", NOW)
    right = ResolutionProfile("natural", "v1|L1", "beginner", "auction-1", NOW)
    assert _profile_fingerprint(left) != _profile_fingerprint(right)


def test_world_disagreement_and_low_confidence_remain_unselected():
    conflict = resolve([], [rule("w1", "external", "1H"), rule("w2", "external", "1S")])
    unresolved = resolve([], [rule("w", "external", "1S", confidence="speculative")])
    assert conflict.outcome == WORLD_CONFLICT and conflict.selected is None
    assert unresolved.outcome == UNRESOLVED_GAP and unresolved.selected is None
