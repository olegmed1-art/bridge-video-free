"""Deterministic manual methodology queue for unresolved lesson skill wording."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from .skill_catalog import SkillCatalogError, validate_catalog

QUEUE_SCHEMA = "evolutionary-course-methodology-review-queue-v1"
DECISION_SCHEMA = "evolutionary-course-methodology-review-decision-v1"
_ALLOWED_DECISIONS = (
    "MAP_EXISTING_SKILL", "PROPOSE_NEW_CANDIDATE", "DEFER", "REJECT"
)
_CANDIDATE_ID = re.compile(r"^candidate\.skill\.[a-z0-9][a-z0-9._-]{2,95}$")


class MethodologyQueueError(ValueError):
    """Review queue input or decision is unsafe."""


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _queue_id(job_id: str, video_id: str, interaction_id: str, wording: str) -> str:
    raw = "|".join((job_id, video_id, interaction_id, wording.casefold()))
    return "methodology.review." + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def build_methodology_review_queue(adapter_reports: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(adapter_reports, list):
        raise MethodologyQueueError("adapter reports must be a list")
    items: dict[str, dict[str, Any]] = {}
    source_jobs: set[str] = set()
    for report in adapter_reports:
        if not isinstance(report, Mapping):
            raise MethodologyQueueError("adapter report must be an object")
        authority = report.get("authority")
        if not isinstance(authority, Mapping) or any(
            authority.get(field) is not False
            for field in (
                "canonical_promotion_allowed", "curriculum_activation_allowed",
                "student_profile_write_allowed", "publication_allowed",
            )
        ):
            raise MethodologyQueueError("adapter authority boundary mismatch")
        job_id = _text(report.get("source_job_id"))
        if not job_id:
            raise MethodologyQueueError("source job identity required")
        source_jobs.add(job_id)
        rejected = report.get("rejected_interactions")
        if not isinstance(rejected, list):
            raise MethodologyQueueError("rejected interactions required")
        for entry in rejected:
            if not isinstance(entry, Mapping):
                continue
            if "SKILL_WORDING_NOT_REVIEWED" not in entry.get("reason_codes", []):
                continue
            candidate = entry.get("review_candidate")
            if not isinstance(candidate, Mapping):
                raise MethodologyQueueError("methodology candidate provenance required")
            interaction_id = _text(entry.get("interaction_id"))
            wording = _text(candidate.get("task_wording"))
            video_id = _text(candidate.get("video_file_id"))
            source_name = _text(candidate.get("source_name"))
            transcript_refs = candidate.get("transcript_segment_ids")
            if not interaction_id or not wording or not video_id or not source_name:
                raise MethodologyQueueError("incomplete methodology candidate identity")
            if not isinstance(transcript_refs, list) or not transcript_refs:
                raise MethodologyQueueError("transcript evidence required")
            try:
                start = float(candidate.get("start_seconds"))
                end = float(candidate.get("end_seconds"))
            except (TypeError, ValueError) as exc:
                raise MethodologyQueueError("invalid candidate interval") from exc
            if start < 0 or end <= start:
                raise MethodologyQueueError("invalid candidate interval")
            queue_id = _queue_id(job_id, video_id, interaction_id, wording)
            item = {
                "queue_id": queue_id,
                "status": "OPEN",
                "source_job_id": job_id,
                "interaction_id": interaction_id,
                "task_wording": wording,
                "source": {
                    "video_file_id": video_id,
                    "source_name": source_name,
                    "start_seconds": start,
                    "end_seconds": end,
                    "transcript_segment_ids": list(transcript_refs),
                },
                "allowed_decisions": list(_ALLOWED_DECISIONS),
            }
            existing = items.get(queue_id)
            if existing is not None and existing != item:
                raise MethodologyQueueError("conflicting duplicate queue item")
            items[queue_id] = item
    return {
        "schema": QUEUE_SCHEMA,
        "source_job_ids": sorted(source_jobs),
        "item_count": len(items),
        "items": [items[key] for key in sorted(items)],
        "authority": {
            "authority_class": "CANDIDATE_RESEARCH",
            "catalog_mutation_allowed": False,
            "canonical_promotion_allowed": False,
            "curriculum_activation_allowed": False,
            "student_profile_write_allowed": False,
            "publication_allowed": False,
        },
    }


def record_methodology_decision(
    item: Mapping[str, Any],
    *,
    decision: str,
    catalog: Mapping[str, Any],
    target_skill_id: str | None = None,
    proposed_candidate_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(item, Mapping) or item.get("status") != "OPEN":
        raise MethodologyQueueError("open queue item required")
    if decision not in _ALLOWED_DECISIONS:
        raise MethodologyQueueError("invalid methodology decision")
    normalized = validate_catalog(catalog)
    reviewed = {
        skill["skill_id"]
        for skill in normalized["skills"]
        if skill["review_state"] == "APPROVED_CANDIDATE"
    }
    if decision == "MAP_EXISTING_SKILL":
        if target_skill_id not in reviewed or proposed_candidate_id is not None:
            raise MethodologyQueueError("mapping requires reviewed catalog skill")
    elif decision == "PROPOSE_NEW_CANDIDATE":
        if not proposed_candidate_id or not _CANDIDATE_ID.fullmatch(proposed_candidate_id):
            raise MethodologyQueueError("valid proposed candidate id required")
        if proposed_candidate_id in {skill["skill_id"] for skill in normalized["skills"]}:
            raise MethodologyQueueError("proposed candidate already exists")
        if target_skill_id is not None:
            raise MethodologyQueueError("proposal cannot map existing skill")
    elif target_skill_id is not None or proposed_candidate_id is not None:
        raise MethodologyQueueError("decision carries forbidden skill target")
    return {
        "schema": DECISION_SCHEMA,
        "queue_id": item.get("queue_id"),
        "decision": decision,
        "target_skill_id": target_skill_id,
        "proposed_candidate_id": proposed_candidate_id,
        "catalog_version": normalized["catalog_version"],
        "catalog_mutated": False,
        "school_canon_mutated": False,
        "curriculum_activated": False,
        "student_profile_written": False,
        "publication_allowed": False,
        "review_required": True,
    }


__all__ = [
    "DECISION_SCHEMA", "MethodologyQueueError", "QUEUE_SCHEMA",
    "build_methodology_review_queue", "record_methodology_decision",
]
