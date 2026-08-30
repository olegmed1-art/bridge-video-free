"""Fail-closed anonymous speaker-structure stage for bridge lessons.

The optional diarizer is treated as an untrusted producer. The adapter keeps
the original ASR text and timeline, copies only validated speaker annotations,
renames every accepted cluster to ``SPEAKER_A`` ... ``SPEAKER_H``, and removes
all speaker data unless the separation gate passes. It never persists audio,
voice embeddings, source labels, or real-person identities.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA = "universal-video-speaker-structure-v1"
TEST_SCHEMA = "universal-video-speaker-structure-v2"
MIN_TEST_LABEL_COVERAGE = 0.80
MAX_SPEAKERS = 8
ANONYMOUS_LABELS = tuple(f"SPEAKER_{chr(ord('A') + index)}" for index in range(MAX_SPEAKERS))
_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIAGNOSTIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")
_POSITIVE_STATUSES = frozenset(
    {"DIARIZED_ROLE_MAPPED", "DIARIZED_UNMAPPED", "EXISTING_SPEAKER_LABELS_PRESERVED"}
)
_ALLOWED_STATUSES = _POSITIVE_STATUSES | frozenset(
    {"UNAVAILABLE", "UNAVAILABLE_INSUFFICIENT_SEGMENTS", "DISABLED"}
)
_SPEAKER_FIELDS = frozenset(
    {
        "speaker",
        "speaker_cluster",
        "speaker_confidence",
        "speaker_role_candidate",
        "speaker_role_confidence",
        "speaker_assignment_revision",
    }
)
_REASONS = frozenset(
    {
        "NONE",
        "OPTIONAL_RUNTIME_UNAVAILABLE",
        "AUDIO_EXTRACTION_FAILED",
        "AUDIO_FORMAT_UNSUPPORTED",
        "INSUFFICIENT_VOICED_SEGMENTS",
        "ACOUSTIC_CLUSTERS_NOT_SEPARATED",
        "DIARIZATION_ENGINE_FAILED",
        "UNSUPPORTED_STATUS",
        "SEGMENT_COUNT_MISMATCH",
        "NO_SPEAKER_LABELS",
        "SPEAKER_COLLAPSE_RISK",
        "TOO_MANY_SPEAKERS",
        "INVALID_SPEAKER_ANNOTATION",
        "INSUFFICIENT_SEGMENTS",
        "INSUFFICIENT_LABEL_COVERAGE",
        "DISABLED",
    }
)


def _producer_failure_reason(report: Mapping[str, Any]) -> str:
    """Return only a bounded diagnostic code from an unavailable producer."""
    code = str(report.get("diagnostic_code") or "").strip().upper()
    if code in _REASONS - {"NONE"}:
        return code
    producer_error = str(report.get("reason") or "").strip()
    if producer_error in {"ImportError", "ModuleNotFoundError", "FileNotFoundError"}:
        return "OPTIONAL_RUNTIME_UNAVAILABLE"
    return "DIARIZATION_ENGINE_FAILED"


def _strip_speaker_fields(segment: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in dict(segment).items() if key not in _SPEAKER_FIELDS}


def _duration(segment: Mapping[str, Any]) -> float:
    try:
        start = float(segment.get("start"))
        end = float(segment.get("end"))
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        return 0.0
    return end - start


def _report(
    *,
    revision: str,
    status: str,
    reason: str,
    segments: Sequence[Mapping[str, Any]],
    role_mapping_supported: bool,
    min_label_coverage: float | None,
    speaker_count_evidence: Mapping[str, Any] | None = None,
    role_mapping_proof_status: str | None = None,
    rejected_candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    labels = [str(segment.get("speaker")) for segment in segments if segment.get("speaker")]
    counts = Counter(labels)
    unique_labels = [label for label in ANONYMOUS_LABELS if label in counts]
    report = {
        "schema": TEST_SCHEMA if min_label_coverage is not None else SCHEMA,
        "revision": revision if _REVISION.fullmatch(revision) else "bridge-speaker-structure-v1",
        "status": status,
        "quality_gate": "PASS" if status in _POSITIVE_STATUSES else "INCONCLUSIVE",
        "reason": reason,
        "segments_total": len(segments),
        "segments_labeled": len(labels),
        "speaker_count": len(unique_labels),
        "speaker_labels": unique_labels,
        "speaker_clusters": {label: counts[label] for label in unique_labels},
        "role_mapping_supported": role_mapping_supported,
        "teacher_student_attribution": "SUGGESTION_ONLY" if role_mapping_supported else "UNAVAILABLE",
        "privacy": {
            "real_person_identity_claimed": False,
            "raw_audio_persisted": False,
            "voice_embedding_persisted": False,
            "cross_lesson_voice_profile_persisted": False,
            "source_speaker_labels_persisted": False,
        },
    }
    if min_label_coverage is not None:
        report["label_coverage"] = len(labels) / len(segments) if segments else 0.0
        total_duration = sum(_duration(segment) for segment in segments)
        labeled_duration = sum(
            _duration(segment) for segment in segments if segment.get("speaker")
        )
        report["speech_duration_coverage"] = (
            labeled_duration / total_duration if total_duration > 0.0 else 0.0
        )
        report["minimum_label_coverage"] = min_label_coverage
        report["speaker_count_evidence"] = (
            dict(speaker_count_evidence) if speaker_count_evidence else None
        )
        report["role_mapping_proof_status"] = role_mapping_proof_status or "NOT_APPLICABLE"
        if rejected_candidate is not None:
            report["rejected_candidate"] = dict(rejected_candidate)
    return report


def _unavailable(
    transcript: Sequence[Mapping[str, Any]],
    *,
    revision: str,
    status: str = "UNAVAILABLE",
    reason: str,
    min_label_coverage: float | None = None,
    rejected_candidate: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    copied = [_strip_speaker_fields(segment) for segment in transcript]
    bounded_status = status if status in _ALLOWED_STATUSES - _POSITIVE_STATUSES else "UNAVAILABLE"
    bounded_reason = reason if reason in _REASONS else "OPTIONAL_RUNTIME_UNAVAILABLE"
    return copied, _report(
        revision=revision,
        status=bounded_status,
        reason=bounded_reason,
        segments=copied,
        role_mapping_supported=False,
        min_label_coverage=min_label_coverage,
        rejected_candidate=rejected_candidate,
    )


def _candidate_observation(
    segments: Sequence[Mapping[str, Any]], producer_report: Mapping[str, Any]
) -> dict[str, Any]:
    """Keep only anonymous aggregates for a candidate rejected by the gate."""
    labels = [str(segment.get("speaker") or "").strip() for segment in segments]
    labeled = sum(bool(label) for label in labels)
    total_duration = sum(_duration(segment) for segment in segments)
    labeled_duration = sum(
        _duration(segment) for segment, label in zip(segments, labels) if label
    )
    selected = str(
        producer_report.get("selected_hypothesis")
        or producer_report.get("model_id")
        or "unknown"
    )
    if not _DIAGNOSTIC_ID.fullmatch(selected):
        selected = "unknown"
    status = str(producer_report.get("status") or "UNAVAILABLE").upper()
    if status not in _ALLOWED_STATUSES | {"DIARIZED_COLLAPSE_RISK"}:
        status = "UNAVAILABLE"
    return {
        "schema": "universal-video-rejected-speaker-candidate-v1",
        "producer_status": status,
        "selected_hypothesis": selected,
        "segments_total": len(segments),
        "segments_labeled": labeled,
        "speaker_count": len({label for label in labels if label}),
        "segment_coverage": labeled / len(segments) if segments else 0.0,
        "speech_duration_coverage": (
            labeled_duration / total_duration if total_duration > 0.0 else 0.0
        ),
    }


def _probability(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def run_speaker_structure(
    video_path: Path,
    transcript: Sequence[Mapping[str, Any]],
    work_dir: Path,
    *,
    min_label_coverage: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the optional diarizer and return only gate-passing anonymous labels."""
    normalized_min_coverage: float | None = None
    if min_label_coverage is not None:
        if isinstance(min_label_coverage, bool):
            raise ValueError("min label coverage outside [0,1]")
        try:
            normalized_min_coverage = float(min_label_coverage)
        except (TypeError, ValueError) as exc:
            raise ValueError("min label coverage outside [0,1]") from exc
        if not math.isfinite(normalized_min_coverage) or not 0.0 <= normalized_min_coverage <= 1.0:
            raise ValueError("min label coverage outside [0,1]")
    original = [_strip_speaker_fields(segment) for segment in transcript]
    revision = "bridge-speaker-structure-v1"
    try:
        if normalized_min_coverage is None:
            from bridge_speaker_diarization import DIARIZATION_REVISION, diarize_transcript
        else:
            from bridge_speaker_diarization_v3 import DIARIZATION_REVISION, diarize_transcript

        raw_segments, raw_report = diarize_transcript(video_path, transcript, work_dir, enabled=True)
        raw_report = raw_report if isinstance(raw_report, Mapping) else {}
        revision = str(raw_report.get("revision") or DIARIZATION_REVISION)
    except Exception:  # noqa: BLE001 - optional diarizer must fail closed without breaking ASR
        return _unavailable(
            original,
            revision=revision,
            reason="OPTIONAL_RUNTIME_UNAVAILABLE",
            min_label_coverage=normalized_min_coverage,
        )

    status = str(raw_report.get("status") or "UNAVAILABLE").upper()
    if status == "DIARIZED_COLLAPSE_RISK":
        return _unavailable(
            original,
            revision=revision,
            reason="SPEAKER_COLLAPSE_RISK",
            min_label_coverage=normalized_min_coverage,
        )
    if status not in _ALLOWED_STATUSES:
        return _unavailable(original, revision=revision, reason="UNSUPPORTED_STATUS", min_label_coverage=normalized_min_coverage)
    if status not in _POSITIVE_STATUSES:
        reason = {
            "UNAVAILABLE_INSUFFICIENT_SEGMENTS": "INSUFFICIENT_SEGMENTS",
            "DISABLED": "DISABLED",
        }.get(status, _producer_failure_reason(raw_report))
        return _unavailable(original, revision=revision, status=status, reason=reason, min_label_coverage=normalized_min_coverage)
    if not isinstance(raw_segments, Sequence) or isinstance(raw_segments, (str, bytes)):
        return _unavailable(original, revision=revision, reason="SEGMENT_COUNT_MISMATCH", min_label_coverage=normalized_min_coverage)
    if len(raw_segments) != len(original) or any(not isinstance(segment, Mapping) for segment in raw_segments):
        return _unavailable(original, revision=revision, reason="SEGMENT_COUNT_MISMATCH", min_label_coverage=normalized_min_coverage)
    rejected_candidate = _candidate_observation(raw_segments, raw_report)

    source_labels: list[str] = []
    for segment in raw_segments:
        label = str(segment.get("speaker") or "").strip()
        if label and label not in source_labels:
            source_labels.append(label)
    if not source_labels:
        return _unavailable(original, revision=revision, reason="NO_SPEAKER_LABELS", min_label_coverage=normalized_min_coverage, rejected_candidate=rejected_candidate)
    if len(source_labels) == 1:
        return _unavailable(original, revision=revision, reason="SPEAKER_COLLAPSE_RISK", min_label_coverage=normalized_min_coverage, rejected_candidate=rejected_candidate)
    if len(source_labels) > MAX_SPEAKERS:
        return _unavailable(original, revision=revision, reason="TOO_MANY_SPEAKERS", min_label_coverage=normalized_min_coverage, rejected_candidate=rejected_candidate)

    label_map = {source: ANONYMOUS_LABELS[index] for index, source in enumerate(source_labels)}
    normalized: list[dict[str, Any]] = []
    for base, raw in zip(original, raw_segments):
        copied = dict(base)
        source_label = str(raw.get("speaker") or "").strip()
        if not source_label:
            normalized.append(copied)
            continue
        speaker = label_map[source_label]
        confidence = _probability(raw.get("speaker_confidence"))
        if confidence is None:
            return _unavailable(original, revision=revision, reason="INVALID_SPEAKER_ANNOTATION", min_label_coverage=normalized_min_coverage)
        role = str(raw.get("speaker_role_candidate") or "unknown").lower()
        if role not in {"teacher", "student", "unknown"}:
            return _unavailable(original, revision=revision, reason="INVALID_SPEAKER_ANNOTATION", min_label_coverage=normalized_min_coverage)
        role_confidence = _probability(raw.get("speaker_role_confidence", 0.0))
        if role_confidence is None:
            return _unavailable(original, revision=revision, reason="INVALID_SPEAKER_ANNOTATION", min_label_coverage=normalized_min_coverage)
        copied.update(
            {
                "speaker": speaker,
                "speaker_cluster": speaker,
                "speaker_confidence": confidence,
                "speaker_role_candidate": role,
                "speaker_role_confidence": role_confidence,
                "speaker_assignment_revision": (
                    revision if _REVISION.fullmatch(revision) else "bridge-speaker-structure-v1"
                ),
            }
        )
        normalized.append(copied)

    observed = {segment.get("speaker") for segment in normalized if segment.get("speaker")}
    if len(observed) < 2:
        return _unavailable(original, revision=revision, reason="SPEAKER_COLLAPSE_RISK", min_label_coverage=normalized_min_coverage)
    labeled_count = sum(bool(segment.get("speaker")) for segment in normalized)
    observed_coverage = labeled_count / len(normalized) if normalized else 0.0
    total_duration = sum(_duration(segment) for segment in normalized)
    labeled_duration = sum(
        _duration(segment) for segment in normalized if segment.get("speaker")
    )
    observed_duration_coverage = (
        labeled_duration / total_duration if total_duration > 0.0 else 0.0
    )
    if normalized_min_coverage is not None and (
        observed_coverage < normalized_min_coverage
        or observed_duration_coverage < normalized_min_coverage
    ):
        return _unavailable(
            original,
            revision=revision,
            reason="INSUFFICIENT_LABEL_COVERAGE",
            min_label_coverage=normalized_min_coverage,
            rejected_candidate=rejected_candidate,
        )
    role_mapping_supported = status == "DIARIZED_ROLE_MAPPED" and bool(raw_report.get("role_mapping_supported"))
    if status == "DIARIZED_ROLE_MAPPED" and not role_mapping_supported:
        status = "DIARIZED_UNMAPPED"
    if not role_mapping_supported:
        for segment in normalized:
            if segment.get("speaker"):
                segment["speaker_role_candidate"] = "unknown"
                segment["speaker_role_confidence"] = 0.0
    report = _report(
        revision=revision,
        status=status,
        reason="NONE",
        segments=normalized,
        role_mapping_supported=role_mapping_supported,
        min_label_coverage=normalized_min_coverage,
        speaker_count_evidence=(
            raw_report.get("speaker_count_evidence")
            if isinstance(raw_report.get("speaker_count_evidence"), Mapping)
            else None
        ),
    )
    # A producer's MAPPED label is not proof.  For the open-set route an
    # independent artifact-only verifier must establish both anonymous roles.
    # Otherwise retain the separated clusters but erase role candidates.
    if normalized_min_coverage is not None and role_mapping_supported:
        from .speaker_role_verifier import verify_speaker_roles

        proof = verify_speaker_roles(
            normalized,
            report,
            expected_speakers=2,
            minimum_coverage=normalized_min_coverage,
        )
        if proof.get("status") != "PASS":
            for segment in normalized:
                if segment.get("speaker"):
                    segment["speaker_role_candidate"] = "unknown"
                    segment["speaker_role_confidence"] = 0.0
            report = _report(
                revision=revision,
                status="DIARIZED_UNMAPPED",
                reason="NONE",
                segments=normalized,
                role_mapping_supported=False,
                min_label_coverage=normalized_min_coverage,
                speaker_count_evidence=(
                    raw_report.get("speaker_count_evidence")
                    if isinstance(raw_report.get("speaker_count_evidence"), Mapping)
                    else None
                ),
                role_mapping_proof_status="INCONCLUSIVE",
            )
        else:
            report["role_mapping_proof_status"] = "PASS"
    return normalized, report


__all__ = [
    "ANONYMOUS_LABELS",
    "MIN_TEST_LABEL_COVERAGE",
    "SCHEMA",
    "TEST_SCHEMA",
    "run_speaker_structure",
]
