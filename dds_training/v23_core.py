from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

ALGORITHM_VERSION = "dds-learning-v2.3"
SEATS = "NESW"
SUITS = "SHDC"
RANKS = "AKQJT98765432"
RANK_VALUE = {rank: 14 - i for i, rank in enumerate(RANKS)}


# ---------------------------------------------------------------------------
# Canonical helpers
# ---------------------------------------------------------------------------


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: object, length: int = 16) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:length]


def _seat_index(value: int | str) -> int:
    if isinstance(value, int):
        if value not in range(4):
            raise ValueError(f"Seat must be 0..3, got {value}")
        return value
    value = str(value).upper()
    if value not in SEATS:
        raise ValueError(f"Unknown seat: {value}")
    return SEATS.index(value)


def _strain_index(value: int | str) -> int:
    if isinstance(value, int):
        if value not in range(5):
            raise ValueError(f"Strain must be 0..4, got {value}")
        return value
    value = str(value).upper().replace("N", "NT")
    mapping = {"S": 0, "H": 1, "D": 2, "C": 3, "NT": 4}
    if value not in mapping:
        raise ValueError(f"Unknown strain: {value}")
    return mapping[value]


# ---------------------------------------------------------------------------
# PBN/card parsing and legal play-line validation
# ---------------------------------------------------------------------------


def parse_deal(pbn: str) -> dict[int, list[str]]:
    """Parse a four-hand PBN deal into S/H/D/C strings indexed by N/E/S/W."""
    pbn = pbn.strip()
    if len(pbn) < 3 or pbn[1] != ":":
        raise ValueError(f"Expected seat-prefixed PBN deal, got {pbn!r}")
    start = _seat_index(pbn[0])
    raw_hands = pbn[2:].split()
    if len(raw_hands) != 4:
        raise ValueError("A complete Stage-2 position must contain four hands")
    hands: dict[int, list[str]] = {}
    seen: set[str] = set()
    for offset, raw in enumerate(raw_hands):
        suits = raw.split(".")
        if len(suits) != 4:
            raise ValueError(f"Bad PBN hand: {raw!r}")
        count = 0
        normalized: list[str] = []
        for suit_index, cards in enumerate(suits):
            if any(rank not in RANKS for rank in cards):
                raise ValueError(f"Bad rank in PBN hand: {raw!r}")
            if len(set(cards)) != len(cards):
                raise ValueError(f"Repeated rank inside suit: {raw!r}")
            cards = "".join(sorted(cards, key=RANKS.index))
            normalized.append(cards)
            count += len(cards)
            for rank in cards:
                card = f"{SUITS[suit_index]}{rank}"
                if card in seen:
                    raise ValueError(f"Duplicate physical card: {card}")
                seen.add(card)
        if count != 13:
            raise ValueError(f"Each full hand must contain 13 cards, found {count}")
        hands[(start + offset) % 4] = normalized
    if len(seen) != 52:
        raise ValueError(f"Expected 52 unique cards, found {len(seen)}")
    return hands


def render_deal(hands: dict[int, Sequence[str]]) -> str:
    return "N:" + " ".join(".".join(hands[seat]) for seat in range(4))


def normalize_card(card: str) -> str:
    card = str(card).strip().upper().replace("10", "T")
    if len(card) != 2 or card[0] not in SUITS or card[1] not in RANKS:
        raise ValueError(f"Bad card token: {card!r}")
    return card


def hand_cards(hand: Sequence[str]) -> set[str]:
    return {f"{SUITS[suit]}{rank}" for suit, cards in enumerate(hand) for rank in cards}


def _remove_card(hands: dict[int, list[str]], seat: int, card: str) -> None:
    suit = SUITS.index(card[0])
    rank = card[1]
    cards = hands[seat][suit]
    if rank not in cards:
        raise ValueError(f"{SEATS[seat]} does not hold {card}")
    hands[seat][suit] = cards.replace(rank, "", 1)


