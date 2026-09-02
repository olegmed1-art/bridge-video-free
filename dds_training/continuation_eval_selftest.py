from __future__ import annotations

"""DDS-backed contract tests for blind mid-play continuation evaluation."""

import json

from continuation_eval import evaluate_continuation
from continuation_tasks import continuation_tasks_from_line

DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def main() -> None:
    source = {
        "task_id": "CONT-EVAL-SOURCE",
        "deal_id": "CONT-EVAL-DEAL",
        "root_deal_id": "CONT-EVAL-DEAL",
        "split": "train",
        "task_type": "contract_tricks",
        "deal": DEAL,
        "declarer": 2,
        "strain": 4,
        "leader": 3,
    }
    legal_line = ["SA", "SQ", "S8", "SK", "S2", "SJ", "S7", "S5"]
    tasks = continuation_tasks_from_line(source, legal_line, prefix_indexes=[4])
    assert len(tasks) == 1, tasks
    task = tasks[0]
    assert task["actor"] == "defense"
    assert task["next_seat"] == 3

    chosen = evaluate_continuation(task, {"card": "S2", "locked": True})
    assert chosen["legal_or_equivalent"] is True, chosen
    assert chosen["chosen_card"] == "S2"
    assert chosen["chosen_card"] in chosen["scores"]
    assert chosen["dd_regret"] == (
        chosen["best_side_to_play_tricks"] - chosen["chosen_side_to_play_tricks"]
    )
    assert chosen["error_code"] in {"OK", "F_CONTINUATION_REGRET"}

    optimal_card = chosen["optimal_cards"][0]
    optimal = evaluate_continuation(task, {"card": optimal_card, "locked": True})
    assert optimal["legal_or_equivalent"] is True, optimal
    assert optimal["dd_regret"] == 0, optimal
    assert optimal["error_code"] == "OK"
    assert optimal["position_id"] == chosen["position_id"] == task["position_id"]

    illegal = evaluate_continuation(task, {"card": "S3", "locked": True})
    assert illegal["legal_or_equivalent"] is False, illegal
    assert illegal["error_code"] == "CONTINUATION_ILLEGAL_CARD"
    assert illegal["dd_regret"] is None

    actor_mismatch = dict(task)
    actor_mismatch["actor"] = "declarer"
    try:
        evaluate_continuation(actor_mismatch, {"card": "S2", "locked": True})
    except ValueError as exc:
        assert "differs from reconstructed actor" in str(exc)
    else:
        raise AssertionError("Actor mismatch was accepted")

    seat_mismatch = dict(task)
    seat_mismatch["next_seat"] = 0
    try:
        evaluate_continuation(seat_mismatch, {"card": "S2", "locked": True})
    except ValueError as exc:
        assert "next_seat differs" in str(exc)
    else:
        raise AssertionError("next_seat mismatch was accepted")

    print(
        json.dumps(
            {
                "ok": True,
                "position_id": task["position_id"],
                "actor": task["actor"],
                "chosen_card": chosen["chosen_card"],
                "chosen_regret": chosen["dd_regret"],
                "optimal_card": optimal_card,
                "optimal_regret": optimal["dd_regret"],
                "illegal_card_blocked": True,
                "actor_and_seat_provenance_checked": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
