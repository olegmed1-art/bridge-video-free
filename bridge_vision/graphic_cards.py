"""Fail-closed composition boundary for graphic bridge-card observations.

A pixel-specific recognizer may propose separate rank and suit observations.
This module composes a card only when both signals are explicit and confident.
It never derives a missing rank/suit and never assigns a seat; native_cards owns
seat geometry and duplicate/conflict rejection.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

GRAPHIC_CARD_BACKEND_VERSION = "bridge-graphic-cards-v1"
_RANK = re.compile(r"^(10|[2-9AKQJT])$", re.IGNORECASE)
_SUITS = {"S": "S", "H": "H", "D": "D", "C": "C", "♠": "S", "♥": "H", "♦": "D", "♣": "C"}
GraphicRunner = Callable[[Path], Mapping[str, Any]]


def _rank(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if not _RANK.fullmatch(text):
        return None
    return "T" if text == "10" else text


def _suit(value: Any) -> str | None:
    return _SUITS.get(str(value or "").strip().upper())


def _confidence(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if 0.0 <= result <= 1.0 else None


class GraphicCardBackend:
    """Compose separate visual rank+suit signals into native observations."""

    def __init__(self, runner: GraphicRunner, *, min_rank_confidence: float = 0.90, min_suit_confidence: float = 0.90):
        if not 0.0 <= min_rank_confidence <= 1.0 or not 0.0 <= min_suit_confidence <= 1.0:
            raise ValueError("graphic confidence threshold outside [0,1]")
        self.runner = runner
        self.min_rank_confidence = float(min_rank_confidence)
        self.min_suit_confidence = float(min_suit_confidence)

    def __call__(self, frame: Path) -> Mapping[str, Any]:
        payload = self.runner(frame)
        table = payload.get("table_region")
        candidates = payload.get("candidates") or []
        if not isinstance(table, Mapping):
            raise ValueError("graphic table_region must be an object")
        if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
            raise ValueError("graphic candidates must be an array")
        cards: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for index, raw in enumerate(candidates):
            if not isinstance(raw, Mapping):
                raise ValueError("graphic candidate must be an object")
            rank, suit = _rank(raw.get("rank")), _suit(raw.get("suit"))
            rank_conf = _confidence(raw.get("rank_confidence"))
            suit_conf = _confidence(raw.get("suit_confidence"))
            box = raw.get("box")
            if rank is None or suit is None:
                rejected.append({"index": index, "reason": "INCOMPLETE_OR_INVALID_CARD", "box": dict(box) if isinstance(box, Mapping) else None})
                continue
            if rank_conf is None or suit_conf is None:
                rejected.append({"index": index, "reason": "INVALID_CONFIDENCE"})
                continue
            if rank_conf < self.min_rank_confidence or suit_conf < self.min_suit_confidence:
                rejected.append({"index": index, "card": rank + suit, "reason": "LOW_GRAPHIC_CONFIDENCE", "rank_confidence": rank_conf, "suit_confidence": suit_conf, "box": dict(box) if isinstance(box, Mapping) else None})
                continue
            if not isinstance(box, Mapping):
                rejected.append({"index": index, "card": rank + suit, "reason": "INVALID_BOX"})
                continue
            cards.append({"card": rank + suit, "confidence": min(rank_conf, suit_conf), "box": dict(box)})
        return {"table_region": dict(table), "cards": cards, "graphic_evidence": {"backend_version": GRAPHIC_CARD_BACKEND_VERSION, "candidate_count": len(candidates), "accepted_card_count": len(cards), "rejected": rejected, "min_rank_confidence": self.min_rank_confidence, "min_suit_confidence": self.min_suit_confidence}}


__all__ = ["GRAPHIC_CARD_BACKEND_VERSION", "GraphicCardBackend"]
