"""Deterministic, fail-closed deal-world generation for bidding search.

The generator completes one known 13-card hand into full 52-card deals.  A world
is called constraint-compatible only when every supplied hard HCP/suit-length
constraint passes.  Auction inference belongs upstream and must be supplied as
explicit constraints; this module never invents bidding meanings.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any


SEATS = ("N", "E", "S", "W")
SUITS = ("S", "H", "D", "C")
RANKS = "AKQJT98765432"
HCP = {"A": 4, "K": 3, "Q": 2, "J": 1}
DECK = tuple(f"{suit}{rank}" for suit in SUITS for rank in RANKS)


class WorldGenerationError(ValueError):
    pass


@dataclass(frozen=True)
class Range:
    minimum: int
    maximum: int

    @classmethod
    def parse(cls, raw: Any, *, lower: int, upper: int, label: str) -> "Range":
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise WorldGenerationError(f"{label} must be [minimum, maximum]")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in raw):
            raise WorldGenerationError(f"{label} bounds must be integers")
        minimum, maximum = raw
        if minimum < lower or maximum > upper or minimum > maximum:
            raise WorldGenerationError(f"{label} bounds are invalid")
        return cls(minimum, maximum)

    def contains(self, value: int) -> bool:
        return self.minimum <= value <= self.maximum


@dataclass(frozen=True)
class SeatConstraints:
    hcp: Range = field(default_factory=lambda: Range(0, 37))
    suits: dict[str, Range] = field(default_factory=dict)


@dataclass(frozen=True)
class WorldConstraints:
    seats: dict[str, SeatConstraints] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: dict[str, Any] | None) -> "WorldConstraints":
        if raw is None:
            return cls()
        if not isinstance(raw, dict) or set(raw) - {"seats"}:
            raise WorldGenerationError("constraints may contain only seats")
        raw_seats = raw.get("seats", {})
        if not isinstance(raw_seats, dict):
            raise WorldGenerationError("constraints.seats must be an object")
        seats: dict[str, SeatConstraints] = {}
        for raw_seat, value in raw_seats.items():
            seat = str(raw_seat).upper()
            if seat not in SEATS or not isinstance(value, dict):
                raise WorldGenerationError("constraint seat is invalid")
            if set(value) - {"hcp", "suits"}:
                raise WorldGenerationError(f"unsupported constraint for seat {seat}")
            hcp = Range.parse(value.get("hcp", [0, 37]), lower=0, upper=37, label=f"{seat}.hcp")
            raw_suits = value.get("suits", {})
            if not isinstance(raw_suits, dict):
                raise WorldGenerationError(f"{seat}.suits must be an object")
            suits: dict[str, Range] = {}
            for raw_suit, suit_range in raw_suits.items():
                suit = str(raw_suit).upper()
                if suit not in SUITS:
                    raise WorldGenerationError(f"{seat} suit is invalid")
                suits[suit] = Range.parse(
                    suit_range, lower=0, upper=13, label=f"{seat}.{suit}"
                )
            seats[seat] = SeatConstraints(hcp=hcp, suits=suits)
        return cls(seats=seats)


def parse_hand_pbn(hand_pbn: str) -> tuple[str, ...]:
    parts = str(hand_pbn or "").strip().replace("_", ".").split(".")
    if len(parts) != 4:
        raise WorldGenerationError("known hand must contain four PBN suits")
    cards: list[str] = []
    for suit, raw_ranks in zip(SUITS, parts):
        ranks = "" if raw_ranks in {"-", "—"} else raw_ranks.upper()
        if any(rank not in RANKS for rank in ranks) or len(set(ranks)) != len(ranks):
            raise WorldGenerationError("known hand contains invalid or duplicate ranks")
        cards.extend(f"{suit}{rank}" for rank in ranks)
    if len(cards) != 13 or len(set(cards)) != 13:
        raise WorldGenerationError("known hand must contain exactly 13 unique cards")
    return tuple(cards)


def _hand_hcp(cards: tuple[str, ...] | list[str]) -> int:
    return sum(HCP.get(card[1], 0) for card in cards)


def _suit_lengths(cards: tuple[str, ...] | list[str]) -> dict[str, int]:
    return {suit: sum(card[0] == suit for card in cards) for suit in SUITS}


def _compatible(seat: str, cards: list[str], constraints: WorldConstraints) -> bool:
    rule = constraints.seats.get(seat)
    if rule is None:
        return True
    if not rule.hcp.contains(_hand_hcp(cards)):
        return False
    lengths = _suit_lengths(cards)
    return all(length_range.contains(lengths[suit]) for suit, length_range in rule.suits.items())


def _hand_to_pbn(cards: tuple[str, ...] | list[str]) -> str:
    by_suit = {suit: [] for suit in SUITS}
    for card in cards:
        by_suit[card[0]].append(card[1])
    return ".".join("".join(rank for rank in RANKS if rank in by_suit[suit]) for suit in SUITS)


def _world_fingerprint(hands: dict[str, str]) -> str:
    canonical = json.dumps(hands, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _constraints_fingerprint(constraints: dict[str, Any] | None) -> str:
    canonical = json.dumps(constraints or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generate_worlds(
    *,
    known_seat: str,
    known_hand_pbn: str,
    constraints: dict[str, Any] | None,
    count: int,
    seed: int,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    """Generate unique full deals satisfying only explicit hard constraints."""
    seat = str(known_seat or "").upper()
    if seat not in SEATS:
        raise WorldGenerationError("known_seat is invalid")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10_000:
        raise WorldGenerationError("count must be between 1 and 10000")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise WorldGenerationError("seed must be an integer")
    known = parse_hand_pbn(known_hand_pbn)
    rules = WorldConstraints.parse(constraints)
    if not _compatible(seat, list(known), rules):
        raise WorldGenerationError("known hand violates supplied constraints")

    limit = max_attempts if max_attempts is not None else max(count * 200, count)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < count:
        raise WorldGenerationError("max_attempts must be an integer >= count")

    rng = random.Random(seed)
    remaining = [card for card in DECK if card not in known]
    other_seats = [candidate for candidate in SEATS if candidate != seat]
    worlds: list[dict[str, Any]] = []
    fingerprints: set[str] = set()
    attempts = 0
    while len(worlds) < count and attempts < limit:
        attempts += 1
        shuffled = remaining.copy()
        rng.shuffle(shuffled)
        hands_cards: dict[str, list[str]] = {seat: list(known)}
        for index, other in enumerate(other_seats):
            hands_cards[other] = shuffled[index * 13:(index + 1) * 13]
        if not all(_compatible(candidate, cards, rules) for candidate, cards in hands_cards.items()):
            continue
        hands = {candidate: _hand_to_pbn(hands_cards[candidate]) for candidate in SEATS}
        fingerprint = _world_fingerprint(hands)
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        worlds.append({
            "world_index": len(worlds),
            "hands": hands,
            "pbn": "N:" + " ".join(hands[candidate] for candidate in SEATS),
            "fingerprint": fingerprint,
        })

    return {
        "generator": "explicit-constraint-deal-sampler",
        "generator_version": "v1",
        "seed": seed,
        "requested": count,
        "attempts": attempts,
        "accepted": len(worlds),
        "complete": len(worlds) == count,
        "constraint_class": "EXPLICIT_HARD_CONSTRAINTS" if rules.seats else "KNOWN_HAND_ONLY",
        "constraints_sha256": _constraints_fingerprint(constraints),
        "engine": "WORLD_GENERATOR",
        "fallback_used": False,
        "worlds": worlds,
    }


__all__ = ["WorldGenerationError", "generate_worlds", "parse_hand_pbn"]
