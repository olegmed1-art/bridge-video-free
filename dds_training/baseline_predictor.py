from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

SEATS = "NESW"
SUITS = "SHDC"
RANK_VALUE = {"A": 4, "K": 3, "Q": 2, "J": 1, "T": 0, "9": 0, "8": 0, "7": 0, "6": 0, "5": 0, "4": 0, "3": 0, "2": 0}
RANK_ORDER = "AKQJT98765432"


def parse_deal(pbn: str) -> dict[int, list[str]]:
    pbn = pbn.strip()
    if len(pbn) < 3 or pbn[1] != ":":
        raise ValueError(f"Bad PBN deal: {pbn!r}")
    start = SEATS.index(pbn[0].upper())
    hands = pbn[2:].split()
    if len(hands) != 4:
        raise ValueError("Expected four hands")
    out: dict[int, list[str]] = {}
    for offset, hand in enumerate(hands):
        suits = hand.split(".")
        if len(suits) != 4:
            raise ValueError(f"Bad hand: {hand}")
        out[(start + offset) % 4] = suits
    return out


def hcp(hand: list[str]) -> int:
    return sum(RANK_VALUE.get(r, 0) for cards in hand for r in cards)


def top_honor_strength(cards: str) -> float:
    score = 0.0
    for i, r in enumerate(cards):
        if r == "A":
            score += 1.0
        elif r == "K":
            score += 0.75 if len(cards) >= 2 else 0.35
        elif r == "Q":
            score += 0.55 if len(cards) >= 3 else 0.2
        elif r == "J":
            score += 0.30 if len(cards) >= 4 else 0.1
        elif r == "T":
            score += 0.12 if i < 4 else 0.03
    return score


def long_suit_potential(cards: str) -> float:
    n = len(cards)
    return max(0.0, (n - 4) * 0.55) + top_honor_strength(cards)


def estimate_contract_tricks(task: dict) -> tuple[int, str, str]:
    hands = parse_deal(task["deal"])
    declarer = int(task["declarer"])
    partner = (declarer + 2) % 4
    strain = int(task["strain"])
    side_hcp = hcp(hands[declarer]) + hcp(hands[partner])

    # This is deliberately a bridge heuristic, not a solver. It sees the full
    # deal but performs no minimax search and never calls DDS. The small
    # conservative bias reduces meaningless overclaims while leaving plenty of
    # genuine errors for DDS learning.
    base = 6.0 + (side_hcp - 20.0) / 3.0
    shape_bonus = 0.0
    if strain == 4:  # NT
        shape_bonus += 0.18 * sum(long_suit_potential(hands[s]) for s in (declarer, partner))
        stopper_suits = 0
        for suit in range(4):
            joined = hands[declarer][suit] + hands[partner][suit]
            if "A" in joined or ("K" in joined and len(joined) >= 3) or ("Q" in joined and len(joined) >= 5):
                stopper_suits += 1
        shape_bonus += 0.18 * (stopper_suits - 2)
    else:
        fit = len(hands[declarer][strain]) + len(hands[partner][strain])
        shape_bonus += 0.45 * max(0, fit - 8)
        for seat in (declarer, partner):
            for suit in range(4):
                if suit == strain:
                    continue
                l = len(hands[seat][suit])
                if l == 0:
                    shape_bonus += 0.45
                elif l == 1:
                    shape_bonus += 0.22
        shape_bonus += 0.12 * sum(long_suit_potential(hands[s][strain]) for s in (declarer, partner))

    raw = base + shape_bonus - 0.45  # conservative blind baseline
    tricks = max(0, min(13, int(math.floor(raw + 0.35))))
    if side_hcp >= 31:
        confidence = "medium"
    elif 18 <= side_hcp <= 24:
        confidence = "low"
    else:
        confidence = "medium"
    reason = f"Blind heuristic: side HCP {side_hcp}; shape/fit adjustment {shape_bonus:.2f}; no DDS/minimax search."
    return tricks, confidence, reason


def sequence_lead(cards: str) -> str | None:
    for seq in ("AK", "KQ", "QJ", "JT", "T9", "98", "87"):
        if all(r in cards for r in seq):
            return seq[0]
    return None