def legal_cards(hands: dict[int, list[str]], seat: int, current_trick: Sequence[tuple[int, str]]) -> list[str]:
    cards = sorted(hand_cards(hands[seat]), key=lambda c: (SUITS.index(c[0]), RANKS.index(c[1])))
    if not current_trick:
        return cards
    led_suit = current_trick[0][1][0]
    follow = [card for card in cards if card[0] == led_suit]
    return follow or cards


def trick_winner(trick: Sequence[tuple[int, str]], trump: int | None) -> int:
    if len(trick) != 4:
        raise ValueError("A winner can be determined only after four cards")
    led_suit = trick[0][1][0]
    trump_suit = None if trump is None else SUITS[trump]

    def key(item: tuple[int, str]) -> tuple[int, int]:
        _, card = item
        if trump_suit is not None and card[0] == trump_suit:
            suit_priority = 2
        elif card[0] == led_suit:
            suit_priority = 1
        else:
            suit_priority = 0
        return suit_priority, RANK_VALUE[card[1]]

    return max(trick, key=key)[0]


def _remaining_hand_strings(hands: dict[int, list[str]]) -> dict[str, list[str]]:
    return {SEATS[seat]: list(hands[seat]) for seat in range(4)}


def validate_play_line(
    deal: str,
    *,
    first_to_play: int | str,
    strain: int | str,
    line: Sequence[str],
    initial_trick: Sequence[tuple[int | str, str]] | None = None,
) -> dict:
    """Validate ownership, turn order and follow-suit for a partial/full line.

    The result is solver-neutral and can be fed to a DDS partial-position adapter.
    It deliberately validates the line *before* DDS exposure.
    """
    hands = parse_deal(deal)
    turn = _seat_index(first_to_play)
    strain_i = _strain_index(strain)
    trump = None if strain_i == 4 else strain_i
    current: list[tuple[int, str]] = []
    completed: list[dict] = []
    states: list[dict] = []

    if initial_trick:
        if len(initial_trick) >= 4:
            raise ValueError("initial_trick must contain 0..3 cards")
        for seat_raw, card_raw in initial_trick:
            seat = _seat_index(seat_raw)
            card = normalize_card(card_raw)
            if seat != turn:
                raise ValueError(f"Initial trick turn mismatch: expected {SEATS[turn]}, got {SEATS[seat]}")
            if card not in legal_cards(hands, seat, current):
                raise ValueError(f"Initial trick card {card} is illegal for {SEATS[seat]}")
            _remove_card(hands, seat, card)
            current.append((seat, card))
            turn = (turn + 1) % 4

    normalized_line = [normalize_card(card) for card in line]
    for decision_index, card in enumerate(normalized_line):
        allowed = legal_cards(hands, turn, current)
        if card not in allowed:
            held = card in hand_cards(hands[turn])
            reason = "failure to follow suit" if held and current else "card not held / already played"
            raise ValueError(
                f"Illegal line at decision {decision_index}: {SEATS[turn]} cannot play {card} ({reason}); legal={allowed}"
            )
        seat = turn
        before_hash = stable_hash({"hands": _remaining_hand_strings(hands), "turn": turn, "trick": current})
        _remove_card(hands, seat, card)
        current.append((seat, card))
        winner = None
        if len(current) == 4:
            winner = trick_winner(current, trump)
            completed.append(
                {
                    "trick_index": len(completed),
                    "cards": [{"seat": SEATS[s], "card": c} for s, c in current],
                    "winner": SEATS[winner],
                }
            )
            current = []
            turn = winner
        else:
            turn = (seat + 1) % 4
        states.append(
            {
                "decision_index": decision_index,
                "seat": SEATS[seat],
                "card": card,
                "position_before": before_hash,
                "next_to_play": SEATS[turn],
                "current_trick": [{"seat": SEATS[s], "card": c} for s, c in current],
                "completed_tricks": len(completed),
            }
        )

    remaining = _remaining_hand_strings(hands)
    return {
        "ok": True,
        "line": normalized_line,
        "line_sha256": hashlib.sha256(" ".join(normalized_line).encode("utf-8")).hexdigest(),
        "states": states,
        "completed": completed,
        "next_to_play": SEATS[turn],
        "current_trick": [{"seat": SEATS[s], "card": c} for s, c in current],
        "remaining_hands": remaining,
        "remaining_deal": render_deal({seat: remaining[SEATS[seat]] for seat in range(4)}),
    }


