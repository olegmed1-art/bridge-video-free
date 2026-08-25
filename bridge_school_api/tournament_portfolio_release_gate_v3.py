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


def build_portfolio_aware_release_gate(
    *,
    preanalysis_gate: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    mp_availability: Mapping[str, Any],
    event_teacher_review_gate: Mapping[str, Any],
    rendered_slide_keys: Sequence[str] | None = None,
    visual_qa_pass: bool | None = None,
) -> dict[str, Any]:
    """Compose the existing v1.4 release gate with the complete event review portfolio.

    This wrapper closes a coverage gap created by additive technical evidence batches:
    an older episode inventory must not allow release while a newer review batch for
    the same event is still PENDING/NEEDS_CONTEXT. Teacher confirmation remains only
    technical relevance; episode scoring and pedagogy stay separate explicit gates.
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
    if not str(event_teacher_review_gate.get("portfolio_id") or "").strip():
        raise TournamentPortfolioReleaseGateError("teacher-review gate lacks portfolio identity")
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

    blockers = sorted(set(blockers))
    return {
        **base,
        "schema": "tournament-v1.4-portfolio-aware-release-gate-v1",
        "event_id": expected_event,
        "teacher_review_portfolio_id": event_teacher_review_gate["portfolio_id"],
        "teacher_review_item_count": int(event_teacher_review_gate.get("review_item_count", 0)),
        "teacher_review_unresolved_count": int(event_teacher_review_gate.get("unresolved_review_count", 0)),
        "teacher_review_release_ready": event_teacher_review_gate.get("teacher_review_release_ready") is True,
        "hard_stop_conditions": blockers,
        "final_report_release_ready": not blockers,
        "portfolio_teacher_decision_gate_enforced": True,
        "automatic_episode_scoring_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "automatic_methodology_invention_allowed": False,
    }
