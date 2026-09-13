"""School-owned four-seat card detector boundary.

This module owns the geometry/evidence logic after a low-level glyph backend has
produced card observations. It is platform-agnostic: no BBO/RealBridge layout is
hard-coded. Seat assignment comes from normalized card centres relative to an
explicit table region. Unknown or ambiguous observations are dropped, never
promoted to FACT.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from bridge_contracts.video_deal import canonicalize_video_deal

CARD_DETECTOR_VERSION = "bridge-native-cards-v1"
RawBackend = Callable[[Path], Mapping[str, Any]]


class NativeCardDetectorError(ValueError):
    pass


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0


@dataclass(frozen=True)
class CardObservation:
    card: str
    confidence: float
    box: Box


@dataclass(frozen=True)
class TableRegion:
    x: float
    y: float
    w: float
    h: float


def _box(raw: Any, field: str) -> Box:
    if not isinstance(raw, Mapping):
        raise NativeCardDetectorError(f"{field} must be an object")
    try:
        values = [float(raw[k]) for k in ("x", "y", "w", "h")]
    except (KeyError, TypeError, ValueError) as exc:
        raise NativeCardDetectorError(f"invalid {field}") from exc
    x, y, w, h = values
    if w <= 0 or h <= 0:
        raise NativeCardDetectorError(f"invalid {field} size")
    return Box(x, y, w, h)


def _table(raw: Any) -> TableRegion:
    b = _box(raw, "table_region")
    return TableRegion(b.x, b.y, b.w, b.h)


def _seat_for(box: Box, table: TableRegion, *, dead_zone: float) -> str | None:
    nx = (box.cx - table.x) / table.w
    ny = (box.cy - table.y) / table.h
    if not 0.0 <= nx <= 1.0 or not 0.0 <= ny <= 1.0:
        return None

    dx = nx - 0.5
    dy = ny - 0.5
    if abs(dx) < dead_zone and abs(dy) < dead_zone:
        return None

    # Pick the dominant axis so corner observations do not get assigned by an
    # arbitrary platform-specific rectangle. This works for four-sided tables.
    if abs(dx) > abs(dy):
        return "E" if dx > 0 else "W"
    return "S" if dy > 0 else "N"


def _normalise_card(card: Any) -> str:
    # Reuse the canonical contract as the single validator/normalizer.
    deal = canonicalize_video_deal({"hands": {"N": [card]}}).to_dict()
    return deal["hands"]["N"]["cards"][0]


def observations_from_backend(
    payload: Mapping[str, Any],
    *,
    min_card_confidence: float = 0.80,
    seat_dead_zone: float = 0.08,
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    if not 0.0 <= min_card_confidence <= 1.0:
        raise ValueError("min_card_confidence outside [0,1]")
    if not 0.0 <= seat_dead_zone < 0.5:
        raise ValueError("seat_dead_zone outside [0,0.5)")
    if not isinstance(payload, Mapping):
        raise NativeCardDetectorError("backend payload must be an object")

    table = _table(payload.get("table_region"))
    raw_cards = payload.get("cards") or []
    if not isinstance(raw_cards, Sequence) or isinstance(raw_cards, (str, bytes)):
        raise NativeCardDetectorError("cards must be an array")

    hands: dict[str, list[str]] = {seat: [] for seat in ("N", "E", "S", "W")}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: dict[str, str] = {}

    for index, raw in enumerate(raw_cards):
        if not isinstance(raw, Mapping):
            raise NativeCardDetectorError("card observation must be an object")
        try:
            confidence = float(raw.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise NativeCardDetectorError("card confidence must be numeric") from exc
        if not 0.0 <= confidence <= 1.0:
            raise NativeCardDetectorError("card confidence outside [0,1]")
        box = _box(raw.get("box"), f"cards[{index}].box")
        try:
            card = _normalise_card(raw.get("card"))
        except Exception as exc:
            rejected.append({"index": index, "reason": "INVALID_CARD"})
            continue
        if confidence < min_card_confidence:
            rejected.append({"index": index, "card": card, "reason": "LOW_CONFIDENCE", "confidence": confidence})
            continue
        seat = _seat_for(box, table, dead_zone=seat_dead_zone)
        if seat is None:
            rejected.append({"index": index, "card": card, "reason": "AMBIGUOUS_SEAT", "confidence": confidence})
            continue
        previous = seen.get(card)
        if previous is not None and previous != seat:
            raise NativeCardDetectorError(f"card {card} assigned to both {previous} and {seat}")
        if card in hands[seat]:
            rejected.append({"index": index, "card": card, "reason": "DUPLICATE_OBSERVATION", "seat": seat})
            continue
        seen[card] = seat
        hands[seat].append(card)
        accepted.append({"index": index, "card": card, "seat": seat, "confidence": confidence})

    canonical = canonicalize_video_deal({"hands": hands}).to_dict()["hands"]
    normalized_hands = {seat: canonical[seat]["cards"] for seat in hands if canonical[seat]["cards"]}
    evidence = {
        "detector_version": CARD_DETECTOR_VERSION,
        "accepted": accepted,
        "rejected": rejected,
        "table_region": {"x": table.x, "y": table.y, "w": table.w, "h": table.h},
        "min_card_confidence": min_card_confidence,
        "seat_dead_zone": seat_dead_zone,
    }
    return normalized_hands, evidence


class NativeFourSeatCardDetector:
    """Adapter from a school-controlled glyph backend into BridgeVisionEngine."""

    def __init__(self, backend: RawBackend, *, min_card_confidence: float = 0.80, seat_dead_zone: float = 0.08):
        self.backend = backend
        self.min_card_confidence = float(min_card_confidence)
        self.seat_dead_zone = float(seat_dead_zone)

    def __call__(self, frame: Path) -> dict[str, Any]:
        payload = self.backend(frame)
        hands, evidence = observations_from_backend(
            payload,
            min_card_confidence=self.min_card_confidence,
            seat_dead_zone=self.seat_dead_zone,
        )
        accepted = evidence["accepted"]
        confidence = min((float(item["confidence"]) for item in accepted), default=0.0)
        return {"hands": hands, "confidence": confidence, "evidence": evidence}


__all__ = [
    "CARD_DETECTOR_VERSION",
    "NativeCardDetectorError",
    "NativeFourSeatCardDetector",
    "observations_from_backend",
]