def validate_line_bearing_prediction(task: dict, prediction: dict, *, require_line: bool = True) -> dict:
    line = prediction.get("line")
    if line is None:
        line = []
    if not isinstance(line, list):
        raise ValueError("prediction.line must be a list of card tokens")
    if require_line and not line:
        raise ValueError("Stage-2 play prediction must contain a pre-DDS legal line")
    candidates = prediction.get("candidates", [])
    if candidates is not None and not isinstance(candidates, list):
        raise ValueError("prediction.candidates must be a list")
    first = task.get("next_to_play", task.get("first_to_play", task.get("leader")))
    if first is None:
        raise ValueError("Task must define next_to_play/first_to_play/leader")
    result = validate_play_line(
        task["deal"],
        first_to_play=first,
        strain=task["strain"],
        line=line,
        initial_trick=[(x["seat"], x["card"]) for x in task.get("current_trick", [])],
    )
    initial_hands = parse_deal(task["deal"])
    initial_legal = legal_cards(initial_hands, _seat_index(first), [])
    normalized_candidates = [normalize_card(card) for card in candidates or []]
    illegal_candidates = sorted(set(normalized_candidates) - set(initial_legal))
    if illegal_candidates:
        raise ValueError(f"Prediction contains illegal initial candidates: {illegal_candidates}")
    probability = prediction.get("confidence_probability")
    if probability is not None and not (0.0 <= float(probability) <= 1.0):
        raise ValueError("confidence_probability must be in [0,1]")
    result["candidate_cards"] = normalized_candidates
    result["confidence_probability"] = None if probability is None else float(probability)
    return result


# ---------------------------------------------------------------------------
# Family lineage, split isolation and cross-fitting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FamilyLineage:
    family_id: str
    root_deal_id: str
    root_split: str
    fold: int


def family_id_for(task: dict) -> str:
    explicit = task.get("family_id")
    if explicit:
        return str(explicit)
    root_id = str(task.get("root_deal_id") or task.get("source_root_deal_id") or task.get("deal_id"))
    return f"F-{stable_hash({'root_deal_id': root_id}, 20)}"


