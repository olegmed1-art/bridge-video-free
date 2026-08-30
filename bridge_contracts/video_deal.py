"""Canonical fail-closed contract for bridge deals recognized from video.

Observed card identities are preserved exactly. Missing cards stay unknown by
default. When an upstream reconstruction step explicitly requests it, the
fourth hand may be computed from three complete 13-card hands; that computation
is recorded separately and never masquerades as visual observation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

BRIDGE_VIDEO_DEAL_CONTRACT_VERSION = "bridge-video-deal-v3"
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


def _derive_fourth_hand(
    hands: dict[str, CanonicalHand],
    observed_cards: set[str],
) -> tuple[dict[str, CanonicalHand], tuple[dict[str, Any], ...], set[str]]:
    complete = [seat for seat in SEATS if len(hands[seat].cards) == 13]
    if len(complete) != 3:
        return hands, (), set()

    target = next(seat for seat in SEATS if seat not in complete)
    complete_cards = {card for seat in complete for card in hands[seat].cards}
    if len(complete_cards) != 39:
        raise BridgeVideoDealContractError("three complete hands do not contain 39 unique cards")

    remaining = set(FULL_DECK) - complete_cards
    if len(remaining) != 13:
        raise BridgeVideoDealContractError("fourth-hand derivation did not produce exactly 13 cards")

    target_observed = set(hands[target].cards)
    if not target_observed.issubset(remaining):
        raise BridgeVideoDealContractError("observed fourth-hand cards conflict with derived hand")

    computed = remaining - target_observed
    cards = tuple(sorted(remaining, key=_card_sort_key))
    updated = dict(hands)
    updated[target] = CanonicalHand(cards=cards, unknown_count=0)

    derivation = {
        "seat": target,
        "method": "deck_subtraction_from_three_complete_hands",
        "provenance_class": "DERIVED",
        "evidence_basis": "39_unique_cards_in_three_complete_observed_hands",
        "from_seats": list(complete),
        "observed_cards_preserved": sorted(target_observed, key=_card_sort_key),
        "computed_cards": sorted(computed, key=_card_sort_key),
        "confidence": {
            "logical_complement": 1.0,
            "source_observation_floor": None,
        },
    }
    return updated, (derivation,), computed


def canonicalize_video_deal(
    payload: Any,
    *,
    derive_fourth_hand: bool = False,
) -> CanonicalVideoDeal:
    """Normalize recognizer/reconstruction output with explicit uncertainty.

    Accepted input is ``{"hands": {"N": [...], "E": [...], ...}}``. Seats may
    be omitted. By default an omitted seat is represented as 13 unknown cards.

    ``derive_fourth_hand=True`` implements the reconstruction rule from the
    school's Video Analysis 3.1 FREE standard: if exactly three hands are
    complete, the remaining hand may be computed by deck subtraction, but the
    computed card identities are explicitly recorded in ``derivations``.
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
    computed_cards: set[str] = set()
    if derive_fourth_hand:
        hands, derivations, computed_cards = _derive_fourth_hand(hands, observed_cards)

    output_cards = {card for hand in hands.values() for card in hand.cards}
    expected_cards = observed_cards | computed_cards
    if output_cards != expected_cards:
        raise BridgeVideoDealContractError("canonicalization changed card identities outside explicit derivation")

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
