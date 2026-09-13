import copy

import pytest

import bridge_school_api.tournament_portfolio_episode_scoring_v3 as scoring


def _decision(review_id, event_id, deal_id, category, status):
    return {
        "review_id": review_id,
        "event_id": event_id,
        "deal_id": deal_id,
        "category": category,
        "status": status,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
    }


def _payload(status1="PENDING", status2="PENDING"):
    portfolio = {
        "portfolio_id": "p" * 64,
        "items": [
            {"source_bundle_id": "a" * 64, "review_id": "r1", "event_id": "30041", "deal_id": "30041:round-2:2", "category": "contract_result"},
            {"source_bundle_id": "b" * 64, "review_id": "r2", "event_id": "30041", "deal_id": "30041:round-2:2", "category": "opening_lead_dds3"},
        ],
    }
    result = {
        "schema": "tournament-teacher-review-portfolio-decision-result-v1",
        "portfolio_id": portfolio["portfolio_id"],
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "bundle_results": [
            {"source_bundle_id": "a" * 64, "ledger": {"decisions": [_decision("r1", "30041", "30041:round-2:2", "contract_result", status1)]}},
            {"source_bundle_id": "b" * 64, "ledger": {"decisions": [_decision("r2", "30041", "30041:round-2:2", "opening_lead_dds3", status2)]}},
        ],
    }
    return portfolio, result


def test_pending_items_are_all_present_but_not_scoreable(monkeypatch):
    portfolio, result = _payload()
    monkeypatch.setattr(scoring, "verify_teacher_review_portfolio", lambda p, b: None)
    intake = scoring.build_portfolio_episode_scoring_template(portfolio, [{}, {}], result)
    assert intake["review_item_count"] == 2
    assert intake["confirmed_technical_count"] == 0
    assert intake["unresolved_teacher_review_count"] == 2
    assert {row["status"] for row in intake["rows"]} == {"BLOCKED_TEACHER_DECISION"}
    out = scoring.apply_portfolio_episode_scoring_intake(portfolio, [{}, {}], result, intake)
    assert out["explicitly_scored_count"] == 0
    assert out["unresolved_teacher_review_count"] == 2
    assert out["portfolio_episode_scoring_complete"] is False


def test_confirmed_items_require_separate_explicit_scoring(monkeypatch):
    portfolio, result = _payload("CONFIRMED_TECHNICAL_RELEVANCE", "DISMISSED")
    monkeypatch.setattr(scoring, "verify_teacher_review_portfolio", lambda p, b: None)
    intake = scoring.build_portfolio_episode_scoring_template(portfolio, [{}, {}], result)
    r1 = next(row for row in intake["rows"] if row["review_id"] == "r1")
    r2 = next(row for row in intake["rows"] if row["review_id"] == "r2")
    assert r1["status"] == "PENDING_EPISODE_SCORING" and r1["episode_scoring_required"] is True
    assert r2["status"] == "NOT_APPLICABLE_DISMISSED" and r2["episode_scoring_required"] is False
    out = scoring.apply_portfolio_episode_scoring_intake(portfolio, [{}, {}], result, intake)
    assert out["confirmed_unscored_count"] == 1
    assert out["dismissed_count"] == 1
    assert out["portfolio_episode_scoring_complete"] is False


def test_explicit_confirmed_score_can_complete_without_pedagogical_attribution(monkeypatch):
    portfolio, result = _payload("CONFIRMED_TECHNICAL_RELEVANCE", "DISMISSED")
    monkeypatch.setattr(scoring, "verify_teacher_review_portfolio", lambda p, b: None)
    intake = scoring.build_portfolio_episode_scoring_template(portfolio, [{}, {}], result)
    changed = copy.deepcopy(intake)
    r1 = next(row for row in changed["rows"] if row["review_id"] == "r1")
    r1.update(
        explicit_episode_adjudication=True,
        impact_score=2,
        transferability_score=1,
        reliability_score=2,
        score_actor="teacher",
        score_provenance={"decision_source": "EXPLICIT_EPISODE_ADJUDICATION"},
        status="SCORED_EXPLICITLY",
    )
    out = scoring.apply_portfolio_episode_scoring_intake(portfolio, [{}, {}], result, changed)
    assert out["portfolio_episode_scoring_complete"] is True
    assert out["explicitly_scored_count"] == 1
    item = out["scored_items"][0]
    assert item["total_score"] == 5 and item["tier"] == "SIGNIFICANT_DEEP_SLIDE"
    assert item["causal_link"] == "NOT_ESTABLISHED"
    assert item["methodology_mapping"] is None and item["student_error_attribution"] is None


def test_scoring_unconfirmed_item_fails_closed(monkeypatch):
    portfolio, result = _payload("DISMISSED", "PENDING")
    monkeypatch.setattr(scoring, "verify_teacher_review_portfolio", lambda p, b: None)
    intake = scoring.build_portfolio_episode_scoring_template(portfolio, [{}, {}], result)
    changed = copy.deepcopy(intake)
    r1 = next(row for row in changed["rows"] if row["review_id"] == "r1")
    r1.update(
        explicit_episode_adjudication=True,
        impact_score=2,
        transferability_score=2,
        reliability_score=2,
        score_actor="teacher",
        score_provenance={"decision_source": "EXPLICIT_EPISODE_ADJUDICATION"},
        status="SCORED_EXPLICITLY",
    )
    with pytest.raises(scoring.TournamentPortfolioEpisodeScoringError, match="forbidden"):
        scoring.apply_portfolio_episode_scoring_intake(portfolio, [{}, {}], result, changed)
