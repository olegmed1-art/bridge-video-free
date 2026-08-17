from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path

from launch_guard import enforce_mass_evaluate_guard

# Hard fallback: even if Python did not discover the project-level sitecustomize
# during interpreter startup, run_stage imports dds_engine before any DDS solve.
# Unauthorized mass evaluation therefore still exits before solver work begins.
enforce_mass_evaluate_guard()

RANK_CHARS = {
    14: "A",
    13: "K",
    12: "Q",
    11: "J",
    10: "T",
    9: "9",
    8: "8",
    7: "7",
    6: "6",
    5: "5",
    4: "4",
    3: "3",
    2: "2",
}
SUIT_CHARS = {0: "S", 1: "H", 2: "D", 3: "C"}
EXPECTED_DDS_SOURCE_COMMIT = "37c8a79f4c67c55d1a309ccb66dd00cb58af464a"


def _dds3():
    import dds3

    return dds3


def _wheel_provenance() -> dict:
    path = Path(__file__).resolve().with_name(".wheel-cache") / "dds3" / "build_provenance.json"
    result = {
        "dds_build_provenance": str(path),
        "dds_source_commit": None,
        "dds_wheel_sha256": None,
        "dds_provenance_verified": False,
    }
    if not path.is_file():
        return result
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        wheel_name = str(payload.get("wheel", "")).strip()
        if not wheel_name or Path(wheel_name).name != wheel_name:
            return result
        wheel = path.parent / wheel_name
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    except (OSError, json.JSONDecodeError):
        return result
    verified = (
        payload.get("schema") == "dds3-wheel-provenance-v1"
        and payload.get("dds_source_commit") == EXPECTED_DDS_SOURCE_COMMIT
        and payload.get("actual_source_commit") == EXPECTED_DDS_SOURCE_COMMIT
        and digest == payload.get("wheel_sha256")
    )
    result.update(
        {
            "dds_source_commit": payload.get("dds_source_commit"),
            "dds_wheel_sha256": payload.get("wheel_sha256"),
            "dds_provenance_verified": bool(verified),
        }
    )
    return result


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
        **_wheel_provenance(),
    }


def contract_tricks(deal_pbn: str, strain: int, declarer: int) -> int:
    """Return DD tricks for declarer (N=0,E=1,S=2,W=3) in strain S,H,D,C,NT=0..4."""

    dds3 = _dds3()
    result = dds3.calc_all_tables_pbn(
        [deal_pbn], mode=-1, trump_filter=(0, 0, 0, 0, 0)
    )
    return int(result["tables"][0]["res_table"][strain][declarer])


def contract_tricks_batch(deals: list[str]) -> list[list[list[int]]]:
    dds3 = _dds3()
    result = dds3.calc_all_tables_pbn(
        deals, mode=-1, trump_filter=(0, 0, 0, 0, 0)
    )
    return [table["res_table"] for table in result["tables"]]


def _expand_equals(suit: int, rank: int, equals_mask: int) -> list[str]:
    cards = [f"{SUIT_CHARS[suit]}{RANK_CHARS[rank]}"]
    for value in range(2, 15):
        if value != rank and equals_mask & (1 << value):
            cards.append(f"{SUIT_CHARS[suit]}{RANK_CHARS[value]}")
    return cards


def opening_lead_scores(deal_pbn: str, strain: int, declarer: int) -> dict[str, int]:
    """Score every DDS-returned legal opening lead from defender perspective.

    Raw DDS FutureTricks scores are tricks available to the side that is on
    lead. With first=LHO of declarer, higher is better for the defense.
    Equivalent ranks reported through the DDS ``equals`` mask receive the same
    score.
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
    count = int(result["cards"])
    out: dict[str, int] = {}
    for index in range(count):
        suit = int(result["suit"][index])
        rank = int(result["rank"][index])
        score = int(result["score"][index])
        equals = int(result["equals"][index])
        for card in _expand_equals(suit, rank, equals):
            out[card] = score
    if not out:
        raise RuntimeError("DDS returned no opening-lead candidates")
    return out


def evaluate_opening_lead(
    deal_pbn: str,
    strain: int,
    declarer: int,
    chosen_card: str,
) -> dict:
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
