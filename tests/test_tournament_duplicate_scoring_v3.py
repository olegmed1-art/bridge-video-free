from bridge_school_api.tournament_duplicate_scoring_v3 import (
    duplicate_score_declarer,
    score_for_target_pair,
    validate_tournament_fact_scores,
)


def test_standard_made_contracts_and_slams():
    assert duplicate_score_declarer("4S", result_delta=0, vulnerable=False) == 420
    assert duplicate_score_declarer("4S", result_delta=0, vulnerable=True) == 620
    assert duplicate_score_declarer("3NT", result_delta=0, vulnerable=False) == 400
    assert duplicate_score_declarer("3NT", result_delta=0, vulnerable=True) == 600
    assert duplicate_score_declarer("6H", result_delta=0, vulnerable=True) == 1430
    assert duplicate_score_declarer("7NT", result_delta=0, vulnerable=False) == 1520


def test_doubles_redoubles_overtricks_and_undertricks():
    assert duplicate_score_declarer("4HX", result_delta=-1, vulnerable=True) == -200
    assert duplicate_score_declarer("3SX", result_delta=-1, vulnerable=False) == -100
    assert duplicate_score_declarer("3SX", result_delta=-4, vulnerable=False) == -800
    assert duplicate_score_declarer("2SX", result_delta=1, vulnerable=False) == 570
    assert duplicate_score_declarer("1NTXX", result_delta=0, vulnerable=False) == 560
    assert duplicate_score_declarer("2HXX", result_delta=-2, vulnerable=True) == -1000


def test_target_pair_perspective_flips_declarer_score():
    assert score_for_target_pair(
        "3NT", declarer="S", result_delta=0, vulnerability="NS", target_side="E-W"
    ) == -600
    assert score_for_target_pair(
        "4HX", declarer="S", result_delta=-1, vulnerability="NS", target_side="E-W"
    ) == 200


def test_facts_validator_skips_administrative_and_unplayed_rows():
    source = {
        "schema": "bridge-tournament-facts-v1",
        "columns": [
            "board", "dealer", "vulnerability", "pair_direction", "status", "contract", "declarer",
            "result_delta", "pair_score"
        ],
        "rows": [
            "1|N|None|N-S|average||||",
            "2|E|NS|E-W|played|3NT|S|0|-600",
            "3|S|EW||unplayed||||",
        ],
    }
    report = validate_tournament_fact_scores(source)
    assert report["played_scores_checked"] == 1
    assert report["skipped_nonplayed"] == 2
    assert report["all_published_scores_match"] is True
    assert report["administrative_results_recalculated"] is False


def test_facts_validator_surfaces_score_mismatch_without_rewriting_source():
    source = {
        "schema": "bridge-tournament-facts-v1",
        "columns": [
            "board", "dealer", "vulnerability", "pair_direction", "status", "contract", "declarer",
            "result_delta", "pair_score"
        ],
        "rows": ["1|N|None|N-S|played|4S|N|0|430"],
    }
    report = validate_tournament_fact_scores(source)
    assert report["all_published_scores_match"] is False
    assert report["mismatches"][0]["published_score"] == 430
    assert report["mismatches"][0]["recalculated_score"] == 420
