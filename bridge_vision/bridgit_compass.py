"""Source-bound, fail-closed Bridgit compass/board context adapter."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

POSITIONS = ("top", "right", "bottom", "left")
SEATS = ("N", "E", "S", "W")
ROTATIONS = (
    ("N", "E", "S", "W"), ("W", "N", "E", "S"),
    ("S", "W", "N", "E"), ("E", "S", "W", "N"),
)
VULNERABILITY_CYCLE = (
    "NONE", "NS", "EW", "BOTH", "NS", "EW", "BOTH", "NONE",
    "EW", "BOTH", "NONE", "NS", "BOTH", "NONE", "NS", "EW",
)
MIN_COMPASS_CONFIDENCE = 0.90
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{2,191}$")


class BridgitCompassError(ValueError):
    pass


def _region(raw: Any, name: str) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        raise BridgitCompassError(f"missing {name}")
    try:
        value = {key: float(raw[key]) for key in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError) as exc:
        raise BridgitCompassError(f"invalid {name}") from exc
    if min(value["x"], value["y"]) < 0 or value["w"] <= 0 or value["h"] <= 0:
        raise BridgitCompassError(f"invalid {name}")
    return value


def _field(raw: Any, name: str, *, frame_sha256: str, minimum: float) -> tuple[Any, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise BridgitCompassError(f"missing {name} observation")
    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise BridgitCompassError(f"invalid {name} confidence") from exc
    if not 0.0 <= confidence <= 1.0 or confidence < minimum:
        raise BridgitCompassError(f"{name} confidence below gate")
    if raw.get("source") not in {"VISUAL_TEXT", "VISUAL_MARKER"}:
        raise BridgitCompassError(f"{name} must have visual evidence")
    if raw.get("frame_sha256") != frame_sha256:
        raise BridgitCompassError(f"{name} evidence is not bound to the frame")
    locator = str(raw.get("evidence_locator") or "").strip()
    if not locator or len(locator) > 256:
        raise BridgitCompassError(f"invalid {name} evidence locator")
    return raw.get("value"), {
        "confidence": confidence, "source": raw["source"],
        "frame_sha256": frame_sha256, "evidence_locator": locator,
    }


def parse_bridgit_compass(
    raw: Any, *, expected_region: Mapping[str, Any], reference_size: Mapping[str, Any],
    min_confidence: float = MIN_COMPASS_CONFIDENCE, region_tolerance_px: float = 3.0,
) -> dict[str, Any]:
    if min_confidence < MIN_COMPASS_CONFIDENCE or min_confidence > 1.0:
        raise BridgitCompassError("compass confidence threshold cannot be lowered")
    if not isinstance(raw, Mapping) or raw.get("interface") != "BRIDGIT":
        raise BridgitCompassError("unsupported compass observation")
    if raw.get("human_verified_profile") is not True:
        raise BridgitCompassError("human-verified compass profile is required")
    frame_sha = str(raw.get("frame_sha256") or "")
    anchor_sha = str(raw.get("deal_anchor_frame_sha256") or "")
    if not _SHA256.fullmatch(frame_sha) or not _SHA256.fullmatch(anchor_sha):
        raise BridgitCompassError("frame and deal anchor hashes are required")
    source_id = str(raw.get("source_id") or "").strip()
    scope = str(raw.get("scope") or "").strip()
    instance_id = str(raw.get("deal_instance_id") or "").strip()
    if any(not _IDENTIFIER.fullmatch(value) for value in (source_id, scope, instance_id)):
        raise BridgitCompassError("source, scope, and deal instance identity are required")
    timestamp_ms = raw.get("timestamp_ms")
    if not isinstance(timestamp_ms, int) or timestamp_ms < 0:
        raise BridgitCompassError("timestamp_ms must be a non-negative integer")

    observed = _region(raw.get("region"), "compass region")
    expected = _region(expected_region, "expected compass region")
    if any(abs(observed[key] - expected[key]) > region_tolerance_px for key in observed):
        raise BridgitCompassError("compass lies outside the verified region")
    try:
        width, height = float(reference_size["width"]), float(reference_size["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BridgitCompassError("invalid reference size") from exc
    if width <= 0 or height <= 0 or observed["x"] < width / 2 or observed["y"] >= height / 2:
        raise BridgitCompassError("verified compass region is not upper-right")
    if observed["x"] + observed["w"] > width or observed["y"] + observed["h"] > height:
        raise BridgitCompassError("compass region exceeds the reference frame")

    labels = raw.get("seat_labels")
    if not isinstance(labels, Mapping) or set(labels) != set(POSITIONS):
        raise BridgitCompassError("compass must contain all four seat labels")
    seat_positions, label_evidence = {}, {}
    for position in POSITIONS:
        value, evidence = _field(labels[position], f"{position} seat label", frame_sha256=frame_sha, minimum=min_confidence)
        seat_positions[position] = str(value or "").strip().upper()
        label_evidence[position] = evidence
    cycle = tuple(seat_positions[position] for position in POSITIONS)
    if cycle not in ROTATIONS:
        raise BridgitCompassError("seat labels are not a complete compass rotation")

    value, board_evidence = _field(raw.get("board_number"), "board number", frame_sha256=frame_sha, minimum=min_confidence)
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]{0,5}", str(value).strip()):
        raise BridgitCompassError("invalid board number")
    board_number = int(str(value).strip())
    expected_dealer = SEATS[(board_number - 1) % 4]
    expected_vulnerability = VULNERABILITY_CYCLE[(board_number - 1) % 16]

    dealer_position, dealer_evidence = _field(raw.get("dealer_marker"), "dealer marker", frame_sha256=frame_sha, minimum=min_confidence)
    dealer_position = str(dealer_position or "").strip().lower()
    if dealer_position not in POSITIONS or seat_positions[dealer_position] != expected_dealer:
        raise BridgitCompassError("dealer marker conflicts with board cycle")
    vulnerability, vulnerability_evidence = _field(raw.get("vulnerability"), "vulnerability", frame_sha256=frame_sha, minimum=min_confidence)
    vulnerability = str(vulnerability or "").strip().upper().replace("-", "").replace(" ", "")
    vulnerability = {"LOVE": "NONE", "ALL": "BOTH"}.get(vulnerability, vulnerability)
    if vulnerability != expected_vulnerability:
        raise BridgitCompassError("vulnerability conflicts with board cycle")

    return {
        "schema": "bridge-source-bound-board-context-v1",
        "timestamp_ms": timestamp_ms, "frame_sha256": frame_sha, "source_id": source_id,
        "deal_identity": {
            "kind": "SOURCE_BOUND_BOARD_INSTANCE", "scope": scope, "instance_id": instance_id,
            "board_number": board_number, "anchor_frame_sha256": anchor_sha,
        },
        "board_metadata": {
            "status": "OBSERVED_SINGLE_FRAME",
            "board_number": board_number, "dealer": expected_dealer,
            "vulnerability": expected_vulnerability,
            "provenance": {
                "board_number": board_evidence, "dealer": dealer_evidence,
                "vulnerability": vulnerability_evidence,
            },
        },
        "seat_positions": seat_positions,
        "rotation_degrees_clockwise": 90 * ROTATIONS.index(cycle),
        "compass_evidence": {"region": observed, "seat_labels": label_evidence},
        "production_activation_allowed": False,
    }


__all__ = ["BridgitCompassError", "MIN_COMPASS_CONFIDENCE", "parse_bridgit_compass"]
