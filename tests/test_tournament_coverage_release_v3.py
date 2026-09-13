from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from bridge_school_api.tournament_coverage_release_v3 import (
    TournamentCoverageError,
    build_coverage_manifest,
    build_release_gate,
    validate_rendered_slide_coverage,
)
from bridge_school_api.tournament_mp_validation_v3 import assess_mp_recalculation_availability
from bridge_school_api.tournament_preanalysis_gate_v3 import build_preanalysis_gate


FACTS_PATH = Path("data/tournaments/tournament_30041_round2_diana_facts_v1.json")


def _source():
    return json.loads(FACTS_PATH.read_text(encoding="utf-8"))


def _real_preanalysis(source):
    raw = FACTS_PATH.read_bytes()
    return build_preanalysis_gate(
        source,
        normalized_facts_sha256=hashlib.sha256(raw).hexdigest(),
        normalized_facts_size_bytes=len(raw),
        normalized_facts_received_at="2026-08-21T11:05:01Z",
        normalized_facts_commit="0158e506-repository-ingestion",
        algorithm_revision_id="AIroW35dhYwaAOQ1dDxMCkYjVKitrJKTII3Zx0IS7RNHuQDBE8iXBw-l2Ux9qF42DTU1gUWmWDu3kn9XVb1ne2cTls8HTLvblELsTAZRRuY",
    )


def test_real_30041_builds_minimum_v14_coverage_but_blocks_final_release_until_episode_inventory_and_qa():
    source = _source()
    coverage = build_coverage_manifest(source, episodes=(), episode_inventory_complete=False)

    assert coverage["status_counts"] == {"played": 21, "average": 1, "unplayed": 2}
    assert len(coverage["played_boards"]) == 21
    assert coverage["planned_deck_slide_count"] == 24  # title + overview + 21 played + final
    assert coverage["significant_episode_count"] == 0
    assert coverage["coverage_plan_release_ready"] is False
    assert coverage["release_blockers"] == ["EPISODE_INVENTORY_NOT_COMPLETE"]
    assert coverage["automatic_episode_scoring_allowed"] is False

    administrative = [row for row in coverage["boards"] if row["status"] != "played"]
    assert len(administrative) == 3
    assert all(row["student_decision_statistics_allowed"] is False for row in administrative)
    assert all(row["base_slide_required"] is False for row in administrative)

    preanalysis = _real_preanalysis(source)
    mp = assess_mp_recalculation_availability(source)
    release = build_release_gate(
        preanalysis_gate=preanalysis,
        coverage_manifest=coverage,
        mp_availability=mp,
    )
    assert release["technical_analysis_ready"] is True
    assert release["full_causal_replay_ready"] is False
    assert release["full_traveller_available"] is False
    assert release["final_report_release_ready"] is False
    assert "EPISODE_INVENTORY_NOT_COMPLETE" in release["hard_stop_conditions"]
    assert "RENDERED_SLIDE_COVERAGE_NOT_PROVIDED" in release["hard_stop_conditions"]
    assert "VISUAL_QA_NOT_PASSED" in release["hard_stop_conditions"]
    assert "FULL_TRAVELLER_ABSENT_OFFICIAL_PERCENTAGE_RETAINED" in release["limitations"]


def test_significant_episode_gets_separate_adjacent_slide_but_standard_episode_does_not():
    source = _source()
    episodes = [
        {
            "episode_id": "e-board2-significant",
            "board_number": 2,
            "impact_score": 2,
            "transferability_score": 2,
            "reliability_score": 1,
            "score_provenance": {"basis": "explicit-reviewed-test-fixture"},
        },
        {
            "episode_id": "e-board3-standard",
            "board_number": 3,
            "impact_score": 1,
            "transferability_score": 1,
            "reliability_score": 1,
            "score_provenance": {"basis": "explicit-reviewed-test-fixture"},
        },
    ]
    coverage = build_coverage_manifest(source, episodes=episodes, episode_inventory_complete=True)

    assert coverage["coverage_plan_release_ready"] is True
    assert coverage["significant_episode_count"] == 1
    assert coverage["planned_deck_slide_count"] == 25
    board2 = next(row for row in coverage["boards"] if row["board_number"] == 2)
    board3 = next(row for row in coverage["boards"] if row["board_number"] == 3)
    assert board2["planned_slide_keys"] == ["board-2-base", "board-2-deep-1"]
    assert board3["planned_slide_keys"] == ["board-3-base"]
    assert coverage["expected_slide_keys"].index("board-2-deep-1") == coverage["expected_slide_keys"].index("board-2-base") + 1

    checked = validate_rendered_slide_coverage(coverage, coverage["expected_slide_keys"])
    assert checked["export_coverage_gate_pass"] is True


