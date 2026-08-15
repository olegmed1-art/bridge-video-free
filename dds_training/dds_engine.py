from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass

RANK_CHARS = {14: "A", 13: "K", 12: "Q", 11: "J", 10: "T", 9: "9", 8: "8", 7: "7", 6: "6", 5: "5", 4: "4", 3: "3", 2: "2"}
SUIT_CHARS = {0: "S", 1: "H", 2: "D", 3: "C"}


def _dds3():
    import dds3
    return dds3


def engine_info() -> dict:
    dds3 = _dds3()
    try:
        endplay_version = importlib.metadata.version("endplay")
    except importlib.metadata.PackageNotFoundError:
        endplay_version = None
    return {
        "dds3_module": getattr(dds3, "__file__", None),
        "solver_context": hasattr(dds3, "SolverContext"),
        "endplay_version": endplay_version,
    }


def contract_tricks(deal_pbn: str, strain: int, declarer: int) -> int:
    """Return DD tricks for declarer (N=0,E=1,S=2,W=3) in strain S,H,D,C,NT=0..4."""
    dds3 = _dds3()
    result = dds3.calc_all_tables_pbn([deal_pbn], mode=-1, trump_filter=(0, 0, 0, 0, 0))
    return int(result["tables"][0]["res_table"][strain][declarer])


def contract_tricks_batch(deals: list[str]) -> list[list[list[int]]]:
    dds3 = _dds3()
    result = dds3.calc_all_tables_pbn(deals, mode=-1, trump_filter=(0, 0, 0, 0, 0))
    return [t["res_table"] for t in result["tables"]]


def _expand_equals(suit: int, rank: int, equals_mask: int) -> list[str]:
    cards = [f"{SUIT_CHARS[suit]}{RANK_CHARS[rank]}"]
    for r in range(2, 15):
        if r != rank and equals_mask & (1 << r):
            cards.append(f"{SUIT_CHARS[suit]}{RANK_CHARS[r]}")
    return cards


def opening_lead_scores(deal_pbn: str, strain: int, declarer: int) -> dict[str, int]:
    """Score every DDS-returned legal opening lead from defender perspective.

    Raw DDS FutureTricks scores are tricks available to the side that is on lead.
    With first=LHO of declarer, higher is better for the defense.
    Equivalent ranks reported through the DDS `equals` mask receive the same score.
    """
    dds3 = _dds3()
    first = (declarer + 1) % 4
    context = dds3.SolverContext() if hasattr(dds3, "SolverContext") else None
    kwargs = dict(
        remain_cards=deal_pbn,
        trump=strain,
        first=first,
        current_trick_suit=(0, 0, 0),
        current_trick_rank=(0, 0, 0),
        target=-1,
        solutions=3,
        mode=0,
        thread_index=0,
    )
    if context is not None:
        kwargs["context"] = context
    result = dds3.solve_board_pbn(**kwargs)
    n = int(result["cards"])
    out: dict[str, int] = {}
    for i in range(n):
        suit = int(result["suit"][i])
        rank = int(result["rank"][i])
        score = int(result["score"][i])
        eq = int(result["equals"][i])
        for card in _expand_equals(suit, rank, eq):
            out[card] = score
    if not out:
        raise RuntimeError("DDS returned no opening-lead candidates")
    return out


def evaluate_opening_lead(deal_pbn: str, strain: int, declarer: int, chosen_card: str) -> dict:
    scores = opening_lead_scores(deal_pbn, strain, declarer)
    best = max(scores.values())
    optimal = sorted(card for card, score in scores.items() if score == best)
    chosen = scores.get(chosen_card.upper())
    return {
        "scores": scores,
        "best_defense_tricks": best,
        "optimal_cards": optimal,
        "chosen_card": chosen_card.upper(),
        "chosen_defense_tricks": chosen,
        "legal_or_equivalent": chosen is not None,
        "dd_regret": None if chosen is None else best - chosen,
    }
