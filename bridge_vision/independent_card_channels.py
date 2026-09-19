"""Fail-closed composition of independent visual card channels.

This boundary never creates a card from rank+suit alone.  Rank, suit and a
separate full-card recognizer must agree, each must have distinct provenance,
and each must be stable on multiple source-bound frames.  Seat assignment is
left to :mod:`bridge_vision.native_cards` geometry.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

CHANNEL_BACKEND_VERSION = "bridge-independent-card-channels-v1"
MIN_CHANNEL_CONFIDENCE = 0.90
MIN_TEMPORAL_SUPPORT = 2
_RANK = re.compile(r"^(10|[2-9AKQJT])$", re.IGNORECASE)
_SUITS = {"S": "S", "H": "H", "D": "D", "C": "C", "♠": "S", "♥": "H", "♦": "D", "♣": "C"}
_CARD = re.compile(r"^(10|[2-9AKQJT])([SHDC♠♥♦♣])$", re.IGNORECASE)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ChannelRunner = Callable[[Path], Mapping[str, Any]]


def _rank(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if not _RANK.fullmatch(text):
        return None
    return "T" if text == "10" else text


def _suit(value: Any) -> str | None:
    return _SUITS.get(str(value or "").strip().upper())


def _card(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    match = _CARD.fullmatch(text)
    if not match:
        return None
    rank = "T" if match.group(1) == "10" else match.group(1)
    return rank + _SUITS[match.group(2)]


def _confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return confidence if 0.0 <= confidence <= 1.0 else None


def _frames(raw: Any) -> tuple[str, ...] | None:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return None
    frames = tuple(str(value) for value in raw)
    if len(set(frames)) != len(frames) or any(not _SHA256.fullmatch(value) for value in frames):
        return None
    return frames


class IndependentCardChannelBackend:
    """Accept only three-channel, temporally stable, mutually consistent cards."""

    shadow_only = True

    def __init__(
        self,
        runner: ChannelRunner,
        *,
        min_rank_confidence: float = MIN_CHANNEL_CONFIDENCE,
        min_suit_confidence: float = MIN_CHANNEL_CONFIDENCE,
        min_full_card_confidence: float = MIN_CHANNEL_CONFIDENCE,
        min_temporal_support: int = MIN_TEMPORAL_SUPPORT,
    ):
        thresholds = (min_rank_confidence, min_suit_confidence, min_full_card_confidence)
        if any(value < MIN_CHANNEL_CONFIDENCE or value > 1.0 for value in thresholds):
            raise ValueError("channel confidence thresholds cannot be lowered below 0.90")
        if min_temporal_support < MIN_TEMPORAL_SUPPORT:
            raise ValueError("temporal support cannot be lowered below two frames")
        self.runner = runner
        self.thresholds = tuple(float(value) for value in thresholds)
        self.min_temporal_support = int(min_temporal_support)

    def __call__(self, frame: Path) -> Mapping[str, Any]:
        payload = self.runner(frame)
        if not isinstance(payload, Mapping) or not isinstance(payload.get("table_region"), Mapping):
            raise ValueError("channel runner must return a table_region object")
        candidates = payload.get("candidates") or []
        if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
            raise ValueError("channel candidates must be an array")
        cards: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for index, raw in enumerate(candidates):
            if not isinstance(raw, Mapping):
                raise ValueError("channel candidate must be an object")
            rank, suit, full_card = _rank(raw.get("rank")), _suit(raw.get("suit")), _card(raw.get("full_card"))
            confidences = (
                _confidence(raw.get("rank_confidence")),
                _confidence(raw.get("suit_confidence")),
                _confidence(raw.get("full_card_confidence")),
            )
            sources = tuple(str(raw.get(f"{name}_source") or "").strip() for name in ("rank", "suit", "full_card"))
            channel_frames = raw.get("channel_frames")
            frames = (
                _frames(channel_frames.get("rank")) if isinstance(channel_frames, Mapping) else None,
                _frames(channel_frames.get("suit")) if isinstance(channel_frames, Mapping) else None,
                _frames(channel_frames.get("full_card")) if isinstance(channel_frames, Mapping) else None,
            )
            evidence = {
                "index": index,
                "box": dict(raw["box"]) if isinstance(raw.get("box"), Mapping) else None,
                "channels": {
                    "rank": {"value": rank, "confidence": confidences[0], "source": sources[0], "frames": list(frames[0] or ())},
                    "suit": {"value": suit, "confidence": confidences[1], "source": sources[1], "frames": list(frames[1] or ())},
                    "full_card": {"value": full_card, "confidence": confidences[2], "source": sources[2], "frames": list(frames[2] or ())},
                },
            }
            reason = None
            if rank is None or suit is None or full_card is None:
                reason = "MISSING_OR_INVALID_CHANNEL"
            elif any(confidence is None for confidence in confidences):
                reason = "INVALID_CHANNEL_CONFIDENCE"
            elif any(confidence < threshold for confidence, threshold in zip(confidences, self.thresholds)):
                reason = "LOW_CHANNEL_CONFIDENCE"
            elif any(not source for source in sources) or len(set(sources)) != 3:
                reason = "NON_INDEPENDENT_CHANNEL_PROVENANCE"
            elif any(values is None or len(values) < self.min_temporal_support for values in frames):
                reason = "INSUFFICIENT_TEMPORAL_EVIDENCE"
            elif rank + suit != full_card:
                reason = "CHANNEL_CONFLICT"
            elif not isinstance(raw.get("box"), Mapping):
                reason = "INVALID_BOX"
            if reason:
                evidence["reason"] = reason
                rejected.append(evidence)
                observations.append(evidence)
                continue
            evidence["reason"] = None
            observations.append(evidence)
            cards.append({
                "card": full_card,
                "confidence": min(confidences),
                "box": dict(raw["box"]),
                "channel_evidence_index": index,
            })
        return {
            "table_region": dict(payload["table_region"]),
            "cards": cards,
            "channel_evidence": {
                "schema": CHANNEL_BACKEND_VERSION,
                "result_scope": "SHADOW_ONLY",
                "production_activation_allowed": False,
                "candidate_count": len(candidates),
                "accepted_count": len(cards),
                "observations": observations,
                "rejected": rejected,
                "thresholds": {
                    "rank": self.thresholds[0], "suit": self.thresholds[1],
                    "full_card": self.thresholds[2], "temporal_support": self.min_temporal_support,
                },
            },
        }


__all__ = [
    "CHANNEL_BACKEND_VERSION", "MIN_CHANNEL_CONFIDENCE", "MIN_TEMPORAL_SUPPORT",
    "IndependentCardChannelBackend",
]
