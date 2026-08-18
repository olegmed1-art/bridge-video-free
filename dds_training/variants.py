from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from config import FOLLOWUP_SOURCE_POLICY, STRAINS

RANK_ORDER = "AKQJT98765432"
SEATS = "NESW"
VUL_ODD_ROTATION = {"None": "None", "All": "All", "NS": "EW", "EW": "NS"}


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


def _mark_derived(
    out: dict,
    source: dict,
    evidence_type: str,
    *,
    variant_kind: str,
    transfer_eligible: bool = False,
) -> None:
    out["split"] = "derived"
    out["derived_from_task_id"] = source["task_id"]
    out["source_split"] = source.get("split")
    out["source_root_split"] = _root_split(source)
    out["evidence_type"] = evidence_type
    out["variant_kind"] = variant_kind
    out["transfer_eligible"] = bool(transfer_eligible)
    out["blind"] = True


def _version_task_attempt(out: dict, tag: str | None) -> dict:
    if tag is None:
        return out
    out["variant_base_task_id"] = out["task_id"]
    out["task_id"] = f"{out['task_id']}-B{tag}"
    out["followup_batch"] = tag
    return out


def retest_task(task: dict) -> dict:
    """Create a fresh blind attempt on the exact same mathematical position.

    This is regression evidence, not transfer evidence. A new task/deal id keeps
    the new locked prediction append-only while source provenance remains explicit.
    """
    out = copy.deepcopy(task)
    out["task_id"] = f"{task['task_id']}-RETEST"
    out["deal_id"] = f"{task['deal_id']}-RETEST"
    _mark_derived(out, task, "regression", variant_kind="exact_retest")
    return out


def rotate_task(task: dict, steps: int = 2) -> dict:
    """Rotate all seats while preserving the mathematical bridge position.

    Dealer and vulnerability metadata rotate too. They do not affect the current
    DD value task, but keeping them consistent prevents later human-analysis and
    display code from learning from contradictory metadata.
    """
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
    dealer = str(task.get("dealer", ""))
    if dealer in SEATS:
        out["dealer"] = SEATS[(SEATS.index(dealer) + steps) % 4]
    vulnerability = str(task.get("vulnerability", ""))
    if steps % 2 and vulnerability in VUL_ODD_ROTATION:
        out["vulnerability"] = VUL_ODD_ROTATION[vulnerability]
    _mark_derived(out, task, "symmetry", variant_kind=f"seat_rotation_{steps}")
    return out


def permute_suits_task(task: dict, mapping: tuple[int, int, int, int] = (1, 0, 3, 2)) -> dict:
    """Rename suits consistently; mapping[old_suit] gives new_suit.

    The default swaps spades/hearts and diamonds/clubs. NT stays NT. This is an
    invariance/reinforcement probe, not independent transfer evidence.
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
    _mark_derived(out, task, "symmetry", variant_kind=f"suit_permutation_{tag}")
    return out


def perturb_task(task: dict, salt: str = "p1") -> dict:
    """Create a minimal legal perturbation by swapping two ranks in one suit.

    A single physical card cannot move between hands while preserving 13 cards in
    every hand, so the minimal complete-deal perturbation is a two-card swap. Suit
    lengths remain unchanged; only two rank locations change. Until a fresh model
    excludes the source deal, this is reinforcement/discrimination evidence rather
    than independent transfer evidence.
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
    _mark_derived(out, task, "perturbation", variant_kind="same_suit_rank_swap")
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
    """Create exact regression plus invariance/discrimination reinforcement.

    A new `batch_id` creates fresh task IDs even for exact retests, so a later
    analyzer revision can make a new locked blind prediction without mutating the
    old one. These variants are not counted as independent transfer unless a
    separate cross-fit/fresh-corpus generator explicitly marks them eligible.
    """
    tag = _batch_tag(batch_id)
    perturb_suffix = "base" if tag is None else tag
    variants = [
        _version_task_attempt(retest_task(task), tag),
        _version_task_attempt(rotate_task(task, 1), tag),
        _version_task_attempt(rotate_task(task, 2), tag),
        _version_task_attempt(permute_suits_task(task), tag),
        _version_task_attempt(perturb_task(task, f"p1-{perturb_suffix}"), tag),
        _version_task_attempt(perturb_task(task, f"p2-{perturb_suffix}"), tag),
    ]
    unique: dict[tuple, dict] = {}
    for v in variants:
        key = (
            v["deal"],
            v.get("task_type"),
            v.get("declarer"),
            v.get("strain"),
            v.get("leader"),
            v.get("evidence_type"),
            v.get("variant_kind"),
        )
        unique.setdefault(key, v)
    return list(unique.values())


def _default_followup_batch(con: sqlite3.Connection) -> str:
    """Idempotent until learning progress changes, fresh after new evaluations."""
    evaluated = int(con.execute("SELECT COUNT(*) FROM dds_results").fetchone()[0])
    evidence = int(con.execute("SELECT COUNT(*) FROM skill_evidence").fetchone()[0])
    return f"auto-e{evaluated}-s{evidence}"


