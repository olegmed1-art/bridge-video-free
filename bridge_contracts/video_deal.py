"""Canonical fail-closed contract for bridge deals recognized from video.

The contract preserves only card identities explicitly observed by an upstream
recognizer. Missing cards remain unknown. In particular, it never reconstructs
an unseen hand by subtracting known cards from a 52-card deck.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

BRIDGE_VIDEO_DEAL_CONTRACT_VERSION = "bridge-video-deal-v1"
SEATS = ("N", "E", "S", "W")
SUIT_ORDER = {"S": 0, "H": 1, "D": 2, "C": 3}
RANK_ORDER = {rank: idx for idx, rank in enumerate("AKQJT98765432")}
UNICODE_SUITS = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}


class BridgeVideoDealContractError(ValueError):
    pass


@dataclass(frozen=True)
class CanonicalHand:
    cards: tuple[str, ...]
    unknown_count: int

    def to_dict(self) -> dict[str, Any]:
        return {"cards": list(self.cards), "unknown_count": self.unknown_count}


@dataclass(frozen=True)
class CanonicalVideoDeal:
    hands: dict[str, CanonicalHand]

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": BRIDGE_VIDEO_DEAL_CONTRACT_VERSION,
            "hands": {seat: self.hands[seat].to_dict() for seat in SEATS},
        }


def _normalize_card(value: Any) -> str:
    if not isinstance(value, str):
        raise BridgeVideoDealContractError("card must be a string")
    card = value.strip().upper()
    for symbol, suit in UNICODE_SUITS.items():
        card = card.replace(symbol, suit)
    if card.startswith("10"):
        card = "T" + card[2:]
    if len(card) != 2:
        raise BridgeVideoDealContractError(f"invalid card: {value!r}")
    rank, suit = card[0], card[1]
    if rank not in RANK_ORDER or suit not in SUIT_ORDER:
        raise BridgeVideoDealContractError(f"invalid card: {value!r}")
    return rank + suit


def _card_sort_key(card: str) -> tuple[int, int]:
    return (SUIT_ORDER[card[1]], RANK_ORDER[card[0]])


def canonicalize_video_deal(payload: Any) -> CanonicalVideoDeal:
    """Normalize recognizer output without inventing unobserved card identities.

    Accepted input is ``{"hands": {"N": [...], "E": [...], ...}}``. Seats may
    be omitted. An omitted seat is represented as 13 unknown cards, not inferred
    from cards seen in the other three hands.
    """

    if not isinstance(payload, Mapping):
        raise BridgeVideoDealContractError("deal payload must be an object")
    hands_raw = payload.get("hands")
    if not isinstance(hands_raw, Mapping):
        raise BridgeVideoDealContractError("hands must be an object")

    unknown_seats = set(hands_raw) - set(SEATS)
    if unknown_seats:
        raise BridgeVideoDealContractError(
            "unsupported seat(s): " + ", ".join(sorted(str(x) for x in unknown_seats))
        )

    seen_cards: dict[str, str] = {}
    hands: dict[str, CanonicalHand] = {}
    observed_cards: set[str] = set()

    for seat in SEATS:
        raw_cards = hands_raw.get(seat, [])
        if not isinstance(raw_cards, (list, tuple)):
            raise BridgeVideoDealContractError(f"hand {seat} must be an array")
        normalized = [_normalize_card(card) for card in raw_cards]
        if len(normalized) > 13:
            raise BridgeVideoDealContractError(f"hand {seat} has more than 13 cards")
        if len(set(normalized)) != len(normalized):
            raise BridgeVideoDealContractError(f"hand {seat} contains duplicate cards")

        for card in normalized:
            previous_seat = seen_cards.get(card)
            if previous_seat is not None:
                raise BridgeVideoDealContractError(
                    f"card {card} appears in both {previous_seat} and {seat}"
                )
            seen_cards[card] = seat
            observed_cards.add(card)

        cards = tuple(sorted(normalized, key=_card_sort_key))
        hands[seat] = CanonicalHand(cards=cards, unknown_count=13 - len(cards))

    output_cards = {card for hand in hands.values() for card in hand.cards}
    if output_cards != observed_cards:
        raise BridgeVideoDealContractError("canonicalization changed observed card identities")

    return CanonicalVideoDeal(hands=hands)


__all__ = [
    "BRIDGE_VIDEO_DEAL_CONTRACT_VERSION",
    "BridgeVideoDealContractError",
    "CanonicalHand",
    "CanonicalVideoDeal",
    "SEATS",
    "canonicalize_video_deal",
]
