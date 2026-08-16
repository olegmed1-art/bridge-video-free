from __future__ import annotations

"""DDS-backed value trajectory for a legal proposed card line.

DDS3 v3.0.0 documents AnalysePlay in the native library, but its released
Python wheel exposes `solve_board_pbn` rather than `analyse_play_pbn`.  We
therefore reconstruct every legal prefix and solve that exact position.  This
is also the consistency method used by the upstream DDS regression tests.
"""

import json

from experience_events import summarize_value_trajectory
from playline import RANK_VALUE, SUITS, replay_line


def _dds3():
    import dds3
    return dds3


def _current_trick_arrays(snapshot: dict) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    trick = list(snapshot.get("current_trick", []))
    if len(trick) > 3:
        raise RuntimeError(f"DDS current trick has {len(trick)} cards, maximum is 3")
    suits = []
    ranks = []
    for item in trick:
        token = str(item["card"]).upper().replace("10", "T")
        if len(token) != 2 or token[0] not in SUITS or token[1] not in RANK_VALUE:
            raise RuntimeError(f"Invalid card in reconstructed DDS trick: {token!r}")
        suits.append(SUITS.index(token[0]))
        ranks.append(int(RANK_VALUE[token[1]]))
    while len(suits) < 3:
        suits.append(0)
        ranks.append(0)
    return tuple(suits), tuple(ranks)


def _solve_prefixes(
    *,
    dds,
    snapshots: list[dict],
    trump: int,
    thread_index: int,
    reuse_context: bool,
) -> tuple[list[int], dict]:
    context = None
    if reuse_context and hasattr(dds, "SolverContext"):
        context = dds.SolverContext()

    scores: list[int] = []
    nodes = 0
    for index, snapshot in enumerate(snapshots):
        remaining_cards = sum(int(x) for x in snapshot["remaining_card_counts"].values())
        if remaining_cards == 0:
            scores.append(0)
            continue

        current_suits, current_ranks = _current_trick_arrays(snapshot)
        kwargs = {
            "remain_cards": snapshot["remaining_deal"],
            "trump": int(trump),
            # DDS `first` is the leader of the current trick, not necessarily
            # the next player when one to three cards have already been played.
            "first": int(snapshot["trick_leader"]),
            "current_trick_suit": current_suits,
            "current_trick_rank": current_ranks,
            "target": -1,
            "solutions": 1,
            # Upstream AnalysePlay consistency tests use mode=1 for an exact
            # maximum score at every reconstructed prefix.
            "mode": 1,
            "thread_index": int(thread_index),
        }
        if context is not None:
            kwargs["context"] = context
        result = dds.solve_board_pbn(**kwargs)
        if int(result.get("cards", 0)) < 1 or not result.get("score"):
            raise RuntimeError(f"DDS returned no score at play prefix {index}: {result}")
        score = int(result["score"][0])
        scores.append(score)
        nodes += int(result.get("nodes", 0))

    return scores, {
        "method": "repeated_solve_board_pbn",
        "prefixes_solved": len(scores),
        "context_reused": context is not None,
        "nodes": nodes,
        "python_analyse_play_available": hasattr(dds, "analyse_play_pbn"),
    }


def analyse_line(
    *,
    deal: str,
    declarer: int,
    trump: int,
    cards: list[str],
    opening_leader: int | None = None,
    thread_index: int = 0,
    reuse_context: bool = True,
) -> dict:
    """Validate a line and calculate projected declarer value after every card.

    Each prefix is solved independently on the DDS side-to-play scale and then
    converted to one constant scale: projected final tricks for declarer's
    partnership.  This makes declarer losses and defensive gifts comparable.
    """
    first = (int(declarer) + 1) % 4 if opening_leader is None else int(opening_leader)
    legal = replay_line(
        deal=deal,
        declarer=int(declarer),
        trump=int(trump),
        cards=cards,
        opening_leader=first,
    )
    snapshots = legal["snapshots"]
    dds = _dds3()
    raw_tricks, solver_info = _solve_prefixes(
        dds=dds,
        snapshots=snapshots,
        trump=int(trump),
        thread_index=int(thread_index),
        reuse_context=bool(reuse_context),
    )
    if len(raw_tricks) != len(snapshots):
        raise RuntimeError(
            f"DDS trajectory length {len(raw_tricks)} does not match legal snapshots {len(snapshots)}"
        )

    projected = []
    normalized = []
    for index, (score, snapshot) in enumerate(zip(raw_tricks, snapshots)):
        # An incomplete current trick is part of the remaining trick count, as
        # in the upstream SolveBoard/AnalysePlay consistency calculation.
        remaining_tricks = 13 - int(snapshot["completed_tricks"])
        if score < 0 or score > remaining_tricks:
            raise RuntimeError(
                f"DDS score {score} outside 0..{remaining_tricks} at prefix {index}"
            )
        if snapshot["next_actor"] == "declarer":
            declarer_remaining = score
        else:
            declarer_remaining = remaining_tricks - score
        value = int(snapshot["declarer_tricks"]) + declarer_remaining
        if value < 0 or value > 13:
            raise RuntimeError(f"Normalized declarer value {value} outside 0..13")
        projected.append(value)
        normalized.append({
            "prefix_cards": index,
            "dds_side_to_play_tricks": score,
            "side_to_play": snapshot["next_actor"],
            "next_seat": snapshot["next_seat"],
            "trick_leader": snapshot["trick_leader"],
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
        "dds_raw": {"number": len(raw_tricks), "tricks": raw_tricks, **solver_info},
        "normalized_positions": normalized,
        "projected_declarer_values": projected,
        "actors": actors,
        "trajectory": summary,
    }


def first_refutation(analysis: dict, claimed_tricks: int) -> dict:
    """Explain the earliest prefix where a claimed result is no longer attainable."""
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