def choose_opening_lead(task: dict) -> tuple[str, str, str]:
    hands = parse_deal(task["deal"])
    leader = int(task["leader"])
    strain = int(task["strain"])
    hand = hands[leader]

    candidates: list[tuple[float, int, str, str]] = []
    for suit in range(4):
        cards = hand[suit]
        if not cards:
            continue
        is_trump = strain != 4 and suit == strain
        seq = sequence_lead(cards)
        if seq:
            lead_rank = seq
        elif "A" in cards and len(cards) <= 2:
            lead_rank = "A"
        elif len(cards) >= 4:
            # fourth-best style proxy from a long suit, except when a clear
            # honor sequence already exists.
            lead_rank = cards[min(3, len(cards) - 1)]
        else:
            lead_rank = cards[-1]

        length_score = len(cards) * (1.15 if strain == 4 else 0.95)
        honor_score = top_honor_strength(cards) * 1.2
        seq_score = 1.4 if seq else 0.0
        trump_penalty = 2.0 if is_trump else 0.0
        singleton_bonus = 0.55 if strain != 4 and len(cards) == 1 and not is_trump else 0.0
        unsupported_honor_penalty = 0.0
        if len(cards) == 1 and cards[0] in "KQJ":
            unsupported_honor_penalty = 0.7
        score = length_score + honor_score + seq_score + singleton_bonus - trump_penalty - unsupported_honor_penalty
        candidates.append((score, suit, lead_rank, cards))

    if not candidates:
        raise ValueError("Leader has no cards")
    candidates.sort(key=lambda x: (-x[0], x[1], RANK_ORDER.index(x[2])))
    score, suit, rank, cards = candidates[0]
    card = f"{SUITS[suit]}{rank}"
    confidence = "medium" if candidates[0][0] - (candidates[1][0] if len(candidates) > 1 else 0) >= 1.5 else "low"
    reason = f"Blind lead heuristic: suit length {len(cards)}, sequence/strength score {score:.2f}; no DDS/minimax search."
    return card, confidence, reason


def prediction_for(task: dict, predictor_version: str) -> dict:
    if task["task_type"] == "contract_tricks":
        tricks, confidence, reason = estimate_contract_tricks(task)
        return {
            "task_id": task["task_id"],
            "tricks": tricks,
            "confidence": confidence,
            "reason": reason,
            "line": [],
            "predictor_version": predictor_version,
            "locked": True,
        }
    if task["task_type"] == "opening_lead":
        card, confidence, reason = choose_opening_lead(task)
        return {
            "task_id": task["task_id"],
            "card": card,
            "expected_defense_tricks": None,
            "confidence": confidence,
            "reason": reason,
            "line": [card],
            "predictor_version": predictor_version,
            "locked": True,
        }
    raise ValueError(f"Unsupported task type: {task['task_type']}")


def generate(tasks_path: Path, out_path: Path, splits: set[str], predictor_version: str) -> dict:
    total = 0
    by_type: dict[str, int] = {}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tasks_path.open("r", encoding="utf-8") as src, out_path.open("w", encoding="utf-8") as out:
        for line in src:
            if not line.strip():
                continue
            task = json.loads(line)
            if task.get("split") not in splits:
                continue
            pred = prediction_for(task, predictor_version)
            out.write(json.dumps(pred, ensure_ascii=False, sort_keys=True) + "\n")
            total += 1
            by_type[task["task_type"]] = by_type.get(task["task_type"], 0) + 1
    return {"predictions": total, "by_type": by_type, "splits": sorted(splits), "predictor_version": predictor_version, "dds_called": False}


def main() -> None:
    p = argparse.ArgumentParser(description="Blind non-DDS baseline predictor for the DDS learning project")
    p.add_argument("--tasks", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--splits", nargs="+", required=True)
    p.add_argument("--predictor-version", default="bridge-baseline-v0.1")
    args = p.parse_args()
    result = generate(Path(args.tasks), Path(args.out), set(args.splits), args.predictor_version)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
