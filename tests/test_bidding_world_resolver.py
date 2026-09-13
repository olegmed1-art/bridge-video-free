from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from unittest.mock import patch

from bridge_school_api.bidding_world_resolver import (
    CANON_CONFLICT, UNRESOLVED_GAP, WORLD_CONFLICT, WORLD_FALLBACK,
    CanonGapReceipt, KnowledgeRule, PostgresCanonGapStore, PostgresCanonRuleStore,
    PostgresWorldRuleStore, ResolutionProfile, learner_response, resolve_two_lane,
)
from bridge_school_api.bidding_world_resolver import _gap_fingerprint, _profile_fingerprint, _request_fingerprint

NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)
PROFILE = ResolutionProfile("natural", "v1", "L1", "auction-1", NOW)
REQUEST = {"acting_seat": "N", "acting_hand": {"cards": ["AC"]},
           "public_auction": {"calls": []}, "public_context": {"dealer": "N"}}
REQUEST_HASH = _request_fingerprint(**REQUEST)
GAP_HASH = _gap_fingerprint(REQUEST_HASH, PROFILE)


def rule(key, lane, action, *, profile=PROFILE, priority=1, specificity=1,
         confidence="high", provenance=None):
    if provenance is None:
        provenance = ({"source_id": "source-1", "source_manifest_key": "manifest-1",
                       "source_sha256": "a" * 64, "repository_ref": "world-fixture-v1",
                       "knowledge_version_id": "version-1", "version_no": 1,
                       "method_version": profile.system_version, "dependencies": []}
                      if lane == "external" else {})
    return KnowledgeRule(key, lane, action, profile.system_profile, profile.system_version,
                         profile.learner_level, profile.auction_context_id,
                         priority=priority, specificity=specificity, confidence=confidence,
                         provenance=provenance)


def verified(gap_id, school_id, fingerprint, profile):
    profile_key = _profile_fingerprint(profile)
    return CanonGapReceipt(gap_id, school_id, fingerprint, profile_key, profile.effective_at, NOW)


STORE = PostgresCanonGapStore(lambda: None)
CANON_STORE = PostgresCanonRuleStore(lambda: None)
WORLD_STORE = PostgresWorldRuleStore(lambda: None)


def resolve(canon, world):
    with patch.object(PostgresCanonRuleStore, "fetch_current",
                      side_effect=[(PROFILE, tuple(canon)), (PROFILE, ())]), \
         patch.object(PostgresCanonGapStore, "persist_and_verify",
                      return_value=verified("gap-1", "school-1", GAP_HASH, PROFILE)), \
         patch.object(PostgresWorldRuleStore, "fetch_verified", return_value=tuple(world)):
        return resolve_two_lane(school_id="school-1", **REQUEST, profile=PROFILE,
                                canon_store=CANON_STORE, gap_store=STORE,
                                world_supplier=WORLD_STORE)


def test_canon_match_does_not_persist_gap_or_query_world():
    result = resolve([rule("c", "school_canon", "1H")], [])
    assert result.outcome == "CANON_MATCH" and result.trace["world_searched"] is False


def test_canon_conflict_stops_before_gap_and_world():
    result = resolve([rule("c1", "school_canon", "1H"), rule("c2", "school_canon", "1S")], [])
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
         patch.object(PostgresCanonGapStore, "persist_and_verify", side_effect=persisted), \
         patch.object(PostgresWorldRuleStore, "fetch_verified", side_effect=supplied):
        result = resolve_two_lane(school_id="school-1", **REQUEST, profile=PROFILE,
                                  canon_store=CANON_STORE, gap_store=STORE, world_supplier=WORLD_STORE)
    assert events == ["gap_committed", "gap_verified_post_commit", "world_queried"] and result.outcome == WORLD_FALLBACK


