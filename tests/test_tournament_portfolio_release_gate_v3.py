import pytest

from bridge_school_api.tournament_portfolio_release_gate_v3 import (
    TournamentPortfolioReleaseGateError,
    build_portfolio_aware_release_gate,
)


def _base_inputs():
    pre = {
        "schema": "tournament-preanalysis-gate-v1",
        "run_id": "run",
        "tournament": {"provider_native_key": "bridge.co.il:event:30041:round:2"},
        "facts_only_analysis_ready": True,
        "full_causal_replay_ready": False,
        "hard_stop_conditions": [],
        "limitations": [],
    }
    coverage = {
        "schema": "tournament-coverage-manifest-v1",
        "episode_inventory_complete": True,
        "coverage_plan_release_ready": True,
        "release_blockers": [],
        "expected_slide_keys": ["deck-title", "deck-overview", "deck-final"],
    }
    mp = {
        "schema": "tournament-mp-recalculation-availability-v1",
        "full_traveller_available": False,
        "status": "OFFICIAL_PERCENTAGE_NOT_INDEPENDENTLY_RECALCULATED",
    }
    review = {
        "schema": "tournament-event-teacher-review-release-gate-v1",
        "normative_algorithm_version": "1.4",
        "portfolio_id": "a" * 64,
        "event_id": "30041",
        "review_item_count": 11,
        "unresolved_review_count": 11,
        "teacher_review_release_ready": False,
        "release_blockers": ["TEACHER_REVIEW_PORTFOLIO_UNRESOLVED"],
        "cross_category_causal_collapse_allowed": False,
        "automatic_episode_scoring_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "causal_error_attribution_allowed": False,
    }
    return pre, coverage, mp, review


def test_pending_portfolio_blocks_otherwise_releasable_report():
    pre, coverage, mp, review = _base_inputs()
    out = build_portfolio_aware_release_gate(
        preanalysis_gate=pre,
        coverage_manifest=coverage,
        mp_availability=mp,
        event_teacher_review_gate=review,
        rendered_slide_keys=coverage["expected_slide_keys"],
        visual_qa_pass=True,
    )
    assert out["final_report_release_ready"] is False
    assert out["teacher_review_item_count"] == 11
    assert out["teacher_review_unresolved_count"] == 11
    assert "TEACHER_REVIEW_PORTFOLIO_UNRESOLVED" in out["hard_stop_conditions"]
    assert out["portfolio_teacher_decision_gate_enforced"] is True
    assert out["automatic_episode_scoring_allowed"] is False


def test_resolved_review_does_not_remove_other_release_blockers():
    pre, coverage, mp, review = _base_inputs()
    review = {**review, "unresolved_review_count": 0, "teacher_review_release_ready": True, "release_blockers": []}
    out = build_portfolio_aware_release_gate(
        preanalysis_gate=pre,
        coverage_manifest=coverage,
        mp_availability=mp,
        event_teacher_review_gate=review,
        rendered_slide_keys=None,
        visual_qa_pass=None,
    )
    assert out["teacher_review_release_ready"] is True
    assert out["final_report_release_ready"] is False
    assert "RENDERED_SLIDE_COVERAGE_NOT_PROVIDED" in out["hard_stop_conditions"]
    assert "VISUAL_QA_NOT_PASSED" in out["hard_stop_conditions"]


def test_resolved_review_can_open_only_when_all_base_gates_open():
    pre, coverage, mp, review = _base_inputs()
    review = {**review, "unresolved_review_count": 0, "teacher_review_release_ready": True, "release_blockers": []}
    out = build_portfolio_aware_release_gate(
        preanalysis_gate=pre,
        coverage_manifest=coverage,
        mp_availability=mp,
        event_teacher_review_gate=review,
        rendered_slide_keys=coverage["expected_slide_keys"],
        visual_qa_pass=True,
    )
    assert out["final_report_release_ready"] is True
    assert out["hard_stop_conditions"] == []
    assert out["automatic_student_error_attribution_allowed"] is False
    assert out["automatic_methodology_invention_allowed"] is False


def test_wrong_event_fails_closed():
    pre, coverage, mp, review = _base_inputs()
    review = {**review, "event_id": "29912"}
    with pytest.raises(TournamentPortfolioReleaseGateError, match="does not match"):
        build_portfolio_aware_release_gate(
            preanalysis_gate=pre,
            coverage_manifest=coverage,
            mp_availability=mp,
            event_teacher_review_gate=review,
        )
