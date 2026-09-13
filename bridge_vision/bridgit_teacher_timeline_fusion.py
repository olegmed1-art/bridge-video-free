"""Bind teacher speech claims to timestamped visual card observations.

The speech interval and visual frame timestamp live on the same source-video
clock. A played card may precede the teacher's utterance, so the temporal gate
is intentionally asymmetric: more look-back than look-ahead. Speech never
creates a card without a matching visual observation.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from bridge_vision.bridgit_teacher_asr_adapter import (
    adapt_universal_video_teacher_segments,
)
from bridge_vision.bridgit_teacher_speech import accumulate_teacher_speech

SEATS = tuple("NESW")
_CARD = re.compile(r"^(A|K|Q|J|T|9|8|7|6|5|4|3|2)(H|C|D|S)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
LOOKBACK_MS = 12_000
LOOKAHEAD_MS = 5_000


class TeacherTimelineFusionError(ValueError):
    """Timestamped visual/speech evidence is malformed."""


def _confidence(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise TeacherTimelineFusionError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TeacherTimelineFusionError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise TeacherTimelineFusionError(f"{field} is outside [0,1]")
    return number


def _timestamp(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TeacherTimelineFusionError(f"{field} must be a non-negative integer")
    return value


def _temporal_distance(timestamp_ms: int, first_ms: int, last_ms: int) -> int:
    if first_ms <= timestamp_ms <= last_ms:
        return 0
    if timestamp_ms < first_ms:
        return first_ms - timestamp_ms
    return timestamp_ms - last_ms


def _temporal_weight(timestamp_ms: int, first_ms: int, last_ms: int) -> float:
    if first_ms <= timestamp_ms <= last_ms:
        return 1.0
    if timestamp_ms < first_ms:
        delta = first_ms - timestamp_ms
        return max(0.0, 1.0 - delta / LOOKBACK_MS)
    delta = timestamp_ms - last_ms
    return max(0.0, 1.0 - delta / LOOKAHEAD_MS)


def _visual_observations(frames: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(frames, Sequence) or isinstance(frames, (str, bytes)):
        raise TeacherTimelineFusionError("visual_frames must be an array")
    result: list[dict[str, Any]] = []
    for frame_index, frame in enumerate(frames):
        if not isinstance(frame, Mapping):
            raise TeacherTimelineFusionError(
                f"visual_frames[{frame_index}] must be an object"
            )
        timestamp_ms = _timestamp(
            frame.get("timestamp_ms"), f"visual_frames[{frame_index}].timestamp_ms"
        )
        frame_sha = str(frame.get("frame_sha256") or "")
        if not _SHA256.fullmatch(frame_sha):
            raise TeacherTimelineFusionError("frame_sha256 is invalid")
        cards = frame.get("cards")
        if not isinstance(cards, Sequence) or isinstance(cards, (str, bytes)):
            raise TeacherTimelineFusionError("frame cards must be an array")
        for card_index, raw in enumerate(cards):
            if not isinstance(raw, Mapping):
                raise TeacherTimelineFusionError("card observation must be an object")
            card = str(raw.get("card") or "").upper().replace("10", "T")
            if not _CARD.fullmatch(card):
                raise TeacherTimelineFusionError("card observation is invalid")
            seat = str(raw.get("seat") or "").upper()
            if seat not in SEATS:
                raise TeacherTimelineFusionError("card seat is invalid")
            source = str(raw.get("source") or "").upper()
            if source not in {"HAND", "PLAYED"}:
                raise TeacherTimelineFusionError("card source must be HAND or PLAYED")
            evidence_sha = str(raw.get("evidence_pixel_sha256") or "")
            if not _SHA256.fullmatch(evidence_sha):
                raise TeacherTimelineFusionError("evidence_pixel_sha256 is invalid")
            result.append(
                {
                    "frame_index": frame_index,
                    "card_index": card_index,
                    "frame_sha256": frame_sha,
                    "timestamp_ms": timestamp_ms,
                    "card": card,
                    "seat": seat,
                    "source": source,
                    "confidence": _confidence(
                        raw.get("confidence"), "visual card confidence"
                    ),
                    "evidence_pixel_sha256": evidence_sha,
                }
            )
    return result


def fuse_teacher_speech_with_visual_timeline(
    visual_frames: Sequence[Mapping[str, Any]],
    universal_transcript: Sequence[Mapping[str, Any]],
    *,
    trusted_teacher_track: bool = False,
) -> dict[str, Any]:
    """Corroborate visual HAND/PLAYED ownership with teacher speech.

    A speech claim is accepted as corroboration only when the exact card+seat
    has direct visual evidence in the allowed temporal window. No visual match
    means unresolved speech, not a new card observation.
    """

    visual = _visual_observations(visual_frames)
    adapted = adapt_universal_video_teacher_segments(
        universal_transcript,
        trusted_teacher_track=trusted_teacher_track,
    )
    speech = accumulate_teacher_speech(adapted["segments"])

    corroborations: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for claim in speech["claims"]:
        if claim.get("retracted") or not claim.get("complete"):
            continue
        first_ms = int(claim["first_ms"])
        last_ms = int(claim["last_ms"])
        matches = []
        for observation in visual:
            if observation["card"] != claim["card"] or observation["seat"] != claim["seat"]:
                continue
            timestamp_ms = int(observation["timestamp_ms"])
            if timestamp_ms < first_ms - LOOKBACK_MS:
                continue
            if timestamp_ms > last_ms + LOOKAHEAD_MS:
                continue
            weight = _temporal_weight(timestamp_ms, first_ms, last_ms)
            if weight <= 0.0:
                continue
            matches.append(
                {
                    **observation,
                    "temporal_distance_ms": _temporal_distance(
                        timestamp_ms, first_ms, last_ms
                    ),
                    "temporal_weight": round(weight, 6),
                }
            )
        if not matches:
            unresolved.append(
                {
                    "claim_id": claim["claim_id"],
                    "seat": claim["seat"],
                    "card": claim["card"],
                    "speech_confidence": claim["speech_confidence"],
                    "reason": "NO_MATCHING_VISUAL_CARD_IN_TEMPORAL_WINDOW",
                }
            )
            continue

        matches.sort(
            key=lambda item: (
                item["temporal_distance_ms"],
                -item["confidence"],
                item["frame_sha256"],
            )
        )
        unique_by_pixels: dict[str, dict[str, Any]] = {}
        for item in matches:
            previous = unique_by_pixels.get(item["evidence_pixel_sha256"])
            if previous is None or (
                item["temporal_distance_ms"], -item["confidence"]
            ) < (
                previous["temporal_distance_ms"], -previous["confidence"]
            ):
                unique_by_pixels[item["evidence_pixel_sha256"]] = item
        independent = sorted(
            unique_by_pixels.values(),
            key=lambda item: (
                item["temporal_distance_ms"],
                -item["confidence"],
                item["frame_sha256"],
            ),
        )
        best = independent[0]
        combined = min(
            0.995,
            float(claim["speech_confidence"])
            * float(best["confidence"])
            * float(best["temporal_weight"]),
        )
        corroborations.append(
            {
                "claim_id": claim["claim_id"],
                "seat": claim["seat"],
                "card": claim["card"],
                "source": best["source"],
                "ownership": claim["seat"],
                "visual_frame_sha256": best["frame_sha256"],
                "visual_timestamp_ms": best["timestamp_ms"],
                "speech_first_ms": first_ms,
                "speech_last_ms": last_ms,
                "temporal_distance_ms": best["temporal_distance_ms"],
                "speech_confidence": claim["speech_confidence"],
                "visual_confidence": best["confidence"],
                "combined_corroboration": round(combined, 6),
                "independent_visual_evidence_count": len(independent),
                "provenance": (
                    "PLAYED_PLUS_TEACHER_SPEECH"
                    if best["source"] == "PLAYED"
                    else "HAND_PLUS_TEACHER_SPEECH"
                ),
                "cursor_evidence_used": False,
            }
        )

    return {
        "status": "FUSED",
        "corroborations": corroborations,
        "unresolved_speech_claims": unresolved,
        "teacher_claims": speech["claims"],
        "asr_adapter_rejected": adapted["rejected"],
        "source_clock": adapted["source_clock"],
        "hidden_hand_inference_used": False,
        "deck_complement_used": False,
        "mouse_cursor_used": False,
        "canonical_promotion_allowed": False,
    }
