import pytest

from bridge_school_api.tournament_episode_coverage_handoff_v3 import (
    TournamentEpisodeCoverageHandoffError,
    build_episode_coverage_handoff,
)
from bridge_school_api.tournament_episode_scoring_intake_v3 import build_episode_scoring_template
from bridge_school_api.tournament_episode_source_census_v3 import source_facts_sha256


QUEUE_SHA = "a" * 64


def _source():
    return {
        "schema": "bridge-tournament-facts-v1",
        "tournament": {"provider_native_key": "bridge.co.il:event:30041:round:2"},
        "columns": ["board", "status"],
        "rows": ["1|played", "2|unplayed"],
    }


def _candidate():
    return {
        "candidate_id": "candidate-1",
        "review_id": "review-1",
        "event_id": "30041",
        "deal_id": "30041:round-2:1",
        "board_number": 1,
        "category": "contract_result",
        "review_status": "PENDING_TEACHER_REVIEW",
        "coverage_eligible": False,
    }


def _inventory():
    return {
        "schema": "tournament-evidence-episode-candidate-inventory-v1",
        "normative_algorithm_version": "1.4",
        "event_id": "30041",
        "queue_sha256": QUEUE_SHA,
        "technical_candidates": [_candidate()],
        "evidence_candidate_inventory_complete": True,
        "automatic_episode_scoring_allowed": False,
        "automatic_transferability_judgment_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "release_blockers": [
            "EXPLICIT_EPISODE_SCORING_NOT_AVAILABLE",
            "NON_DDS_EPISODE_COVERAGE_NOT_ESTABLISHED",
        ],
    }


def _ledger():
    candidate = _candidate()
    return {
        "schema": "tournament-teacher-decision-ledger-v1",
        "queue_sha256": QUEUE_SHA,
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "decisions": [
            {
                "review_id": candidate["review_id"],
                "event_id": candidate["event_id"],
                "deal_id": candidate["deal_id"],
                "category": candidate["category"],
                "queue_item_sha256": "b" * 64,
                "status": "PENDING",
                "teacher_decision_required": True,
                "automatic_methodology_mapping_allowed": False,
                "automatic_student_error_attribution_allowed": False,
                "decision_note": None,
                "decision_provenance": None,
            }
        ],
    }


def _census(source, *, complete=True, blockers=None):
    return {
        "schema": "tournament-episode-source-census-v1",
        "normative_algorithm_version": "1.4",
        "source_facts_sha256": source_facts_sha256(source),
        "provider_native_key": source["tournament"]["provider_native_key"],
        "non_dd_episode_source_census_complete": complete,
        "census_blockers": list(blockers or []),
        "unavailable_evidence_not_reconstructed": True,
        "automatic_episode_creation_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
    }


def test_complete_bound_census_resolves_only_non_dd_inventory_blocker():
    source = _source()
    inventory = _inventory()
    intake = build_episode_scoring_template(inventory)
    handoff = build_episode_coverage_handoff(
        source,
        inventory,
        intake,
        _ledger(),
        source_census=_census(source),
    )

    assert handoff["non_dd_source_census_complete"] is True
    assert "NON_DDS_EPISODE_COVERAGE_NOT_ESTABLISHED" not in handoff["handoff_blockers"]
    assert "TEACHER_DECISION_PENDING" in handoff["handoff_blockers"]
    assert handoff["handoff_ready"] is False


def test_absent_census_preserves_non_dd_blocker():
    source = _source()
    inventory = _inventory()
    handoff = build_episode_coverage_handoff(
        source, inventory, build_episode_scoring_template(inventory), _ledger()
    )
    assert handoff["non_dd_source_census_supplied"] is False
    assert "NON_DDS_EPISODE_COVERAGE_NOT_ESTABLISHED" in handoff["handoff_blockers"]


def test_incomplete_census_exposes_its_specific_blocker():
    source = _source()
    inventory = _inventory()
    handoff = build_episode_coverage_handoff(
        source,
        inventory,
        build_episode_scoring_template(inventory),
        _ledger(),
        source_census=_census(
            source,
            complete=False,
            blockers=["ACTUAL_AUCTION_EVIDENCE_REQUIRES_EPISODE_ANALYSIS"],
        ),
    )
    assert handoff["non_dd_source_census_complete"] is False
    assert "ACTUAL_AUCTION_EVIDENCE_REQUIRES_EPISODE_ANALYSIS" in handoff["handoff_blockers"]
    assert "NON_DDS_SOURCE_CENSUS_NOT_COMPLETE" in handoff["handoff_blockers"]


def test_census_for_different_source_fails_closed():
    source = _source()
    census = _census(source)
    changed = _source()
    changed["rows"] = ["1|unplayed", "2|unplayed"]
    inventory = _inventory()

    with pytest.raises(TournamentEpisodeCoverageHandoffError, match="not bound"):
        build_episode_coverage_handoff(
            changed,
            inventory,
            build_episode_scoring_template(inventory),
            _ledger(),
            source_census=census,
        )
