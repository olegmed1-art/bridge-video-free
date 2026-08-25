from __future__ import annotations

from typing import Any, Mapping

from .tournament_coverage_release_v3 import build_coverage_manifest
from .tournament_episode_scoring_intake_v3 import validate_episode_scoring_intake


class TournamentEpisodeCoverageHandoffError(ValueError):
    pass


def build_episode_coverage_handoff(
    source: Mapping[str, Any],
    inventory: Mapping[str, Any],
    intake: Mapping[str, Any],
) -> dict[str, Any]:
    """Bridge explicit episode scoring into the v1.4 coverage plan.

    This layer is deliberately inert until every evidence candidate has an explicit
    adjudication accepted by ``validate_episode_scoring_intake``. Pending candidates
    never disappear silently and never enter slide coverage with inferred scores.
    """
    validation = validate_episode_scoring_intake(inventory, intake)
    if validation.get("schema") != "tournament-episode-scoring-validation-v1":
        raise TournamentEpisodeCoverageHandoffError("unsupported scoring validation schema")

    complete = validation.get("episode_scoring_complete") is True
    episodes = validation.get("coverage_episode_inputs")
    if not isinstance(episodes, list):
        raise TournamentEpisodeCoverageHandoffError("coverage_episode_inputs must be a list")

    coverage_manifest = build_coverage_manifest(
        source,
        episodes=episodes,
        episode_inventory_complete=complete,
    )

    blockers = list(coverage_manifest.get("release_blockers") or [])
    if not complete:
        blockers.append("EPISODE_SCORING_NOT_COMPLETE")
    blockers = sorted(set(str(value) for value in blockers))

    if complete and validation.get("pending_scoring_count") != 0:
        raise TournamentEpisodeCoverageHandoffError("complete scoring cannot have pending rows")
    if not complete and validation.get("pending_scoring_count") == 0:
        raise TournamentEpisodeCoverageHandoffError("incomplete scoring must retain pending rows")

    return {
        "schema": "tournament-episode-coverage-handoff-v1",
        "normative_algorithm_version": "1.4",
        "event_id": inventory.get("event_id"),
        "inventory_sha256": validation.get("inventory_sha256"),
        "candidate_count": validation.get("candidate_count"),
        "explicitly_scored_count": validation.get("explicitly_scored_count"),
        "pending_scoring_count": validation.get("pending_scoring_count"),
        "episode_scoring_complete": complete,
        "coverage_episode_count": len(episodes),
        "coverage_manifest": coverage_manifest,
        "handoff_ready": complete and coverage_manifest.get("coverage_plan_release_ready") is True,
        "handoff_blockers": blockers,
        "automatic_episode_scoring_used": False,
        "automatic_methodology_mapping_used": False,
        "automatic_student_error_attribution_used": False,
        "interpretation": (
            "Only explicitly adjudicated 0..2 episode scores may enter v1.4 slide coverage. "
            "If any evidence candidate remains PENDING_SCORING, the coverage handoff stays blocked."
        ),
    }
