"""Private-review request contract for evidence-bound learning episodes."""
from __future__ import annotations

from typing import Any, Mapping

from .contract import canonical_sha256, validate_episode
from .skill_catalog import resolve_reviewed_skill, validate_catalog

EPISODE_REVIEW_REQUEST_SCHEMA = "evolutionary-course-private-episode-review-request-v1"
_DECISIONS = ("ACCEPT", "REVISE", "REJECT")
_REVIEWER_AUTHORITIES = ("SCHOOL_DIRECTOR", "AUTHORIZED_EPISODE_REVIEWER")


class EpisodeReviewError(ValueError):
    """The episode cannot safely enter private review."""


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


__all__ = [
    "EPISODE_REVIEW_REQUEST_SCHEMA", "EpisodeReviewError",
    "build_episode_review_request",
]
