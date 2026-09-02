import copy

import pytest

import bridge_school_api.tournament_portfolio_episode_coverage_handoff_v3 as handoff
from bridge_school_api.tournament_episode_source_census_v3 import source_facts_sha256


A = "a" * 64
B = "b" * 64
C = "c" * 64
P = "p" * 64


def _source():
    return {
        "schema": "bridge-tournament-facts-v1",
        "tournament": {"provider_native_key": "bridge.co.il:event:30041:round:2"},
        "columns": ["board", "status"],
        "rows": ["1|played"],
    }


def _census(source, *, complete=True):
    blockers = [] if complete else ["ACTUAL_AUCTION_EVIDENCE_REQUIRES_EPISODE_ANALYSIS"]
    return {
        "schema": "tournament-episode-source-census-v1",
        "normative_algorithm_version": "1.4",
        "source_facts_sha256": source_facts_sha256(source),
        "provider_native_key": "bridge.co.il:event:30041:round:2",
        "non_dd_episode_source_census_complete": complete,
        "census_blockers": blockers,
        "unavailable_evidence_not_reconstructed": True,
        "automatic_episode_creation_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
    }


def _item(bundle, review, event, category):
    return {
        "source_bundle_id": bundle,
        "review_id": review,
        "event_id": event,
        "deal_id": f"{event}:round-2:1",
        "category": category,
    }


def _decision(item, status):
    return {
        "review_id": item["review_id"],
        "event_id": item["event_id"],
        "deal_id": item["deal_id"],
        "category": item["category"],
        "status": status,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
    }


def _base(status1="PENDING", status2="PENDING", status3="PENDING"):
    i1 = _item(A, "r1", "30041", "contract_result")
    i2 = _item(B, "r2", "30041", "opening_lead_dds3")
    i3 = _item(C, "r3", "29912", "contract_result")
    portfolio = {"portfolio_id": P, "items": [i1, i2, i3]}
    result = {
        "schema": "tournament-teacher-review-portfolio-decision-result-v1",
        "portfolio_id": P,
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "bundle_results": [
            {"source_bundle_id": A, "ledger": {"decisions": [_decision(i1, status1)]}},
            {"source_bundle_id": B, "ledger": {"decisions": [_decision(i2, status2)]}},
            {"source_bundle_id": C, "ledger": {"decisions": [_decision(i3, status3)]}},
        ],
    }
    scoring = {
        "schema": "tournament-portfolio-episode-scoring-result-v1",
        "normative_algorithm_version": "1.4",
        "portfolio_id": P,
        "review_item_count": 3,
        "explicitly_scored_count": 0,
        "scored_items": [],
        "automatic_episode_scoring_used": False,
        "automatic_methodology_mapping_used": False,
        "automatic_student_error_attribution_used": False,
        "causal_error_attribution_allowed": False,
    }
    return portfolio, result, scoring


def _scored(item, impact=2, transfer=1, reliability=2):
    total = impact + transfer + reliability
    return {
        "source_bundle_id": item["source_bundle_id"],
        "review_id": item["review_id"],
        "event_id": item["event_id"],
        "deal_id": item["deal_id"],
        "category": item["category"],
        "impact_score": impact,
        "transferability_score": transfer,
        "reliability_score": reliability,
        "total_score": total,
        "tier": "SIGNIFICANT_DEEP_SLIDE" if total >= 4 else "STANDARD_BOARD_ANALYSIS" if total >= 2 else "BRIEF_REVIEW",
        "score_actor": "teacher",
        "score_provenance": {"decision_source": "EXPLICIT_EPISODE_ADJUDICATION"},
        "causal_link": "NOT_ESTABLISHED",
        "methodology_mapping": None,
        "student_error_attribution": None,
    }


def _run(monkeypatch, portfolio, result, scoring, census=True):
    monkeypatch.setattr(handoff, "verify_teacher_review_portfolio", lambda p, b: None)
    source = _source()
    return handoff.build_portfolio_episode_coverage_handoff(
        source,
        portfolio,
        [{}, {}, {}],
        result,
        scoring,
        source_census=_census(source) if census else None,
    )


def test_pending_event_portfolio_blocks_coverage(monkeypatch):
    portfolio, result, scoring = _base()
    out = _run(monkeypatch, portfolio, result, scoring)
    assert out["event_review_item_count"] == 2
    assert out["event_pending_decision_count"] == 2
    assert out["coverage_episode_count"] == 0
    assert out["v1_4_episode_inventory_complete"] is False
    assert "TEACHER_DECISION_PENDING" in out["handoff_blockers"]


def test_other_event_pending_does_not_block_complete_source_event(monkeypatch):
    portfolio, result, scoring = _base("CONFIRMED_TECHNICAL_RELEVANCE", "DISMISSED", "PENDING")
    scoring["scored_items"] = [_scored(portfolio["items"][0])]
    scoring["explicitly_scored_count"] = 1
    out = _run(monkeypatch, portfolio, result, scoring)
    assert out["event_confirmed_technical_count"] == 1
    assert out["event_dismissed_count"] == 1
    assert out["event_pending_decision_count"] == 0
    assert out["coverage_episode_count"] == 1
    assert out["v1_4_episode_inventory_complete"] is True
    assert out["handoff_ready"] is True
    episode = out["coverage_manifest"]["episodes"][0]
    binding = episode["score_provenance"]["portfolio_binding"]
    assert binding["source_bundle_id"] == A and binding["review_id"] == "r1"
    assert binding["category"] == "contract_result"


