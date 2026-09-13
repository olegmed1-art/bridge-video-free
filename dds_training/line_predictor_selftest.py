from __future__ import annotations

import json

from line_predictor import generate_line, prediction_for
from playline import replay_line


DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def main() -> None:
    task = {
        "task_id": "LP-CT",
        "deal_id": "LP-DEAL",
        "task_type": "contract_tricks",
        "split": "train",
        "deal": DEAL,
        "declarer": 2,
        "strain": 4,
        "leader": 3,
    }
    first = generate_line(task, cards_to_play=16)
    second = generate_line(task, cards_to_play=16)
    assert first == second
    assert len(first) == 16
    legal = replay_line(deal=DEAL, declarer=2, trump=4, opening_leader=3, cards=first)
    assert legal["legal"] is True and legal["cards_played"] == 16
    prediction = prediction_for(task, cards_to_play=16, predictor_version="selftest")
    assert prediction["locked"] is True
    assert prediction["dds_called"] is False
    assert prediction["line"] == first

    print(json.dumps({
        "ok": True,
        "line_cards": len(first),
        "deterministic": True,
        "legal": True,
        "dds_called": False,
    }, indent=2))


if __name__ == "__main__":
    main()