def assign_fold(family_id: str, *, folds: int = 5, seed: int = 20260815) -> int:
    if folds < 2:
        raise ValueError("Cross-fitting requires at least two folds")
    digest = hashlib.sha256(f"{seed}:{family_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % folds


def stamp_root_task(task: dict, *, folds: int = 5, seed: int = 20260815) -> dict:
    out = copy.deepcopy(task)
    root_id = str(out.get("root_deal_id") or out["deal_id"])
    root_split = str(out.get("root_split") or out.get("split") or "train")
    family = family_id_for({**out, "root_deal_id": root_id})
    out.update(
        {
            "root_deal_id": root_id,
            "root_split": root_split,
            "family_id": family,
            "crossfit_fold": assign_fold(family, folds=folds, seed=seed),
            "lineage_version": ALGORITHM_VERSION,
        }
    )
    return out


def derive_task(source: dict, *, task_id: str, deal_id: str, evidence_role: str, changes: dict | None = None) -> dict:
    if evidence_role not in {"regression", "reinforcement", "transfer", "real_world", "counterexample"}:
        raise ValueError(f"Unsupported evidence role: {evidence_role}")
    root = stamp_root_task(source)
    out = copy.deepcopy(source)
    out.update(changes or {})
    out.update(
        {
            "task_id": task_id,
            "deal_id": deal_id,
            "split": "derived",
            "derived_from_task_id": source["task_id"],
            "root_deal_id": root["root_deal_id"],
            "root_split": root["root_split"],
            "source_root_split": root["root_split"],
            "family_id": root["family_id"],
            "crossfit_fold": root["crossfit_fold"],
            "evidence_role": evidence_role,
            "independent_transfer": evidence_role in {"transfer", "real_world"} and deal_id != root["root_deal_id"],
            "lineage_version": ALGORITHM_VERSION,
        }
    )
    return out


def audit_lineage(tasks: Iterable[dict]) -> dict:
    tasks = [stamp_root_task(task) for task in tasks]
    by_family: dict[str, list[dict]] = defaultdict(list)
    issues: list[dict] = []
    for task in tasks:
        by_family[task["family_id"]].append(task)
        if task.get("split") == "derived" and task.get("source_root_split") not in {None, task["root_split"]}:
            issues.append({"code": "ROOT_SPLIT_MISMATCH", "task_id": task["task_id"]})
        if task.get("evidence_role") in {"reinforcement", "regression"} and task.get("independent_transfer"):
            issues.append({"code": "SAME_SOURCE_MARKED_INDEPENDENT", "task_id": task["task_id"]})

    for family, rows in by_family.items():
        roots = {row["root_split"] for row in rows}
        folds = {int(row["crossfit_fold"]) for row in rows}
        ordinary_splits = {row.get("split") for row in rows if row.get("split") != "derived"}
        if len(roots) != 1:
            issues.append({"code": "FAMILY_MULTIPLE_ROOT_SPLITS", "family_id": family, "values": sorted(roots)})
        if len(folds) != 1:
            issues.append({"code": "FAMILY_MULTIPLE_FOLDS", "family_id": family, "values": sorted(folds)})
        if "train" in ordinary_splits and ordinary_splits & {"validation", "sealed_test"}:
            issues.append({"code": "FAMILY_HOLDOUT_LEAK", "family_id": family, "splits": sorted(ordinary_splits)})
        if "validation" in ordinary_splits and "sealed_test" in ordinary_splits:
            issues.append({"code": "VALIDATION_SEALED_FAMILY_OVERLAP", "family_id": family})
    return {
        "status": "ok" if not issues else "error",
        "tasks": len(tasks),
        "families": len(by_family),
        "issues": issues,
    }


def crossfit_training_families(tasks: Iterable[dict], held_out_fold: int, *, folds: int = 5) -> dict:
    stamped = [stamp_root_task(task, folds=folds) for task in tasks]
    train = sorted({t["family_id"] for t in stamped if t["root_split"] == "train" and t["crossfit_fold"] != held_out_fold})
    held = sorted({t["family_id"] for t in stamped if t["root_split"] == "train" and t["crossfit_fold"] == held_out_fold})
    if set(train) & set(held):
        raise AssertionError("Family appeared in both cross-fit train and held-out sets")
    return {"fold": held_out_fold, "training_families": train, "held_out_families": held}


# ---------------------------------------------------------------------------
# Continuation/decision tasks and human-information masking
# ---------------------------------------------------------------------------


def make_continuation_task(base_task: dict, play_prefix: Sequence[str], *, decision_id: str, information_mode: str = "double_dummy") -> dict:
    first = base_task.get("first_to_play", base_task.get("leader", base_task.get("next_to_play")))
    if first is None:
        raise ValueError("Base task lacks first_to_play/leader/next_to_play")
    state = validate_play_line(
        base_task["deal"],
        first_to_play=first,
        strain=base_task["strain"],
        line=play_prefix,
    )
    declarer = _seat_index(base_task["declarer"])
    dummy = (declarer + 2) % 4
    next_seat = _seat_index(state["next_to_play"])
    side = "declarer" if next_seat in {declarer, dummy} else "defense"
    remaining_hands = {SEATS.index(seat): suits for seat, suits in state["remaining_hands"].items()}
    legal = legal_cards(
        remaining_hands,
        next_seat,
        [(SEATS.index(x["seat"]), x["card"]) for x in state["current_trick"]],
    )
    root = stamp_root_task(base_task)
    task = {
        **root,
        "task_id": decision_id,
        "task_type": "play_decision",
        "position_id": f"P-{stable_hash({'family': root['family_id'], 'line': list(play_prefix), 'next': next_seat}, 24)}",
        "deal": state["remaining_deal"],
        "original_deal": base_task["deal"],
        "play_prefix": [normalize_card(card) for card in play_prefix],
        "next_to_play": next_seat,
        "actor_side": side,
        "current_trick": state["current_trick"],
        "completed_tricks": len(state["completed"]),
        "legal_cards": legal,
        "information_mode": information_mode,
        "prediction_schema": {
            "chosen_card": "one legal card",
            "candidates": "all serious candidates considered before DDS",
            "reason": "bridge mechanism and assumptions",
            "line": "legal continuation planned before DDS",
            "confidence_probability": "0..1 probability that chosen card is DD-optimal",
        },
    }
    task["visible_information"] = mask_information(task, viewer=next_seat, mode=information_mode)
    return task


def mask_information(task: dict, *, viewer: int | str, mode: str) -> dict:
    viewer_i = _seat_index(viewer)
    if mode not in {"double_dummy", "human"}:
        raise ValueError("information_mode must be double_dummy or human")
    hands = parse_deal(task["deal"])
    declarer = _seat_index(task["declarer"])
    dummy = (declarer + 2) % 4
    if mode == "double_dummy":
        visible = set(range(4))
    else:
        visible = {viewer_i}
        dummy_revealed = bool(task.get("dummy_revealed", task.get("completed_tricks", 0) > 0 or task.get("play_prefix")))
        if dummy_revealed:
            visible.add(dummy)
    hand_view = {SEATS[seat]: list(hands[seat]) if seat in visible else None for seat in range(4)}
    return {
        "mode": mode,
        "viewer": SEATS[viewer_i],
        "visible_hands": sorted(SEATS[s] for s in visible),
        "hands": hand_view,
        "auction": task.get("auction"),
        "public_play": task.get("play_prefix", []),
        "current_trick": task.get("current_trick", []),
    }


def audit_information_mask(task: dict) -> dict:
    visible = task.get("visible_information") or {}
    if visible.get("mode") != "human":
        return {"status": "not_applicable", "issues": []}
    allowed = set(visible.get("visible_hands", []))
    issues = []
    for seat, hand in (visible.get("hands") or {}).items():
        if seat not in allowed and hand is not None:
            issues.append({"code": "HIDDEN_HAND_LEAK", "seat": seat})
    return {"status": "ok" if not issues else "error", "issues": issues}


# ---------------------------------------------------------------------------
# Confidence calibration
# ---------------------------------------------------------------------------


def calibration_report(probabilities: Sequence[float], outcomes: Sequence[bool | int], *, bins: int = 10) -> dict:
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError("Calibration requires non-empty equally sized arrays")
    probs = [min(1.0, max(0.0, float(p))) for p in probabilities]
    ys = [1.0 if bool(y) else 0.0 for y in outcomes]
    buckets: list[list[int]] = [[] for _ in range(bins)]
    for i, p in enumerate(probs):
        index = min(bins - 1, int(p * bins))
        buckets[index].append(i)
    reliability = []
    ece = 0.0
    mce = 0.0
    for index, members in enumerate(buckets):
        if not members:
            continue
        mean_p = sum(probs[i] for i in members) / len(members)
        rate = sum(ys[i] for i in members) / len(members)
        gap = abs(mean_p - rate)
        ece += len(members) / len(probs) * gap
        mce = max(mce, gap)
        reliability.append(
            {
                "bin": index,
                "count": len(members),
                "mean_probability": mean_p,
                "empirical_success": rate,
                "gap": gap,
            }
        )
    brier = sum((p - y) ** 2 for p, y in zip(probs, ys)) / len(probs)
    log_loss = -sum(y * math.log(max(p, 1e-12)) + (1 - y) * math.log(max(1 - p, 1e-12)) for p, y in zip(probs, ys)) / len(probs)
    high = [i for i, p in enumerate(probs) if p >= 0.8]
    return {
        "n": len(probs),
        "brier": brier,
        "log_loss": log_loss,
        "ece": ece,
        "mce": mce,
        "high_confidence_coverage": len(high) / len(probs),
        "high_confidence_success": None if not high else sum(ys[i] for i in high) / len(high),
        "reliability": reliability,
    }


def fit_histogram_calibrator(probabilities: Sequence[float], outcomes: Sequence[bool | int], *, bins: int = 10) -> dict:
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError("Calibrator requires non-empty equally sized arrays")
    counts = [0] * bins
    successes = [0] * bins
    for p, outcome in zip(probabilities, outcomes):
        p = min(1.0, max(0.0, float(p)))
        index = min(bins - 1, int(p * bins))
        counts[index] += 1
        successes[index] += int(bool(outcome))
    # Laplace smoothing and monotone cumulative correction.
    rates = [(successes[i] + 1) / (counts[i] + 2) for i in range(bins)]
    for i in range(1, bins):
        if rates[i] < rates[i - 1]:
            rates[i] = rates[i - 1]
    return {"bins": bins, "counts": counts, "rates": rates, "fit_scope": "out_of_fold_train_only"}


def apply_calibrator(probability: float, calibrator: dict) -> float:
    p = min(1.0, max(0.0, float(probability)))
    bins = int(calibrator["bins"])
    index = min(bins - 1, int(p * bins))
    return float(calibrator["rates"][index])


# ---------------------------------------------------------------------------
# Family-preserving sharding and resumability
# ---------------------------------------------------------------------------


def plan_shards(tasks: Iterable[dict], *, max_tasks: int = 1000, seed: int = 20260815) -> dict:
    if max_tasks <= 0:
        raise ValueError("max_tasks must be positive")
    stamped = [stamp_root_task(task, seed=seed) for task in tasks]
    by_family: dict[str, list[dict]] = defaultdict(list)
    for task in stamped:
        by_family[task["family_id"]].append(task)
    families = sorted(by_family, key=lambda family: stable_hash({"seed": seed, "family": family}, 64))
    shards: list[dict] = []
    current: list[dict] = []
    current_families: list[str] = []

    def flush() -> None:
        nonlocal current, current_families
        if not current:
            return
        index = len(shards)
        task_ids = [task["task_id"] for task in current]
        payload = {
            "index": index,
            "task_ids": task_ids,
            "family_ids": list(current_families),
            "task_count": len(task_ids),
        }
        payload["sha256"] = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        payload["run_id"] = f"v23-shard-{index:04d}-{payload['sha256'][:10]}"
        payload["status"] = "planned"
        shards.append(payload)
        current = []
        current_families = []

    for family in families:
        rows = sorted(by_family[family], key=lambda task: task["task_id"])
        if current and len(current) + len(rows) > max_tasks:
            flush()
        current.extend(rows)
        current_families.append(family)
        if len(current) >= max_tasks:
            flush()
    flush()
    manifest = {
        "algorithm_version": ALGORITHM_VERSION,
        "seed": seed,
        "max_tasks": max_tasks,
        "task_count": len(stamped),
        "family_count": len(by_family),
        "shards": shards,
    }
    manifest["manifest_sha256"] = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    return manifest


def audit_shards(manifest: dict) -> dict:
    task_seen: set[str] = set()
    family_seen: set[str] = set()
    issues = []
    for expected_index, shard in enumerate(manifest.get("shards", [])):
        if shard.get("index") != expected_index:
            issues.append({"code": "NON_SEQUENTIAL_SHARD", "expected": expected_index, "found": shard.get("index")})
        tasks = shard.get("task_ids", [])
        families = shard.get("family_ids", [])
        duplicate_tasks = sorted(task_seen & set(tasks))
        duplicate_families = sorted(family_seen & set(families))
        if duplicate_tasks:
            issues.append({"code": "TASK_IN_MULTIPLE_SHARDS", "values": duplicate_tasks[:10]})
        if duplicate_families:
            issues.append({"code": "FAMILY_IN_MULTIPLE_SHARDS", "values": duplicate_families[:10]})
        task_seen.update(tasks)
        family_seen.update(families)
    if len(task_seen) != int(manifest.get("task_count", -1)):
        issues.append({"code": "TASK_COUNT_MISMATCH", "unique": len(task_seen), "declared": manifest.get("task_count")})
    if len(family_seen) != int(manifest.get("family_count", -1)):
        issues.append({"code": "FAMILY_COUNT_MISMATCH", "unique": len(family_seen), "declared": manifest.get("family_count")})
    return {"status": "ok" if not issues else "error", "issues": issues}


# ---------------------------------------------------------------------------
# Counterexamples and versioned rule-promotion policy
# ---------------------------------------------------------------------------


def optimal_actions(scores: dict[str, float], *, maximize: bool = True) -> set[str]:
    if not scores:
        return set()
    target = max(scores.values()) if maximize else min(scores.values())
    return {str(action) for action, value in scores.items() if float(value) == float(target)}


def find_counterexamples(
    source_scores: dict[str, float],
    variants: Iterable[dict],
    *,
    source_action: str,
    maximize: bool = True,
    minimum_regret: float = 1.0,
) -> list[dict]:
    source_optimal = optimal_actions(source_scores, maximize=maximize)
    out = []
    for row in variants:
        scores = {str(k): float(v) for k, v in row["scores"].items()}
        optimum = optimal_actions(scores, maximize=maximize)
        if source_action not in scores:
            continue
        best = max(scores.values()) if maximize else min(scores.values())
        chosen = scores[source_action]
        regret = best - chosen if maximize else chosen - best
        if optimum != source_optimal and regret >= minimum_regret:
            out.append(
                {
                    "task_id": row.get("task_id"),
                    "distance": float(row.get("distance", 9999)),
                    "source_optimal": sorted(source_optimal),
                    "variant_optimal": sorted(optimum),
                    "source_action_regret": regret,
                    "change": row.get("change"),
                }
            )
    return sorted(out, key=lambda x: (x["distance"], -x["source_action_regret"], str(x["task_id"])))


def assess_rule_candidate(rule: dict, evidence: Iterable[dict]) -> dict:
    """Assess a bridge-rule candidate without using validation/sealed for promotion."""
    usable = [
        row for row in evidence
        if row.get("split") not in {"validation", "sealed_test"}
        and row.get("role") in {"regression", "reinforcement", "transfer", "real_world", "counterexample"}
    ]
    independent = [row for row in usable if row.get("role") in {"transfer", "real_world"} and row.get("independent", True)]
    real_world = [row for row in independent if row.get("role") == "real_world"]
    counter = [row for row in usable if row.get("role") == "counterexample"]
    regression = [row for row in usable if row.get("role") == "regression"]
    reinforcement = [row for row in usable if row.get("role") == "reinforcement"]

    def rate(rows: Sequence[dict]) -> float:
        return sum(bool(row.get("success")) for row in rows) / len(rows) if rows else 0.0

    serious_contradictions = [row for row in usable if not row.get("success") and float(row.get("regret", 0) or 0) >= 2]
    blockers = []
    if len(independent) < int(rule.get("min_independent_transfer", 30)):
        blockers.append("insufficient_independent_transfer")
    if rate(independent) < float(rule.get("min_transfer_rate", 0.85)):
        blockers.append("transfer_rate_below_threshold")
    if len(counter) < int(rule.get("min_counterexamples", 5)) or rate(counter) < 0.8:
        blockers.append("counterexample_discrimination_not_proven")
    if len(regression) < int(rule.get("min_regressions", 10)) or rate(regression) < 0.9:
        blockers.append("regression_stability_not_proven")
    if serious_contradictions:
        blockers.append("serious_contradictions_present")

    if serious_contradictions and rule.get("previous_status") in {"confirmed", "stable", "weakened"}:
        status = "weakened"
    elif not blockers:
        status = "stable" if len(real_world) >= int(rule.get("min_real_world", 5)) else "confirmed"
    elif usable:
        status = "testing"
    else:
        status = "candidate"
    return {
        "rule_key": rule["rule_key"],
        "version": int(rule.get("version", 1)),
        "status": status,
        "blockers": blockers,
        "counts": {
            "usable": len(usable),
            "independent_transfer": len(independent),
            "real_world": len(real_world),
            "counterexample": len(counter),
            "regression": len(regression),
            "reinforcement": len(reinforcement),
            "serious_contradictions": len(serious_contradictions),
        },
        "rates": {
            "independent_transfer": rate(independent),
            "counterexample": rate(counter),
            "regression": rate(regression),
        },
    }


# ---------------------------------------------------------------------------
# Negative controls and methodological readiness
# ---------------------------------------------------------------------------


def deterministic_permutation(values: Sequence[object], *, seed: int) -> list[object]:
    out = list(values)
    random.Random(seed).shuffle(out)
    return out


def negative_control_report(real_loss: float, shuffled_label_losses: Sequence[float], *, minimum_gap: float = 0.05) -> dict:
    if not shuffled_label_losses:
        raise ValueError("At least one shuffled-label run is required")
    mean_control = sum(float(x) for x in shuffled_label_losses) / len(shuffled_label_losses)
    gap = mean_control - float(real_loss)
    return {
        "real_loss": float(real_loss),
        "shuffled_mean_loss": mean_control,
        "gap": gap,
        "minimum_gap": minimum_gap,
        "status": "ok" if gap >= minimum_gap else "suspicious",
        "interpretation": "Real labels must beat shuffled-label controls by a practical margin.",
    }


DEFAULT_STAGE2_CAPABILITIES = {
    "family_lineage_and_crossfit": {"status": "ready", "mass_start_required": True},
    "legal_line_validation": {"status": "ready", "mass_start_required": True},
    "continuation_task_schema": {"status": "ready", "mass_start_required": True},
    "human_information_mask": {"status": "ready", "mass_start_required": True},
    "confidence_calibration": {"status": "ready", "mass_start_required": True},
    "family_preserving_shards": {"status": "ready", "mass_start_required": True},
    "counterexample_evaluator": {"status": "ready", "mass_start_required": True},
    "versioned_rule_promotion": {"status": "ready", "mass_start_required": True},
    "negative_control_protocol": {"status": "ready", "mass_start_required": True},
    "dds_partial_position_adapter": {
        "status": "blocked",
        "mass_start_required": True,
        "reason": "Must be integrated and cross-checked on partial tricks before full-play trajectories are trusted.",
    },
    "full_play_trajectory_integration": {
        "status": "blocked",
        "mass_start_required": True,
        "reason": "Requires the verified DDS partial-position adapter and AnalysePlay regression corpus.",
    },
    "real_pbn_play_ingestion": {
        "status": "blocked",
        "mass_start_required": False,
        "reason": "Required before real-world transfer claims, but not before the synthetic main corpus.",
    },
    "stage2_sharded_workflow": {
        "status": "blocked",
        "mass_start_required": True,
        "reason": "The 30k runner must persist each shard independently and resume without opening holdouts early.",
    },
}


def audit_stage2_readiness(capabilities: dict | None = None) -> dict:
    capabilities = copy.deepcopy(capabilities or DEFAULT_STAGE2_CAPABILITIES)
    blockers = [
        {"capability": key, **value}
        for key, value in capabilities.items()
        if value.get("mass_start_required") and value.get("status") != "ready"
    ]
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "status": "ready" if not blockers else "blocked",
        "capabilities": capabilities,
        "mass_start_blockers": blockers,
        "next_action": (
            "Stage 2 may start only after all mass-start blockers are implemented, tested and explicitly approved."
            if blockers
            else "Request explicit user approval for Stage 2; readiness alone never starts it."
        ),
    }


def write_readiness(path: Path, capabilities: dict | None = None) -> dict:
    report = audit_stage2_readiness(capabilities)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return report
