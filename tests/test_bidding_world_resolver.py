from datetime import datetime, timezone

import pytest
from unittest.mock import patch

from bridge_school_api.bidding_world_resolver import (
    CANON_CONFLICT, UNRESOLVED_GAP, WORLD_CONFLICT, WORLD_FALLBACK,
    CanonGapReceipt, KnowledgeRule, PostgresCanonGapStore, PostgresCanonRuleStore,
    ResolutionProfile, learner_response, resolve_two_lane,
)
from bridge_school_api.bidding_world_resolver import _gap_fingerprint, _profile_fingerprint, _request_fingerprint

NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)
PROFILE = ResolutionProfile("natural", "v1", "L1", "auction-1", NOW)
REQUEST = {"acting_seat": "N", "acting_hand": {"cards": ["AC"]},
           "public_auction": {"calls": []}, "public_context": {"dealer": "N"}}
REQUEST_HASH = _request_fingerprint(**REQUEST)
GAP_HASH = _gap_fingerprint(REQUEST_HASH, PROFILE)


def rule(key, lane, action, *, profile=PROFILE, priority=1, specificity=1, confidence="high"):
    return KnowledgeRule(key, lane, action, profile.system_profile, profile.system_version,
                         profile.learner_level, profile.auction_context_id,
                         priority=priority, specificity=specificity, confidence=confidence)


def verified(gap_id, school_id, fingerprint, profile):
    profile_key = _profile_fingerprint(profile)
    return CanonGapReceipt(gap_id, school_id, fingerprint, profile_key, profile.effective_at, NOW)


STORE = PostgresCanonGapStore(lambda: None)
CANON_STORE = PostgresCanonRuleStore(lambda: None)


def resolve(canon, world):
    with patch.object(PostgresCanonRuleStore, "fetch_current",
                      side_effect=[(PROFILE, tuple(canon)), (PROFILE, ())]), \
         patch.object(PostgresCanonGapStore, "persist_and_verify",
                      return_value=verified("gap-1", "school-1", GAP_HASH, PROFILE)):
        return resolve_two_lane(school_id="school-1", **REQUEST, profile=PROFILE,
                                canon_store=CANON_STORE, gap_store=STORE,
                                world_supplier=lambda _receipt, _profile: world)


def test_canon_match_does_not_persist_gap_or_query_world():
    def forbidden(*_args):
        raise AssertionError("unexpected call")
    result = resolve([rule("c", "school_canon", "1H")], forbidden)
    assert result.outcome == "CANON_MATCH" and result.trace["world_searched"] is False


def test_canon_conflict_stops_before_gap_and_world():
    def forbidden(*_args):
        raise AssertionError("unexpected call")
    result = resolve([rule("c1", "school_canon", "1H"), rule("c2", "school_canon", "1S")], forbidden)
    assert result.outcome == CANON_CONFLICT and learner_response(result)["action"] is None


def test_gap_is_committed_before_world_supplier_runs():
    events = []
    def persisted(school_id, fingerprint, profile):
        events.extend(["gap_committed", "gap_verified_post_commit"])
        return verified("gap-1", school_id, fingerprint, profile)
    def supplied(_receipt, _profile):
        events.append("world_queried")
        return [rule("w", "external", "1S", confidence="reproducible")]
    with patch.object(PostgresCanonRuleStore, "fetch_current",
                      side_effect=[(PROFILE, ()), (PROFILE, ())]), \
         patch.object(PostgresCanonGapStore, "persist_and_verify", side_effect=persisted):
        result = resolve_two_lane(school_id="school-1", **REQUEST, profile=PROFILE,
                                  canon_store=CANON_STORE, gap_store=STORE, world_supplier=supplied)
    assert events == ["gap_committed", "gap_verified_post_commit", "world_queried"] and result.outcome == WORLD_FALLBACK


def test_uncommitted_or_wrong_scope_gap_blocks_world():
    called = False
    def supplied(*_args):
        nonlocal called
        called = True
        return []
    with patch.object(PostgresCanonRuleStore, "fetch_current", return_value=(PROFILE, ())), \
         patch.object(PostgresCanonGapStore, "persist_and_verify", side_effect=RuntimeError("not visible")):
        with pytest.raises(RuntimeError):
            resolve_two_lane(school_id="school-1", **REQUEST, profile=PROFILE,
                             canon_store=CANON_STORE, gap_store=STORE, world_supplier=supplied)
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
        Connection("reader", [("gap-1", "school-1", "request-1", profile_hash, NOW, NOW)]),
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


def test_request_fingerprint_is_derived_from_visible_request_fields():
    changed = {**REQUEST, "public_auction": {"calls": ["1S"]}}
    assert _request_fingerprint(**REQUEST) != _request_fingerprint(**changed)


def test_gap_fingerprint_includes_resolution_profile():
    changed = ResolutionProfile("natural", "v1", "L2", "auction-1", NOW)
    assert _gap_fingerprint(REQUEST_HASH, PROFILE) != _gap_fingerprint(REQUEST_HASH, changed)


