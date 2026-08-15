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


def _root_split(task: dict) -> str:
    return str(task.get("source_root_split", task.get("split", "unknown")))


def _batch_tag(batch_id: str | None) -> str | None:
    if batch_id is None:
        return None
    return hashlib.sha256(str(batch_id).encode("utf-8")).hexdigest()[:10]


def _mark_derived(out: dict, source: dict, evidence_type: str) -> None:
    out["split"] = "derived"
    out["derived_from_task_id"] = source["task_id"]
    out["source_split"] = source.get("split")
    out["source_root_split"] = _root_split(source)
    out["evidence_type"] = evidence_type
    out["blind"] = True


def _version_task_attempt(out: dict, tag: str | None) -> dict:
    if tag is None:
        return out
    out["variant_base_task_id"] = out["task_id"]
    out["task_id"] = f"{out['task_id']}-B{tag}"
    out["followup_batch"] = tag
    return out


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
    _mark_derived(out, task, "symmetry")
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
    _mark_derived(out, task, "symmetry")
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

    safe_salt = hashlib.sha256(salt.encode("utf-8")).hexdigest()[:10]
    out = copy.deepcopy(task)
    out["task_id"] = f"{task['task_id']}-PERT-{safe_salt}"
    out["deal_id"] = f"{task['deal_id']}-PERT-{safe_salt}"
    out["deal"] = _render_deal(new_hands)
    _mark_derived(out, task, "perturbation")
    out["perturbation"] = {
        "salt_hash": safe_salt,
        "suit": STRAINS[suit],
        "seat_a": SEATS[a],
        "seat_b": SEATS[b],
        "rank_a_to_b": ra,
        "rank_b_to_a": rb,
    }
    return out


def create_variants(task: dict, batch_id: str | None = None) -> list[dict]:
    """Create a discrimination/retest set with optional unique blind attempt IDs.

    A new `batch_id` creates fresh task IDs even for exact symmetry retests, so a
    later analyzer state can make a new locked blind prediction without mutating
    the old prediction. Perturbation salts also vary by batch, producing new
    nearby positions rather than repeatedly memorizing p1/p2.
    """
    tag = _batch_tag(batch_id)
    perturb_suffix = "base" if tag is None else tag
    variants = [
        _version_task_attempt(rotate_task(task, 1), tag),
        _version_task_attempt(rotate_task(task, 2), tag),
        _version_task_attempt(permute_suits_task(task), tag),
        _version_task_attempt(perturb_task(task, f"p1-{perturb_suffix}"), tag),
        _version_task_attempt(perturb_task(task, f"p2-{perturb_suffix}"), tag),
    ]
    unique: dict[tuple, dict] = {}
    for v in variants:
        key = (v["deal"], v.get("task_type"), v.get("declarer"), v.get("strain"), v.get("leader"))
        unique.setdefault(key, v)
    return list(unique.values())


def _default_followup_batch(con: sqlite3.Connection) -> str:
    """Idempotent until learning progress changes, fresh after new evaluations.

    Manual/automatic repeated generation before any new DDS result returns the
    same batch. Once more tasks have been evaluated, the progress signature
    changes and the next reinforcement set receives fresh blind task IDs/salts.
    """
    evaluated = int(con.execute("SELECT COUNT(*) FROM dds_results").fetchone()[0])
    evidence = int(con.execute("SELECT COUNT(*) FROM skill_evidence").fetchone()[0])
    return f"auto-e{evaluated}-s{evidence}"


def create_error_followups(
    base_tasks_path: Path,
    con: sqlite3.Connection,
    out_path: Path,
    max_sources: int = 500,
    batch_id: str | None = None,
) -> dict:
    """Create fresh blind discrimination tasks from TRAIN errors only.

    Validation and sealed-test errors are deliberately excluded. Their purpose is
    measurement, not training. No DDS answer is copied into derived tasks. If no
    explicit batch id is supplied, the database learning-progress signature is
    used: generation is idempotent until new evaluation/evidence appears and then
    automatically creates a fresh retest batch.
    """
    if batch_id is None:
        batch_id = _default_followup_batch(con)

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
        JOIN dds_results r ON r.task_id=e.task_id
        LEFT JOIN skill_evidence se ON se.task_id=e.task_id
        WHERE r.split='train'
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
        if task is None or task.get("split") != "train":
            skipped += 1
            continue
        for v in create_variants(task, batch_id=batch_id):
            v["source_error_magnitude"] = float(magnitude or 0)
            v["source_high_confidence_error"] = bool(high_conf)
            variants.append(v)

    by_fingerprint: dict[tuple, dict] = {}
    for v in variants:
        key = (v["deal"], v.get("task_type"), v.get("declarer"), v.get("strain"), v.get("leader"))
        current = by_fingerprint.get(key)
        if current is None or v["task_id"] < current["task_id"]:
            by_fingerprint[key] = v
    ordered = sorted(by_fingerprint.values(), key=lambda x: x["task_id"])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in ordered:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "source_errors": len(source_rows),
        "source_errors_skipped": skipped,
        "derived_tasks": len(ordered),
        "source_policy": "train_only",
        "followup_batch": _batch_tag(batch_id),
        "path": str(out_path),
        "dds_called": False,
    }
