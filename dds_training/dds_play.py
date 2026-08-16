from __future__ import annotations

"""DDS-backed value trajectory for a legal proposed card line."""

import json
from typing import Iterable

from experience_events import summarize_value_trajectory
from playline import replay_line


def _dds3():
    import dds3
    return dds3


def _play_string(cards: Iterable[str]) -> str:
    return "".join(str(card).strip().upper().replace("10", "T") for card in cards)


def analyse_line(
    *,
    deal: str,
    declarer: int,
    trump: int,
    cards: list[str],
    opening_leader: int | None = None,
    thread_index: int = 0,
) -> dict:
    """Validate a full/partial line and calculate projected declarer value after every card.

    DDS `analyse_play_pbn` reports tricks for the side to play after each prefix.
    This function converts every prefix to one constant scale: projected final
    tricks for declarer's partnership.  That makes declarer/defense swings
    directly comparable and compatible with the first-error accounting module.
    """
    first = (int(declarer) + 1) % 4 if opening_leader is None else int(opening_leader)
    legal = replay_line(
        deal=deal,
        declarer=int(declarer),
        trump=int(trump),
        cards=cards,
        opening_leader=first,
    )
    dds = _dds3()
    raw = dds.analyse_play_pbn(
        deal,
        play=_play_string(cards),
        trump=int(trump),
        first=first,
        thread_index=int(thread_index),
    )
    raw_tricks = [int(x) for x in raw["tricks"]]
    snapshots = legal["snapshots"]
    if len(raw_tricks) != len(snapshots):
        raise RuntimeError(
            f"DDS trajectory length {len(raw_tricks)} does not match legal snapshots {len(snapshots)}"
        )

    projected = []
    normalized = []
    for index, (score, snapshot) in enumerate(zip(raw_tricks, snapshots)):
        remaining_tricks = 13 - int(snapshot["completed_tricks"])
        if score < 0 or score > remaining_tricks:
            raise RuntimeError(
                f"DDS score {score} outside 0..{remaining_tricks} at prefix {index}"
            )
        if snapshot["next_actor"] == "declarer":
            value = int(snapshot["declarer_tricks"]) + score
        else:
            value = int(snapshot["declarer_tricks"]) + (remaining_tricks - score)
        if value < 0 or value > 13:
            raise RuntimeError(f"Normalized declarer value {value} outside 0..13")
        projected.append(value)
        normalized.append({
            "prefix_cards": index,
            "dds_side_to_play_tricks": score,
            "side_to_play": snapshot["next_actor"],
            "completed_declarer_tricks": snapshot["declarer_tricks"],
            "remaining_tricks": remaining_tricks,
            "projected_final_declarer_tricks": value,
            "position_id": snapshot["position_id"],
        })

    actors = [str(item["actor"]) for item in legal["played"]]
    summary = summarize_value_trajectory(projected, actors)
    summary["line_sha256"] = legal["line_sha256"]
    summary["cards_played"] = legal["cards_played"]
    summary["completed_tricks_in_line"] = legal["completed_tricks"]
    return {
        "legal_play": legal,
        "dds_raw": {"number": int(raw["number"]), "tricks": raw_tricks},
        "normalized_positions": normalized,
        "projected_declarer_values": projected,
        "actors": actors,
        "trajectory": summary,
    }


def first_refutation(analysis: dict, claimed_tricks: int) -> dict:
    """Explain the earliest point where a claimed result is no longer attainable."""
    values = [int(x) for x in analysis["projected_declarer_values"]]
    claim = int(claimed_tricks)
    if not values:
        raise ValueError("analysis has no values")
    if values[0] < claim:
        return {
            "refuted": True,
            "prefix_cards": 0,
            "reason": "The claim already exceeds the DDS ceiling in the initial position.",
            "claimed_tricks": claim,
            "dds_ceiling": values[0],
        }
    for index, value in enumerate(values[1:], 1):
        if value < claim:
            played = analysis["legal_play"]["played"][index - 1]
            return {
                "refuted": True,
                "prefix_cards": index,
                "first_refuting_card": played["card"],
                "actor": played["actor"],
                "seat": played["seat_name"],
                "claimed_tricks": claim,
                "dds_ceiling_after_card": value,
            }
    return {
        "refuted": False,
        "claimed_tricks": claim,
        "minimum_dds_ceiling_on_line": min(values),
    }


def main_example() -> None:
    deal = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"
    result = analyse_line(
        deal=deal,
        declarer=2,
        trump=4,
        opening_leader=3,
        cards=["SA", "SQ", "S8", "SK"],
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main_example()