def test_additive_later_batch_for_same_event_cannot_be_omitted(monkeypatch):
    portfolio, result, scoring = _base("CONFIRMED_TECHNICAL_RELEVANCE", "PENDING", "DISMISSED")
    scoring["scored_items"] = [_scored(portfolio["items"][0])]
    scoring["explicitly_scored_count"] = 1
    out = _run(monkeypatch, portfolio, result, scoring)
    assert out["coverage_episode_count"] == 1
    assert out["event_pending_decision_count"] == 1
    assert out["portfolio_complete_for_event"] is True
    assert out["handoff_ready"] is False
    assert "TEACHER_DECISION_PENDING" in out["handoff_blockers"]


def test_two_categories_same_board_remain_distinct_episodes(monkeypatch):
    portfolio, result, scoring = _base(
        "CONFIRMED_TECHNICAL_RELEVANCE",
        "CONFIRMED_TECHNICAL_RELEVANCE",
        "DISMISSED",
    )
    scoring["scored_items"] = [_scored(portfolio["items"][0]), _scored(portfolio["items"][1])]
    scoring["explicitly_scored_count"] = 2
    out = _run(monkeypatch, portfolio, result, scoring)
    assert out["coverage_episode_count"] == 2
    episodes = out["coverage_manifest"]["episodes"]
    assert len({episode["episode_id"] for episode in episodes}) == 2
    assert out["coverage_manifest"]["significant_episode_count"] == 2
    assert out["cross_category_causal_collapse_allowed"] is False


def test_other_event_scored_item_does_not_leak_into_source_event(monkeypatch):
    portfolio, result, scoring = _base("DISMISSED", "DISMISSED", "CONFIRMED_TECHNICAL_RELEVANCE")
    scoring["scored_items"] = [_scored(portfolio["items"][2])]
    scoring["explicitly_scored_count"] = 1
    out = _run(monkeypatch, portfolio, result, scoring)
    assert out["coverage_episode_count"] == 0
    assert out["event_dismissed_count"] == 2
    assert out["handoff_ready"] is True


def test_confirmed_unscored_review_blocks_event_handoff(monkeypatch):
    portfolio, result, scoring = _base("CONFIRMED_TECHNICAL_RELEVANCE", "DISMISSED", "DISMISSED")
    out = _run(monkeypatch, portfolio, result, scoring)
    assert out["event_confirmed_unscored_count"] == 1
    assert "CONFIRMED_EPISODE_SCORING_NOT_COMPLETE" in out["handoff_blockers"]
    assert out["handoff_ready"] is False


def test_non_dd_census_is_required_for_inventory_completeness(monkeypatch):
    portfolio, result, scoring = _base("DISMISSED", "DISMISSED", "DISMISSED")
    out = _run(monkeypatch, portfolio, result, scoring, census=False)
    assert out["event_episode_adjudication_complete"] is True
    assert out["non_dd_source_census_complete"] is False
    assert out["v1_4_episode_inventory_complete"] is False
    assert "NON_DDS_SOURCE_CENSUS_NOT_SUPPLIED" in out["handoff_blockers"]


def test_tampered_scoring_identity_fails_closed(monkeypatch):
    portfolio, result, scoring = _base("CONFIRMED_TECHNICAL_RELEVANCE", "DISMISSED", "DISMISSED")
    bad = _scored(portfolio["items"][0])
    bad["category"] = "opening_lead_dds3"
    scoring["scored_items"] = [bad]
    scoring["explicitly_scored_count"] = 1
    with pytest.raises(handoff.TournamentPortfolioEpisodeCoverageError, match="identity mismatch"):
        _run(monkeypatch, portfolio, result, scoring)


def test_weakened_scoring_causal_boundary_fails_closed(monkeypatch):
    portfolio, result, scoring = _base("CONFIRMED_TECHNICAL_RELEVANCE", "DISMISSED", "DISMISSED")
    scoring["scored_items"] = [_scored(portfolio["items"][0])]
    scoring["explicitly_scored_count"] = 1
    scoring["causal_error_attribution_allowed"] = True
    with pytest.raises(handoff.TournamentPortfolioEpisodeCoverageError, match="boundary weakened"):
        _run(monkeypatch, portfolio, result, scoring)


def test_source_census_is_exact_source_bound(monkeypatch):
    portfolio, result, scoring = _base("DISMISSED", "DISMISSED", "DISMISSED")
    monkeypatch.setattr(handoff, "verify_teacher_review_portfolio", lambda p, b: None)
    source = _source()
    census = _census(source)
    census["source_facts_sha256"] = "0" * 64
    with pytest.raises(handoff.TournamentPortfolioEpisodeCoverageError, match="exact facts"):
        handoff.build_portfolio_episode_coverage_handoff(
            source, portfolio, [{}, {}, {}], result, scoring, source_census=census
        )
