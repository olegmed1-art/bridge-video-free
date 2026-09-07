"""Deterministic, fail-closed evidence report for video-recognized bridge deals.

The report keeps visual observations, teacher pointer corroboration, temporal
consensus and unknown slots as different provenance classes.  Only visual
observations enter the canonical observed deal.  Logical complement is an
explicitly prohibited legacy source and is never emitted.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from bridge_contracts.video_deal import SEATS, canonicalize_video_deal

DEAL_EVIDENCE_SCHEMA = "bridge-video-deal-evidence/v1"
PROVENANCE_SOURCES = (
    "VISUAL",
    "TEACHER_POINTER",
    "TEMPORAL_CONSENSUS",
    "LOGICAL_INFERENCE",
    "UNKNOWN",
)
SUITS = ("H", "C", "D", "S")
RANKS = tuple("AKQJT98765432")
MAX_VISUAL_OBSERVATIONS = 1024
MAX_POINTER_EVENTS = 256

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{2,127}$")
_COORDINATE_QUANTA = 100_000_000
_MIN_REGION_SPAN_QUANTA = 100


class DealEvidenceError(ValueError):
    """The evidence input is malformed or exceeds a bounded limit."""


def _normalise_card(value: Any) -> str:
    try:
        deal = canonicalize_video_deal({"hands": {"N": [value]}}).to_dict()
    except (TypeError, ValueError) as exc:
        raise DealEvidenceError("invalid card") from exc
    return deal["hands"]["N"]["cards"][0]


def _confidence(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DealEvidenceError(f"invalid {field}") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise DealEvidenceError(f"invalid {field}")
    return number


def _timestamp(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DealEvidenceError(f"invalid {field}")
    if value < 0 or value > 10**12:
        raise DealEvidenceError(f"invalid {field}")
    return value


def _sha(value: Any, field: str) -> str:
    text = str(value or "").lower()
    if not _SHA256.fullmatch(text):
        raise DealEvidenceError(f"invalid {field}")
    return text


def _region(value: Any, field: str) -> dict[str, float | str]:
    if not isinstance(value, Mapping):
        raise DealEvidenceError(f"invalid {field}")
    if value.get("coordinate_space") != "NORMALIZED_FRAME":
        raise DealEvidenceError(f"invalid {field}.coordinate_space")
    result: dict[str, float | str] = {"coordinate_space": "NORMALIZED_FRAME"}
    for name in ("x", "y", "width", "height"):
        result[name] = _confidence(value.get(name), f"{field}.{name}")

    def quantized_span(start: float, size: float) -> int:
        return round((start + size) * _COORDINATE_QUANTA) - round(
            start * _COORDINATE_QUANTA
        )

    if (
        quantized_span(float(result["x"]), float(result["width"]))
        < _MIN_REGION_SPAN_QUANTA
        or quantized_span(float(result["y"]), float(result["height"]))
        < _MIN_REGION_SPAN_QUANTA
    ):
        raise DealEvidenceError(f"invalid {field} size")
    if (
        float(result["x"]) + float(result["width"]) > 1.0 + 1e-9
        or float(result["y"]) + float(result["height"]) > 1.0 + 1e-9
    ):
        raise DealEvidenceError(f"{field} leaves normalized frame")
    return result


def _point(value: Any, field: str) -> dict[str, float | str]:
    if not isinstance(value, Mapping):
        raise DealEvidenceError(f"invalid {field}")
    if value.get("coordinate_space") != "NORMALIZED_FRAME":
        raise DealEvidenceError(f"invalid {field}.coordinate_space")
    return {
        "coordinate_space": "NORMALIZED_FRAME",
        "x": _confidence(value.get("x"), f"{field}.x"),
        "y": _confidence(value.get("y"), f"{field}.y"),
    }


def _normalise_visual_observation(
    raw: Mapping[str, Any], index: int, recognizer_version: str
) -> dict[str, Any]:
    seat = str(raw.get("seat") or "").upper()
    if seat not in SEATS:
        raise DealEvidenceError(f"invalid visual_observations[{index}].seat")
    card_value = raw.get("card")
    if card_value is None:
        card_value = str(raw.get("rank") or "") + str(raw.get("suit") or "")
    card = _normalise_card(card_value)
    declared_source = str(raw.get("source") or "VISUAL")
    if declared_source != "VISUAL":
        raise DealEvidenceError(f"visual_observations[{index}].source must be VISUAL")
    version = str(raw.get("recognizer_version") or recognizer_version)
    if version != recognizer_version:
        raise DealEvidenceError("mixed recognizer versions are not allowed")
    return {
        "seat": seat,
        "suit": card[1],
        "rank": card[0],
        "card": card,
        "source": "VISUAL",
        "frame_sha256": _sha(
            raw.get("frame_sha256"), f"visual_observations[{index}].frame_sha256"
        ),
        "decoded_pixel_sha256": (
            _sha(
                raw.get("decoded_pixel_sha256"),
                f"visual_observations[{index}].decoded_pixel_sha256",
            )
            if raw.get("decoded_pixel_sha256") is not None
            else None
        ),
        "timestamp_ms": _timestamp(
            raw.get("timestamp_ms"), f"visual_observations[{index}].timestamp_ms"
        ),
        "region": _region(raw.get("region"), f"visual_observations[{index}].region"),
        "confidence": _confidence(
            raw.get("confidence"), f"visual_observations[{index}].confidence"
        ),
        "confidence_kind": str(
            raw.get("confidence_kind") or "UNCALIBRATED_VISUAL_SCORE"
        ),
        "recognizer_version": version,
    }


def _contains(region: Mapping[str, Any], point: Mapping[str, Any]) -> bool:
    x = float(point["x"])
    y = float(point["y"])
    return float(region["x"]) <= x <= float(region["x"]) + float(
        region["width"]
    ) and float(region["y"]) <= y <= float(region["y"]) + float(region["height"])


def _regions_overlap(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    def edge(value: float) -> int:
        return round(value * _COORDINATE_QUANTA)

    overlap_x = min(
        edge(float(first["x"]) + float(first["width"])),
        edge(float(second["x"]) + float(second["width"])),
    ) - max(edge(float(first["x"])), edge(float(second["x"])))
    overlap_y = min(
        edge(float(first["y"]) + float(first["height"])),
        edge(float(second["y"]) + float(second["height"])),
    ) - max(edge(float(first["y"])), edge(float(second["y"])))
    return overlap_x > 1 and overlap_y > 1


def _unknown_record(seat: str, slot: int, recognizer_version: str) -> dict[str, Any]:
    return {
        "seat": seat,
        "suit": None,
        "rank": None,
        "source": "UNKNOWN",
        "frame_sha256": None,
        "timestamp_ms": None,
        "region": None,
        "confidence": 0.0,
        "confidence_kind": "UNKNOWN",
        "recognizer_version": recognizer_version,
        "unknown_slot": slot,
        "visually_recognized": False,
        "available_to_player": False,
        "accepted_as_visual_observation": False,
    }


def _record_sort_key(record: Mapping[str, Any]) -> tuple[int, int, int, int]:
    seat_index = SEATS.index(str(record["seat"]))
    suit = record.get("suit")
    rank = record.get("rank")
    suit_index = SUITS.index(str(suit)) if suit in SUITS else len(SUITS)
    rank_index = RANKS.index(str(rank)) if rank in RANKS else len(RANKS)
    return seat_index, suit_index, rank_index, int(record.get("unknown_slot") or 0)


def normalize_teacher_pointer_events(
    teacher_pointer_events: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validate pointer fields that do not depend on recognized visual targets."""

    if not isinstance(teacher_pointer_events, Iterable) or isinstance(
        teacher_pointer_events, (str, bytes)
    ):
        raise DealEvidenceError("teacher_pointer_events must be an iterable")
    result = []
    for index, raw in enumerate(teacher_pointer_events):
        if index >= MAX_POINTER_EVENTS:
            raise DealEvidenceError("too many teacher pointer events")
        if not isinstance(raw, Mapping):
            raise DealEvidenceError("teacher pointer event must be an object")
        if raw.get("source") != "TEACHER_POINTER":
            raise DealEvidenceError(
                f"teacher_pointer_events[{index}].source is invalid"
            )
        claimed_card = (
            _normalise_card(raw.get("claimed_card"))
            if raw.get("claimed_card") is not None
            else None
        )
        claimed_seat = str(raw.get("claimed_seat") or "").upper() or None
        if claimed_seat is not None and claimed_seat not in SEATS:
            raise DealEvidenceError(
                f"invalid teacher_pointer_events[{index}].claimed_seat"
            )
        result.append(
            {
                "source": "TEACHER_POINTER",
                "frame_sha256": _sha(
                    raw.get("frame_sha256"),
                    f"teacher_pointer_events[{index}].frame_sha256",
                ),
                "timestamp_ms": _timestamp(
                    raw.get("timestamp_ms"),
                    f"teacher_pointer_events[{index}].timestamp_ms",
                ),
                "point": _point(
                    raw.get("point"), f"teacher_pointer_events[{index}].point"
                ),
                "confidence": _confidence(
                    raw.get("confidence"),
                    f"teacher_pointer_events[{index}].confidence",
                ),
                "claimed_card": claimed_card,
                "claimed_seat": claimed_seat,
                "accepted_as_visual_observation": False,
            }
        )
    return result


