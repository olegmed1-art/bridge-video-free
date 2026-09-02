from __future__ import annotations

"""DDS-regret evaluation for a chosen card in a reconstructed mid-play position."""

from dds_play import _candidate_scores, _dds3
from playline import PlayLineError, normalize_card, replay_line


def evaluate_continuation(task: dict, prediction: dict, *, thread_index: int = 0) -> dict:
    prefix = task.get("play_prefix") or []
    if not isinstance(prefix, list):
        raise ValueError("continuation task play_prefix must be a list")
    chosen = prediction.get("card")
    if chosen is None:
        raise ValueError("continuation prediction requires card")
    chosen_card = normalize_card(str(chosen)).token

    replay = replay_line(
        deal=task["deal"],
        declarer=int(task["declarer"]),
        trump=int(task["strain"]),
        opening_leader=int(task.get("leader", (int(task["declarer"]) + 1) % 4)),
        cards=list(prefix),
    )
    snapshot = replay["snapshots"][-1]
    expected_actor = str(task.get("actor", snapshot["next_actor"]))
    if snapshot["next_actor"] != expected_actor:
        raise ValueError(
            f"Task actor {expected_actor} differs from reconstructed actor {snapshot['next_actor']}"
        )
    if int(task.get("next_seat", snapshot["next_seat"])) != int(snapshot["next_seat"]):
        raise ValueError("Task next_seat differs from reconstructed position")

    # Validate the chosen card against ownership and follow-suit by extending the
    # public prefix one card. No DDS answer is needed for this legality check.
    try:
        replay_line(
            deal=task["deal"],
            declarer=int(task["declarer"]),
            trump=int(task["strain"]),
            opening_leader=int(task.get("leader", (int(task["declarer"]) + 1) % 4)),
            cards=[*prefix, chosen_card],
        )
    except PlayLineError as exc:
        return {
            "chosen_card": chosen_card,
            "legal_or_equivalent": False,
            "dd_regret": None,
            "error_code": "CONTINUATION_ILLEGAL_CARD",
            "investigation_required": False,
            "legality_error": str(exc),
            "position_id": snapshot["position_id"],
            "actor": expected_actor,
        }

    dds = _dds3()
    context = dds.SolverContext() if hasattr(dds, "SolverContext") else None
    candidates = _candidate_scores(
        dds=dds,
        snapshot=snapshot,
        trump=int(task["strain"]),
        thread_index=int(thread_index),
        context=context,
    )
    chosen_score = candidates["scores"].get(chosen_card)
    if chosen_score is None:
        return {
            "chosen_card": chosen_card,
            "legal_or_equivalent": False,
            "dd_regret": None,
            "error_code": "CONTINUATION_UNREPRESENTED_CARD",
            "investigation_required": False,
            "position_id": snapshot["position_id"],
            "actor": expected_actor,
            **candidates,
        }
    regret = int(candidates["best_side_to_play_tricks"]) - int(chosen_score)
    prefix_key = "D" if expected_actor == "declarer" else "F"
    return {
        "chosen_card": chosen_card,
        "chosen_side_to_play_tricks": int(chosen_score),
        "legal_or_equivalent": True,
        "dd_regret": regret,
        "error_code": "OK" if regret == 0 else f"{prefix_key}_CONTINUATION_REGRET",
        "investigation_required": False,
        "position_id": snapshot["position_id"],
        "actor": expected_actor,
        "prefix_cards": len(prefix),
        **candidates,
    }
