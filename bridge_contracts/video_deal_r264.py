"""Canonical fail-closed contract for bridge deals recognized from video.

Observed card identities are preserved exactly. A fourth hand may be derived
only when exactly three complete, disjoint 13-card hands were observed. The
derived cards remain explicitly separated from visual evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

BRIDGE_VIDEO_DEAL_CONTRACT_VERSION = "bridge-video-deal-v5"
SEATS = ("N", "E", "S", "W")
SUIT_ORDER = {"S": 0, "H": 1, "D": 2, "C": 3}
RANK_ORDER = {rank: idx for idx, rank in enumerate("AKQJT98765432")}
UNICODE_SUITS = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}
FULL_DECK = frozenset(rank + suit for suit in SUIT_ORDER for rank in RANK_ORDER)


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
    derivations: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        derived_by_seat = {
            str(item.get("seat")): set(item.get("computed_cards") or [])
            for item in self.derivations
            if isinstance(item, dict)
        }
        return {
            "contract_version": BRIDGE_VIDEO_DEAL_CONTRACT_VERSION,
            "hands": {seat: self.hands[seat].to_dict() for seat in SEATS},
            "card_provenance": {
                seat: {
                    "observed_cards": [
                        card for card in self.hands[seat].cards if card not in derived_by_seat.get(seat, set())
                    ],
                    "derived_cards": [
                        card for card in self.hands[seat].cards if card in derived_by_seat.get(seat, set())
                    ],
                }
                for seat in SEATS
            },
            "derivations": [dict(item) for item in self.derivations],
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


def canonicalize_video_deal(
    payload: Any,
    *,
    derive_fourth_hand: bool = False,
) -> CanonicalVideoDeal:
    """Normalize recognizer/reconstruction output with explicit uncertainty.

    Accepted input is ``{"hands": {"N": [...], "E": [...], ...}}``. Seats may
    be omitted. By default an omitted seat is represented as 13 unknown cards.

    ``derive_fourth_hand=True`` permits exactly one operation: complementing
    three complete visual hands to one wholly absent fourth hand. Partial hands
    and every other shape fail closed.
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

    derivations: tuple[dict[str, Any], ...] = ()
    if derive_fourth_hand:
        complete_seats = [seat for seat in SEATS if len(hands[seat].cards) == 13]
        absent_seats = [seat for seat in SEATS if len(hands[seat].cards) == 0]
        if (
            len(complete_seats) != 3
            or len(absent_seats) != 1
            or len(observed_cards) != 39
        ):
            raise BridgeVideoDealContractError(
                "fourth-hand derivation requires exactly three complete hands and one absent hand"
            )
        missing_seat = absent_seats[0]
        computed = tuple(sorted(FULL_DECK - observed_cards, key=_card_sort_key))
        if len(computed) != 13:
            raise BridgeVideoDealContractError("deck complement is not exactly 13 cards")
        hands[missing_seat] = CanonicalHand(cards=computed, unknown_count=0)
        derivations = (
            {
                "type": "DECK_COMPLEMENT_FROM_THREE_VISUAL_HANDS",
                "seat": missing_seat,
                "source_seats": complete_seats,
                "observed_card_count": 39,
                "computed_cards": list(computed),
                "provenance": "INFERRED_DECK_COMPLEMENT",
            },
        )

    output_cards = {card for hand in hands.values() for card in hand.cards}
    expected_output = FULL_DECK if derivations else observed_cards
    if output_cards != expected_output:
        raise BridgeVideoDealContractError("canonicalization changed observed card identities")

    return CanonicalVideoDeal(hands=hands, derivations=derivations)


__all__ = [
    "BRIDGE_VIDEO_DEAL_CONTRACT_VERSION",
    "BridgeVideoDealContractError",
    "CanonicalHand",
    "CanonicalVideoDeal",
    "FULL_DECK",
    "SEATS",
    "canonicalize_video_deal",
]
