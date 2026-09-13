from __future__ import annotations

"""DDS-backed value trajectory for a legal proposed card line.

DDS3 v3.0.0 documents AnalysePlay in the native library, but its released
Python wheel exposes `solve_board_pbn` rather than `analyse_play_pbn`. We
therefore reconstruct every legal prefix and solve that exact position. This is
also the consistency method used by the upstream DDS regression tests.

Only value-changing mistakes receive the more expensive all-candidate query.
That preserves scalability while still identifying the exact proposed card,
equal-optimal alternatives and DD-regret at every real swing.
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


def _solve_kwargs(snapshot: dict, *, trump: int, thread_index: int, solutions: int, context) -> dict:
    current_suits, current_ranks = _current_trick_arrays(snapshot)
    kwargs = {
        "remain_cards": snapshot["remaining_deal"],
        "trump": int(trump),
        # DDS `first` is the leader of the current trick, not necessarily the
        # next player when one to three cards have already been played.
        "first": int(snapshot["trick_leader"]),
        "current_trick_suit": current_suits,
        "current_trick_rank": current_ranks,
        "target": -1,
        "solutions": int(solutions),
        # Upstream AnalysePlay consistency tests use mode=1 for an exact
        # maximum score at every reconstructed prefix.
        "mode": 1,
        "thread_index": int(thread_index),
    }
    if context is not None:
        kwargs["context"] = context
    return kwargs


def _solve_prefixes(
    *,
    dds,
    snapshots: list[dict],
    trump: int,
    thread_index: int,
    reuse_context: bool,
) -> tuple[list[int], dict, object | None]:
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
        result = dds.solve_board_pbn(
            **_solve_kwargs(
                snapshot,
                trump=trump,
                thread_index=thread_index,
                solutions=1,
                context=context,
            )
        )
        if int(result.get("cards", 0)) < 1 or not result.get("score"):
            raise RuntimeError(f"DDS returned no score at play prefix {index}: {result}")
        scores.append(int(result["score"][0]))
        nodes += int(result.get("nodes", 0))

    return scores, {
        "method": "repeated_solve_board_pbn",
        "prefixes_solved": len(scores),
        "context_reused": context is not None,
        "nodes": nodes,
        "python_analyse_play_available": hasattr(dds, "analyse_play_pbn"),
    }, context


def _rank_token(rank: int) -> str:
    for token, value in RANK_VALUE.items():
        if int(value) == int(rank):
            return token
    raise RuntimeError(f"DDS returned invalid rank {rank}")


def _expand_equal_cards(suit: int, rank: int, equals_mask: int) -> list[str]:
    cards = [f"{SUITS[int(suit)]}{_rank_token(int(rank))}"]
    for candidate_rank in range(2, 15):
        if candidate_rank != int(rank) and int(equals_mask) & (1 << candidate_rank):
            cards.append(f"{SUITS[int(suit)]}{_rank_token(candidate_rank)}")
    return cards


def _candidate_scores(
    *,
    dds,
    snapshot: dict,
    trump: int,
    thread_index: int,
    context,
) -> dict:
    result = dds.solve_board_pbn(
        **_solve_kwargs(
            snapshot,
            trump=trump,
            thread_index=thread_index,
            solutions=3,
            context=context,
        )
    )
    count = int(result.get("cards", 0))
    mapping: dict[str, int] = {}
    for index in range(count):
        suit = int(result["suit"][index])
        rank = int(result["rank"][index])
        score = int(result["score"][index])
        equals = int(result["equals"][index])
        for card in _expand_equal_cards(suit, rank, equals):
            previous = mapping.get(card)
            if previous is not None and previous != score:
                raise RuntimeError(f"DDS gave conflicting scores for equivalent card {card}")
            mapping[card] = score
    if not mapping:
        raise RuntimeError(f"DDS returned no candidate cards for position {snapshot['position_id']}")
    best = max(mapping.values())
    return {
        "scores": dict(sorted(mapping.items())),
        "best_side_to_play_tricks": best,
        "optimal_cards": sorted(card for card, score in mapping.items() if score == best),
        "nodes": int(result.get("nodes", 0)),
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

    Each prefix is solved on the DDS side-to-play scale and converted to one
    constant scale: projected final tricks for declarer's partnership. For each
    value-changing error, all legal DDS candidates are then compared.
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
    raw_tricks, solver_info, context = _solve_prefixes(
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

    decision_errors = []
    all_candidate_nodes = 0
    for decision_index, actor in enumerate(actors):
        before = int(projected[decision_index])
        after = int(projected[decision_index + 1])
        magnitude = (before - after) if actor == "declarer" else (after - before)
        if magnitude <= 0:
            continue
        candidates = _candidate_scores(
            dds=dds,
            snapshot=snapshots[decision_index],
            trump=int(trump),
            thread_index=int(thread_index),
            context=context,
        )
        all_candidate_nodes += int(candidates["nodes"])
        chosen = str(legal["played"][decision_index]["card"]).upper()
        chosen_score = candidates["scores"].get(chosen)
        if chosen_score is None:
            raise RuntimeError(
                f"Chosen legal card {chosen} was absent from DDS candidate list at decision {decision_index}"
            )
        regret = int(candidates["best_side_to_play_tricks"]) - int(chosen_score)
        decision_errors.append({
            "decision_index": decision_index,
            "prefix_cards_before_decision": decision_index,
            "actor": actor,
            "seat": legal["played"][decision_index]["seat_name"],
            "chosen_card": chosen,
            "chosen_side_to_play_tricks": int(chosen_score),
            "best_side_to_play_tricks": int(candidates["best_side_to_play_tricks"]),
            "dd_regret": regret,
            "value_swing_magnitude": magnitude,
            "regret_matches_value_swing": regret == magnitude,
            "optimal_cards": candidates["optimal_cards"],
            "candidate_scores": candidates["scores"],
            "position_id": snapshots[decision_index]["position_id"],
        })
    solver_info["all_candidate_error_positions"] = len(decision_errors)
    solver_info["all_candidate_nodes"] = all_candidate_nodes

    first_declarer_error = next((x for x in decision_errors if x["actor"] == "declarer"), None)
    first_defense_error = next((x for x in decision_errors if x["actor"] == "defense"), None)
    return {
        "legal_play": legal,
        "dds_raw": {"number": len(raw_tricks), "tricks": raw_tricks, **solver_info},
        "normalized_positions": normalized,
        "projected_declarer_values": projected,
        "actors": actors,
        "trajectory": summary,
        "decision_errors": decision_errors,
        "first_declarer_error": first_declarer_error,
        "first_defense_error": first_defense_error,
    }


def first_refutation(analysis: dict, claimed_tricks: int) -> dict:
    """Explain the earliest point where a claimed result is no longer attainable."""
    values = [int(x) for x in analysis["projected_declarer_values"]]
    claim = int(claimed_tricks)
    if not values:
        raise ValueError("analysis has no values")
    if values[0] < claim:
        result = {
            "refuted": True,
            "prefix_cards": 0,
            "reason": "The claim already exceeds the DDS ceiling in the initial position.",
            "claimed_tricks": claim,
            "dds_ceiling": values[0],
        }
        # The first suboptimal defensive card in the proposed line is the first
        # concrete opponent error on which that impossible result relies.
        if analysis.get("first_defense_error"):
            result["first_assumed_defense_error"] = analysis["first_defense_error"]
        else:
            result["line_limitation"] = (
                "The supplied partial line contains no defensive gift yet; extend it to localize the assumed opponent error."
            )
        return result
    for index, value in enumerate(values[1:], 1):
        if value < claim:
            played = analysis["legal_play"]["played"][index - 1]
            detail = next(
                (x for x in analysis.get("decision_errors", []) if x["decision_index"] == index - 1),
                None,
            )
            result = {
                "refuted": True,
                "prefix_cards": index,
                "first_refuting_card": played["card"],
                "actor": played["actor"],
                "seat": played["seat_name"],
                "claimed_tricks": claim,
                "dds_ceiling_after_card": value,
            }
            if detail:
                result["decision_detail"] = detail
            return result
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
