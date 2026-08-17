from __future__ import annotations

"""A deterministic blind baseline that emits a legal multi-card principal line.

The policy is intentionally simple and contains no DDS/minimax call.  Its value
is experimental: every overclaim now has a concrete legal line that DDS can
refute at a precise prefix instead of being closed only as a structural guess.
"""

import argparse
import json
from pathlib import Path

import baseline_predictor as bp
from playline import Card, NT, RANKS, RANK_VALUE, SUITS, _trick_winner, parse_deal, replay_line


def _all_cards(hands: dict[int, list[set[str]]], seat: int) -> list[Card]:
    return [Card(suit, rank) for suit in range(4) for rank in hands[seat][suit]]


def _legal_cards(
    hands: dict[int, list[set[str]]],
    seat: int,
    trick: list[tuple[int, Card]],
) -> list[Card]:
    if trick:
        led = trick[0][1].suit
        if hands[seat][led]:
            return [Card(led, rank) for rank in hands[seat][led]]
    return _all_cards(hands, seat)


def _current_winner(trick: list[tuple[int, Card]], trump: int) -> int | None:
    if not trick:
        return None
    led = trick[0][1].suit
    trumps = [item for item in trick if trump != NT and item[1].suit == trump]
    eligible = trumps if trumps else [item for item in trick if item[1].suit == led]
    return max(eligible, key=lambda item: RANK_VALUE[item[1].rank])[0]


def _lead_score(card: Card, holding: set[str], trump: int) -> tuple:
    ordered = [rank for rank in RANKS if rank in holding]
    sequence_top = 0
    for left, right in zip(ordered, ordered[1:]):
        if RANK_VALUE[left] == RANK_VALUE[right] + 1 and card.rank == left:
            sequence_top = 1
            break
    is_trump = int(trump != NT and card.suit == trump)
    length = len(holding)
    # Prefer a sequence top, then a long non-trump suit.  The final rank key is
    # conservative: low cards before unsupported honours when no sequence exists.
    return (
        sequence_top,
        length - 2 * is_trump,
        -RANK_VALUE[card.rank] if not sequence_top else RANK_VALUE[card.rank],
        -card.suit,
    )


def _choose_card(
    hands: dict[int, list[set[str]]],
    seat: int,
    trick: list[tuple[int, Card]],
    trump: int,
) -> Card:
    legal = _legal_cards(hands, seat, trick)
    if not legal:
        raise ValueError(f"Seat {seat} has no legal cards")
    if not trick:
        return max(legal, key=lambda card: _lead_score(card, hands[seat][card.suit], trump))

    current = _current_winner(trick, trump)
    winning = []
    for card in legal:
        probe = trick + [(seat, card)]
        if _current_winner(probe, trump) == seat and current != seat:
            winning.append(card)
    if winning:
        # Win as cheaply as possible, preserving higher equals/entries.
        return min(winning, key=lambda card: (RANK_VALUE[card.rank], card.suit))
    # Otherwise discard/follow with the cheapest legal card.
    return min(legal, key=lambda card: (RANK_VALUE[card.rank], card.suit))


def generate_line(task: dict, *, cards_to_play: int = 16) -> list[str]:
    if cards_to_play < 1 or cards_to_play > 52:
        raise ValueError("cards_to_play must be 1..52")
    hands = parse_deal(task["deal"])
    declarer = int(task["declarer"])
    trump = int(task["strain"])
    next_seat = int(task.get("leader", (declarer + 1) % 4))
    trick: list[tuple[int, Card]] = []
    line = []

    for _ in range(cards_to_play):
        card = _choose_card(hands, next_seat, trick, trump)
        hands[next_seat][card.suit].remove(card.rank)
        trick.append((next_seat, card))
        line.append(card.token)
        if len(trick) == 4:
            next_seat = _trick_winner(trick, trump)
            trick = []
        else:
            next_seat = (next_seat + 1) % 4

    replay_line(
        deal=task["deal"],
        declarer=declarer,
        trump=trump,
        opening_leader=task.get("leader", (declarer + 1) % 4),
        cards=line,
    )
    return line


def prediction_for(task: dict, *, cards_to_play: int, predictor_version: str) -> dict:
    tricks, confidence, baseline_reason = bp.estimate_contract_tricks(task)
    line = generate_line(task, cards_to_play=cards_to_play)
    return {
        "task_id": task["task_id"],
        "tricks": tricks,
        "confidence": confidence,
        "reason": (
            f"{baseline_reason} A legal {len(line)}-card greedy principal line was fixed before DDS."
        ),
        "line": line,
        "line_policy": "greedy-cheapest-winner-v1",
        "line_cards": len(line),
        "predictor_version": predictor_version,
        "dds_called": False,
        "locked": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create locked blind legal line-bearing predictions")
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--splits", nargs="+", required=True)
    parser.add_argument("--cards", type=int, default=16)
    parser.add_argument("--predictor-version", default="bridge-line-baseline-v1")
    args = parser.parse_args()
    splits = set(args.splits)
    total = 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with Path(args.tasks).open("r", encoding="utf-8") as src, out.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            task = json.loads(line)
            if task.get("split") not in splits or task.get("task_type") != "contract_tricks":
                continue
            prediction = prediction_for(
                task,
                cards_to_play=args.cards,
                predictor_version=args.predictor_version,
            )
            dst.write(json.dumps(prediction, ensure_ascii=False, sort_keys=True) + "\n")
            total += 1
    print(json.dumps({
        "predictions": total,
        "cards_per_line": args.cards,
        "splits": sorted(splits),
        "predictor_version": args.predictor_version,
        "dds_called": False,
        "path": str(out),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
