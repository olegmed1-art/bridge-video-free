from __future__ import annotations

from typing import Any, Mapping

from .tournament_coverage_release_v3 import build_coverage_manifest
from .tournament_episode_scoring_authority_v3 import authorize_episode_scoring
from .tournament_episode_source_census_v3 import source_facts_sha256


class TournamentEpisodeCoverageHandoffError(ValueError):
    pass


_RESOLVED_BY_SCORING = {"EXPLICIT_EPISODE_SCORING_NOT_AVAILABLE"}
_NON_DDS_BLOCKER = "NON_DDS_EPISODE_COVERAGE_NOT_ESTABLISHED"


def _validate_source_census(source: Mapping[str, Any], census: Mapping[str, Any]) -> tuple[bool, list[str]]:
    if census.get("schema") != "tournament-episode-source-census-v1":
        raise TournamentEpisodeCoverageHandoffError("unsupported episode source census schema")
    if census.get("normative_algorithm_version") != "1.4":
        raise TournamentEpisodeCoverageHandoffError("episode source census algorithm boundary mismatch")
    if census.get("source_facts_sha256") != source_facts_sha256(source):
        raise TournamentEpisodeCoverageHandoffError("episode source census is not bound to exact facts")
    tournament = source.get("tournament")
    provider_key = str(tournament.get("provider_native_key") or "") if isinstance(tournament, Mapping) else ""
    if census.get("provider_native_key") != provider_key:
        raise TournamentEpisodeCoverageHandoffError("episode source census provider identity mismatch")
    for field in (
        "automatic_episode_creation_allowed",
        "automatic_methodology_mapping_allowed",
        "automatic_student_error_attribution_allowed",
    ):
        if census.get(field) is not False:
            raise TournamentEpisodeCoverageHandoffError(f"episode source census boundary was weakened: {field}")
    if census.get("unavailable_evidence_not_reconstructed") is not True:
        raise TournamentEpisodeCoverageHandoffError("episode source census may not reconstruct unavailable evidence")
    blockers = census.get("census_blockers")
    if not isinstance(blockers, list):
        raise TournamentEpisodeCoverageHandoffError("episode source census blockers must be a list")
    complete = census.get("non_dd_episode_source_census_complete") is True
    if complete and blockers:
        raise TournamentEpisodeCoverageHandoffError("complete episode source census cannot have blockers")
    if not complete and not blockers:
        raise TournamentEpisodeCoverageHandoffError("incomplete episode source census must expose blockers")
    return complete, [str(value) for value in blockers]


def build_episode_coverage_handoff(
    source: Mapping[str, Any],
    inventory: Mapping[str, Any],
    intake: Mapping[str, Any],
    decision_ledger: Mapping[str, Any],
    *,
    source_census: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bridge teacher-authorized episode scores into the v1.4 coverage plan.

    Raw scoring is never authority. A scored candidate reaches slide coverage only
    after the corresponding teacher decision is explicitly
    CONFIRMED_TECHNICAL_RELEVANCE. Independent non-DDS coverage may be resolved only
    by an exact-source-bound evidence census; absent auction/play is recorded as a
    limitation and never reconstructed.
    """
    authority = authorize_episode_scoring(inventory, intake, decision_ledger)
    if authority.get("schema") != "tournament-episode-scoring-authority-v1":
        raise TournamentEpisodeCoverageHandoffError("unsupported episode scoring authority schema")

    episodes = authority.get("authorized_coverage_episode_inputs")
    if not isinstance(episodes, list):
        raise TournamentEpisodeCoverageHandoffError("authorized coverage episodes must be a list")

    resolved = set(_RESOLVED_BY_SCORING)
    census_complete = False
    census_blockers: list[str] = []
    if source_census is not None:
        census_complete, census_blockers = _validate_source_census(source, source_census)
        if census_complete:
            resolved.add(_NON_DDS_BLOCKER)

    upstream_blockers = sorted(
        {
            str(value)
            for value in (inventory.get("release_blockers") or [])
            if str(value) not in resolved
        }
    )
    adjudication_complete = authority.get("episode_adjudication_complete") is True
    inventory_complete = adjudication_complete and not upstream_blockers and not census_blockers

    coverage_manifest = build_coverage_manifest(
        source,
        episodes=episodes,
        episode_inventory_complete=inventory_complete,
    )

    blockers = set(str(value) for value in coverage_manifest.get("release_blockers") or [])
    blockers.update(upstream_blockers)
    blockers.update(census_blockers)
    if int(authority.get("pending_decision_count") or 0):
        blockers.add("TEACHER_DECISION_PENDING")
    if int(authority.get("needs_context_count") or 0):
        blockers.add("TEACHER_CONTEXT_REQUIRED")
    if int(authority.get("confirmed_unscored_count") or 0):
        blockers.add("CONFIRMED_EPISODE_SCORING_NOT_COMPLETE")
    if not adjudication_complete:
        blockers.add("EPISODE_ADJUDICATION_NOT_COMPLETE")
    if source_census is not None and not census_complete:
        blockers.add("NON_DDS_SOURCE_CENSUS_NOT_COMPLETE")
    blockers = sorted(blockers)

    return {
        "schema": "tournament-episode-coverage-handoff-v3",
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
        "non_dd_source_census_supplied": source_census is not None,
        "non_dd_source_census_complete": census_complete,
        "non_dd_source_census_blockers": census_blockers,
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
            "A complete exact-source-bound non-DDS census may close only the non-DDS coverage-accounting gap; it does not "
            "turn unavailable auction/play into evidence or create a pedagogical conclusion."
        ),
    }
