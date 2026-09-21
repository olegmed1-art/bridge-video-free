"""Orchestrate teacher-video candidates through the AI-only Canon gate."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from bridge_contracts.video_canon_ai_promotion import (
    VideoCanonAIPromotionError,
    build_ai_canon_promotion,
)
from bridge_contracts.video_canon_evidence import (
    VideoCanonEvidenceError,
    build_video_canon_candidate,
)


SCHEMA = "video-canon-auto-pipeline-v1"


def run_video_canon_auto_pipeline(
    learning_candidate: Mapping[str, Any],
    assertions: Sequence[Mapping[str, Any]],
    verification_by_assertion_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Produce only sealed activation commands or explicit fail-closed gaps."""
    candidates: list[dict[str, Any]] = []
    promotion_commands: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []
    if not isinstance(assertions, Sequence) or isinstance(assertions, (str, bytes)):
        raise ValueError("assertions must be a sequence")
    if not isinstance(verification_by_assertion_id, Mapping):
        raise ValueError("verification_by_assertion_id must be an object")

    for raw in assertions:
        assertion_id = str(raw.get("assertion_id") or "UNKNOWN") if isinstance(raw, Mapping) else "UNKNOWN"
        try:
            candidate = build_video_canon_candidate(learning_candidate, raw)
        except VideoCanonEvidenceError as exc:
            gaps.append({
                "assertion_id": assertion_id,
                "status": "EVIDENCE_REJECTED",
                "reason": str(exc),
            })
            continue
        candidates.append(candidate)
        if candidate["quality_status"] != "AI_VERIFICATION_PENDING":
            gaps.append({
                "assertion_id": assertion_id,
                "status": "EVIDENCE_ONLY",
                "reason": "candidate did not reach the AI verification threshold",
            })
            continue
        verification = verification_by_assertion_id.get(assertion_id)
        if verification is None:
            gaps.append({
                "assertion_id": assertion_id,
                "status": "AI_VERIFICATION_PENDING",
                "reason": "sealed verification bundle missing",
            })
            continue
        try:
            promotion = build_ai_canon_promotion(candidate, verification)
        except VideoCanonAIPromotionError as exc:
            gaps.append({
                "assertion_id": assertion_id,
                "status": "AI_VERIFICATION_FAILED",
                "reason": str(exc),
            })
            continue
        promotion_commands.append(promotion)

    return {
        "schema": SCHEMA,
        "status": "AUTO_PROMOTION_READY" if promotion_commands else "NO_PROMOTION_READY",
        "candidates": candidates,
        "promotion_commands": promotion_commands,
        "gaps": gaps,
        "human_approval_required": False,
        "world_lookup_performed": False,
        "authoritative_write_performed": False,
    }


__all__ = ["SCHEMA", "run_video_canon_auto_pipeline"]
