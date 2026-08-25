import copy

import pytest

from bridge_school_api.tournament_episode_coverage_handoff_v3 import build_episode_coverage_handoff
from bridge_school_api.tournament_episode_scoring_intake_v3 import (
    TournamentEpisodeScoringIntakeError,
    build_episode_scoring_template,
)


def _source():
    return {
        "schema": "bridge-tournament-facts-v1",
        "tournament": {"provider_native_key": "bridge.co.il:event:30041:round:2"},
        "columns": ["board", "status"],
        "rows": ["1|played", "2|unplayed"],
    }


def _candidate(candidate_id="candidate-1", board_number=1):
    return {
        "candidate_id": candidate_id,
        "review_id": "review-1",
        "event_id": "30041",
        "deal_id": f"30041:round-2:{board_number}",
        "board_number": board_number,
        "category": "contract_result",
        "review_status": "PENDING_TEACHER_REVIEW",
        "coverage_eligible": False,
    }


def _inventory(candidates=None):
    candidates = candidates or [_candidate()]
    return {
        "schema": "tournament-evidence-episode-candidate-inventory-v1",
        "normative_algorithm_version": "1.4",
        "event_id": "30041",
        "technical_candidates": candidates,
        "evidence_candidate_inventory_complete": True,
        "automatic_episode_scoring_allowed": False,
        "automatic_transferability_judgment_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
    }


def _score_row(row, *, impact, transferability, reliability):
    row["explicit_episode_adjudication"] = True
    row["impact_score"] = impact
    row["transferability_score"] = transferability
    row["reliability_score"] = reliability
    row["score_actor"] = "teacher:test"
    row["score_provenance"] = {"source": "explicit-test-adjudication"}
    row["status"] = "SCORED_EXPLICITLY"


def test_pending_scoring_cannot_enter_release_coverage():
    inventory = _inventory()
    intake = build_episode_scoring_template(inventory)

    handoff = build_episode_coverage_handoff(_source(), inventory, intake)

    assert handoff["candidate_count"] == 1
    assert handoff["explicitly_scored_count"] == 0
    assert handoff["pending_scoring_count"] == 1
    assert handoff["episode_scoring_complete"] is False
    assert handoff["coverage_episode_count"] == 0
    assert handoff["handoff_ready"] is False
    assert "EPISODE_SCORING_NOT_COMPLETE" in handoff["handoff_blockers"]
    assert "EPISODE_INVENTORY_NOT_COMPLETE" in handoff["handoff_blockers"]
    assert handoff["coverage_manifest"]["significant_episode_count"] == 0


def test_explicit_significant_score_creates_deep_slide_plan():
    inventory = _inventory()
    intake = build_episode_scoring_template(inventory)
    _score_row(intake["rows"][0], impact=2, transferability=1, reliability=1)

    handoff = build_episode_coverage_handoff(_source(), inventory, intake)

    assert handoff["episode_scoring_complete"] is True
    assert handoff["pending_scoring_count"] == 0
    assert handoff["explicitly_scored_count"] == 1
    assert handoff["handoff_ready"] is True
    assert handoff["handoff_blockers"] == []
    manifest = handoff["coverage_manifest"]
    assert manifest["significant_episode_count"] == 1
    assert manifest["episodes"][0]["total_score"] == 4
    assert manifest["episodes"][0]["tier"] == "SIGNIFICANT_DEEP_SLIDE"
    assert manifest["episodes"][0]["required_separate_slide_key"] == "board-1-deep-1"
    assert "board-1-deep-1" in manifest["expected_slide_keys"]


def test_explicit_low_score_is_covered_without_deep_slide():
    inventory = _inventory()
    intake = build_episode_scoring_template(inventory)
    _score_row(intake["rows"][0], impact=1, transferability=0, reliability=0)

    handoff = build_episode_coverage_handoff(_source(), inventory, intake)

    assert handoff["handoff_ready"] is True
    manifest = handoff["coverage_manifest"]
    assert manifest["significant_episode_count"] == 0
    assert manifest["episodes"][0]["tier"] == "BRIEF_REVIEW"
    assert "required_separate_slide_key" not in manifest["episodes"][0]


def test_partial_adjudication_remains_blocked_and_preserves_pending_candidate():
    candidates = [_candidate("candidate-1"), {**_candidate("candidate-2"), "review_id": "review-2"}]
    inventory = _inventory(candidates)
    intake = build_episode_scoring_template(inventory)
    _score_row(intake["rows"][0], impact=2, transferability=2, reliability=2)

    handoff = build_episode_coverage_handoff(_source(), inventory, intake)

    assert handoff["explicitly_scored_count"] == 1
    assert handoff["pending_scoring_count"] == 1
    assert handoff["coverage_episode_count"] == 1
    assert handoff["handoff_ready"] is False
    assert "EPISODE_SCORING_NOT_COMPLETE" in handoff["handoff_blockers"]


def test_pending_row_cannot_smuggle_score_into_handoff():
    inventory = _inventory()
    intake = build_episode_scoring_template(inventory)
    tampered = copy.deepcopy(intake)
    tampered["rows"][0]["impact_score"] = 2

    with pytest.raises(TournamentEpisodeScoringIntakeError):
        build_episode_coverage_handoff(_source(), inventory, tampered)
