"""Adapter from Universal Video transcript segments to teacher-speech evidence.

The Universal Video ASR timeline already uses source-video-relative start/end
seconds. This adapter preserves that clock, converts to milliseconds, and only
emits TEACHER speech when speaker-role evidence is explicit enough. It never
uses cursor position and never invents per-segment ASR probabilities.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


class TeacherAsrAdapterError(ValueError):
    """Transcript metadata is insufficient for teacher-card evidence."""


def _probability(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise TeacherAsrAdapterError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TeacherAsrAdapterError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise TeacherAsrAdapterError(f"{field} is outside [0,1]")
    return number


def _seconds(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise TeacherAsrAdapterError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TeacherAsrAdapterError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0.0:
        raise TeacherAsrAdapterError(f"{field} must be non-negative")
    return number


def adapt_universal_video_teacher_segments(
    transcript: Sequence[Mapping[str, Any]],
    *,
    minimum_speaker_confidence: float = 0.80,
    minimum_role_confidence: float = 0.80,
    trusted_teacher_track: bool = False,
) -> dict[str, Any]:
    """Return bounded TEACHER segments on the same source-video clock.

    Universal Video currently exposes a reliable/unreliable segment flag, but
    not a calibrated text-correctness probability per segment. Therefore this
    adapter does not fabricate one. Instead, accepted reliable segments get a
    deterministic evidence weight derived only from explicit speaker/role
    proof (or from an explicitly trusted teacher-only track). That weight is a
    fusion policy value, not an ASR accuracy claim.
    """

    if not isinstance(transcript, Sequence) or isinstance(transcript, (str, bytes)):
        raise TeacherAsrAdapterError("transcript must be an array")
    min_speaker = _probability(minimum_speaker_confidence, "minimum_speaker_confidence")
    min_role = _probability(minimum_role_confidence, "minimum_role_confidence")

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, raw in enumerate(transcript):
        if not isinstance(raw, Mapping):
            raise TeacherAsrAdapterError(f"transcript[{index}] must be an object")
        start = _seconds(raw.get("start"), f"transcript[{index}].start")
        end = _seconds(raw.get("end"), f"transcript[{index}].end")
        if end < start:
            raise TeacherAsrAdapterError("transcript segment end precedes start")
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        if raw.get("unreliable") is True:
            rejected.append({"index": index, "reason": "ASR_SEGMENT_UNRELIABLE"})
            continue

        if trusted_teacher_track:
            speaker_confidence = 1.0
            role_confidence = 1.0
            role_ok = True
        else:
            role = str(raw.get("speaker_role_candidate") or "").strip().lower()
            if role != "teacher":
                rejected.append({"index": index, "reason": "NOT_TEACHER_ROLE"})
                continue
            speaker_confidence = _probability(
                raw.get("speaker_confidence"),
                f"transcript[{index}].speaker_confidence",
            )
            role_confidence = _probability(
                raw.get("speaker_role_confidence"),
                f"transcript[{index}].speaker_role_confidence",
            )
            role_ok = (
                speaker_confidence >= min_speaker and role_confidence >= min_role
            )
        if not role_ok:
            rejected.append({"index": index, "reason": "TEACHER_ROLE_UNCERTAIN"})
            continue

        # This is intentionally a policy weight, not a claimed ASR probability.
        evidence_weight = min(0.95, speaker_confidence, role_confidence)
        accepted.append(
            {
                "event_id": f"uv-teacher-{index:06d}",
                "speaker_role": "TEACHER",
                "start_ms": int(round(start * 1000.0)),
                "end_ms": int(round(end * 1000.0)),
                "text": text,
                "asr_confidence": round(evidence_weight, 6),
                "confidence_kind": "POLICY_WEIGHT_FROM_RELIABLE_ASR_AND_TEACHER_ROLE",
                "source_segment_index": index,
                "source_speaker_confidence": round(speaker_confidence, 6),
                "source_role_confidence": round(role_confidence, 6),
            }
        )

    return {
        "segments": accepted,
        "rejected": rejected,
        "source_clock": "SOURCE_VIDEO_TIMELINE",
        "cursor_used": False,
        "per_segment_asr_probability_claimed": False,
    }