def test_rendered_coverage_fails_closed_on_missing_extra_or_wrong_order():
    coverage = build_coverage_manifest(_source(), episodes=(), episode_inventory_complete=True)
    expected = list(coverage["expected_slide_keys"])

    missing = validate_rendered_slide_coverage(coverage, expected[:-1])
    assert missing["export_coverage_gate_pass"] is False
    assert missing["missing_slide_keys"] == ["deck-final"]

    extra = validate_rendered_slide_coverage(coverage, expected + ["unexpected"])
    assert extra["export_coverage_gate_pass"] is False
    assert extra["extra_slide_keys"] == ["unexpected"]

    swapped = expected[:]
    swapped[2], swapped[3] = swapped[3], swapped[2]
    wrong_order = validate_rendered_slide_coverage(coverage, swapped)
    assert wrong_order["order_matches_plan"] is False
    assert wrong_order["export_coverage_gate_pass"] is False

    with pytest.raises(TournamentCoverageError):
        validate_rendered_slide_coverage(coverage, expected + [expected[-1]])


def test_episode_scores_require_explicit_provenance_and_played_board():
    source = _source()
    missing_provenance = {
        "episode_id": "e1",
        "board_number": 2,
        "impact_score": 2,
        "transferability_score": 2,
        "reliability_score": 2,
    }
    with pytest.raises(TournamentCoverageError):
        build_coverage_manifest(source, episodes=[missing_provenance], episode_inventory_complete=True)

    on_unplayed = {
        **missing_provenance,
        "board_number": 21,
        "score_provenance": {"basis": "explicit"},
    }
    with pytest.raises(TournamentCoverageError):
        build_coverage_manifest(source, episodes=[on_unplayed], episode_inventory_complete=True)

    invalid_score = {
        **missing_provenance,
        "impact_score": 3,
        "score_provenance": {"basis": "explicit"},
    }
    with pytest.raises(TournamentCoverageError):
        build_coverage_manifest(source, episodes=[invalid_score], episode_inventory_complete=True)


def test_release_gate_allows_explicit_limited_release_without_full_traveller_or_causal_replay():
    source = _source()
    coverage = build_coverage_manifest(source, episodes=(), episode_inventory_complete=True)
    preanalysis = {
        "schema": "tournament-preanalysis-gate-v1",
        "run_id": "run-test",
        "tournament": {"provider_native_key": "bridge.co.il:event:30041:round:2"},
        "facts_only_analysis_ready": True,
        "full_causal_replay_ready": False,
        "hard_stop_conditions": [],
        "limitations": ["ACTUAL_AUCTION_ABSENT_FOR_SOME_OR_ALL_PLAYED_BOARDS"],
    }
    mp = {
        "schema": "tournament-mp-recalculation-availability-v1",
        "scoring_method": "MP",
        "applicable": True,
        "full_traveller_available": False,
        "independent_mp_recalculation_allowed": False,
        "status": "OFFICIAL_PERCENTAGE_NOT_INDEPENDENTLY_RECALCULATED",
    }
    release = build_release_gate(
        preanalysis_gate=preanalysis,
        coverage_manifest=coverage,
        mp_availability=mp,
        rendered_slide_keys=coverage["expected_slide_keys"],
        visual_qa_pass=True,
    )
    assert release["final_report_release_ready"] is True
    assert release["full_causal_replay_ready"] is False
    assert release["full_traveller_available"] is False
    assert release["automatic_student_error_attribution_allowed"] is False
    assert release["automatic_methodology_invention_allowed"] is False
    assert "FULL_TRAVELLER_ABSENT_OFFICIAL_PERCENTAGE_RETAINED" in release["limitations"]


def test_traveller_present_but_not_yet_recalculated_is_a_release_blocker():
    source = copy.deepcopy(_source())
    coverage = build_coverage_manifest(source, episodes=(), episode_inventory_complete=True)
    preanalysis = {
        "schema": "tournament-preanalysis-gate-v1",
        "run_id": "run-test",
        "tournament": {"provider_native_key": "bridge.co.il:event:30041:round:2"},
        "facts_only_analysis_ready": True,
        "full_causal_replay_ready": False,
        "hard_stop_conditions": [],
        "limitations": [],
    }
    mp = {
        "schema": "tournament-mp-recalculation-availability-v1",
        "full_traveller_available": True,
        "status": "TRAVELLER_AVAILABLE_RECALCULATION_REQUIRED",
    }
    release = build_release_gate(
        preanalysis_gate=preanalysis,
        coverage_manifest=coverage,
        mp_availability=mp,
        rendered_slide_keys=coverage["expected_slide_keys"],
        visual_qa_pass=True,
    )
    assert release["final_report_release_ready"] is False
    assert "TRAVELLER_PRESENT_MP_RECALCULATION_STILL_REQUIRED" in release["hard_stop_conditions"]
