"""Evidence-first native Bridge Vision engine.

The engine owns how observations from one or more school-controlled detectors
are validated and fused. It never infers hidden cards from layout assumptions,
never uses time proximity as deal identity, and fails closed on cross-seat card
conflicts. Platform-specific recognizers are plugins, not the architecture.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge_contracts.video_deal import SEATS, canonicalize_video_deal

NATIVE_ENGINE_VERSION = "bridge-vision-native-v2"
Detector = Callable[[Path], Mapping[str, Any]]


class BridgeVisionError(ValueError):
    pass


@dataclass(frozen=True)
class VisionCandidate:
    detector: str
    hands: dict[str, tuple[str, ...]]
    confidence: float
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "hands": {seat: list(cards) for seat, cards in self.hands.items()},
            "confidence": self.confidence,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class VisionResult:
    status: str
    deal: dict[str, Any] | None
    candidates: tuple[VisionCandidate, ...]
    conflicts: tuple[dict[str, Any], ...]
    engine_version: str = NATIVE_ENGINE_VERSION
    diagnostics: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_version": self.engine_version,
            "status": self.status,
            "deal": self.deal,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "conflicts": list(self.conflicts),
            "diagnostics": list(self.diagnostics),
        }


def _candidate(name: str, raw: Mapping[str, Any]) -> VisionCandidate:
    if not isinstance(raw, Mapping):
        raise BridgeVisionError(f"detector {name} returned non-object")
    hands_raw = raw.get("hands") or {}
    if not isinstance(hands_raw, Mapping):
        raise BridgeVisionError(f"detector {name} hands must be an object")
    unknown = set(hands_raw) - set(SEATS)
    if unknown:
        raise BridgeVisionError(f"detector {name} returned unsupported seats")

    try:
        confidence = float(raw.get("confidence", 1.0))
    except (TypeError, ValueError) as exc:
        raise BridgeVisionError(f"detector {name} confidence must be numeric") from exc
    if not 0.0 <= confidence <= 1.0:
        raise BridgeVisionError(f"detector {name} confidence outside [0,1]")

    # Reuse the canonical card validator but keep only the detector's observed cards.
    canonical = canonicalize_video_deal({"hands": dict(hands_raw)}).to_dict()["hands"]
    hands = {
        seat: tuple(canonical[seat]["cards"])
        for seat in SEATS
        if canonical[seat]["cards"]
    }
    evidence = raw.get("evidence") or {}
    if not isinstance(evidence, Mapping):
        raise BridgeVisionError(f"detector {name} evidence must be an object")
    return VisionCandidate(name, hands, confidence, dict(evidence))


class BridgeVisionEngine:
    """Native multi-detector fusion with explicit evidence and fail-closed conflicts."""

    def __init__(self, detectors: Mapping[str, Detector] | None = None, *, min_confidence: float = 0.60):
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence outside [0,1]")
        self._detectors = dict(detectors or {})
        self._validate_detector_modes()
        self.min_confidence = float(min_confidence)

    def _validate_detector_modes(self) -> None:
        modes = {bool(getattr(detector, "shadow_only", False)) for detector in self._detectors.values()}
        if len(modes) > 1:
            raise ValueError("shadow-only and canonical detectors cannot be mixed")

    @property
    def detector_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._detectors))

    @property
    def shadow_only(self) -> bool:
        return bool(self._detectors) and all(
            bool(getattr(detector, "shadow_only", False))
            for detector in self._detectors.values()
        )

    def register(self, name: str, detector: Detector) -> None:
        key = str(name or "").strip()
        if not key or key.startswith("legacy:"):
            raise ValueError("invalid native detector name")
        if key in self._detectors:
            raise ValueError(f"detector already registered: {key}")
        self._detectors[key] = detector
        try:
            self._validate_detector_modes()
        except ValueError:
            self._detectors.pop(key, None)
            raise

    def analyze_frame(self, frame: Path) -> VisionResult:
        candidates: list[VisionCandidate] = []
        detector_conflicts: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        for name, detector in sorted(self._detectors.items()):
            raw = detector(frame)
            if isinstance(raw, Mapping) and str(raw.get("status") or "").upper() == "CONFLICT":
                raw_conflicts = raw.get("conflicts") or []
                if not isinstance(raw_conflicts, (list, tuple)):
                    raise BridgeVisionError(f"detector {name} conflicts must be an array")
                if not raw_conflicts:
                    raise BridgeVisionError(f"detector {name} returned an empty conflict")
                for conflict in raw_conflicts:
                    if not isinstance(conflict, Mapping):
                        raise BridgeVisionError(f"detector {name} conflict must be an object")
                    detector_conflicts.append({**dict(conflict), "detector": name})
                evidence = raw.get("evidence") or {}
                diagnostics.append({
                    "detector": name,
                    "status": "CONFLICT",
                    "evidence": dict(evidence) if isinstance(evidence, Mapping) else {},
                })
                continue
            candidate = _candidate(name, raw)
            if candidate.confidence >= self.min_confidence and candidate.hands:
                candidates.append(candidate)
            else:
                diagnostics.append({
                    "detector": name,
                    "status": str(raw.get("status") or "REJECTED").upper(),
                    "confidence": candidate.confidence,
                    "evidence": dict(candidate.evidence),
                })

        if detector_conflicts:
            return VisionResult(
                "CONFLICT",
                None,
                tuple(candidates),
                tuple(detector_conflicts),
                diagnostics=tuple(diagnostics),
            )
        if not candidates:
            return VisionResult("UNAVAILABLE", None, (), (), diagnostics=tuple(diagnostics))

        # A card assigned to different seats by accepted detectors is a hard conflict.
        card_to_seat: dict[str, str] = {}
        card_sources: dict[str, list[str]] = {}
        conflicts: list[dict[str, Any]] = []
        merged: dict[str, set[str]] = {seat: set() for seat in SEATS}
        for candidate in candidates:
            for seat, cards in candidate.hands.items():
                for card in cards:
                    previous = card_to_seat.get(card)
                    if previous is not None and previous != seat:
                        conflicts.append({
                            "card": card,
                            "seats": sorted({previous, seat}),
                            "detectors": sorted(set(card_sources.get(card, [])) | {candidate.detector}),
                        })
                    else:
                        card_to_seat[card] = seat
                        card_sources.setdefault(card, []).append(candidate.detector)
                        merged[seat].add(card)

        if conflicts:
            return VisionResult(
                "CONFLICT",
                None,
                tuple(candidates),
                tuple(conflicts),
                diagnostics=tuple(diagnostics),
            )

        deal = canonicalize_video_deal(
            {"hands": {seat: sorted(cards) for seat, cards in merged.items()}},
        ).to_dict()
        observed = len(card_to_seat)
        status = "PARTIAL_BOARD_OBSERVATION" if observed >= 4 else "INSUFFICIENT"
        return VisionResult(status, deal, tuple(candidates), (), diagnostics=tuple(diagnostics))


__all__ = [
    "NATIVE_ENGINE_VERSION",
    "BridgeVisionEngine",
    "BridgeVisionError",
    "VisionCandidate",
    "VisionResult",
]
