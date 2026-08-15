from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from config import STRAINS

RANK_ORDER = "AKQJT98765432"
SEATS = "NESW"


def _parse_deal(pbn: str) -> dict[int, list[str]]:
    pbn = pbn.strip()
    if len(pbn) < 3 or pbn[1] != ":":
        raise ValueError(f"Expected seat-prefixed PBN deal: {pbn!r}")
    start = SEATS.index(pbn[0].upper())
    hands = pbn[2:].split()
    if len(hands) != 4:
        raise ValueError("Variant generation requires all four hands")
    out: dict[int, list[str]] = {}
    for offset, hand in enumerate(hands):
        suits = hand.split(".")
        if len(suits) != 4:
            raise ValueError(f"Bad PBN hand: {hand!r}")
        out[(start + offset) % 4] = suits
    return out


def _render_deal(hands: dict[int, list[str]]) -> str:
    return "N:" + " ".join(".".join(hands[i]) for i in range(4))


def _sort_cards(cards: Iterable[str]) -> str:
    order = {r: i for i, r in enumerate(RANK_ORDER)}
    return "".join(sorted(cards, key=lambda r: order[r]))


def rotate_task(task: dict, steps: int = 2) -> dict:
    """Rotate all four seats while preserving the mathematical bridge position."""
    steps %= 4
    hands = _parse_deal(task["deal"])
    rotated = {(seat + steps) % 4: suits[:] for seat, suits in hands.items()}
    out = copy.deepcopy(task)
    out["task_id"] = f"{task['task_id']}-ROT{steps}"
    out["deal_id"] = f"{task['deal_id']}-ROT{steps}"
    out["deal"] = _render_deal(rotated)
    out["declarer"] = (int(task["declarer"]) + steps) % 4
    if "leader" in task:
        out["leader"] = (int(task["leader"]) + steps) % 4
    out["split"] = "derived"
    out["derived_from_task_id"] = task["task_id"]
    out["evidence_type"] = "symmetry"
    return out


def permute_suits_task(task: dict, mapping: tuple[int, int, int, int] = (1, 0, 3, 2)) -> dict:
    """Rename suits consistently; mapping[old_suit] gives new_suit.

    The default swaps spades/hearts and diamonds/clubs. NT stays NT.
    """
    if sorted(mapping) != [0, 1, 2, 3]:
        raise ValueError("mapping must be a permutation of 0..3")
    hands = _parse_deal(task["deal"])
    transformed: dict[int, list[str]] = {}
    for seat, suits in hands.items():
        new = ["", "", "", ""]
        for old_suit, cards in enumerate(suits):
            new[mapping[old_suit]] = cards
        transformed[seat] = new
    out = copy.deepcopy(task)
    tag = "".join(str(x) for x in mapping)
    out["task_id"] = f"{task['task_id']}-SUIT{tag}"
    out["deal_id"] = f"{task['deal_id']}-SUIT{tag}"
    out["deal"] = _render_deal(transformed)
    old_strain = int(task["strain"])
    out["strain"] = 4 if old_strain == 4 else mapping[old_strain]
    out["strain_name"] = STRAINS[out["strain"]]
    out["split"] = "derived"
    out["derived_from_task_id"] = task["task_id"]
    out["evidence_type"] = "symmetry"
    return out


def perturb_task(task: dict, salt: str = "p1") -> dict:
    """Create a minimal legal perturbation by swapping two ranks in one suit.

    A single physical card cannot move between hands while preserving 13 cards in
    every hand, so the minimal complete-deal perturbation is a two-card swap. Suit
    lengths remain unchanged; only two rank locations change.
    """
    hands = _parse_deal(task["deal"])
    candidates: list[tuple[int, int, int, str, str]] = []
    for suit in range(4):
        for a in range(4):
            for b in range(a + 1, 4):
                for ra in hands[a][suit]:
                    for rb in hands[b][suit]:
                        if ra != rb:
                            candidates.append((suit, a, b, ra, rb))
    if not candidates:
        raise ValueError("No legal rank-swap perturbation found")
    digest = hashlib.sha256(f"{task['deal_id']}:{salt}".encode()).digest()
    pick = candidates[int.from_bytes(digest[:8], "big") % len(candidates)]
    suit, a, b, ra, rb = pick
    new_hands = {seat: suits[:] for seat, suits in hands.items()}
    ca = list(new_hands[a][suit])
    cb = list(new_hands[b][suit])
    ca[ca.index(ra)] = rb
    cb[cb.index(rb)] = ra
    new_hands[a][suit] = _sort_cards(ca)
    new_hands[b][suit] = _sort_cards(cb)

    out = copy.deepcopy(task)
    out["task_id"] = f"{task['task_id']}-PERT-{salt}"
    out["deal_id"] = f"{task['deal_id']}-PERT-{salt}"
    out["deal"] = _render_deal(new_hands)
    out["split"] = "derived"
    out["derived_from_task_id"] = task["task_id"]
    out["evidence_type"] = "perturbation"
    out["perturbation"] = {
        "suit": STRAINS[suit],
        "seat_a": SEATS[a],
        "seat_b": SEATS[b],
        "rank_a_to_b": ra,
        "rank_b_to_a": rb,
    }
    return out


def create_variants(task: dict) -> list[dict]:
    """Standard discrimination set: seat rotation, suit rename and perturbation."""
    return [
        rotate_task(task, 1),
        rotate_task(task, 2),
        permute_suits_task(task),
        perturb_task(task, "p1"),
        perturb_task(task, "p2"),
    ]


def create_error_followups(
    base_tasks_path: Path,
    con: sqlite3.Connection,
    out_path: Path,
    max_sources: int = 500,
) -> dict:
    """Create blind discrimination tasks from the strongest recorded errors.

    No DDS answer is copied into the derived tasks. They must receive fresh blind
    predictions before evaluation, so transfer cannot be faked by answer leakage.
    """
    base = {}
    for line in base_tasks_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            task = json.loads(line)
            base[task["task_id"]] = task

    source_rows = con.execute(
        """
        SELECT e.task_id, MAX(COALESCE(e.magnitude,0)) AS magnitude,
               MAX(CASE WHEN se.confidence='high' AND se.outcome!='success' THEN 1 ELSE 0 END) AS high_conf
        FROM error_events e
        LEFT JOIN skill_evidence se ON se.task_id=e.task_id
        GROUP BY e.task_id
        ORDER BY high_conf DESC, magnitude DESC, e.task_id
        LIMIT ?
        """,
        (max_sources,),
    ).fetchall()

    variants: list[dict] = []
    skipped = 0
    for source_id, magnitude, high_conf in source_rows:
        task = base.get(source_id)
        if task is None:
            skipped += 1
            continue
        for v in create_variants(task):
            v["source_error_magnitude"] = float(magnitude or 0)
            v["source_high_confidence_error"] = bool(high_conf)
            v["blind"] = True
            variants.append(v)

    # Deterministic de-duplication keeps the file reproducible across reruns.
    by_id = {v["task_id"]: v for v in variants}
    ordered = [by_id[k] for k in sorted(by_id)]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in ordered:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "source_errors": len(source_rows),
        "source_errors_skipped": skipped,
        "derived_tasks": len(ordered),
        "path": str(out_path),
        "dds_called": False,
    }
