"""Deterministic, fail-closed evidence report for video-recognized bridge deals.

The report keeps visual observations, teacher pointer corroboration, temporal
consensus, logical complement and unknown slots as different provenance
classes.  Only visual observations enter the canonical observed deal.  A
logical complement is an offline review aid and is never marked as visible or
available to a player.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from bridge_contracts.video_deal import FULL_DECK, SEATS, canonicalize_video_deal

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
    except (TypeError, ValueError) as exc:
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
    if float(result["width"]) <= 0.0 or float(result["height"]) <= 0.0:
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


def build_deal_evidence_report(
    visual_observations: Iterable[Mapping[str, Any]],
    teacher_pointer_events: Iterable[Mapping[str, Any]] = (),
    *,
    recognizer_version: str,
    required_visual_frames: int = 2,
    allow_logical_inference: bool = False,
) -> dict[str, Any]:
    """Build a provenance-preserving deal report without promoting hidden cards.

    ``allow_logical_inference`` affects only the review diagram.  Inferred cards
    are excluded from ``canonical_observed_deal`` and carry
    ``available_to_player=false`` and ``accepted_as_visual_observation=false``.
    """

    if not _VERSION.fullmatch(str(recognizer_version or "")):
        raise DealEvidenceError("invalid recognizer_version")
    if isinstance(required_visual_frames, bool) or not isinstance(
        required_visual_frames, int
    ):
        raise DealEvidenceError("invalid required_visual_frames")
    if not 1 <= required_visual_frames <= 16:
        raise DealEvidenceError("invalid required_visual_frames")
    if not isinstance(allow_logical_inference, bool):
        raise DealEvidenceError("allow_logical_inference must be boolean")
    if isinstance(visual_observations, (str, bytes)):
        raise DealEvidenceError("visual_observations must be an iterable")
    if isinstance(teacher_pointer_events, (str, bytes)):
        raise DealEvidenceError("teacher_pointer_events must be an iterable")

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
            if len(seen_frames) >= required_visual_frames
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
    pointer_evidence: list[dict[str, Any]] = []
    for index, raw in enumerate(teacher_pointer_events):
        if index >= MAX_POINTER_EVENTS:
            raise DealEvidenceError("too many teacher pointer events")
        if not isinstance(raw, Mapping):
            raise DealEvidenceError("teacher pointer event must be an object")
        if raw.get("source") != "TEACHER_POINTER":
            raise DealEvidenceError(
                f"teacher_pointer_events[{index}].source is invalid"
            )
        frame_sha = _sha(
            raw.get("frame_sha256"),
            f"teacher_pointer_events[{index}].frame_sha256",
        )
        timestamp_ms = _timestamp(
            raw.get("timestamp_ms"),
            f"teacher_pointer_events[{index}].timestamp_ms",
        )
        point = _point(raw.get("point"), f"teacher_pointer_events[{index}].point")
        confidence = _confidence(
            raw.get("confidence"), f"teacher_pointer_events[{index}].confidence"
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
        targets = [
            item
            for item in frame_observations.get(frame_sha, ())
            if _contains(item["region"], point)
        ]
        event: dict[str, Any] = {
            "source": "TEACHER_POINTER",
            "frame_sha256": frame_sha,
            "timestamp_ms": timestamp_ms,
            "point": point,
            "confidence": confidence,
            "claimed_card": claimed_card,
            "claimed_seat": claimed_seat,
            "accepted_as_visual_observation": False,
        }
        frame_timestamps = {
            item["timestamp_ms"] for item in frame_observations.get(frame_sha, ())
        }
        if frame_timestamps and timestamp_ms not in frame_timestamps:
            event["resolution"] = "POINTER_TIMESTAMP_DOES_NOT_MATCH_FRAME"
            event["visual_frame_timestamps_ms"] = sorted(frame_timestamps)
            pointer_evidence.append(event)
            review_reasons.append("teacher_pointer_timestamp_mismatch")
            continue
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
    inferred_cards: list[str] = []
    inference_reason = "NOT_REQUESTED"
    every_observed_card_has_consensus = bool(all_records) and all(
        record["source"] == "TEMPORAL_CONSENSUS" for record in all_records
    )
    if allow_logical_inference:
        inference_reason = "PRECONDITIONS_NOT_MET"
        complete_seats = [seat for seat in SEATS if len(seat_cards[seat]) == 13]
        missing_seats = [seat for seat in SEATS if len(seat_cards[seat]) == 0]
        if (
            not conflicts
            and not review_reasons
            and len(accepted_by_card) == 39
            and len(complete_seats) == 3
            and len(missing_seats) == 1
            and every_observed_card_has_consensus
        ):
            missing_seat = missing_seats[0]
            inferred_cards = sorted(
                FULL_DECK - set(accepted_by_card),
                key=lambda card: (SUITS.index(card[1]), RANKS.index(card[0])),
            )
            for card in inferred_cards:
                all_records.append(
                    {
                        "seat": missing_seat,
                        "suit": card[1],
                        "rank": card[0],
                        "source": "LOGICAL_INFERENCE",
                        "frame_sha256": None,
                        "timestamp_ms": None,
                        "region": None,
                        "confidence": 1.0,
                        "confidence_kind": "DECK_COMPLEMENT_LOGICAL_CERTAINTY",
                        "recognizer_version": recognizer_version,
                        "visually_recognized": False,
                        "available_to_player": False,
                        "accepted_as_visual_observation": False,
                        "accepted_as_canonical_observation": False,
                        "inference": {
                            "method": "UNIQUE_52_CARD_COMPLEMENT",
                            "observed_cards_required": 39,
                            "observed_seats_required": complete_seats,
                        },
                    }
                )
            inference_reason = "UNIQUE_52_CARD_COMPLEMENT"

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
    consensus_complete = observed_count == 52 and every_observed_card_has_consensus
    if conflicts or review_reasons:
        status = "NEEDS_REVIEW"
    elif consensus_complete:
        status = "COMPLETE_VISUAL"
    elif observed_count == 52:
        status = "PENDING_TEMPORAL_CONSENSUS"
    elif inferred_cards:
        status = "COMPLETE_WITH_LOGICAL_INFERENCE"
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
            "requested": allow_logical_inference,
            "performed": bool(inferred_cards),
            "reason": inference_reason,
            "cards": inferred_cards,
            "analysis_only": True,
            "canonical_promotion_allowed": False,
            "hidden_information_use_allowed": False,
        },
        "integrity": {
            "observed_cards": observed_count,
            "inferred_cards": len(inferred_cards),
            "unknown_slots": len(unknown_records),
            "unique_known_cards": len(accepted_by_card) + len(inferred_cards),
            "total_seat_slots": len(all_records),
            "observed_seat_counts": {seat: len(seat_cards[seat]) for seat in SEATS},
            "known_seat_counts": known_by_seat,
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
    lines.append("Legend: V=visual, T=temporal consensus, L=logical inference.")
    return "\n".join(lines)


__all__ = [
    "DEAL_EVIDENCE_SCHEMA",
    "MAX_POINTER_EVENTS",
    "MAX_VISUAL_OBSERVATIONS",
    "PROVENANCE_SOURCES",
    "DealEvidenceError",
    "build_deal_evidence_report",
    "render_deal_diagram_markdown",
]
