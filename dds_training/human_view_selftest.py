from __future__ import annotations

import json

from human_view import build_human_view


DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def main() -> None:
    task = {
        "task_id": "HUMAN-VIEW",
        "deal_id": "HUMAN-DEAL",
        "deal": DEAL,
        "declarer": 2,
        "strain": 4,
        "leader": 3,
        "auction": ["1NT", "Pass", "3NT", "Pass", "Pass", "Pass"],
    }
    before = build_human_view(task, perspective=3, play_prefix=[])
    assert set(before["visible_hands"]) == {"W"}
    assert before["dummy_exposed"] is False
    assert "N" in before["hidden_seats"]

    after = build_human_view(task, perspective=0, play_prefix=["SA"])
    assert after["dummy_exposed"] is True
    assert set(after["visible_hands"]) == {"N"}
    # Perspective is North and dummy is North in this contract, so no other
    # exact hand becomes visible. Hidden East/South/West remain absent.
    assert set(after["hidden_seats"]) == {"E", "S", "W"}
    serialized = json.dumps(after)
    assert "AT942.AQ4.32.KJ3" not in serialized  # West hidden
    assert "K5.T83.KQ9.A7652" not in serialized  # South hidden

    defender = build_human_view(task, perspective=1, play_prefix=["SA"])
    assert set(defender["visible_hands"]) == {"N", "E"}
    assert set(defender["hidden_seats"]) == {"S", "W"}

    print(json.dumps({
        "ok": True,
        "dummy_hidden_before_lead": True,
        "dummy_visible_after_lead": True,
        "hidden_hands_not_serialized": True,
    }, indent=2))


if __name__ == "__main__":
    main()
