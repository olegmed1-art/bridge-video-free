from __future__ import annotations

from dataclasses import dataclass

SEATS = ("N", "E", "S", "W")
SUITS = ("S", "H", "D", "C")
RANKS = "AKQJT98765432"


class DealValidationError(ValueError):
    pass


@dataclass(frozen=True)
class BridgeDeal:
    hands: dict[str, dict[str, str]]
    dealer: str = "N"
    vulnerability: str = "None"

    def validate(self) -> None:
        if set(self.hands) != set(SEATS):
            raise DealValidationError("exactly N,E,S,W hands are required")
        cards: list[str] = []
        for seat in SEATS:
            hand = self.hands[seat]
            if set(hand) != set(SUITS):
                raise DealValidationError(f"{seat}: exactly S,H,D,C suits are required")
            count = 0
            for suit in SUITS:
                ranks = hand[suit].upper().replace("10", "T").replace("-", "")
                if any(r not in RANKS for r in ranks) or len(set(ranks)) != len(ranks):
                    raise DealValidationError(f"{seat}: invalid or duplicate rank in {suit}")
                count += len(ranks)
                cards.extend(suit + r for r in ranks)
            if count != 13:
                raise DealValidationError(f"{seat}: expected 13 cards, got {count}")
        if len(cards) != 52 or len(set(cards)) != 52:
            raise DealValidationError("deal must contain 52 unique cards")
        if set(cards) != {s + r for s in SUITS for r in RANKS}:
            raise DealValidationError("deal is not a complete standard deck")

    def to_pbn(self) -> str:
        self.validate()
        parts = []
        for seat in SEATS:
            h = self.hands[seat]
            parts.append(".".join(h[s].upper().replace("10", "T").replace("-", "") for s in SUITS))
        return "N:" + " ".join(parts)