def build_deal_evidence_report(
    visual_observations: Iterable[Mapping[str, Any]],
    teacher_pointer_events: Iterable[Mapping[str, Any]] = (),
    *,
    recognizer_version: str,
    required_visual_frames: int = 2,
    allow_logical_inference: bool = False,
) -> dict[str, Any]:
    """Build a provenance-preserving deal report without deriving hidden cards."""

    if not _VERSION.fullmatch(str(recognizer_version or "")):
        raise DealEvidenceError("invalid recognizer_version")
    if isinstance(required_visual_frames, bool) or not isinstance(
        required_visual_frames, int
    ):
        raise DealEvidenceError("invalid required_visual_frames")
    if not 2 <= required_visual_frames <= 16:
        raise DealEvidenceError("invalid required_visual_frames")
    if not isinstance(allow_logical_inference, bool):
        raise DealEvidenceError("allow_logical_inference must be boolean")
    if allow_logical_inference:
        raise DealEvidenceError("logical fourth-hand inference is prohibited")
    if not isinstance(visual_observations, Iterable) or isinstance(
        visual_observations, (str, bytes)
    ):
        raise DealEvidenceError("visual_observations must be an iterable")
    if not isinstance(teacher_pointer_events, Iterable) or isinstance(
        teacher_pointer_events, (str, bytes)
    ):
        raise DealEvidenceError("teacher_pointer_events must be an iterable")
    normalized_pointer_events = normalize_teacher_pointer_events(teacher_pointer_events)

    observations: list[dict[str, Any]] = []
    for index, raw in enumerate(visual_observations):
        if index >= MAX_VISUAL_OBSERVATIONS:
            raise DealEvidenceError("too many visual observations")
        if not isinstance(raw, Mapping):
            raise DealEvidenceError("visual observation must be an object")
        observations.append(
            _normalise_visual_observation(raw, index, recognizer_version)
        )
    observations.sort(
        key=lambda item: (
            item["card"],
            item["seat"],
            item["timestamp_ms"],
            item["frame_sha256"],
            float(item["region"]["x"]),
            float(item["region"]["y"]),
        )
    )
    timestamp_by_frame: dict[str, int] = {}
    frame_by_timestamp: dict[int, str] = {}
    pixels_by_frame: dict[str, set[str | None]] = defaultdict(set)
    regions_by_frame: dict[str, list[tuple[Mapping[str, Any], tuple[str, str]]]] = (
        defaultdict(list)
    )
    for item in observations:
        frame = item["frame_sha256"]
        timestamp = item["timestamp_ms"]
        if frame in timestamp_by_frame and timestamp_by_frame[frame] != timestamp:
            raise DealEvidenceError("one frame cannot have multiple timestamps")
        if timestamp in frame_by_timestamp and frame_by_timestamp[timestamp] != frame:
            raise DealEvidenceError("independent frames require distinct timestamps")
        timestamp_by_frame[frame] = timestamp
        frame_by_timestamp[timestamp] = frame
        pixels_by_frame[frame].add(item["decoded_pixel_sha256"])
        region = item["region"]
        target = (item["card"], item["seat"])
        if any(
            prior_target != target and _regions_overlap(prior_region, region)
            for prior_region, prior_target in regions_by_frame[frame]
        ):
            raise DealEvidenceError(
                "visual region reused or overlaps a different target"
            )
        regions_by_frame[frame].append((region, target))
    if any(len(values) != 1 for values in pixels_by_frame.values()):
        raise DealEvidenceError("one frame cannot have multiple decoded pixel hashes")
    frame_pixel_hashes = [next(iter(values)) for values in pixels_by_frame.values()]
    pixel_identity_proven = all(value is not None for value in frame_pixel_hashes)
    if pixel_identity_proven and len(set(frame_pixel_hashes)) != len(
        frame_pixel_hashes
    ):
        raise DealEvidenceError("independent frames require distinct decoded pixels")

    conflicts: list[dict[str, Any]] = []
    review_reasons: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        grouped[observation["card"]].append(observation)

    accepted_by_card: dict[str, dict[str, Any]] = {}
    for card in sorted(grouped):
        evidence = grouped[card]
        seats = sorted({item["seat"] for item in evidence})
        if len(seats) != 1:
            conflicts.append(
                {
                    "type": "VISUAL_CARD_SEAT_CONFLICT",
                    "card": card,
                    "seats": seats,
                    "frame_sha256s": sorted(
                        {item["frame_sha256"] for item in evidence}
                    ),
                }
            )
            continue
        seen_frames: dict[str, int] = defaultdict(int)
        for item in evidence:
            seen_frames[item["frame_sha256"]] += 1
        replayed = sorted(frame for frame, count in seen_frames.items() if count > 1)
        if replayed:
            conflicts.append(
                {
                    "type": "DUPLICATE_CARD_OBSERVATION_IN_FRAME",
                    "card": card,
                    "seat": seats[0],
                    "frame_sha256s": replayed,
                }
            )
            continue
        representative = min(
            evidence,
            key=lambda item: (item["timestamp_ms"], item["frame_sha256"]),
        )
        source = (
            "TEMPORAL_CONSENSUS"
            if pixel_identity_proven and len(seen_frames) >= required_visual_frames
            else "VISUAL"
        )
        accepted_by_card[card] = {
            "seat": seats[0],
            "suit": card[1],
            "rank": card[0],
            "source": source,
            "frame_sha256": representative["frame_sha256"],
            "timestamp_ms": representative["timestamp_ms"],
            "region": representative["region"],
            "confidence": round(min(item["confidence"] for item in evidence), 6),
            "confidence_kind": "MIN_SUPPORTING_VISUAL_SCORE",
            "recognizer_version": recognizer_version,
            "visually_recognized": True,
            "available_to_player": None,
            "player_availability": "NOT_EVALUATED",
            "accepted_as_canonical_observation": False,
            "evidence_scope": "SHADOW_MODEL_CANDIDATE",
            "evidence": [dict(item) for item in evidence],
        }

    seat_cards: dict[str, list[str]] = {seat: [] for seat in SEATS}
    for card, record in accepted_by_card.items():
        seat_cards[record["seat"]].append(card)
    for seat in SEATS:
        if len(seat_cards[seat]) > 13:
            conflicts.append(
                {
                    "type": "HAND_EXCEEDS_13_CARDS",
                    "seat": seat,
                    "cards": sorted(seat_cards[seat]),
                }
            )

    frame_observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        frame_observations[observation["frame_sha256"]].append(observation)
    expected_visual_pairs = {
        (record["seat"], card) for card, record in accepted_by_card.items()
    }
    complete_supporting_frames = sorted(
        frame_sha
        for frame_sha, frame_items in frame_observations.items()
        if {(item["seat"], item["card"]) for item in frame_items}
        == expected_visual_pairs
    )
    global_temporal_support = (
        pixel_identity_proven
        and len(complete_supporting_frames) >= required_visual_frames
    )
    pointer_evidence: list[dict[str, Any]] = []
    for event in normalized_pointer_events:
        frame_sha = event["frame_sha256"]
        timestamp_ms = event["timestamp_ms"]
        point = event["point"]
        claimed_card = event["claimed_card"]
        claimed_seat = event["claimed_seat"]
        targets = [
            item
            for item in frame_observations.get(frame_sha, ())
            if _contains(item["region"], point)
        ]
        event = dict(event)
        if not targets:
            event["resolution"] = "NO_VISUAL_TARGET_AT_POINTER"
            pointer_evidence.append(event)
            review_reasons.append("teacher_pointer_without_visual_target")
            continue
        target_keys = sorted({(item["seat"], item["card"]) for item in targets})
        if len(target_keys) != 1:
            event["resolution"] = "AMBIGUOUS_VISUAL_TARGET_AT_POINTER"
            event["candidate_targets"] = [
                {"seat": seat, "card": card} for seat, card in target_keys
            ]
            pointer_evidence.append(event)
            review_reasons.append("teacher_pointer_target_ambiguous")
            continue
        target_seat, target_card = target_keys[0]
        event["visual_target"] = {"seat": target_seat, "card": target_card}
        target_timestamps = {
            item["timestamp_ms"]
            for item in targets
            if (item["seat"], item["card"]) == (target_seat, target_card)
        }
        if timestamp_ms not in target_timestamps:
            event["resolution"] = "POINTER_TIMESTAMP_DOES_NOT_MATCH_FRAME"
            event["visual_target_timestamps_ms"] = sorted(target_timestamps)
            pointer_evidence.append(event)
            review_reasons.append("teacher_pointer_timestamp_mismatch")
            continue
        contradicts = (claimed_card is not None and claimed_card != target_card) or (
            claimed_seat is not None and claimed_seat != target_seat
        )
        if contradicts:
            event["resolution"] = "CONFLICTS_WITH_VISUAL"
            pointer_evidence.append(event)
            conflicts.append(
                {
                    "type": "TEACHER_POINTER_VISUAL_CONFLICT",
                    "frame_sha256": frame_sha,
                    "timestamp_ms": timestamp_ms,
                    "visual_target": event["visual_target"],
                    "claimed_card": claimed_card,
                    "claimed_seat": claimed_seat,
                }
            )
            continue
        event["resolution"] = "CORROBORATES_VISUAL"
        pointer_evidence.append(event)
        accepted = accepted_by_card.get(target_card)
        if accepted is not None and accepted["seat"] == target_seat:
            accepted["evidence"].append(event)

    if conflicts:
        review_reasons.append("evidence_conflict")
    review_reasons = sorted(set(review_reasons))

    observed_deal = None
    if not any(item["type"] == "HAND_EXCEEDS_13_CARDS" for item in conflicts):
        try:
            observed_deal = canonicalize_video_deal({"hands": seat_cards}).to_dict()
        except ValueError:
            review_reasons.append("canonical_observed_deal_invalid")

    all_records = list(accepted_by_card.values())
    every_observed_card_has_consensus = bool(all_records) and all(
        record["source"] == "TEMPORAL_CONSENSUS" for record in all_records
    )

    known_by_seat = {seat: 0 for seat in SEATS}
    for record in all_records:
        known_by_seat[record["seat"]] += 1
    unknown_records = [
        _unknown_record(seat, slot, recognizer_version)
        for seat in SEATS
        for slot in range(1, 14 - known_by_seat[seat])
    ]
    all_records.extend(unknown_records)
    all_records.sort(key=_record_sort_key)

    observed_count = len(accepted_by_card)
    consensus_complete = (
        observed_count == 52
        and every_observed_card_has_consensus
        and global_temporal_support
    )
    if conflicts or review_reasons:
        status = "NEEDS_REVIEW"
    elif consensus_complete:
        status = "COMPLETE_VISUAL"
    elif observed_count == 52:
        status = "PENDING_TEMPORAL_CONSENSUS"
    else:
        status = "PARTIAL"

    diagram_hands: dict[str, dict[str, list[dict[str, str]]]] = {
        seat: {suit: [] for suit in SUITS} for seat in SEATS
    }
    for record in all_records:
        if record["rank"] is not None:
            diagram_hands[record["seat"]][record["suit"]].append(
                {"rank": record["rank"], "source": record["source"]}
            )

    return {
        "schema": DEAL_EVIDENCE_SCHEMA,
        "status": status,
        "recognizer_version": recognizer_version,
        "suit_order": list(SUITS),
        "rank_order": list(RANKS),
        "card_records": all_records,
        "pointer_evidence": pointer_evidence,
        "conflicts": conflicts,
        "review_reasons": sorted(set(review_reasons)),
        "canonical_observed_deal": observed_deal,
        "logical_inference": {
            "requested": False,
            "performed": False,
            "reason": "PROHIBITED_BY_VIDEO_3_1_FREE",
            "cards": [],
            "analysis_only": False,
            "canonical_promotion_allowed": False,
            "hidden_information_use_allowed": False,
        },
        "integrity": {
            "observed_cards": observed_count,
            "inferred_cards": 0,
            "unknown_slots": len(unknown_records),
            "unique_known_cards": len(accepted_by_card),
            "total_seat_slots": len(all_records),
            "observed_seat_counts": {seat: len(seat_cards[seat]) for seat in SEATS},
            "known_seat_counts": known_by_seat,
            "complete_supporting_frame_sha256s": complete_supporting_frames,
            "global_temporal_support": global_temporal_support,
        },
        "diagram": {
            "seat_order": list(SEATS),
            "suit_order": list(SUITS),
            "hands": diagram_hands,
            "unknown_by_seat": {
                seat: max(0, 13 - known_by_seat[seat]) for seat in SEATS
            },
        },
        "canonical_observed_deal_scope": "SHADOW_MODEL_CANDIDATE",
        "canonical_promotion_allowed": False,
        "school_canon_write_performed": False,
    }


