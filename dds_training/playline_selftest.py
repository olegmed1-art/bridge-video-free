from __future__ import annotations

import json

from playline import PlayLineError, replay_line, validate_prediction_line


DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def main() -> None:
    task = {
        "task_id": "PLAY-SELFTEST",
        "deal_id": "PLAY-DEAL",
        "task_type": "contract_line",
        "deal": DEAL,
        "declarer": 2,
        "strain": 4,
        "leader": 3,
    }
    line = ["SA", "SQ", "S8", "SK", "S2", "SJ", "S7", "S5"]
    result = replay_line(deal=DEAL, declarer=2, trump=4, opening_leader=3, cards=line)
    assert result["legal"] is True
    assert result["cards_played"] == 8
    assert result["completed_tricks"] == 2
    assert result["declarer_tricks"] == 1
    assert result["defense_tricks"] == 1
    assert result["next_seat"] == 0  # North won trick two with SJ.
    assert len(result["snapshots"]) == 9
    assert len({x["position_id"] for x in result["snapshots"]}) == 9

    prediction = {"line": line, "locked": True}
    checked = validate_prediction_line(task, prediction, require_nonempty=True)
    assert checked["line_sha256"] == result["line_sha256"]

    try:
        replay_line(
            deal=DEAL,
            declarer=2,
            trump=4,
            opening_leader=3,
            cards=["SA", "HK"],  # North still holds spades and must follow suit.
        )
    except PlayLineError as exc:
        assert "Revoke" in str(exc)
    else:
        raise AssertionError("Revoke was accepted")

    try:
        validate_prediction_line(task, {"line": []}, require_nonempty=True)
    except PlayLineError:
        pass
    else:
        raise AssertionError("Empty mandatory line was accepted")

    print(json.dumps({
        "ok": True,
        "legal_line_cards": result["cards_played"],
        "completed_tricks": result["completed_tricks"],
        "position_snapshots": len(result["snapshots"]),
        "revoke_blocked": True,
        "empty_line_blocked": True,
    }, indent=2))


if __name__ == "__main__":
    main()
