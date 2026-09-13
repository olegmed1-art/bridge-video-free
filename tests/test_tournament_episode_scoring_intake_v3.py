import copy
import pytest

from bridge_school_api.tournament_episode_scoring_intake_v3 import (
    TournamentEpisodeScoringIntakeError,
    build_episode_scoring_template,
    validate_episode_scoring_intake,
)


def _inventory():
    candidate = {
        "candidate_id": "technical-review:abc",
        "review_id": "r1",
        "event_id": "42",
        "deal_id": "42:round-1:7",
        "board_number": 7,
        "category": "dds3_pair_same_contract_delta",
        "candidate_kind": "TECHNICAL_REVIEW_CANDIDATE",
        "review_status": "PENDING_TEACHER_REVIEW",
        "technical_finding_sha256": "b" * 64,
        "technical_repeat_key": "DDS3_PAIR_SAME_CONTRACT_DELTA_V1",
        "observed_outcome_context": {},
        "impact_score": None,
        "transferability_score": None,
        "reliability_score": None,
        "total_score": None,
        "coverage_tier": None,
        "deep_slide_required": None,
        "coverage_eligible": False,
        "methodology_mapping": None,
        "student_error_attribution": None,
        "causal_link": "NOT_ESTABLISHED",
    }
    return {
        "schema": "tournament-evidence-episode-candidate-inventory-v1",
        "event_id": "42",
        "evidence_candidate_inventory_complete": True,
        "automatic_episode_scoring_allowed": False,
        "automatic_transferability_judgment_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "technical_candidates": [candidate],
    }


def test_template_is_inert_and_has_no_prescored_values():
    template = build_episode_scoring_template(_inventory())
    row = template["rows"][0]
    assert template["automatic_scoring_allowed"] is False
    assert row["explicit_episode_adjudication"] is False
    assert row["impact_score"] is None
    assert row["transferability_score"] is None
    assert row["reliability_score"] is None
    assert row["score_actor"] is None
    assert row["score_provenance"] is None
    assert row["status"] == "PENDING_SCORING"
    result = validate_episode_scoring_intake(_inventory(), template)
    assert result["explicitly_scored_count"] == 0
    assert result["pending_scoring_count"] == 1
    assert result["coverage_episode_inputs"] == []


def test_explicit_scoring_can_emit_coverage_compatible_episode_only_with_provenance():
    inventory = _inventory()
    intake = build_episode_scoring_template(inventory)
    row = intake["rows"][0]
    row.update(
        explicit_episode_adjudication=True,
        impact_score=2,
        transferability_score=1,
        reliability_score=2,
        score_actor="teacher-or-authorized-evidence-reviewer",
        score_provenance={"source": "explicit-review", "decision_id": "d1"},
        status="SCORED_EXPLICITLY",
    )
    result = validate_episode_scoring_intake(inventory, intake)
    assert result["explicitly_scored_count"] == 1
    assert result["pending_scoring_count"] == 0
    assert result["episode_scoring_complete"] is True
    episode = result["coverage_episode_inputs"][0]
    assert episode["board_number"] == 7
    assert episode["impact_score"] == 2
    assert episode["transferability_score"] == 1
    assert episode["reliability_score"] == 2
    assert episode["score_provenance"]["explicit_episode_adjudication"] is True


def test_scores_without_explicit_adjudication_are_rejected():
    inventory = _inventory()
    intake = build_episode_scoring_template(inventory)
    intake["rows"][0]["impact_score"] = 2
    with pytest.raises(TournamentEpisodeScoringIntakeError, match="pending row cannot contain"):
        validate_episode_scoring_intake(inventory, intake)


def test_explicit_scoring_requires_actor_and_provenance():
    inventory = _inventory()
    intake = build_episode_scoring_template(inventory)
    row = intake["rows"][0]
    row.update(
        explicit_episode_adjudication=True,
        impact_score=1,
        transferability_score=1,
        reliability_score=1,
        status="SCORED_EXPLICITLY",
    )
    with pytest.raises(TournamentEpisodeScoringIntakeError, match="score_actor"):
        validate_episode_scoring_intake(inventory, intake)


def test_out_of_range_scores_are_rejected():
    inventory = _inventory()
    intake = build_episode_scoring_template(inventory)
    row = intake["rows"][0]
    row.update(
        explicit_episode_adjudication=True,
        impact_score=3,
        transferability_score=1,
        reliability_score=1,
        score_actor="reviewer",
        score_provenance={"source": "review"},
        status="SCORED_EXPLICITLY",
    )
    with pytest.raises(TournamentEpisodeScoringIntakeError, match="impact_score"):
        validate_episode_scoring_intake(inventory, intake)


def test_candidate_binding_is_immutable():
    inventory = _inventory()
    intake = build_episode_scoring_template(inventory)
    intake["rows"][0]["board_number"] = 8
    with pytest.raises(TournamentEpisodeScoringIntakeError, match="immutable candidate binding"):
        validate_episode_scoring_intake(inventory, intake)


def test_weakened_boundary_is_rejected():
    inventory = _inventory()
    intake = build_episode_scoring_template(inventory)
    intake["automatic_scoring_allowed"] = True
    with pytest.raises(TournamentEpisodeScoringIntakeError, match="boundary was weakened"):
        validate_episode_scoring_intake(inventory, intake)
