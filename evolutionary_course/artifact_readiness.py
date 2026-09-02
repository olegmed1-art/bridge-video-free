"""Fail-closed readiness assessment for real longitudinal pilot artifacts."""
from __future__ import annotations

from typing import Any, Mapping

ARTIFACT_READINESS_SCHEMA = "evolutionary-course-artifact-readiness-v1"
_TERMINAL = {"COMPLETED", "FAILED", "INCONCLUSIVE", "NOT_CONFIRMED"}
_REQUIRED_FLAGS = (
    "exact_source_identity_verified",
    "terminal_result_verified",
    "role_attribution_supported",
    "course_adapter_report_available",
    "reviewed_catalog_binding",
)
_FLAG_BLOCKERS = {
    "exact_source_identity_verified": "EXACT_SOURCE_IDENTITY_NOT_VERIFIED",
    "terminal_result_verified": "TERMINAL_RESULT_NOT_VERIFIED",
    "role_attribution_supported": "ROLE_ATTRIBUTION_UNSUPPORTED",
    "course_adapter_report_available": "COURSE_ADAPTER_REPORT_MISSING",
    "reviewed_catalog_binding": "REVIEWED_CATALOG_BINDING_MISSING",
}


class ArtifactReadinessError(ValueError):
    """Artifact inventory violates the research evidence boundary."""


def _text(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ArtifactReadinessError(f"{label} required")
    return result


def assess_artifact_readiness(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Assess, but never create, adapt, publish, or promote course evidence."""
    if not isinstance(manifest, Mapping) or set(manifest) != {
        "schema", "as_of", "minimum_distinct_lessons", "candidates", "authority"
    }:
        raise ArtifactReadinessError("manifest fields mismatch")
    if manifest.get("schema") != ARTIFACT_READINESS_SCHEMA:
        raise ArtifactReadinessError("manifest schema mismatch")
    minimum = manifest.get("minimum_distinct_lessons")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 3:
        raise ArtifactReadinessError("minimum must preserve longitudinal pilot boundary")
    if manifest.get("authority") != {
        "authority_class": "CANDIDATE_RESEARCH",
        "media_processing_allowed": False,
        "episode_creation_allowed": False,
        "curriculum_activation_allowed": False,
        "student_profile_write_allowed": False,
        "publication_allowed": False,
    }:
        raise ArtifactReadinessError("authority boundary mismatch")
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        raise ArtifactReadinessError("candidates must be a list")
    normalized = []
    lesson_ids: set[str] = set()
    for raw in candidates:
        expected = {
            "lesson_id", "source_name", "source_video_file_id", "source_job_id",
            "terminal_status", "evidence_refs", *_REQUIRED_FLAGS,
        }
        if not isinstance(raw, Mapping) or set(raw) != expected:
            raise ArtifactReadinessError("candidate fields mismatch")
        lesson_id = _text(raw.get("lesson_id"), "lesson_id")
        if lesson_id in lesson_ids:
            raise ArtifactReadinessError("duplicate lesson_id")
        lesson_ids.add(lesson_id)
        status = raw.get("terminal_status")
        if status not in _TERMINAL:
            raise ArtifactReadinessError("invalid terminal status")
        refs = raw.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(not str(ref).strip() for ref in refs):
            raise ArtifactReadinessError("evidence refs required")
        flags = {}
        for field in _REQUIRED_FLAGS:
            if raw.get(field) not in {True, False} or not isinstance(raw.get(field), bool):
                raise ArtifactReadinessError(f"{field} must be boolean")
            flags[field] = raw[field]
        if status != "COMPLETED" and flags["terminal_result_verified"]:
            raise ArtifactReadinessError("non-completed artifact cannot verify terminal result")
        blockers = [_FLAG_BLOCKERS[field] for field, value in flags.items() if not value]
        if status != "COMPLETED":
            blockers.insert(0, f"TERMINAL_STATUS_{status}")
        normalized.append({
            "lesson_id": lesson_id,
            "source_name": _text(raw.get("source_name"), "source_name"),
            "source_video_file_id": str(raw.get("source_video_file_id") or "").strip() or None,
            "source_job_id": str(raw.get("source_job_id") or "").strip() or None,
            "terminal_status": status,
            "evidence_refs": sorted(set(str(ref).strip() for ref in refs)),
            **flags,
            "eligible_for_longitudinal_pilot": not blockers,
            "blockers": blockers,
        })
    normalized.sort(key=lambda item: item["lesson_id"])
    eligible = [item["lesson_id"] for item in normalized if item["eligible_for_longitudinal_pilot"]]
    return {
        "schema": ARTIFACT_READINESS_SCHEMA,
        "as_of": _text(manifest.get("as_of"), "as_of"),
        "status": "READY" if len(eligible) >= minimum else "BLOCKED",
        "minimum_distinct_lessons": minimum,
        "candidate_count": len(normalized),
        "eligible_count": len(eligible),
        "eligible_lesson_ids": eligible,
        "candidates": normalized,
        "media_processed": False,
        "episode_created": False,
        "authority": dict(manifest["authority"]),
    }


__all__ = ["ARTIFACT_READINESS_SCHEMA", "ArtifactReadinessError", "assess_artifact_readiness"]
