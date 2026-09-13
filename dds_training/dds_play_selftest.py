from __future__ import annotations

import json

from dds_play import analyse_line, first_refutation


DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def main() -> None:
    cards = ["SA", "SQ", "S8", "SK", "S2", "SJ", "S7", "S5"]
    analysis = analyse_line(
        deal=DEAL,
        declarer=2,
        trump=4,
        opening_leader=3,
        cards=cards,
    )
    assert len(analysis["projected_declarer_values"]) == len(cards) + 1
    assert len(analysis["actors"]) == len(cards)
    assert analysis["trajectory"]["value_definition"] == "projected_final_declarer_tricks"
    assert not analysis["trajectory"]["invariant_violations"], analysis["trajectory"]
    assert analysis["dds_raw"]["method"] == "repeated_solve_board_pbn"
    assert analysis["decision_errors"], analysis
    assert all(item["dd_regret"] > 0 for item in analysis["decision_errors"])
    assert all(item["optimal_cards"] for item in analysis["decision_errors"])
    assert all(item["chosen_card"] in item["candidate_scores"] for item in analysis["decision_errors"])
    assert all(item["regret_matches_value_swing"] for item in analysis["decision_errors"]), analysis["decision_errors"]

    start = analysis["projected_declarer_values"][0]
    refuted = first_refutation(analysis, start + 1)
    assert refuted["refuted"] is True and refuted["prefix_cards"] == 0
    if analysis["first_defense_error"]:
        assert refuted["first_assumed_defense_error"]["optimal_cards"]
    attainable = first_refutation(analysis, min(analysis["projected_declarer_values"]))
    assert attainable["refuted"] is False

    print(json.dumps({
        "ok": True,
        "cards": len(cards),
        "positions": len(analysis["projected_declarer_values"]),
        "start_value": start,
        "first_error": analysis["trajectory"]["first_error"],
        "decision_errors": len(analysis["decision_errors"]),
        "first_declarer_error": analysis["first_declarer_error"],
        "first_defense_error": analysis["first_defense_error"],
        "all_swings_explained_by_candidate_regret": True,
        "invariant_violations": 0,
        "initial_overclaim_refuted": True,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
