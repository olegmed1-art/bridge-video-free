import copy

import pytest

from bridge_school_api.tournament_opening_lead_dds3_v3 import (
    TournamentOpeningLeadDDS3Error,
    analyze_opening_leads,
)


def _source(*, pair_direction="E-W", opening_lead="HA"):
    columns = [
        "board",
        "dealer",
        "vulnerability",
        "N",
        "E",
        "S",
        "W",
        "pair_direction",
        "status",
        "contract",
        "declarer",
        "result_delta",
        "opening_lead",
        "pair_score",
        "pair_percentage",
    ]
    values = [
        "1",
        "N",
        "None",
        "AKQJT98765432...",
        ".AKQJT98765432..",
        "..AKQJT98765432.",
        "...AKQJT98765432",
        pair_direction,
        "played",
        "1NT",
        "N",
        "0",
        opening_lead,
        "90" if pair_direction == "N-S" else "-90",
        "50.0",
    ]
    return {
        "schema": "bridge-tournament-facts-v1",
        "source": {"drive_id": "x", "sha256": "f" * 64, "size_bytes": 1, "title": "fixture"},
        "tournament": {"provider_native_key": "bridge.co.il:event:30041:round:2", "scoring": "MP"},
        "policy": {"mode": "FACTS_ONLY"},
        "columns": columns,
        "rows": ["|".join(values)],
    }


def _solver(position):
    assert position["first"] == "E"
    assert position["trump"] == "NT"
    assert position["current_trick"] == []
    assert position["pbn"].startswith("N:")
    return {
        "engine": "DDS3",
        "engine_version": "v3.0.0+test",
        "fallback_used": False,
        "operation": "position_all_moves",
        "best_tricks": 7,
        "optimal_cards": ["HK"],
        "moves": [
            {"card": "HK", "tricks": 7, "regret": 0, "optimal": True},
            {"card": "HA", "tricks": 6, "regret": 1, "optimal": False},
        ],
    }


def test_target_pair_positive_lead_regret_becomes_teacher_review_candidate_only():
    report = analyze_opening_leads(_source(), solve_position=_solver)

    assert report["played_leads_analyzed"] == 1
    assert report["target_pair_opening_leads_analyzed"] == 1
    assert report["target_pair_positive_regret_candidates"] == 1
    item = report["results"][0]
    assert item["deal_id"] == "30041:round-2:1"
    assert item["actual_opening_lead"] == "HA"
    assert item["lead_regret_tricks"] == 1
    assert item["actual_lead_dd_optimal"] is False
    assert item["evidence_kind"] == "DDS_FACT"
    assert item["causal_error_attribution"] == "NOT_ESTABLISHED"
    assert item["student_error_attribution"] is None
    assert item["methodology_mapping"] is None
    candidate = report["teacher_review_candidates"][0]
    assert candidate["teacher_review_required"] is True
    assert candidate["coverage_eligible"] is False


def test_opponent_opening_lead_is_analyzed_but_not_attributed_to_target_pair():
    report = analyze_opening_leads(_source(pair_direction="N-S"), solve_position=_solver)
    assert report["played_leads_analyzed"] == 1
    assert report["target_pair_opening_leads_analyzed"] == 0
    assert report["target_pair_positive_regret_candidates"] == 0
    assert report["results"][0]["lead_regret_tricks"] == 1


def test_actual_optimal_lead_has_zero_regret_and_no_candidate():
    def optimal_solver(position):
        result = _solver(position)
        result["best_tricks"] = 7
        result["optimal_cards"] = ["HA", "HK"]
        result["moves"] = [
            {"card": "HA", "tricks": 7, "regret": 0, "optimal": True},
            {"card": "HK", "tricks": 7, "regret": 0, "optimal": True},
        ]
        return result

    report = analyze_opening_leads(_source(), solve_position=optimal_solver)
    assert report["dd_optimal_actual_leads"] == 1
    assert report["positive_regret_actual_leads"] == 0
    assert report["target_pair_positive_regret_candidates"] == 0


def test_actual_source_lead_must_exist_among_dds3_legal_moves():
    source = _source(opening_lead="HQ")
    with pytest.raises(TournamentOpeningLeadDDS3Error, match="not present"):
        analyze_opening_leads(source, solve_position=_solver)


def test_fallback_or_wrong_operation_fails_closed():
    fallback = _solver({"first": "E", "trump": "NT", "current_trick": [], "pbn": "N:x"})
    fallback["fallback_used"] = True
    with pytest.raises(TournamentOpeningLeadDDS3Error, match="fallback"):
        analyze_opening_leads(_source(), solve_position=lambda _: fallback)

    wrong = copy.deepcopy(fallback)
    wrong["fallback_used"] = False
    wrong["operation"] = "dd_table"
    with pytest.raises(TournamentOpeningLeadDDS3Error, match="unexpected"):
        analyze_opening_leads(_source(), solve_position=lambda _: wrong)
