from __future__ import annotations

from typing import Any, Mapping, Sequence

from .tournament_coverage_release_v3 import build_release_gate


class TournamentPortfolioReleaseGateError(ValueError):
    pass


def _provider_event_id(preanalysis_gate: Mapping[str, Any]) -> str:
    tournament = preanalysis_gate.get("tournament")
    if not isinstance(tournament, Mapping):
        raise TournamentPortfolioReleaseGateError("preanalysis tournament metadata missing")
    provider_key = str(tournament.get("provider_native_key") or "").strip()
    prefix = "bridge.co.il:event:"
    marker = ":round:"
    if not provider_key.startswith(prefix) or marker not in provider_key:
        raise TournamentPortfolioReleaseGateError("unsupported provider_native_key for event release binding")
    event_id = provider_key[len(prefix):].split(marker, 1)[0].strip()
    if not event_id:
        raise TournamentPortfolioReleaseGateError("provider event id missing")
    return event_id


def _validate_portfolio_coverage_handoff(
    handoff: Mapping[str, Any],
    *,
    expected_event: str,
    expected_portfolio_id: str,
    expected_review_count: int,
    coverage_manifest: Mapping[str, Any],
) -> None:
    if handoff.get("schema") != "tournament-portfolio-episode-coverage-handoff-v1":
        raise TournamentPortfolioReleaseGateError("unsupported portfolio episode coverage handoff schema")
    if handoff.get("normative_algorithm_version") != "1.4":
        raise TournamentPortfolioReleaseGateError("portfolio coverage handoff normative version mismatch")
    if str(handoff.get("event_id") or "") != expected_event:
        raise TournamentPortfolioReleaseGateError("portfolio coverage handoff event does not match preanalysis source")
    if str(handoff.get("portfolio_id") or "") != expected_portfolio_id:
        raise TournamentPortfolioReleaseGateError("portfolio coverage handoff identity mismatch")
    if int(handoff.get("event_review_item_count") or 0) != expected_review_count:
        raise TournamentPortfolioReleaseGateError("portfolio coverage/review cardinality mismatch")
    if handoff.get("coverage_manifest") != coverage_manifest:
        raise TournamentPortfolioReleaseGateError("release coverage manifest is not the exact portfolio handoff manifest")
    if handoff.get("portfolio_complete_for_event") is not True:
        raise TournamentPortfolioReleaseGateError("portfolio coverage handoff is not complete for event")
    if handoff.get("teacher_decision_gate_enforced") is not True:
        raise TournamentPortfolioReleaseGateError("portfolio coverage teacher decision gate not enforced")
    for field in (
        "cross_category_causal_collapse_allowed",
        "automatic_teacher_decisions_used",
        "automatic_episode_scoring_used",
        "automatic_methodology_mapping_used",
        "automatic_student_error_attribution_used",
        "causal_error_attribution_allowed",
    ):
        if handoff.get(field) is not False:
            raise TournamentPortfolioReleaseGateError(f"portfolio coverage boundary weakened: {field}")


