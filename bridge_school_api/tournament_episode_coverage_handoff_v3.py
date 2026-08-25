from __future__ import annotations

from typing import Any, Mapping

from .tournament_coverage_release_v3 import build_coverage_manifest
from .tournament_episode_scoring_authority_v3 import authorize_episode_scoring


class TournamentEpisodeCoverageHandoffError(ValueError):
    pass


_RESOLVED_BY_THIS_LAYER = {"EXPLICIT_EPISODE_SCORING_NOT_AVAILABLE"}


def build_episode_coverage_handoff(
    source: Mapping[str, Any],
    inventory: Mapping[str, Any],
    intake: Mapping[str, Any],
    decision_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    """Bridge teacher-authorized episode scores into the v1.4 coverage plan.

    Raw scoring is never authority. A scored candidate reaches slide coverage only
    after the corresponding teacher decision is explicitly
    CONFIRMED_TECHNICAL_RELEVANCE. DISMISSED candidates resolve without scores;
    PENDING/NEEDS_CONTEXT and confirmed-but-unscored candidates keep the handoff
    blocked. Independent upstream inventory gaps (for example non-DDS episode
    coverage) remain blockers and cannot be erased by this layer.
    """
    authority = authorize_episode_scoring(inventory, intake, decision_ledger)
    if authority.get("schema") != "tournament-episode-scoring-authority-v1":
        raise TournamentEpisodeCoverageHandoffError("unsupported episode scoring authority schema")

    episodes = authority.get("authorized_coverage_episode_inputs")
    if not isinstance(episodes, list):
        raise TournamentEpisodeCoverageHandoffError("authorized coverage episodes must be a list")

    upstream_blockers = sorted(
        {
            str(value)
            for value in (inventory.get("release_blockers") or [])
            if str(value) not in _RESOLVED_BY_THIS_LAYER
        }
    )
    adjudication_complete = authority.get("episode_adjudication_complete") is True
    inventory_complete = adjudication_complete and not upstream_blockers

    coverage_manifest = build_coverage_manifest(
        source,
        episodes=episodes,
        episode_inventory_complete=inventory_complete,
    )

    blockers = set(str(value) for value in coverage_manifest.get("release_blockers") or [])
    blockers.update(upstream_blockers)
    if int(authority.get("pending_decision_count") or 0):
        blockers.add("TEACHER_DECISION_PENDING")
    if int(authority.get("needs_context_count") or 0):
        blockers.add("TEACHER_CONTEXT_REQUIRED")
    if int(authority.get("confirmed_unscored_count") or 0):
        blockers.add("CONFIRMED_EPISODE_SCORING_NOT_COMPLETE")
    if not adjudication_complete:
        blockers.add("EPISODE_ADJUDICATION_NOT_COMPLETE")
    blockers = sorted(blockers)

    return {
        "schema": "tournament-episode-coverage-handoff-v2",
        "normative_algorithm_version": "1.4",
        "event_id": inventory.get("event_id"),
        "inventory_sha256": authority.get("inventory_sha256"),
        "candidate_count": authority.get("candidate_count"),
        "confirmed_decision_count": authority.get("confirmed_decision_count"),
        "dismissed_count": authority.get("dismissed_count"),
        "needs_context_count": authority.get("needs_context_count"),
        "pending_decision_count": authority.get("pending_decision_count"),
        "authorized_scored_count": authority.get("authorized_scored_count"),
        "confirmed_unscored_count": authority.get("confirmed_unscored_count"),
        "episode_adjudication_complete": adjudication_complete,
        "upstream_episode_inventory_blockers": upstream_blockers,
        "v1_4_episode_inventory_complete": inventory_complete,
        "coverage_episode_count": len(episodes),
        "coverage_manifest": coverage_manifest,
        "handoff_ready": inventory_complete and coverage_manifest.get("coverage_plan_release_ready") is True,
        "handoff_blockers": blockers,
        "teacher_decision_gate_enforced": True,
        "automatic_teacher_decisions_used": False,
        "automatic_episode_scoring_used": False,
        "automatic_methodology_mapping_used": False,
        "automatic_student_error_attribution_used": False,
        "causal_error_attribution_allowed": False,
        "interpretation": (
            "Only explicitly teacher-confirmed technical candidates with explicit 0..2 scores may enter v1.4 slide coverage. "
            "Dismissal resolves a technical candidate without creating an episode. Pending/context-required decisions, "
            "confirmed-but-unscored candidates, and independent upstream inventory gaps remain fail-closed blockers."
        ),
    }
