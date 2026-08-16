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
    start = analysis["projected_declarer_values"][0]
    refuted = first_refutation(analysis, start + 1)
    assert refuted["refuted"] is True and refuted["prefix_cards"] == 0
    attainable = first_refutation(analysis, min(analysis["projected_declarer_values"]))
    assert attainable["refuted"] is False

    print(json.dumps({
        "ok": True,
        "cards": len(cards),
        "positions": len(analysis["projected_declarer_values"]),
        "start_value": start,
        "first_error": analysis["trajectory"]["first_error"],
        "invariant_violations": 0,
        "initial_overclaim_refuted": True,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
