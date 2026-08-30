"""Private-review request contract for evidence-bound learning episodes."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Mapping

from .contract import canonical_sha256, validate_episode
from .skill_catalog import resolve_reviewed_skill, validate_catalog

EPISODE_REVIEW_REQUEST_SCHEMA = "evolutionary-course-private-episode-review-request-v1"
EPISODE_REVIEW_DECISION_SCHEMA = "evolutionary-course-private-episode-review-decision-v1"
_DECISIONS = ("ACCEPT", "REVISE", "REJECT")
_REVIEWER_AUTHORITIES = ("SCHOOL_DIRECTOR", "AUTHORIZED_EPISODE_REVIEWER")


class EpisodeReviewError(ValueError):
    """The episode cannot safely enter private review."""


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _reviewed_at(value: Any) -> str:
    text = _text(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EpisodeReviewError("valid reviewed_at required") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EpisodeReviewError("reviewed_at timezone required")
    return text


def build_episode_review_request(
    episode: Mapping[str, Any], *, catalog: Mapping[str, Any]
) -> dict[str, Any]:
    normalized = validate_episode(episode)
    normalized_catalog = validate_catalog(catalog)
    skill_id = normalized["learning_task"]["skill_id"]
    try:
        resolved = resolve_reviewed_skill(
            normalized_catalog, normalized["learning_task"]["title"]
        )
    except ValueError as exc:
        raise EpisodeReviewError("episode wording is not an approved catalog skill") from exc
    if resolved != skill_id:
        raise EpisodeReviewError("episode skill binding mismatch")
    if normalized["authority"]["review_state"] != "APPROVED_CANDIDATE":
        raise EpisodeReviewError("approved candidate episode required")
    transition = normalized["mastery_transition"]
    return {
        "schema": EPISODE_REVIEW_REQUEST_SCHEMA,
        "status": "AWAITING_PRIVATE_REVIEW",
        "episode_id": normalized["episode_id"],
        "episode_sha256": canonical_sha256(normalized),
        "skill_id": skill_id,
        "catalog_version": normalized_catalog["catalog_version"],
        "review_summary": {
            "occurred_at": normalized["occurred_at"],
            "outcome": normalized["interaction"]["outcome"],
            "support_level": normalized["interaction"]["support_level"],
            "mastery_from_state": transition["from_state"],
            "mastery_to_state": transition["to_state"],
            "claim_count": len(normalized["claims"]),
            "transcript_evidence_count": len(normalized["source"]["transcript_segment_ids"]),
            "visual_evidence_count": len(normalized["source"]["frame_sha256"]),
        },
        "allowed_decisions": list(_DECISIONS),
        "allowed_reviewer_authorities": list(_REVIEWER_AUTHORITIES),
        "decision_input": {
            "decision": None,
            "reviewer_id": None,
            "reviewer_authority": None,
            "reviewed_at": None,
            "rationale": None,
        },
        "authority": {
            "episode_persistence_allowed": False,
            "canonical_promotion_allowed": False,
            "curriculum_activation_allowed": False,
            "student_profile_write_allowed": False,
            "publication_allowed": False,
        },
    }


def record_episode_review_decision(
    episode: Mapping[str, Any], *, catalog: Mapping[str, Any], decision: str,
    reviewer_id: str, reviewer_authority: str, reviewed_at: str, rationale: str,
) -> dict[str, Any]:
    request = build_episode_review_request(episode, catalog=catalog)
    if decision not in _DECISIONS:
        raise EpisodeReviewError("invalid private episode review decision")
    reviewer_id = _text(reviewer_id)
    rationale = _text(rationale)
    if not reviewer_id:
        raise EpisodeReviewError("reviewer identity required")
    if reviewer_authority not in _REVIEWER_AUTHORITIES:
        raise EpisodeReviewError("authorized episode reviewer required")
    if not rationale:
        raise EpisodeReviewError("review rationale required")
    reviewed_at = _reviewed_at(reviewed_at)
    disposition = {
        "ACCEPT": "PRIVATE_RESEARCH_ACCEPTED",
        "REVISE": "REVISION_REQUIRED",
        "REJECT": "REJECTED",
    }[decision]
    return {
        "schema": EPISODE_REVIEW_DECISION_SCHEMA,
        "episode_id": request["episode_id"],
        "episode_sha256": request["episode_sha256"],
        "skill_id": request["skill_id"],
        "catalog_version": request["catalog_version"],
        "decision": decision,
        "disposition": disposition,
        "reviewer": {"reviewer_id": reviewer_id, "authority": reviewer_authority},
        "reviewed_at": reviewed_at,
        "rationale": rationale,
        "episode_persisted": False,
        "canonical_promoted": False,
        "curriculum_activated": False,
        "student_profile_written": False,
        "publication_allowed": False,
        "follow_up_required": True,
    }


__all__ = [
    "EPISODE_REVIEW_DECISION_SCHEMA", "EPISODE_REVIEW_REQUEST_SCHEMA",
    "EpisodeReviewError", "build_episode_review_request",
    "record_episode_review_decision",
]
