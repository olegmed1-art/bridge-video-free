from __future__ import annotations

"""Legal card-play reconstruction for line-bearing DDS tasks.

This module does not call DDS. Its job is to make a blind proposed line
machine-checkable before the line is exposed to a solver. It validates card
ownership, turn order, follow-suit obligations, trick winners and produces a
position snapshot after every card. A later DDS adapter can evaluate those
snapshots without inventing a line that the predictor never supplied.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable

SEATS = "NESW"
SUITS = "SHDC"
RANKS = "AKQJT98765432"
RANK_VALUE = {rank: 14 - index for index, rank in enumerate(RANKS)}
NT = 4


class PlayLineError(ValueError):
    """Raised when a proposed line is illegal or inconsistent with the task."""


@dataclass(frozen=True)
class Card:
    suit: int
    rank: str

    @property
    def token(self) -> str:
        return f"{SUITS[self.suit]}{self.rank}"


def normalize_card(value: str) -> Card:
    text = str(value).strip().upper().replace("10", "T")
    if len(text) != 2 or text[0] not in SUITS or text[1] not in RANKS:
        raise PlayLineError(f"Bad card token: {value!r}; expected SA, H7, DT, ...")
    return Card(SUITS.index(text[0]), text[1])


def parse_deal(pbn: str, *, require_full: bool = True) -> dict[int, list[set[str]]]:
    """Parse a complete deal or a validated partial remaining-card position."""
    text = str(pbn).strip()
    if len(text) < 3 or text[1] != ":" or text[0].upper() not in SEATS:
        raise PlayLineError(f"Bad PBN deal: {pbn!r}")
    start = SEATS.index(text[0].upper())
    raw_hands = text[2:].split()
    if len(raw_hands) != 4:
        raise PlayLineError("PBN position must contain four hands")

    hands: dict[int, list[set[str]]] = {}
    seen: set[str] = set()
    hand_counts: dict[int, int] = {}
    for offset, raw_hand in enumerate(raw_hands):
        raw_suits = raw_hand.split(".")
        if len(raw_suits) != 4:
            raise PlayLineError(f"Bad PBN hand: {raw_hand!r}")
        seat = (start + offset) % 4
        hand: list[set[str]] = []
        count = 0
        for suit, raw_cards in enumerate(raw_suits):
            cards: set[str] = set()
            for rank in raw_cards:
                if rank not in RANKS:
                    raise PlayLineError(f"Bad rank {rank!r} in {raw_hand!r}")
                token = f"{SUITS[suit]}{rank}"
                if token in seen:
                    raise PlayLineError(f"Duplicate card in position: {token}")
                seen.add(token)
                cards.add(rank)
                count += 1
            hand.append(cards)
        if count > 13:
            raise PlayLineError(f"Seat {SEATS[seat]} has {count} cards, maximum is 13")
        if require_full and count != 13:
            raise PlayLineError(f"Seat {SEATS[seat]} has {count} cards, expected 13")
        hand_counts[seat] = count
        hands[seat] = hand

    if require_full and len(seen) != 52:
        raise PlayLineError(f"Deal contains {len(seen)} unique cards, expected 52")
    if not require_full and len(seen) > 52:
        raise PlayLineError(f"Position contains {len(seen)} unique cards, maximum is 52")
    if not require_full and max(hand_counts.values(), default=0) - min(hand_counts.values(), default=0) > 1:
        raise PlayLineError(
            f"Impossible remaining-card counts by seat: "
            f"{ {SEATS[seat]: count for seat, count in sorted(hand_counts.items())} }"
        )
    return hands


def render_deal(hands: dict[int, list[set[str]]]) -> str:
    def ordered(cards: set[str]) -> str:
        return "".join(rank for rank in RANKS if rank in cards)

    return "N:" + " ".join(
        ".".join(ordered(hands[seat][suit]) for suit in range(4))
        for seat in range(4)
    )


def _trick_winner(trick: list[tuple[int, Card]], trump: int) -> int:
    if len(trick) != 4:
        raise PlayLineError("A trick winner can be calculated only after four cards")
    led_suit = trick[0][1].suit
    trump_cards = [item for item in trick if trump != NT and item[1].suit == trump]
    eligible = trump_cards if trump_cards else [item for item in trick if item[1].suit == led_suit]
    return max(eligible, key=lambda item: RANK_VALUE[item[1].rank])[0]


def _actor(seat: int, declarer: int) -> str:
    return "declarer" if seat in {declarer, (declarer + 2) % 4} else "defense"


def _snapshot(
    *,
    hands: dict[int, list[set[str]]],
    current_trick: list[tuple[int, Card]],
    next_seat: int,
    trick_leader: int,
    completed_tricks: int,
    declarer_tricks: int,
    defense_tricks: int,
    declarer: int,
    trump: int,
    card_index: int,
) -> dict:
    payload = {
        "card_index": card_index,
        "remaining_deal": render_deal(hands),
        "remaining_card_counts": {
            SEATS[seat]: sum(len(cards) for cards in hands[seat]) for seat in range(4)
        },
        "next_seat": next_seat,
        "next_seat_name": SEATS[next_seat],
        "next_actor": _actor(next_seat, declarer),
        "trick_leader": trick_leader,
        "trick_leader_name": SEATS[trick_leader],
        "current_trick": [
            {"seat": seat, "seat_name": SEATS[seat], "card": card.token}
            for seat, card in current_trick
        ],
        "completed_tricks": completed_tricks,
        "declarer_tricks": declarer_tricks,
        "defense_tricks": defense_tricks,
        "declarer": declarer,
        "trump": trump,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["position_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return payload


def replay_line(
    *,
    deal: str,
    declarer: int,
    trump: int,
    cards: Iterable[str],
    opening_leader: int | None = None,
) -> dict:
    """Validate and replay a complete or partial card sequence.

    The input is a full play-order sequence, including cards of both sides. The
    first player defaults to declarer's LHO. Returned snapshots include the
    initial position and every post-card position.
    """
    declarer = int(declarer)
    trump = int(trump)
    if declarer not in range(4):
        raise PlayLineError("declarer must be 0..3 (N,E,S,W)")
    if trump not in range(5):
        raise PlayLineError("trump must be 0..4 (S,H,D,C,NT)")

    hands = parse_deal(deal, require_full=True)
    next_seat = (declarer + 1) % 4 if opening_leader is None else int(opening_leader)
    if next_seat not in range(4):
        raise PlayLineError("opening_leader must be 0..3")
    trick_leader = next_seat
    current_trick: list[tuple[int, Card]] = []
    completed_tricks = declarer_tricks = defense_tricks = 0
    played: list[dict] = []
    snapshots = [
        _snapshot(
            hands=hands,
            current_trick=current_trick,
            next_seat=next_seat,
            trick_leader=trick_leader,
            completed_tricks=completed_tricks,
            declarer_tricks=declarer_tricks,
            defense_tricks=defense_tricks,
            declarer=declarer,
            trump=trump,
            card_index=0,
        )
    ]

    for index, raw in enumerate(cards, 1):
        card = normalize_card(raw)
        seat = next_seat
        if card.rank not in hands[seat][card.suit]:
            raise PlayLineError(
                f"Card {card.token} at index {index} is not held by {SEATS[seat]}"
            )
        if current_trick:
            led_suit = current_trick[0][1].suit
            if card.suit != led_suit and hands[seat][led_suit]:
                raise PlayLineError(
                    f"Revoke at index {index}: {SEATS[seat]} played {card.token} "
                    f"while still holding {SUITS[led_suit]}"
                )

        hands[seat][card.suit].remove(card.rank)
        current_trick.append((seat, card))
        played_item = {
            "card_index": index,
            "seat": seat,
            "seat_name": SEATS[seat],
            "actor": _actor(seat, declarer),
            "card": card.token,
            "trick_number": completed_tricks + 1,
        }

        if len(current_trick) == 4:
            winner = _trick_winner(current_trick, trump)
            completed_tricks += 1
            if _actor(winner, declarer) == "declarer":
                declarer_tricks += 1
            else:
                defense_tricks += 1
            played_item["trick_winner"] = winner
            played_item["trick_winner_name"] = SEATS[winner]
            current_trick = []
            trick_leader = winner
            next_seat = winner
        else:
            next_seat = (seat + 1) % 4

        played.append(played_item)
        snapshots.append(
            _snapshot(
                hands=hands,
                current_trick=current_trick,
                next_seat=next_seat,
                trick_leader=trick_leader,
                completed_tricks=completed_tricks,
                declarer_tricks=declarer_tricks,
                defense_tricks=defense_tricks,
                declarer=declarer,
                trump=trump,
                card_index=index,
            )
        )

    return {
        "legal": True,
        "cards_played": len(played),
        "completed_tricks": completed_tricks,
        "declarer_tricks": declarer_tricks,
        "defense_tricks": defense_tricks,
        "next_seat": next_seat,
        "next_actor": _actor(next_seat, declarer),
        "played": played,
        "snapshots": snapshots,
        "line_sha256": hashlib.sha256(
            " ".join(item["card"] for item in played).encode("utf-8")
        ).hexdigest(),
    }


def validate_prediction_line(task: dict, prediction: dict, *, require_nonempty: bool = False) -> dict:
    line = prediction.get("line")
    if line is None:
        line = prediction.get("principal_variation")
    if line is None:
        line = []
    if not isinstance(line, list):
        raise PlayLineError("Prediction line must be a list of card tokens")
    if require_nonempty and not line:
        raise PlayLineError("A non-empty legal card line is required for this task")
    return replay_line(
        deal=task["deal"],
        declarer=int(task["declarer"]),
        trump=int(task["strain"]),
        opening_leader=task.get("leader", (int(task["declarer"]) + 1) % 4),
        cards=line,
    )
