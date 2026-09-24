"""Fail-closed Video 3.1 handoff for Evolutionary Course research.

The contract proves that one observed learning interaction, its transcript and
one or more frames belong to the same source.  It deliberately has no writer
or promotion path.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Mapping


SCHEMA = "video31-learning-candidate-v1"
AUTHORITY_CLASS = "CANDIDATE_RESEARCH"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{2,127}$")
_CONTEXT_STATES = {"CONFIRMED", "REVIEW", "UNKNOWN"}
_CONTEXT_FIELDS = ("board", "dealer", "vulnerability", "auction", "deal")


class LearningCandidateError(ValueError):
    """Evidence is insufficient or internally inconsistent."""


def _fail(message: str) -> None:
    raise LearningCandidateError(message)


def _text(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        _fail(f"{label} required")
    return result


def _sha(value: Any, label: str) -> str:
    result = str(value or "").strip().lower()
    if not _SHA256.fullmatch(result):
        _fail(f"invalid {label}")
    return result


def _seconds(value: Any, label: str) -> float:
    if isinstance(value, bool):
        _fail(f"invalid {label}")
    try:
        result = float(value)
    except (TypeError, ValueError):
        _fail(f"invalid {label}")
    if result < 0:
        _fail(f"invalid {label}")
    return result


def _confidence(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"invalid {label}")
    result = float(value)
    if not 0 <= result <= 1:
        _fail(f"invalid {label}")
    return result


def _unique_texts(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        _fail(f"invalid {label}")
    result = [_text(item, label) for item in value]
    if len(result) != len(set(result)):
        _fail(f"duplicate {label}")
    return result


def validate_learning_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize a research-only, source-bound candidate."""
    if not isinstance(candidate, Mapping):
        _fail("candidate must be an object")
    expected_top = {
        "schema", "status", "candidate_id", "observed_episode", "source",
        "transcript_evidence", "frame_evidence", "bridge_context",
        "preliminary_skill", "confidence", "provenance",
        "unresolved_questions", "authority",
    }
    if set(candidate) != expected_top:
        _fail("candidate fields mismatch")
    if candidate.get("schema") != SCHEMA or candidate.get("status") != AUTHORITY_CLASS:
        _fail("research candidate boundary mismatch")
    candidate_id = _text(candidate.get("candidate_id"), "candidate_id")

    source = candidate.get("source")
    if not isinstance(source, Mapping) or set(source) != {
        "video_file_id", "source_name", "source_sha256", "source_fingerprint"
    }:
        _fail("source fields mismatch")
    source_id = _text(source.get("video_file_id"), "video_file_id")
    source_name = _text(source.get("source_name"), "source_name")
    source_sha = _sha(source.get("source_sha256"), "source_sha256")
    source_fingerprint = _text(source.get("source_fingerprint"), "source_fingerprint")

    episode = candidate.get("observed_episode")
    if not isinstance(episode, Mapping) or set(episode) != {
        "interaction_id", "start", "end", "task", "student_action",
        "teacher_intervention", "student_followup", "observed_outcome",
        "actor_attribution_status",
    }:
        _fail("observed_episode fields mismatch")
    interaction_id = _text(episode.get("interaction_id"), "interaction_id")
    start = _seconds(episode.get("start"), "episode start")
    end = _seconds(episode.get("end"), "episode end")
    if end <= start or end - start > 7200:
        _fail("invalid episode interval")
    for field in ("task", "student_action", "teacher_intervention", "student_followup", "observed_outcome"):
        _text(episode.get(field), field)
    if episode.get("actor_attribution_status") != "SUPPORTED":
        _fail("actor attribution unproven")

    transcripts = candidate.get("transcript_evidence")
    if not isinstance(transcripts, list) or not transcripts:
        _fail("transcript evidence required")
    transcript_locators: set[str] = set()
    transcript_intervals: dict[str, tuple[float, float]] = {}
    normalized_transcripts: list[dict[str, Any]] = []
    for item in transcripts:
        if not isinstance(item, Mapping) or set(item) != {
            "locator", "start", "end", "text_sha256", "speaker_id",
            "speaker_identity_status",
        }:
            _fail("transcript evidence fields mismatch")
        locator = _text(item.get("locator"), "transcript locator")
        if locator in transcript_locators:
            _fail("duplicate transcript locator")
        transcript_locators.add(locator)
        item_start = _seconds(item.get("start"), "transcript start")
        item_end = _seconds(item.get("end"), "transcript end")
        if item_end <= item_start or item_start < start or item_end > end:
            _fail("transcript outside episode interval")
        identity_status = item.get("speaker_identity_status")
        if identity_status not in {"VERIFIED", "UNKNOWN"}:
            _fail("invalid speaker identity status")
        speaker_id = str(item.get("speaker_id") or "").strip()
        if identity_status == "VERIFIED" and not speaker_id:
            _fail("verified speaker identity missing")
        if identity_status == "UNKNOWN" and speaker_id not in {"", "UNKNOWN"}:
            _fail("unverified speaker identity must remain UNKNOWN")
        transcript_intervals[locator] = (item_start, item_end)
        normalized_transcripts.append({
            **dict(item), "locator": locator, "start": item_start, "end": item_end,
            "text_sha256": _sha(item.get("text_sha256"), "transcript text_sha256"),
            "speaker_id": speaker_id or "UNKNOWN",
        })

    frames = candidate.get("frame_evidence")
    if not isinstance(frames, list) or not frames:
        _fail("frame evidence required")
    normalized_frames: list[dict[str, Any]] = []
    bound_locators: set[str] = set()
    for item in frames:
        required = {
            "schema", "method", "frame_sha256", "frame_file", "frame_time",
            "speech_start", "speech_end", "transcript_locator",
            "distance_to_midpoint_seconds", "source_fingerprint",
            "single_frame_binding",
        }
        if not isinstance(item, Mapping) or set(item) != required:
            _fail("frame evidence fields mismatch")
        if item.get("schema") != "bridge-speech-frame-binding-v1":
            _fail("unsupported frame binding schema")
        if item.get("method") not in {
            "EXPLICIT_FRAME_SHA256", "NEAREST_FRAME_INSIDE_SPEECH_INTERVAL"
        }:
            _fail("unsupported frame binding method")
        if item.get("single_frame_binding") is not True:
            _fail("speech must bind to exactly one frame")
        if item.get("source_fingerprint") != source_fingerprint:
            _fail("frame source mismatch")
        locator = _text(item.get("transcript_locator"), "frame transcript locator")
        if locator not in transcript_locators:
            _fail("frame binding references unknown transcript")
        if locator in bound_locators:
            _fail("transcript binds to multiple frames")
        bound_locators.add(locator)
        frame_time = _seconds(item.get("frame_time"), "frame time")
        speech_start = _seconds(item.get("speech_start"), "speech start")
        speech_end = _seconds(item.get("speech_end"), "speech end")
        if speech_end <= speech_start or speech_start < start or speech_end > end:
            _fail("bound speech outside episode interval")
        if (speech_start, speech_end) != transcript_intervals[locator]:
            _fail("frame binding interval differs from transcript")
        if not speech_start <= frame_time <= speech_end:
            _fail("frame outside bound speech interval")
        distance = _seconds(
            item.get("distance_to_midpoint_seconds"), "distance to midpoint"
        )
        expected_distance = abs(frame_time - (speech_start + speech_end) / 2)
        if abs(distance - expected_distance) > 1e-9:
            _fail("frame binding midpoint distance mismatch")
        normalized_frames.append({
            **dict(item),
            "frame_sha256": _sha(item.get("frame_sha256"), "frame_sha256"),
            "frame_file": _text(item.get("frame_file"), "frame_file"),
            "frame_time": frame_time,
            "speech_start": speech_start,
            "speech_end": speech_end,
            "distance_to_midpoint_seconds": distance,
        })
    if bound_locators != transcript_locators:
        _fail("every transcript must have one frame binding")

    context = candidate.get("bridge_context")
    if not isinstance(context, Mapping) or set(context) != set(_CONTEXT_FIELDS):
        _fail("bridge context fields mismatch")
    available_refs = transcript_locators | {item["frame_sha256"] for item in normalized_frames}
    for field in _CONTEXT_FIELDS:
        item = context[field]
        if not isinstance(item, Mapping) or set(item) != {"status", "value", "source_refs"}:
            _fail(f"{field} context fields mismatch")
        status = item.get("status")
        if status not in _CONTEXT_STATES:
            _fail(f"invalid {field} status")
        refs = _unique_texts(item.get("source_refs"), f"{field} source_refs", allow_empty=True)
        if not set(refs) <= available_refs:
            _fail(f"{field} references evidence outside candidate")
        if status == "CONFIRMED" and (item.get("value") is None or not refs):
            _fail(f"confirmed {field} lacks evidence")
        if status == "UNKNOWN" and (item.get("value") is not None or refs):
            _fail(f"unknown {field} must not carry inferred data")

    skill = candidate.get("preliminary_skill")
    if not isinstance(skill, Mapping) or set(skill) != {"label", "status"}:
        _fail("preliminary skill fields mismatch")
    _text(skill.get("label"), "preliminary skill label")
    if skill.get("status") not in {"PROPOSED", "UNKNOWN"}:
        _fail("invalid preliminary skill status")

    confidence = candidate.get("confidence")
    confidence_fields = {"transcript", "frame", "actor_attribution", "bridge_context", "preliminary_skill"}
    if not isinstance(confidence, Mapping) or set(confidence) != confidence_fields:
        _fail("confidence fields mismatch")
    normalized_confidence = {
        field: _confidence(confidence[field], f"{field} confidence")
        for field in sorted(confidence_fields)
    }

    provenance = candidate.get("provenance")
    if not isinstance(provenance, Mapping) or set(provenance) != {
        "algorithm_revision", "contract_version"
    }:
        _fail("provenance fields mismatch")
    revision = _text(provenance.get("algorithm_revision"), "algorithm revision")
    if not _SAFE_REVISION.fullmatch(revision):
        _fail("invalid algorithm revision")
    if provenance.get("contract_version") != SCHEMA:
        _fail("contract version mismatch")

    unresolved = _unique_texts(
        candidate.get("unresolved_questions"), "unresolved questions", allow_empty=True
    )
    if any(context[field]["status"] != "CONFIRMED" for field in _CONTEXT_FIELDS) and not unresolved:
        _fail("unresolved bridge context requires questions")

    authority = candidate.get("authority")
    forbidden = {
        "authority_class": AUTHORITY_CLASS,
        "school_canon_write_allowed": False,
        "student_profile_write_allowed": False,
        "approved_course_write_allowed": False,
        "publication_allowed": False,
    }
    if authority != forbidden:
        _fail("authority boundary mismatch")

    result = deepcopy(dict(candidate))
    result["candidate_id"] = candidate_id
    result["source"] = {
        "video_file_id": source_id, "source_name": source_name,
        "source_sha256": source_sha, "source_fingerprint": source_fingerprint,
    }
    result["observed_episode"]["interaction_id"] = interaction_id
    result["observed_episode"]["start"] = start
    result["observed_episode"]["end"] = end
    result["transcript_evidence"] = normalized_transcripts
    result["frame_evidence"] = normalized_frames
    result["confidence"] = normalized_confidence
    result["unresolved_questions"] = unresolved
    return result


def canonical_sha256(candidate: Mapping[str, Any]) -> str:
    normalized = validate_learning_candidate(candidate)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "AUTHORITY_CLASS", "LearningCandidateError", "SCHEMA",
    "canonical_sha256", "validate_learning_candidate",
]