def build_portfolio_aware_release_gate(
    *,
    preanalysis_gate: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    mp_availability: Mapping[str, Any],
    event_teacher_review_gate: Mapping[str, Any],
    portfolio_episode_coverage_handoff: Mapping[str, Any] | None = None,
    rendered_slide_keys: Sequence[str] | None = None,
    visual_qa_pass: bool | None = None,
) -> dict[str, Any]:
    """Compose v1.4 release with the complete review portfolio and portfolio-derived coverage.

    Release must not rely on a stale legacy coverage manifest after additive evidence
    batches appear. The event teacher-review gate proves that every current technical
    review received an explicit teacher disposition. The portfolio episode coverage
    handoff separately proves that every teacher-confirmed review was explicitly scored,
    that non-DDS source channels were censused, and that the exact resulting event
    coverage manifest is the one passed into the final release gate.
    """
    base = build_release_gate(
        preanalysis_gate=preanalysis_gate,
        coverage_manifest=coverage_manifest,
        mp_availability=mp_availability,
        rendered_slide_keys=rendered_slide_keys,
        visual_qa_pass=visual_qa_pass,
    )
    if event_teacher_review_gate.get("schema") != "tournament-event-teacher-review-release-gate-v1":
        raise TournamentPortfolioReleaseGateError("unsupported event teacher-review gate schema")
    if event_teacher_review_gate.get("normative_algorithm_version") != "1.4":
        raise TournamentPortfolioReleaseGateError("teacher-review gate normative version mismatch")

    expected_event = _provider_event_id(preanalysis_gate)
    if str(event_teacher_review_gate.get("event_id") or "") != expected_event:
        raise TournamentPortfolioReleaseGateError("teacher-review gate event does not match preanalysis source")
    portfolio_id = str(event_teacher_review_gate.get("portfolio_id") or "").strip()
    if not portfolio_id:
        raise TournamentPortfolioReleaseGateError("teacher-review gate lacks portfolio identity")
    review_count = int(event_teacher_review_gate.get("review_item_count", 0))
    if review_count <= 0:
        raise TournamentPortfolioReleaseGateError("teacher-review gate review cardinality missing")
    for field in (
        "cross_category_causal_collapse_allowed",
        "automatic_episode_scoring_allowed",
        "automatic_methodology_mapping_allowed",
        "automatic_student_error_attribution_allowed",
        "causal_error_attribution_allowed",
    ):
        if event_teacher_review_gate.get(field) is not False:
            raise TournamentPortfolioReleaseGateError(f"teacher-review boundary weakened: {field}")

    blockers = list(base.get("hard_stop_conditions") or [])
    if event_teacher_review_gate.get("teacher_review_release_ready") is not True:
        blockers.extend(str(value) for value in event_teacher_review_gate.get("release_blockers") or [])
        if not event_teacher_review_gate.get("release_blockers"):
            blockers.append("TEACHER_REVIEW_PORTFOLIO_NOT_READY")

    portfolio_coverage_ready = False
    portfolio_coverage_blockers: list[str] = []
    if portfolio_episode_coverage_handoff is None:
        portfolio_coverage_blockers.append("PORTFOLIO_EPISODE_COVERAGE_HANDOFF_REQUIRED")
    else:
        _validate_portfolio_coverage_handoff(
            portfolio_episode_coverage_handoff,
            expected_event=expected_event,
            expected_portfolio_id=portfolio_id,
            expected_review_count=review_count,
            coverage_manifest=coverage_manifest,
        )
        portfolio_coverage_ready = portfolio_episode_coverage_handoff.get("handoff_ready") is True
        if not portfolio_coverage_ready:
            portfolio_coverage_blockers.extend(
                str(value) for value in portfolio_episode_coverage_handoff.get("handoff_blockers") or []
            )
            if not portfolio_coverage_blockers:
                portfolio_coverage_blockers.append("PORTFOLIO_EPISODE_COVERAGE_NOT_READY")
    blockers.extend(portfolio_coverage_blockers)

    blockers = sorted(set(blockers))
    return {
        **base,
        "schema": "tournament-v1.4-portfolio-aware-release-gate-v2",
        "event_id": expected_event,
        "teacher_review_portfolio_id": portfolio_id,
        "teacher_review_item_count": review_count,
        "teacher_review_unresolved_count": int(event_teacher_review_gate.get("unresolved_review_count", 0)),
        "teacher_review_release_ready": event_teacher_review_gate.get("teacher_review_release_ready") is True,
        "portfolio_episode_coverage_handoff_supplied": portfolio_episode_coverage_handoff is not None,
        "portfolio_episode_coverage_ready": portfolio_coverage_ready,
        "portfolio_episode_coverage_blockers": sorted(set(portfolio_coverage_blockers)),
        "hard_stop_conditions": blockers,
        "final_report_release_ready": not blockers,
        "portfolio_teacher_decision_gate_enforced": True,
        "portfolio_episode_coverage_gate_enforced": True,
        "automatic_episode_scoring_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "automatic_methodology_invention_allowed": False,
    }
