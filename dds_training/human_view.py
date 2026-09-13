from __future__ import annotations

"""Construct the information legally available to one bridge player.

DDS training sees all four hands. Human evaluation must not. This module creates
an auditable single-dummy view from the same position without leaking hidden
cards into the decision prompt.
"""

import argparse
import json
from pathlib import Path

from playline import PlayLineError, SEATS, parse_deal, replay_line


def _hand_text(hand: list[set[str]]) -> str:
    ranks = "AKQJT98765432"
    return ".".join("".join(rank for rank in ranks if rank in hand[suit]) for suit in range(4))


def build_human_view(
    task: dict,
    *,
    perspective: int,
    play_prefix: list[str] | None = None,
    require_turn: bool = False,
) -> dict:
    perspective = int(perspective)
    if perspective not in range(4):
        raise ValueError("perspective must be 0..3")
    prefix = [] if play_prefix is None else list(play_prefix)
    declarer = int(task["declarer"])
    leader = int(task.get("leader", (declarer + 1) % 4))
    replay = replay_line(
        deal=task["deal"],
        declarer=declarer,
        trump=int(task["strain"]),
        opening_leader=leader,
        cards=prefix,
    )
    snapshot = replay["snapshots"][-1]
    if require_turn and int(snapshot["next_seat"]) != perspective:
        raise PlayLineError(
            f"Perspective {SEATS[perspective]} is not on turn; next is {snapshot['next_seat_name']}"
        )

    remaining = parse_deal(snapshot["remaining_deal"], require_full=False)
    dummy = (declarer + 2) % 4
    dummy_exposed = len(prefix) >= 1
    visible = {perspective}
    if dummy_exposed:
        visible.add(dummy)

    visible_hands = {
        SEATS[seat]: _hand_text(remaining[seat])
        for seat in sorted(visible)
    }
    hidden = [SEATS[seat] for seat in range(4) if seat not in visible]
    public_play = [
        {
            "card_index": item["card_index"],
            "seat": item["seat_name"],
            "card": item["card"],
            "trick_number": item["trick_number"],
        }
        for item in replay["played"]
    ]

    return {
        "schema": "bridge-human-information-view-v1",
        "information_mode": "single_dummy",
        "task_id": task.get("task_id"),
        "deal_id": task.get("deal_id"),
        "perspective": perspective,
        "perspective_name": SEATS[perspective],
        "declarer": declarer,
        "declarer_name": SEATS[declarer],
        "dummy": dummy,
        "dummy_name": SEATS[dummy],
        "dummy_exposed": dummy_exposed,
        "next_seat": snapshot["next_seat"],
        "next_seat_name": snapshot["next_seat_name"],
        "visible_hands": visible_hands,
        "hidden_seats": hidden,
        "auction": task.get("auction"),
        "contract": task.get("contract"),
        "vulnerability": task.get("vulnerability"),
        "dealer": task.get("dealer"),
        "public_play": public_play,
        "current_trick": snapshot["current_trick"],
        "completed_tricks": snapshot["completed_tricks"],
        "own_side_tricks": (
            snapshot["declarer_tricks"]
            if perspective in {declarer, dummy}
            else snapshot["defense_tricks"]
        ),
        "prohibited_hidden_information": {
            "full_deal": True,
            "opponent_exact_hands": True,
            "dds_answer": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a single-dummy player view from a bridge task")
    parser.add_argument("--task", required=True, help="JSON task file")
    parser.add_argument("--perspective", required=True, type=int)
    parser.add_argument("--play", help="JSON list of card tokens")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    task = json.loads(Path(args.task).read_text(encoding="utf-8"))
    play = [] if not args.play else json.loads(Path(args.play).read_text(encoding="utf-8"))
    view = build_human_view(task, perspective=args.perspective, play_prefix=play)
    Path(args.out).write_text(json.dumps(view, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(view, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
