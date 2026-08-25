from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge_school_api.tournament_mp_validation_v3 import (
    MatchpointValidationError,
    assess_mp_recalculation_availability,
    calculate_matchpoints,
    validate_published_mp_percentage,
)


FACTS = Path("data/tournaments/tournament_30041_round2_diana_facts_v1.json")


def test_matchpoints_award_two_for_worse_and_one_for_equal():
    # Target 100 against -200, 100, 100, 300 => 2 + 1 + 1 + 0 = 4 / top 8 = 50%.
    result = calculate_matchpoints(100, [-200, 100, 100, 300])
    assert result.comparisons == 4
    assert result.lower_results == 1
    assert result.equal_results == 2
    assert result.higher_results == 1
    assert result.matchpoints == 4
    assert result.top == 8
    assert result.percentage == 50.0


def test_top_is_two_times_number_of_comparisons():
    result = calculate_matchpoints(420, [-50, 170, 420, 450, 480])
    assert result.top == 10
    assert result.matchpoints == 5  # two worse = 4, one equal = 1
    assert result.percentage == 50.0


def test_published_percentage_validation_allows_display_rounding_only():
    check = validate_published_mp_percentage(
        target_score=100,
        comparison_scores=[-100, 0, 100],
        published_percentage=83.33,
    )
    # 2 worse + 1 equal -> 5/6 = 83.333...
    assert check["matchpoints"] == 5
    assert check["top"] == 6
    assert check["matches_within_tolerance"] is True


def test_missing_comparisons_fail_closed():
    with pytest.raises(MatchpointValidationError, match="at least one comparison"):
        calculate_matchpoints(100, [])


def test_real_30041_does_not_claim_independent_mp_recalculation_without_traveller():
    source = json.loads(FACTS.read_text(encoding="utf-8"))
    assert "traveller" not in source
    report = assess_mp_recalculation_availability(source)
    assert report["schema"] == "tournament-mp-recalculation-availability-v1"
    assert report["scoring_method"] == "MP"
    assert report["applicable"] is True
    assert report["full_traveller_available"] is False
    assert report["independent_mp_recalculation_allowed"] is False
    assert report["status"] == "OFFICIAL_PERCENTAGE_NOT_INDEPENDENTLY_RECALCULATED"
    assert "DDS3" in report["forbidden_shortcut"]


def test_explicit_normalized_traveller_enables_recalculation_requirement():
    source = json.loads(FACTS.read_text(encoding="utf-8"))
    source["traveller"] = {"boards": [{"board_number": 1, "scores": [100, 200]}]}
    report = assess_mp_recalculation_availability(source)
    assert report["full_traveller_available"] is True
    assert report["independent_mp_recalculation_allowed"] is True
    assert report["status"] == "TRAVELLER_AVAILABLE_RECALCULATION_REQUIRED"