def test_gap_fingerprint_includes_activation_scope():
    changed = ResolutionProfile("natural", "v1", "L1", "auction-1", NOW, "advanced")
    assert _gap_fingerprint(REQUEST_HASH, PROFILE) != _gap_fingerprint(REQUEST_HASH, changed)


def test_canon_store_binds_database_time_and_returns_visible_predicates():
    executed = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, params=None): executed.append((sql, params))
        def fetchone(self): return (NOW,)
        def fetchall(self):
            return [("rule-1", "1S", "natural", "v1", "L1", "auction-1",
                     NOW, None, 10, 5, {"context_id": "auction-1", "calls": ["1H"]},
                     {"HCP": {"min": 10}}, {"dealer": "N"})]

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return Cursor()

    bound, rules = PostgresCanonRuleStore(Connection).fetch_current("school-1", PROFILE)
    assert bound.effective_at == NOW
    assert rules[0].hand_constraints == {"HCP": {"min": 10}}
    assert executed[0][0] == "SELECT clock_timestamp()"
    assert "c.auction_pattern,c.hand_constraints,c.public_context_constraints" in executed[1][0]
    assert executed[1][1][:3] == ("school-1", "default", NOW)


def test_existing_gap_returns_its_original_effective_time():
    profile_hash = _profile_fingerprint(PROFILE)

    class Cursor:
        def __init__(self, rows): self.rows = iter(rows)
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, *_args): pass
        def fetchone(self): return next(self.rows)

    class Connection:
        def __init__(self, rows): self.rows = rows
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return Cursor(self.rows)
        def commit(self): pass

    connections = iter((
        Connection([("gap-1", profile_hash, NOW)]),
        Connection([("gap-1", "school-1", "request-1", profile_hash, NOW, NOW)]),
    ))
    later = ResolutionProfile(
        PROFILE.system_profile, PROFILE.system_version, PROFILE.learner_level,
        PROFILE.auction_context_id, datetime(2026, 8, 30, 0, 2, tzinfo=timezone.utc),
    )
    receipt = PostgresCanonGapStore(lambda: next(connections)).persist_and_verify(
        "school-1", "request-1", later
    )
    assert receipt.effective_at == NOW


def test_canon_constraints_are_matched_against_visible_request():
    applicable = rule("match", "school_canon", "1S")
    applicable = KnowledgeRule(
        **{**applicable.__dict__,
           "auction_pattern": {"context_id": "auction-1", "calls": ["1H"]},
           "hand_constraints": {"HCP": {"min": 10}},
           "public_context_constraints": {"dealer": "N"}}
    )
    wrong_hand = KnowledgeRule(
        **{**applicable.__dict__, "rule_id": "wrong", "hand_constraints": {"HCP": {"min": 20}}}
    )
    request = {**REQUEST, "acting_hand": {"HCP": 12}, "public_auction": {"calls": ["1H"]}}
    with patch.object(PostgresCanonRuleStore, "fetch_current", return_value=(PROFILE, (applicable, wrong_hand))):
        result = resolve_two_lane(
            school_id="school-1", **request, profile=PROFILE,
            canon_store=CANON_STORE, gap_store=STORE,
            world_supplier=lambda *_: (_ for _ in ()).throw(AssertionError("WORLD called")),
        )
    assert result.outcome == "CANON_MATCH"
    assert result.selected.rule_id == "match"


def test_canon_is_rechecked_immediately_before_world():
    activated = rule("late-canon", "school_canon", "2S")
    events = []
    with patch.object(
        PostgresCanonRuleStore,
        "fetch_current",
        side_effect=[(PROFILE, ()), (PROFILE, (activated,))],
    ), patch.object(
        PostgresCanonGapStore,
        "persist_and_verify",
        return_value=verified("gap-1", "school-1", GAP_HASH, PROFILE),
    ):
        result = resolve_two_lane(
            school_id="school-1", **REQUEST, profile=PROFILE,
            canon_store=CANON_STORE, gap_store=STORE,
            world_supplier=lambda *_: events.append("WORLD"),
        )
    assert result.outcome == "CANON_MATCH"
    assert result.selected.rule_id == "late-canon"
    assert events == []


def test_profile_fingerprint_reuses_durable_gap_across_boundary_times():
    later = ResolutionProfile(
        PROFILE.system_profile, PROFILE.system_version, PROFILE.learner_level,
        PROFILE.auction_context_id, datetime(2026, 8, 30, 0, 1, tzinfo=timezone.utc),
        PROFILE.activation_scope,
    )
    assert _profile_fingerprint(PROFILE) == _profile_fingerprint(later)


def test_untrusted_canon_store_is_rejected():
    with pytest.raises(TypeError, match="sealed active-catalog"):
        resolve_two_lane(school_id="school-1", **REQUEST, profile=PROFILE,
                         canon_store=object(), gap_store=STORE, world_supplier=lambda *_: ())


def test_world_disagreement_and_low_confidence_remain_unselected():
    conflict = resolve([], [rule("w1", "external", "1H"), rule("w2", "external", "1S")])
    unresolved = resolve([], [rule("w", "external", "1S", confidence="speculative")])
    assert conflict.outcome == WORLD_CONFLICT and conflict.selected is None
    assert unresolved.outcome == UNRESOLVED_GAP and unresolved.selected is None