def render_deal_diagram_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact deterministic N/E/S/W review diagram."""

    if report.get("schema") != DEAL_EVIDENCE_SCHEMA:
        raise DealEvidenceError("unsupported deal evidence report")
    diagram = report.get("diagram")
    if not isinstance(diagram, Mapping):
        raise DealEvidenceError("report diagram is missing")
    hands = diagram.get("hands")
    unknown = diagram.get("unknown_by_seat")
    if not isinstance(hands, Mapping) or not isinstance(unknown, Mapping):
        raise DealEvidenceError("report diagram is malformed")
    markers = {
        "VISUAL": "V",
        "TEMPORAL_CONSENSUS": "T",
        "LOGICAL_INFERENCE": "L",
    }
    lines = [
        "| Seat | ♥ H | ♣ C | ♦ D | ♠ S | Unknown |",
        "|---|---|---|---|---|---:|",
    ]
    for seat in SEATS:
        cells = []
        for suit in SUITS:
            entries = hands.get(seat, {}).get(suit, [])
            cells.append(
                " ".join(
                    f"{item['rank']}[{markers.get(item['source'], '?')}]"
                    for item in entries
                )
                or "—"
            )
        lines.append(f"| {seat} | {' | '.join(cells)} | {int(unknown.get(seat, 0))} |")
    lines.append("")
    lines.append(
        "Legend: V=visual, T=temporal consensus; logical inference is prohibited."
    )
    return "\n".join(lines)


__all__ = [
    "DEAL_EVIDENCE_SCHEMA",
    "MAX_POINTER_EVENTS",
    "MAX_VISUAL_OBSERVATIONS",
    "PROVENANCE_SOURCES",
    "DealEvidenceError",
    "build_deal_evidence_report",
    "normalize_teacher_pointer_events",
    "render_deal_diagram_markdown",
]
