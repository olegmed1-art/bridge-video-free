import pytest

from bridge_school_api.tournament_episode_inventory_v3 import (
    TournamentEpisodeInventoryError,
    build_evidence_episode_candidate_inventory,
    coverage_episode_inputs,
)


def _source():
    columns = ["board", "status"]
    rows = ["1|played", "2|played", "3|average", "4|unplayed"]
    return {
        "schema": "bridge-tournament-facts-v1",
        "tournament": {"provider_native_key": "bridge.co.il:event:42:round:1"},
        "columns": columns,
        "rows": rows,
    }


def _item(*, review_id="r1", deal_id="42:round-1:1", category="contract_result"):
    return {
        "review_id": review_id,
        "event_id": "42",
        "deal_id": deal_id,
        "category": category,
        "status": "PENDING",
        "teacher_decision_required": True,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "causal_link": "NOT_ESTABLISHED",
        "queue_context": {
            "outcome_scale": "MP_PERCENTAGE",
            "observed_outcome": 25.0,
            "adverse_outcome_magnitude": 25.0,
            "technical_trick_loss": 1.0,
        },
        "technical_finding": {
            "summary": "technical DD opportunity",
            "trick_loss": 1.0,
            "score_loss": None,
            "tournament_impact": None,
            "observability": "NOT_OBSERVABLE",
            "repeat_key": "DDS3_PAIR_SAME_CONTRACT_DELTA_V1",
            "evidence": [{"kind": "DDS_FACT", "message": "x", "provenance": {}, "confidence": 1.0}],
        },
        "methodology_mapping": None,
        "student_error_attribution": None,
    }


def _dossier(items=None):
    return {
        "schema": "tournament-teacher-review-dossier-v1",
        "queue_sha256": "a" * 64,
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "cross_event_numeric_ranking_allowed": False,
        "items": list(items if items is not None else [_item()]),
    }


def test_pending_technical_review_is_candidate_not_scored_episode():
    inventory = build_evidence_episode_candidate_inventory(_source(), _dossier(), event_id="42")
    assert inventory["played_board_count"] == 2
    assert inventory["technical_candidate_count"] == 1
    assert inventory["technical_candidate_board_count"] == 1
    assert inventory["evidence_candidate_inventory_complete"] is True
    assert inventory["v1_4_episode_inventory_complete"] is False
    assert inventory["coverage_episode_inputs"] == []
    assert inventory["automatic_episode_scoring_allowed"] is False
    candidate = inventory["technical_candidates"][0]
    assert candidate["board_number"] == 1
    assert candidate["review_status"] == "PENDING_TEACHER_REVIEW"
    assert candidate["impact_score"] is None
    assert candidate["transferability_score"] is None
    assert candidate["reliability_score"] is None
    assert candidate["coverage_eligible"] is False
    assert candidate["methodology_mapping"] is None
    assert candidate["student_error_attribution"] is None
    assert coverage_episode_inputs(inventory) == []


def test_non_event_items_are_ignored_without_cross_event_ranking():
    other = _item(review_id="other", deal_id="99:round-1:1")
    other["event_id"] = "99"
    inventory = build_evidence_episode_candidate_inventory(_source(), _dossier([_item(), other]), event_id="42")
    assert inventory["technical_candidate_count"] == 1
    assert {item["event_id"] for item in inventory["technical_candidates"]} == {"42"}


def test_candidate_on_nonplayed_board_is_rejected():
    with pytest.raises(TournamentEpisodeInventoryError, match="played board"):
        build_evidence_episode_candidate_inventory(
            _source(), _dossier([_item(deal_id="42:round-1:3")]), event_id="42"
        )


def test_pedagogical_attribution_in_pending_item_is_rejected():
    item = _item()
    item["methodology_mapping"] = "invented"
    with pytest.raises(TournamentEpisodeInventoryError, match="pedagogical attribution"):
        build_evidence_episode_candidate_inventory(_source(), _dossier([item]), event_id="42")


def test_weakened_dossier_boundary_is_rejected():
    dossier = _dossier()
    dossier["automatic_decisions_allowed"] = True
    with pytest.raises(TournamentEpisodeInventoryError, match="boundary was weakened"):
        build_evidence_episode_candidate_inventory(_source(), dossier, event_id="42")


def test_duplicate_review_identity_is_rejected():
    with pytest.raises(TournamentEpisodeInventoryError, match="duplicate"):
        build_evidence_episode_candidate_inventory(
            _source(), _dossier([_item(), _item()]), event_id="42"
        )


def test_candidate_inventory_cannot_be_smuggled_into_coverage_inputs():
    inventory = build_evidence_episode_candidate_inventory(_source(), _dossier(), event_id="42")
    inventory["coverage_episode_inputs"] = [{"episode_id": "x"}]
    with pytest.raises(TournamentEpisodeInventoryError, match="cannot enter coverage scoring"):
        coverage_episode_inputs(inventory)
