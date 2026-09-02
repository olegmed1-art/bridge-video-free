from __future__ import annotations

import json

from continuation_tasks import continuation_tasks_from_line


DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def main() -> None:
    source = {
        "task_id": "LINE-SOURCE",
        "deal_id": "LINE-DEAL",
        "root_deal_id": "LINE-DEAL",
        "split": "train",
        "task_type": "contract_line",
        "deal": DEAL,
        "declarer": 2,
        "strain": 4,
        "leader": 3,
    }
    line = ["SA", "SQ", "S8", "SK", "S2", "SJ", "S7", "S5"]
    tasks = continuation_tasks_from_line(
        source,
        line,
        prefix_indexes=[1, 4, 5, 8],
        provenance="predicted_line",
    )
    assert tasks
    assert any(x["actor"] == "declarer" for x in tasks)
    assert any(x["actor"] == "defense" for x in tasks)
    assert all(x["blind"] is True for x in tasks)
    assert all(x["evidence_type"] == "reinforcement" for x in tasks)
    assert len({x["position_id"] for x in tasks}) == len(tasks)

    real = continuation_tasks_from_line(
        source,
        line,
        prefix_indexes=[1],
        provenance="real_play",
    )
    assert real[0]["evidence_type"] == "transfer"

    print(json.dumps({
        "ok": True,
        "tasks": len(tasks),
        "declarer_tasks": sum(x["actor"] == "declarer" for x in tasks),
        "defense_tasks": sum(x["actor"] == "defense" for x in tasks),
        "real_play_transfer_marking": True,
    }, indent=2))


if __name__ == "__main__":
    main()
