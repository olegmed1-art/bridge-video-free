"""Convert legacy report pointers into private manual-annotation candidates."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

LEGACY_REPORT_SCHEMA = "evolutionary-course-legacy-report-pointers-v1"
ANNOTATION_QUEUE_SCHEMA = "evolutionary-course-legacy-annotation-queue-v1"
_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{10,128}$")


class LegacyReportAdapterError(ValueError):
    """Legacy report metadata is unsafe or incomplete."""


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _candidate_id(report_id: str, pointer_id: str, label: str) -> str:
    raw = f"{report_id}|{pointer_id}|{label.casefold()}"
    return "legacy.annotation." + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def adapt_legacy_report_pointers(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LegacyReportAdapterError("payload must be an object")
    if set(payload) != {"schema", "report", "source_video_candidate", "pointers"}:
        raise LegacyReportAdapterError("payload fields mismatch")
    if payload.get("schema") != LEGACY_REPORT_SCHEMA:
        raise LegacyReportAdapterError("schema mismatch")
    report = payload.get("report")
    if not isinstance(report, Mapping) or set(report) != {
        "drive_file_id", "title", "sha256", "content_extraction_status"
    }:
        raise LegacyReportAdapterError("report identity fields mismatch")
    report_id = _text(report.get("drive_file_id"))
    report_title = _text(report.get("title"))
    report_sha = _text(report.get("sha256")).lower()
    if not _DRIVE_ID.fullmatch(report_id) or not report_title:
        raise LegacyReportAdapterError("exact report identity required")
    if not re.fullmatch(r"[0-9a-f]{64}", report_sha):
        raise LegacyReportAdapterError("report sha256 required")
    if report.get("content_extraction_status") not in {"TEXT_EXTRACTED", "OCR_EXTRACTED"}:
        raise LegacyReportAdapterError("bounded report extraction required")
    source = payload.get("source_video_candidate")
    if not isinstance(source, Mapping) or set(source) != {
        "video_file_id", "source_name", "identity_status"
    }:
        raise LegacyReportAdapterError("source candidate fields mismatch")
    video_id = _text(source.get("video_file_id"))
    source_name = _text(source.get("source_name"))
    if source.get("identity_status") not in {"CANDIDATE", "REPORT_STATED"}:
        raise LegacyReportAdapterError("legacy report cannot verify source identity")
    if not video_id or not source_name:
        raise LegacyReportAdapterError("source candidate identity required")
    pointers = payload.get("pointers")
    if not isinstance(pointers, list):
        raise LegacyReportAdapterError("pointers must be a list")
    items: dict[str, dict[str, Any]] = {}
    for pointer in pointers:
        if not isinstance(pointer, Mapping) or set(pointer) != {
            "pointer_id", "report_section", "topic_label",
            "approx_start_seconds", "approx_end_seconds"
        }:
            raise LegacyReportAdapterError("pointer fields mismatch")
        pointer_id = _text(pointer.get("pointer_id"))
        section = _text(pointer.get("report_section"))
        label = _text(pointer.get("topic_label"))
        if not pointer_id or not section or not label:
            raise LegacyReportAdapterError("pointer identity required")
        start_raw = pointer.get("approx_start_seconds")
        end_raw = pointer.get("approx_end_seconds")
        if (start_raw is None) != (end_raw is None):
            raise LegacyReportAdapterError("approximate interval must be complete")
        start = end = None
        if start_raw is not None:
            try:
                start, end = float(start_raw), float(end_raw)
            except (TypeError, ValueError) as exc:
                raise LegacyReportAdapterError("invalid approximate interval") from exc
            if start < 0 or end <= start or end - start > 7200:
                raise LegacyReportAdapterError("invalid approximate interval")
        candidate_id = _candidate_id(report_id, pointer_id, label)
        item = {
            "candidate_id": candidate_id,
            "evidence_state": "LEGACY_REPORT_POINTER",
            "status": "MANUAL_SOURCE_REVIEW_REQUIRED",
            "report": {
                "drive_file_id": report_id,
                "title": report_title,
                "sha256": report_sha,
                "report_section": section,
                "pointer_id": pointer_id,
            },
            "source_video_candidate": {
                "video_file_id": video_id,
                "source_name": source_name,
                "identity_status": source.get("identity_status"),
                "approx_start_seconds": start,
                "approx_end_seconds": end,
            },
            "topic_label": label,
            "required_before_episode": [
                "EXACT_SOURCE_IDENTITY_VERIFIED",
                "EXACT_INTERVAL_VERIFIED",
                "TRANSCRIPT_OR_FRAME_EVIDENCE_BOUND",
                "ACTOR_ATTRIBUTION_SUPPORTED",
                "TASK_ACTION_INTERVENTION_FOLLOWUP_OUTCOME_VERIFIED",
                "REVIEWED_SKILL_CATALOG_BINDING",
            ],
        }
        existing = items.get(candidate_id)
        if existing is not None and existing != item:
            raise LegacyReportAdapterError("conflicting duplicate pointer")
        items[candidate_id] = item
    return {
        "schema": ANNOTATION_QUEUE_SCHEMA,
        "report_drive_file_id": report_id,
        "candidate_count": len(items),
        "candidates": [items[key] for key in sorted(items)],
        "episode_creation_allowed": False,
        "mastery_inference_allowed": False,
        "longitudinal_pilot_input_allowed": False,
        "authority": {
            "authority_class": "CANDIDATE_RESEARCH",
            "canonical_promotion_allowed": False,
            "catalog_mutation_allowed": False,
            "curriculum_activation_allowed": False,
            "student_profile_write_allowed": False,
            "publication_allowed": False,
        },
    }


__all__ = [
    "ANNOTATION_QUEUE_SCHEMA", "LEGACY_REPORT_SCHEMA",
    "LegacyReportAdapterError", "adapt_legacy_report_pointers",
]