def test_uncommitted_or_wrong_scope_gap_blocks_world():
    called = False
    def supplied(*_args):
        nonlocal called
        called = True
        return []
    with patch.object(PostgresCanonRuleStore, "fetch_current", return_value=(PROFILE, ())), \
         patch.object(PostgresCanonGapStore, "persist_and_verify", side_effect=RuntimeError("not visible")), \
         patch.object(PostgresWorldRuleStore, "fetch_verified", side_effect=supplied):
        with pytest.raises(RuntimeError):
            resolve_two_lane(school_id="school-1", **REQUEST, profile=PROFILE,
                             canon_store=CANON_STORE, gap_store=STORE, world_supplier=WORLD_STORE)
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
        Connection("writer", [None, {"knowledge_gap_id": "gap-1"}]),
        Connection("reader", [{
            "knowledge_gap_id": "gap-1",
            "school_id": "school-1",
            "request_fingerprint": "request-1",
            "profile_fingerprint": profile_hash,
            "effective_at": NOW,
            "created_at": NOW,
        }]),
    ))
    @contextmanager
    def repository_connect():
        yield next(connections)

    receipt = PostgresCanonGapStore(repository_connect).persist_and_verify(
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


@pytest.mark.parametrize("field,payload", [
    ("public_context", {"opponent_hand": {"cards": ["AS"]}}),
    ("public_context", {"private_material": ["AS"]}),
    ("public_auction", {"calls": [], "notes": {"partner_hand": "AKQ.JT9.876.5432"}}),
])
def test_hidden_cards_in_public_inputs_fail_before_canon_or_world(field, payload):
    request = {**REQUEST, field: payload}
    with patch.object(
        PostgresCanonRuleStore,
        "fetch_current",
        side_effect=AssertionError("Canon queried"),
    ), patch.object(
        PostgresCanonGapStore,
        "persist_and_verify",
        side_effect=AssertionError("gap persisted"),
    ):
        with pytest.raises(ValueError, match="public inputs contain hidden card material"):
            resolve_two_lane(
                school_id="school-1",
                **request,
                profile=PROFILE,
                canon_store=CANON_STORE,
                gap_store=STORE,
                world_supplier=WORLD_STORE,
            )


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
        def fetchone(self): return {"effective_at": NOW}
        def fetchall(self):
            return [{
                "rule_id": "rule-1",
                "action": "1S",
                "bidding_system_key": "natural",
                "method_version": "v1",
                "learner_level": "L1",
                "auction_context_id": "auction-1",
                "valid_from": NOW,
                "valid_to": None,
                "priority": 10,
                "specificity": 5,
                "auction_pattern": {"context_id": "auction-1", "calls": ["1H"]},
                "hand_constraints": {"HCP": {"min": 10}},
                "public_context_constraints": {"dealer": "N"},
            }]

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return Cursor()

    @contextmanager
    def repository_connect():
        yield Connection()

    bound, rules = PostgresCanonRuleStore(repository_connect).fetch_current("school-1", PROFILE)
    assert bound.effective_at == NOW
    assert rules[0].hand_constraints == {"HCP": {"min": 10}}
    assert executed[0][0] == "SELECT clock_timestamp() AS effective_at"
    assert "c.auction_pattern AS auction_pattern" in executed[1][0]
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
            world_supplier=WORLD_STORE,
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
            world_supplier=WORLD_STORE,
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
                         canon_store=object(), gap_store=STORE, world_supplier=WORLD_STORE)


def test_self_declared_world_provenance_cannot_bypass_persisted_store():
    fabricated = rule("fabricated", "external", "1S")
    with patch.object(
        PostgresCanonRuleStore,
        "fetch_current",
        side_effect=[(PROFILE, ()), (PROFILE, ())],
    ), patch.object(
        PostgresCanonGapStore,
        "persist_and_verify",
        return_value=verified("gap-1", "school-1", GAP_HASH, PROFILE),
    ):
        with pytest.raises(TypeError, match="sealed persisted-provenance"):
            resolve_two_lane(
                school_id="school-1", **REQUEST, profile=PROFILE,
                canon_store=CANON_STORE, gap_store=STORE,
                world_supplier=lambda *_: (fabricated,),
            )


def test_world_disagreement_and_low_confidence_remain_unselected():
    conflict = resolve([], [rule("w1", "external", "1H"), rule("w2", "external", "1S")])
    unresolved = resolve([], [rule("w", "external", "1S", confidence="speculative")])
    assert conflict.outcome == WORLD_CONFLICT and conflict.selected is None
    assert unresolved.outcome == UNRESOLVED_GAP and unresolved.selected is None

def test_world_fallback_requires_verifiable_provenance():
    empty = resolve([], [rule("empty", "external", "1S", provenance={})])
    bad_hash = resolve([], [rule(
        "bad-hash", "external", "1S",
        provenance={"source_id": "source-1", "source_manifest_key": "manifest-1",
                    "source_sha256": "not-a-hash", "repository_ref": "world-fixture-v1",
                    "knowledge_version_id": "version-1", "version_no": 1,
                    "method_version": "v1", "dependencies": []},
    )])
    verified = resolve([], [rule("verified", "external", "1S")])

    assert empty.outcome == UNRESOLVED_GAP and empty.selected is None
    assert bad_hash.outcome == UNRESOLVED_GAP and bad_hash.selected is None
    assert verified.outcome == WORLD_FALLBACK and verified.selected.rule_id == "verified"
