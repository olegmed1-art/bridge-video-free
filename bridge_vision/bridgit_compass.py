"""Fail-closed adapter for the Bridgit board compass.

The adapter does not perform OCR.  It validates attributable OCR/template
observations from the human-verified compass ROI and turns them into the
profiled challenger's board/deal metadata contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


POSITIONS = ("top", "right", "bottom", "left")
SEATS = ("N", "E", "S", "W")
ROTATIONS = (
    ("N", "E", "S", "W"),
    ("W", "N", "E", "S"),
    ("S", "W", "N", "E"),
    ("E", "S", "W", "N"),
)
VULNERABILITY_CYCLE = (
    "NONE", "NS", "EW", "BOTH", "NS", "EW", "BOTH", "NONE",
    "EW", "BOTH", "NONE", "NS", "BOTH", "NONE", "NS", "EW",
)


class BridgitCompassError(ValueError):
    """The compass evidence is incomplete, unattributable or conflicting."""


def _probability(value: Any, name: str, minimum: float) -> float:
    if isinstance(value, bool):
        raise BridgitCompassError(f"invalid {name}")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BridgitCompassError(f"invalid {name}") from exc
    if not 0.0 <= result <= 1.0 or result < minimum:
        raise BridgitCompassError(f"{name} below gate")
    return result


def _field(raw: Any, name: str, minimum: float) -> tuple[Any, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise BridgitCompassError(f"missing {name} observation")
    confidence = _probability(raw.get("confidence"), f"{name} confidence", minimum)
    locator = str(raw.get("evidence_locator") or "").strip()
    if not locator or len(locator) > 256:
        raise BridgitCompassError(f"invalid {name} evidence locator")
    return raw.get("value"), {
        "confidence": confidence,
        "source": "VISUAL_TEXT",
        "evidence_locator": locator,
    }


def _region(raw: Any, name: str) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        raise BridgitCompassError(f"missing {name}")
    try:
        region = {key: float(raw[key]) for key in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError) as exc:
        raise BridgitCompassError(f"invalid {name}") from exc
    if region["w"] <= 0 or region["h"] <= 0:
        raise BridgitCompassError(f"invalid {name}")
    return region


def _same_region(observed: Mapping[str, float], expected: Mapping[str, float], tolerance: float) -> bool:
    return all(abs(observed[key] - expected[key]) <= tolerance for key in ("x", "y", "w", "h"))


def parse_bridgit_compass(
    raw: Any,
    *,
    expected_region: Mapping[str, Any],
    reference_size: Mapping[str, Any],
    min_confidence: float = 0.90,
    region_tolerance_px: float = 3.0,
) -> dict[str, Any]:
    """Validate one Bridgit compass observation and return challenger inputs."""
    if not isinstance(raw, Mapping):
        raise BridgitCompassError("compass observation must be an object")
    if raw.get("interface") != "BRIDGIT":
        raise BridgitCompassError("unsupported compass interface")
    if raw.get("human_verified_profile") is not True:
        raise BridgitCompassError("human-verified compass profile is required")

    observed_region = _region(raw.get("region"), "compass region")
    verified_region = _region(expected_region, "expected compass region")
    if not _same_region(observed_region, verified_region, region_tolerance_px):
        raise BridgitCompassError("compass lies outside the verified region")
    try:
        width = float(reference_size["width"])
        height = float(reference_size["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BridgitCompassError("invalid reference size") from exc
    if (
        observed_region["x"] < width / 2
        or observed_region["y"] >= height / 2
        or observed_region["x"] + observed_region["w"] > width
        or observed_region["y"] + observed_region["h"] > height
    ):
        raise BridgitCompassError("verified compass region is not upper-right")

    labels = raw.get("seat_labels")
    if not isinstance(labels, Mapping) or set(labels) != set(POSITIONS):
        raise BridgitCompassError("compass must contain top,right,bottom,left labels")
    seat_positions: dict[str, str] = {}
    label_evidence: dict[str, Any] = {}
    for position in POSITIONS:
        value, evidence = _field(labels[position], f"{position} seat label", min_confidence)
        seat_positions[position] = str(value or "").strip().upper()
        label_evidence[position] = evidence
    position_cycle = tuple(seat_positions[position] for position in POSITIONS)
    if position_cycle not in ROTATIONS:
        raise BridgitCompassError("seat labels are not a complete bridge compass rotation")

    board_value, board_evidence = _field(raw.get("board_number"), "board number", min_confidence)
    if isinstance(board_value, bool):
        raise BridgitCompassError("invalid board number")
    try:
        board_number = int(board_value)
    except (TypeError, ValueError) as exc:
        raise BridgitCompassError("invalid board number") from exc
    if board_number < 1 or str(board_number) != str(board_value).strip():
        raise BridgitCompassError("invalid board number")
    expected_dealer = SEATS[(board_number - 1) % 4]
    expected_vulnerability = VULNERABILITY_CYCLE[(board_number - 1) % 16]

    dealer_position, dealer_evidence = _field(raw.get("dealer_marker"), "dealer marker", min_confidence)
    dealer_position = str(dealer_position or "").strip().lower()
    if dealer_position not in POSITIONS:
        raise BridgitCompassError("dealer marker does not identify a compass position")
    observed_dealer = seat_positions[dealer_position]
    if observed_dealer != expected_dealer:
        raise BridgitCompassError("dealer marker conflicts with board cycle")

    board_metadata: dict[str, Any] = {
        "board_number": {"value": board_number, **board_evidence},
        "dealer": {"value": observed_dealer, **dealer_evidence},
    }
    vulnerability = raw.get("vulnerability")
    if vulnerability is not None:
        value, evidence = _field(vulnerability, "vulnerability", min_confidence)
        normalized = str(value or "").strip().upper().replace("-", "")
        normalized = {"LOVE": "NONE", "ALL": "BOTH"}.get(normalized, normalized)
        if normalized != expected_vulnerability:
            raise BridgitCompassError("vulnerability conflicts with board cycle")
        board_metadata["vulnerability"] = {"value": normalized, **evidence}

    return {
        "deal_identity": {
            "kind": "EXPLICIT_BOARD",
            "scope": str(raw.get("scope") or "BRIDGIT_SESSION"),
            "value": f"board-{board_number}",
        },
        "board_metadata": board_metadata,
        "seat_positions": seat_positions,
        "rotation_degrees_clockwise": 90 * ROTATIONS.index(position_cycle),
        "evidence": {
            "source": "BRIDGIT_UPPER_RIGHT_COMPASS",
            "region": observed_region,
            "seat_labels": label_evidence,
        },
    }


def guard_recognizer_result(result: Any, compass: Any, **kwargs: Any) -> dict[str, Any]:
    """Bind validated compass data to a pixel recognizer result, fail closed."""
    if not isinstance(result, Mapping):
        raise BridgitCompassError("recognizer result must be an object")
    parsed = parse_bridgit_compass(compass, **kwargs)
    guarded = dict(result)
    ordering = result.get("ordering_prior")
    if isinstance(ordering, Mapping):
        configured = ordering.get("seat_positions")
        if configured is not None and dict(configured) != parsed["seat_positions"]:
            raise BridgitCompassError("compass rotation conflicts with recognizer profile")
    guarded["deal_identity"] = parsed["deal_identity"]
    guarded["board_metadata"] = parsed["board_metadata"]
    guarded["compass_evidence"] = parsed["evidence"]
    return guarded


__all__ = ["BridgitCompassError", "guard_recognizer_result", "parse_bridgit_compass"]