def _select_stratified_sources(
    base: dict[str, dict],
    rows: list[tuple[str, float, int, str]],
    max_sources: int,
) -> tuple[list[dict], dict]:
    """Select errors round-robin across task type, error code and strain.

    The pilot's former global magnitude sort selected only opening-lead errors,
    producing zero declarer follow-ups. This deterministic stratification keeps
    every represented skill/error family and strain in the reinforcement set.
    """
    groups: dict[tuple[str, str, int], list[dict]] = defaultdict(list)
    skipped = 0
    for task_id, magnitude, high_conf, error_code in rows:
        task = base.get(task_id)
        if task is None or task.get("split") != "train":
            skipped += 1
            continue
        group = (str(task.get("task_type")), str(error_code or "UNKNOWN"), int(task.get("strain", -1)))
        groups[group].append(
            {
                "task": task,
                "magnitude": float(magnitude or 0),
                "high_confidence": bool(high_conf),
                "error_code": str(error_code or "UNKNOWN"),
                "group": group,
            }
        )
    for items in groups.values():
        items.sort(key=lambda x: (-int(x["high_confidence"]), -x["magnitude"], x["task"]["task_id"]))

    selected: list[dict] = []
    group_keys = sorted(groups)
    index = {key: 0 for key in group_keys}
    while len(selected) < max_sources:
        progress = False
        for key in group_keys:
            i = index[key]
            if i >= len(groups[key]):
                continue
            selected.append(groups[key][i])
            index[key] = i + 1
            progress = True
            if len(selected) >= max_sources:
                break
        if not progress:
            break

    source_group_counts = Counter(
        f"{x['group'][0]}:{x['group'][1]}:{STRAINS[x['group'][2]] if 0 <= x['group'][2] < len(STRAINS) else x['group'][2]}"
        for x in selected
    )
    return selected, {
        "skipped": skipped,
        "available_groups": len(groups),
        "selected_group_counts": dict(sorted(source_group_counts.items())),
    }


def create_error_followups(
    base_tasks_path: Path,
    con: sqlite3.Connection,
    out_path: Path,
    max_sources: int = 500,
    batch_id: str | None = None,
) -> dict:
    """Create balanced blind regression/reinforcement tasks from TRAIN errors.

    Validation and sealed-test errors are excluded. No DDS answer is copied into
    a task. Sources are selected deterministically across task type, error code
    and strain rather than by one global severity ranking. The resulting set is
    intentionally targeted/adversarial and must not be compared directly with a
    random validation sample without a matched baseline.
    """
    if max_sources < 1:
        raise ValueError("max_sources must be positive")
    if batch_id is None:
        batch_id = _default_followup_batch(con)

    base: dict[str, dict] = {}
    for line in base_tasks_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            task = json.loads(line)
            base[task["task_id"]] = task

    source_rows = con.execute(
        """
        SELECT e.task_id,
               MAX(COALESCE(e.magnitude,0)) AS magnitude,
               MAX(CASE WHEN se.confidence='high' AND se.outcome!='success' THEN 1 ELSE 0 END) AS high_conf,
               MAX(COALESCE(json_extract(r.result_json,'$.error_code'),'UNKNOWN')) AS error_code
        FROM error_events e
        JOIN dds_results r ON r.task_id=e.task_id
        LEFT JOIN skill_evidence se ON se.task_id=e.task_id
        WHERE r.split='train'
        GROUP BY e.task_id
        ORDER BY e.task_id
        """
    ).fetchall()
    selected, selection_meta = _select_stratified_sources(base, source_rows, max_sources)

    variants: list[dict] = []
    source_type_counts = Counter()
    source_error_counts = Counter()
    for selection_rank, source in enumerate(selected, 1):
        task = source["task"]
        source_type_counts[str(task["task_type"])] += 1
        source_error_counts[source["error_code"]] += 1
        group_name = f"{task['task_type']}:{source['error_code']}:{task.get('strain_name', task.get('strain'))}"
        for v in create_variants(task, batch_id=batch_id):
            v["source_error_magnitude"] = source["magnitude"]
            v["source_high_confidence_error"] = source["high_confidence"]
            v["source_error_code"] = source["error_code"]
            v["source_task_type"] = task["task_type"]
            v["source_strain"] = task.get("strain")
            v["source_group"] = group_name
            v["source_selection_rank"] = selection_rank
            v["source_selection_policy"] = FOLLOWUP_SOURCE_POLICY
            variants.append(v)

    by_fingerprint: dict[tuple, dict] = {}
    for v in variants:
        key = (
            v["deal"],
            v.get("task_type"),
            v.get("declarer"),
            v.get("strain"),
            v.get("leader"),
            v.get("evidence_type"),
            v.get("variant_kind"),
        )
        current = by_fingerprint.get(key)
        if current is None or v["task_id"] < current["task_id"]:
            by_fingerprint[key] = v
    ordered = sorted(by_fingerprint.values(), key=lambda x: x["task_id"])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in ordered:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    derived_by_type = Counter(str(x.get("task_type")) for x in ordered)
    derived_by_evidence = Counter(str(x.get("evidence_type")) for x in ordered)
    return {
        "source_errors_available": len(source_rows),
        "source_errors_selected": len(selected),
        "source_errors_skipped": selection_meta["skipped"],
        "source_policy": FOLLOWUP_SOURCE_POLICY,
        "source_types": dict(sorted(source_type_counts.items())),
        "source_error_codes": dict(sorted(source_error_counts.items())),
        "source_group_counts": selection_meta["selected_group_counts"],
        "derived_tasks": len(ordered),
        "derived_by_type": dict(sorted(derived_by_type.items())),
        "derived_by_evidence_type": dict(sorted(derived_by_evidence.items())),
        "targeted_adversarial_sample": True,
        "independent_transfer_evidence": False,
        "followup_batch": _batch_tag(batch_id),
        "path": str(out_path),
        "dds_called": False,
    }
